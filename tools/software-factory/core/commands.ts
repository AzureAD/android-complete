// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, realpathSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import type { Application } from "./application.ts";
import { exactKeys, FactoryError, MAX_COMMAND_BYTES, object, parseCommand, parseRequirements, text, validateActor, type Actor, type Consent } from "./contracts.ts";
import { jsonValue, parseConfiguration, parseMonitor, type ExecutionLimits, type Json, type Stage, type WorkflowKind } from "./lifecycle-contracts.ts";
import { hash, now } from "./lifecycle-store.ts";
import { createBackup, inspectBackup, restoreClone } from "./backups.ts";
import { runtimeFingerprint } from "./evidence.ts";
import { parseProgress } from "./progress.ts";
import { decisionMessage, describeDecision, type DecisionPresentation } from "./decisions.ts";
import { publishTarget, sharedTarget } from "./shared-contracts.ts";

export interface PreparedCommand {
    command: Record<string, unknown>; scope: Json; consentKey: string | null; confirmation: string | null; decision: DecisionPresentation | null;
}
const stages = ["design", "plan", "backlog", "dispatch", "complete"];
function limits(value: unknown): ExecutionLimits {
    if (value === undefined) return {};
    const input = object(value, "Limits");
    exactKeys(input, ["maxAiCredits", "maxConcurrentSubagents", "maxTotalSubagents", "timeoutSeconds"], "Limits");
    const result: ExecutionLimits = {};
    for (const [key, number] of Object.entries(input)) {
        if (key === "maxAiCredits" && number === null) {
            result[key] = null;
            continue;
        }
        if (typeof number !== "number" || !Number.isFinite(number) || number <= 0 ||
            (key.includes("Subagents") && !Number.isSafeInteger(number))) throw new FactoryError("invalid_limits", "Use positive finite limits; agent counts must be integers.");
        result[key] = number;
    }
    return result;
}
function stage(value: unknown): Stage {
    if (!stages.includes(String(value))) throw new FactoryError("invalid_stage", "Unknown lifecycle approval stage.");
    return value as Stage;
}

export function legacyPreview(file: unknown) {
    const location = realpathSync(text(file, "Legacy JSON file"));
    const bytes = readFileSync(location);
    if (bytes.length > 4 * 1024 * 1024) throw new FactoryError("import_too_large", "Legacy import files may not exceed 4 MiB.");
    const input = object(JSON.parse(bytes.toString("utf8")), "Legacy state");
    if (!Array.isArray(input.features)) throw new FactoryError("invalid_legacy_state", "Legacy state must contain a features array.");
    return {
        location, hash: createHash("sha256").update(bytes).digest("hex"),
        features: input.features.map((raw: unknown, index: number) => {
            const value = object(raw, "Legacy feature");
            return { legacyId: typeof value.id === "string" ? value.id : String(index), name: text(value.name, "Legacy feature name"), raw: jsonValue(value) };
        }),
    };
}

export function prepare(app: Application, input: unknown): PreparedCommand {
    const command = object(input, "Software Factory command");
    if (Buffer.byteLength(JSON.stringify(command)) > MAX_COMMAND_BYTES) throw new FactoryError("command_too_large", "Command exceeds 64 KiB.", 413);
    let scope: Json = jsonValue(command);
    let confirm = false;
    switch (command.kind) {
        case "publish_feature": {
            exactKeys(command, ["kind", "featureId", "target", "approve", "startDesign", "limits"], "Command");
            const feature = app.registry.get(text(command.featureId, "Feature ID"));
            const config = app.lifecycle.configuration();
            const target = publishTarget(command.target, config.boards);
            if (command.approve !== undefined && typeof command.approve !== "boolean") throw new FactoryError("invalid_input", "approve must be a boolean.");
            if (command.startDesign !== undefined && typeof command.startDesign !== "boolean") throw new FactoryError("invalid_input", "startDesign must be a boolean.");
            if (command.startDesign === true && command.approve !== true) {
                throw new FactoryError("approval_required", "Combined design startup requires approve: true on publish_feature.", 409);
            }
            if (command.approve === true) {
                if (!feature.current.requirements.length) throw new FactoryError("success_criteria_required", "Add and review at least one success criterion before approving this feature for design.", 409);
                if (feature.current.blockingUnknowns.length) throw new FactoryError("blocking_unknowns", "Resolve the scope decisions before approving for design.", 409);
            }
            const requestedLimits = limits(command.limits);
            const combined = command.approve === true || command.startDesign === true || command.limits !== undefined || "create" in target;
            scope = combined ? {
                feature: jsonValue(feature.current), target: jsonValue(target),
                approve: command.approve === true, startDesign: command.startDesign === true,
                ...(command.startDesign === true ? {
                    configurationId: app.lifecycle.store.configuration()?.id || null,
                    userRequestedLimits: jsonValue(requestedLimits),
                    effectiveAiCreditLimit: requestedLimits.maxAiCredits ?? null,
                    executionOwner: app.executionOwner(feature.id),
                } : {}),
                effect: `${"create" in target ? "Create a new ADO Feature and publish" : "Publish"} a shared successor on ADO.${command.approve === true ? " Approve this exact scope for design." : ""}${command.startDesign === true ? " Start the metered native design workflow." : ""}`,
            } : {
                feature: jsonValue(feature.current), target: jsonValue(target),
                effect: "Publish a new shared successor on this ADO Feature. Upload the current requirements as an attachment. Existing local history is preserved; no previous approvals or artifacts become team approvals.",
            };
            confirm = true;
            break;
        }
        case "open_shared_feature":
            exactKeys(command, ["kind", "target"], "Command");
            sharedTarget(command.target);
            break;
        case "list_shared_features":
            exactKeys(command, ["kind"], "Command");
            app.lifecycle.configuration();
            break;
        case "release_shared_claim": {
            exactKeys(command, ["kind", "featureId", "claimId", "reason"], "Command");
            text(command.reason, "Investigation and recovery reason");
            const shared = app.shared.cached(text(command.featureId, "Feature ID"));
            if (!shared || shared.snapshot.lock?.id !== command.claimId) throw new FactoryError("shared_claim_changed", "Inspect the current shared claim before recovery.", 409);
            scope = { command: jsonValue(command), claim: jsonValue(shared.snapshot.lock),
                effect: "Revoke this shared claim only after inspecting its owner and remote receipts. Does not stop native agents or undo remote effects. Old stage results can no longer be published." };
            confirm = true;
            break;
        }
        case "create_feature": case "revise_requirements":
            parseCommand(command);
            break;
        case "report_progress": {
            exactKeys(command, ["kind", "featureId", "expectedRevision", "requestId", "progress"], "Command");
            const feature = app.registry.get(text(command.featureId, "Feature ID"));
            if (feature.current.id !== text(command.expectedRevision, "Expected revision")) throw new FactoryError("stale_revision", "Read the current feature before reporting progress.", 409);
            text(command.requestId, "Progress request ID");
            parseProgress(command.progress);
            break;
        }
        case "approve_requirements": {
            const parsed = parseCommand(command);
            if (parsed.kind !== "approve_requirements") throw new Error("Approval command mismatch.");
            const feature = app.registry.get(parsed.featureId);
            if (feature.current.id !== parsed.expectedRevision) throw new FactoryError("stale_revision", "Review the current requirements revision.", 409);
            if (!feature.current.requirements.length) throw new FactoryError("success_criteria_required", "Add and review at least one success criterion before approving this feature for design.", 409);
            if (feature.current.blockingUnknowns.length) throw new FactoryError("blocking_unknowns", "Resolve the scope decisions before approving for design.", 409);
            scope = { action: "Approve this brief and its success criteria for design eligibility only", featureId: feature.id, requirements: jsonValue(feature.current) };
            confirm = feature.status !== "ready_for_design";
            break;
        }
        case "configure":
            exactKeys(command, ["kind", "configuration"], "Command");
            scope = jsonValue(parseConfiguration(command.configuration));
            confirm = true;
            break;
        case "approve_stage": {
            exactKeys(command, ["kind", "featureId", "stage", "expectedScopeHash", "approved"], "Command");
            if (typeof command.approved !== "boolean") throw new FactoryError("invalid_input", "An explicit approval or rejection is required.");
            const preview = app.lifecycle.gate(text(command.featureId, "Feature ID"), stage(command.stage));
            if (preview.scopeHash !== command.expectedScopeHash) throw new FactoryError("stale_scope", "Review the current approval preview.", 409);
            if (command.approved && !preview.ready) throw new FactoryError("gate_blocked", preview.blockers.join("\n"), 409);
            scope = { action: command.approved ? "Approve" : "Reject", gate: jsonValue(preview) };
            confirm = !(command.approved === true && preview.approved);
            break;
        }
        case "request_revision":
            exactKeys(command, ["kind", "featureId", "stage", "expectedArtifactId", "feedback"], "Command");
            if (!["design", "plan"].includes(String(command.stage))) throw new FactoryError("invalid_stage", "Only design or plan revisions can be requested.");
            text(command.feedback, "Feedback");
            scope = {
                command: jsonValue(command),
                outstandingEffects: jsonValue(app.lifecycle.store.state(text(command.featureId, "Feature ID")).operations),
                effect: "Supersede future work and invalidate downstream approvals. Existing remote artifacts/tasks are not deleted or cancelled.",
            }; confirm = true;
            break;
        case "confirm_operation_not_sent": {
            exactKeys(command, ["kind", "featureId", "operationKey", "reason"], "Command");
            const operation = app.lifecycle.store.state(text(command.featureId, "Feature ID")).operations.find((entry) => entry.key === command.operationKey);
            if (!operation || operation.status !== "reconciliation_required") throw new FactoryError("operation_not_uncertain", "Only an uncertain operation can receive this explicit recovery decision.", 409);
            text(command.reason, "Provider investigation and reason");
            scope = {
                command: jsonValue(command), operation: jsonValue(operation),
                warning: "You attest after provider investigation that no remote mutation occurred. This permits a fresh send and can duplicate work if your assessment is wrong. Nothing is retried by this decision alone.",
            };
            confirm = true;
            break;
        }
        case "supersede_execution": {
            exactKeys(command, ["kind", "featureId", "executionId", "reason"], "Command");
            const execution = app.lifecycle.store.execution(text(command.featureId, "Feature ID"), text(command.executionId, "Execution ID"));
            if (["completed", "cancelled", "superseded"].includes(execution.status)) throw new FactoryError("terminal_execution", "This execution is already terminal.", 409);
            text(command.reason, "Recovery reason");
            scope = {
                command: jsonValue(command), execution: jsonValue(execution),
                effect: "Revoke local authority and permit an explicit successor after reconciliation. This does not claim native cancellation, move journals, or undo in-flight remote work; paid work may still finish.",
            };
            confirm = true;
            break;
        }
        case "attach_pr":
            exactKeys(command, ["kind", "featureId", "itemId", "number"], "Command");
            app.lifecycle.plan(text(command.featureId, "Feature ID"));
            if (!Number.isSafeInteger(command.number) || Number(command.number) <= 0) throw new FactoryError("invalid_pr", "A positive PR number is required.");
            text(command.itemId, "Item ID");
            confirm = true;
            break;
        case "accept_validation_gap":
            exactKeys(command, ["kind", "featureId", "monitorId", "itemId", "criterion", "reason"], "Command");
            app.lifecycle.plan(text(command.featureId, "Feature ID"));
            for (const key of ["monitorId", "itemId", "criterion", "reason"]) text(command[key], key);
            confirm = true;
            break;
        case "post_pr_feedback":
            exactKeys(command, ["kind", "featureId", "itemId", "requestId", "body"], "Command");
            scope = jsonValue(app.lifecycle.feedbackScope(text(command.featureId, "Feature ID"), text(command.itemId, "Item ID"),
                text(command.requestId, "Request ID"), text(command.body, "Feedback")));
            confirm = true;
            break;
        case "review_pr":
            exactKeys(command, ["kind", "featureId", "itemId"], "Command");
            app.lifecycle.plan(text(command.featureId, "Feature ID"));
            text(command.itemId, "Planned work item key");
            break;
        case "start_workflow": {
            exactKeys(command, ["kind", "featureId", "workflow", "limits"], "Command");
            if (!["intake", "design", "plan", "monitor"].includes(String(command.workflow))) throw new FactoryError("invalid_workflow", "Choose intake, design, plan, or monitor. Multi-stage lifecycle runs are retired.");
            const feature = app.registry.get(text(command.featureId, "Feature ID"));
            if (command.workflow !== "intake" && feature.status !== "ready_for_design") throw new FactoryError("approval_required", "Review and approve success criteria before starting design or delivery.", 409);
            const requestedLimits = limits(command.limits);
            scope = { action: "Start native Dynamic Workflow; this may consume AI credits", featureId: feature.id, requirementsRevision: feature.current.id,
                ...(command.workflow === "intake" ? { brief: jsonValue(feature.current), effect: "Propose draft success criteria and open questions only. Do not edit or approve the brief, start design, or create remote work." } : {}),
                configurationId: app.lifecycle.store.configuration()?.id || null, workflow: String(command.workflow),
                userRequestedLimits: jsonValue(requestedLimits), effectiveAiCreditLimit: requestedLimits.maxAiCredits ?? null,
                executionOwner: app.executionOwner(feature.id) };
            confirm = true;
            break;
        }
        case "control_run":
            exactKeys(command, ["kind", "featureId", "executionId", "action", "limits"], "Command");
            if (!["pause", "resume", "cancel", "refresh"].includes(String(command.action))) throw new FactoryError("invalid_control", "Unknown runtime action.");
            app.lifecycle.store.execution(text(command.featureId, "Feature ID"), text(command.executionId, "Execution ID"));
            limits(command.limits);
            scope = jsonValue(command); confirm = command.action !== "refresh";
            break;
        case "create_backlog": case "dispatch_work": {
            exactKeys(command, ["kind", "featureId"], "Command");
            const preview = app.lifecycle.gate(text(command.featureId, "Feature ID"), command.kind === "create_backlog" ? "backlog" : "dispatch");
            if (!preview.ready || !preview.approved) throw new FactoryError("approval_required", "Approve the exact current operation scope first.", 409);
            scope = { command: jsonValue(command), gate: jsonValue(preview) };
            confirm = true;
            break;
        }
        case "reconcile_operation":
            exactKeys(command, ["kind", "featureId", "operationKey"], "Command");
            app.registry.get(text(command.featureId, "Feature ID"));
            text(command.operationKey, "Operation key");
            scope = {
                command: jsonValue(command),
                operation: jsonValue(app.lifecycle.store.state(text(command.featureId, "Feature ID")).operations.find((entry) => entry.key === command.operationKey) || null),
                effect: "Adopt a unique remote receipt or repair idempotent links/status. Never dispatch another coding task.",
            };
            confirm = true;
            break;
        case "set_schedule": {
            exactKeys(command, ["kind", "featureId", "minutes"], "Command");
            app.lifecycle.plan(text(command.featureId, "Feature ID"));
            if (command.minutes !== null && (!Number.isSafeInteger(command.minutes) || Number(command.minutes) < 1 ||
                !Number.isFinite(new Date(Date.now() + Number(command.minutes) * 60_000).getTime()))) throw new FactoryError("invalid_schedule", "Use a positive whole-minute cadence or null to disable.");
            scope = {
                ...jsonValue(command) as Record<string, Json>,
                requirementsRevision: app.registry.get(text(command.featureId, "Feature ID")).current.id,
                planId: app.lifecycle.store.latest(text(command.featureId, "Feature ID"), "plan")?.id || null,
                configurationId: app.lifecycle.store.configuration()?.id || null, runtimeFingerprint: runtimeFingerprint(),
                effect: "Opt in to local monitor runs at this cadence, including their AI usage; only while a host is online.",
            };
            confirm = true;
            break;
        }
        case "preview_import":
            exactKeys(command, ["kind", "path"], "Command");
            legacyPreview(command.path);
            break;
        case "import_legacy": {
            exactKeys(command, ["kind", "path", "expectedHash", "mappings"], "Command");
            const preview = legacyPreview(command.path);
            if (preview.hash !== command.expectedHash) throw new FactoryError("stale_import", "The legacy file changed; preview it again.", 409);
            if (!Array.isArray(command.mappings) || !command.mappings.length) throw new FactoryError("invalid_import", "Supply explicit Stage 0 requirements for each selected legacy ID.");
            for (const raw of command.mappings) {
                const mapping = object(raw, "Legacy mapping");
                if (!preview.features.some((entry) => entry.legacyId === mapping.legacyId)) throw new FactoryError("invalid_import", "Unknown legacy feature.");
                parseRequirements(mapping.input);
            }
            scope = { source: preview.location, sourceHash: preview.hash, mappings: jsonValue(command.mappings), trustedApprovalsImported: false };
            confirm = true;
            break;
        }
        case "cancel_feature":
            exactKeys(command, ["kind", "featureId"], "Command");
            app.registry.get(text(command.featureId, "Feature ID"));
            scope = { featureId: String(command.featureId), effect: "Stop local admissions and request cancellation of owned runs. Remote coding tasks and work items are not deleted or cancelled." };
            confirm = true;
            break;
        case "export_feature":
            exactKeys(command, ["kind", "featureId"], "Command");
            app.registry.get(text(command.featureId, "Feature ID"));
            break;
        case "backup_registry":
            exactKeys(command, ["kind"], "Command");
            scope = { database: app.registry.databasePath, effect: "Create a consistent private SQLite snapshot of this user's registry. No external actions or credentials are copied." };
            confirm = true;
            break;
        case "inspect_backup":
            exactKeys(command, ["kind", "path"], "Command");
            text(command.path, "Backup path");
            break;
        case "restore_backup_clone":
            exactKeys(command, ["kind", "path", "expectedHash", "originalHostsStopped"], "Command");
            text(command.path, "Backup path"); text(command.expectedHash, "Backup hash");
            if (command.originalHostsStopped !== true) throw new FactoryError("stop_original_hosts", "Explicitly stop original hosts before using a restored registry.", 409);
            scope = { command: jsonValue(command), effect: "Create an isolated restored registry, disable schedules, and mark interrupted runs. Never replace the live database or copy native journals." };
            confirm = true;
            break;
        case "preflight":
            exactKeys(command, ["kind"], "Command");
            app.lifecycle.configuration();
            break;
        default:
            throw new FactoryError("unsupported_command", "Unknown Software Factory command.");
    }
    const decision = confirm ? describeDecision(app, command, scope) : null;
    return {
        command, scope, consentKey: confirm ? hash(scope) : null,
        confirmation: decision ? decisionMessage(decision) : null, decision,
    };
}

export async function dispatch(app: Application, input: unknown, actor: Actor, consent?: Consent): Promise<Record<string, Json>> {
    const prepared = prepare(app, input);
    const command = prepared.command;
    validateActor(actor);
    if (prepared.consentKey && (!consent || consent.granted !== true || consent.revisionId !== prepared.consentKey ||
        JSON.stringify(validateActor(consent.actor)) !== JSON.stringify(validateActor(actor)))) {
        throw new FactoryError("consent_required", "This exact command scope requires a genuine UI or chat-host confirmation.", 403);
    }
    const id = () => text(command.featureId, "Feature ID");
    switch (command.kind) {
        case "publish_feature": {
            const config = app.lifecycle.configuration();
            const target = publishTarget(command.target, config.boards);
            const published = await app.shared.publish(id(), target, actor, { approve: command.approve === true });
            if (command.startDesign === true) {
                const scopeValue = object(prepared.scope, "Execution scope");
                const owner = object(scopeValue.executionOwner, "Execution owner");
                const admission = {
                    requirementsRevision: published.current.id,
                    configurationId: scopeValue.configurationId === null ? null : text(scopeValue.configurationId, "Approved execution configuration"),
                    sessionOwner: {
                        sessionId: owner.sessionId === null ? null : text(owner.sessionId, "Owning session"),
                        kind: owner.kind === "chat" ? "chat" as const : "owned" as const,
                    },
                };
                const execution = await app.startWorkflow(published.id, "design", actor, limits(command.limits), admission);
                return {
                    feature: jsonValue(app.lifecycle.store.view(published.id)),
                    execution: jsonValue(execution),
                    outcome: execution.status === "error" ? "workflow_failed" : "workflow_requested",
                };
            }
            return { feature: jsonValue(published), ...(command.approve === true ? { outcome: "approved" } : {}) };
        }
        case "open_shared_feature":
            return { feature: jsonValue(await app.shared.open(sharedTarget(command.target))) };
        case "list_shared_features": {
            const boards = app.lifecycle.configuration().boards;
            return { features: jsonValue(await app.shared.provider.list(boards.organization, boards.project)) };
        }
        case "release_shared_claim":
            await app.shared.release(id(), text(command.claimId, "Claim ID"), text(command.reason, "Recovery reason"), actor);
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "create_feature": case "revise_requirements": case "approve_requirements": {
            if (command.kind === "approve_requirements" && app.registry.get(id()).status === "ready_for_design") {
                return { outcome: "already_approved", feature: jsonValue(app.lifecycle.store.view(id())) };
            }
            const coreConsent = command.kind === "approve_requirements"
                ? { granted: true as const, revisionId: text(command.expectedRevision, "Revision"), actor } : undefined;
            const feature = app.execute(command, actor, coreConsent);
            return { feature: jsonValue(app.lifecycle.store.view(feature.id)) };
        }
        case "configure":
            return { configuration: jsonValue(app.lifecycle.configure(command.configuration, actor)) };
        case "report_progress":
            return { progress: jsonValue(app.lifecycle.reportProgress(id(), text(command.expectedRevision, "Expected revision"),
                text(command.requestId, "Progress request ID"), command.progress, actor)),
                feature: jsonValue(app.lifecycle.store.view(id())) };
        case "approve_stage":
            if (command.approved === true && app.lifecycle.gate(id(), stage(command.stage)).approved) {
                return { outcome: "already_approved", feature: jsonValue(app.lifecycle.store.view(id())) };
            }
            if (command.stage === "complete" && command.approved === true) {
                const previous = app.lifecycle.monitor(id());
                const fresh = parseMonitor(await app.lifecycle.observe(id()));
                if (!previous || !fresh.delivered || hash({ items: fresh.items, prs: fresh.prs }) !== hash({ items: previous.items, prs: previous.prs })) {
                    throw new FactoryError("stale_delivery_evidence", "Provider delivery state changed; run monitoring and review completion again.", 409);
                }
            }
            app.lifecycle.decide(id(), stage(command.stage), text(command.expectedScopeHash, "Scope hash"), command.approved === true, actor);
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "attach_pr":
            return { pr: jsonValue(await app.lifecycle.attachPr(id(), text(command.itemId, "Item ID"), Number(command.number), actor)), feature: jsonValue(app.lifecycle.store.view(id())) };
        case "accept_validation_gap":
            return { waiver: jsonValue(app.lifecycle.waive(id(), text(command.monitorId, "Monitor ID"), text(command.itemId, "Item ID"),
                text(command.criterion, "Criterion"), text(command.reason, "Reason"), actor)), feature: jsonValue(app.lifecycle.store.view(id())) };
        case "post_pr_feedback":
            return { receipt: await app.lifecycle.postFeedback(id(), text(command.itemId, "Item ID"), text(command.requestId, "Request ID"), text(command.body, "Feedback"), actor, prepared.consentKey!) };
        case "review_pr":
            return { review: await app.lifecycle.review(id(), text(command.itemId, "Item ID")) };
        case "request_revision":
            app.lifecycle.feedback(id(), command.stage as "design" | "plan", text(command.expectedArtifactId, "Artifact ID"), text(command.feedback, "Feedback"));
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "supersede_execution":
            app.lifecycle.store.change(id(), (state) => {
                const execution = state.executions.find((entry) => entry.id === command.executionId);
                if (!execution || ["completed", "cancelled", "superseded"].includes(execution.status)) throw new FactoryError("stale_execution", "The execution state changed.", 409);
                execution.status = "superseded"; execution.error = `Operator recovery: ${text(command.reason, "Reason")}`;
                for (const control of state.controls.filter((entry) => entry.executionId === execution.id && ["pending", "running"].includes(entry.status))) {
                    control.status = "error"; control.error = "Execution superseded by an explicit operator decision.";
                }
                app.lifecycle.store.event(state, "execution_authority_superseded", execution.id);
            });
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "start_workflow": {
            const scope = object(prepared.scope, "Execution scope");
            const owner = object(scope.executionOwner, "Execution owner");
            const admission = { requirementsRevision: text(scope.requirementsRevision, "Approved execution revision"),
                configurationId: scope.configurationId === null ? null : text(scope.configurationId, "Approved execution configuration"),
                sessionOwner: { sessionId: owner.sessionId === null ? null : text(owner.sessionId, "Owning session"),
                    kind: owner.kind === "chat" ? "chat" as const : "owned" as const } };
            const execution = await app.startWorkflow(id(), command.workflow as WorkflowKind, actor, limits(command.limits), admission);
            return { execution: jsonValue(execution), feature: jsonValue(app.lifecycle.store.view(id())),
                outcome: execution.status === "error" ? "workflow_failed" : "workflow_requested" };
        }
        case "control_run":
            return { control: await (await app.runtime()).control(id(), text(command.executionId, "Execution ID"),
                command.action as "pause" | "resume" | "cancel" | "refresh", actor, limits(command.limits)) };
        case "create_backlog":
            return { receipt: await app.lifecycle.backlog(id()), feature: jsonValue(app.lifecycle.store.view(id())) };
        case "dispatch_work":
            await app.lifecycle.repairDispatchStatus(id());
            return { receipts: await app.lifecycle.dispatch(id()), feature: jsonValue(app.lifecycle.store.view(id())) };
        case "reconcile_operation":
            return { receipt: await app.lifecycle.reconcile(id(), text(command.operationKey, "Operation key")), feature: jsonValue(app.lifecycle.store.view(id())) };
        case "confirm_operation_not_sent":
            app.lifecycle.store.change(id(), (state) => {
                const operation = state.operations.find((entry) => entry.key === command.operationKey);
                if (!operation || operation.status !== "reconciliation_required") throw new FactoryError("stale_operation", "The operation's recovery state changed.", 409);
                operation.resolution = { kind: "confirmed_not_sent", reason: text(command.reason, "Reason"), actor, createdAt: now() };
                operation.status = "failed";
                app.lifecycle.store.event(state, "operator_confirmed_no_remote_effect", operation.key);
            });
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "set_schedule":
            app.lifecycle.store.change(id(), (state) => {
                state.schedule = command.minutes === null ? null : {
                    minutes: Number(command.minutes), nextAt: new Date(Date.now() + Number(command.minutes) * 60_000).toISOString(), actor,
                    scopeHash: hash({ requirementsRevision: app.registry.get(id()).current.id, planId: app.lifecycle.store.latest(id(), "plan")?.id || null,
                        configurationId: app.lifecycle.store.configuration()?.id || null, runtimeFingerprint: runtimeFingerprint() }),
                };
            });
            await app.runtime();
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        case "preflight":
            return { providers: await app.lifecycle.provider.preflight(app.lifecycle.configuration()) };
        case "preview_import": {
            const preview = legacyPreview(command.path);
            return { path: preview.location, hash: preview.hash, features: jsonValue(preview.features.map(({ legacyId, name }) => ({ legacyId, name, requiresExplicitRequirements: true }))) };
        }
        case "import_legacy": {
            const preview = legacyPreview(command.path);
            if (preview.hash !== command.expectedHash) throw new FactoryError("stale_import", "The source changed before import admission; preview it again.", 409);
            const imported: Json[] = [];
            for (const raw of command.mappings as unknown[]) {
                const mapping = object(raw, "Legacy mapping");
                const original = preview.features.find((entry) => entry.legacyId === mapping.legacyId)!;
                const feature = app.execute({
                    kind: "create_feature", requestId: `legacy:${preview.hash}:${original.legacyId}`, input: parseRequirements(mapping.input),
                }, actor);
                app.lifecycle.store.artifact(feature.id, "legacy", feature.current.id, {
                    sourceHash: preview.hash, original: original.raw, historicalOnly: true, approvalsTrusted: false,
                });
                imported.push(jsonValue(app.lifecycle.store.view(feature.id)));
            }
            return { features: imported };
        }
        case "export_feature": {
            const feature = app.lifecycle.store.view(id());
            const directory = path.join(path.dirname(app.registry.databasePath), "exports");
            mkdirSync(directory, { recursive: true, mode: 0o700 });
            const file = path.join(directory, `${feature.id}-${hash(feature)}.json`);
            if (!existsSync(file)) writeFileSync(file, JSON.stringify(feature, null, 2), { flag: "wx", mode: 0o600 });
            return { path: file, feature: jsonValue(feature) };
        }
        case "backup_registry":
            return { backup: jsonValue(await createBackup(app.registry)) };
        case "inspect_backup":
            return { backup: jsonValue(await inspectBackup(text(command.path, "Backup path"))) };
        case "restore_backup_clone":
            return { restored: jsonValue(await restoreClone(app.registry, text(command.path, "Backup path"), text(command.expectedHash, "Backup hash"))) };
        case "cancel_feature": {
            const pending = app.lifecycle.store.state(id()).executions.filter((run) => ["starting", "running", "paused"].includes(run.status));
            if (pending.length) {
                const runtime = await app.runtime();
                for (const run of pending) await runtime.control(id(), run.id, "cancel", actor);
            }
            app.lifecycle.store.change(id(), (state) => { state.cancelled = true; state.schedule = null; });
            return { feature: jsonValue(app.lifecycle.store.view(id())) };
        }
        default:
            throw new FactoryError("unsupported_command", "No handler exists for this command.");
    }
}
