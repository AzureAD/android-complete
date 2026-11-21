// Copyright (c) Microsoft Corporation. All rights reserved.

import type { Application } from "./application.ts";
import type { Json } from "./lifecycle-contracts.ts";
import type { DecisionPresentation } from "./contracts.ts";
import { digest } from "./shared-contracts.ts";
export type { DecisionPresentation } from "./contracts.ts";

function record(value: unknown): Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readable(value: unknown, indent = ""): string {
    if (Array.isArray(value)) return value.map((entry, index) => `${indent}${index + 1}. ${typeof entry === "object" && entry !== null ? `\n${readable(entry, indent + "   ")}` : String(entry)}`).join("\n");
    if (value !== null && typeof value === "object") return Object.entries(value).map(([key, entry]) => {
        const label = key.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " ");
        return typeof entry === "object" && entry !== null ? `${indent}${label}:\n${readable(entry, indent + "  ")}` : `${indent}${label}: ${String(entry)}`;
    }).join("\n");
    return `${indent}${String(value)}`;
}

function recoveryReview(scope: Record<string, unknown>): string {
    const attempted = record(scope.attempted);
    const local = record(scope.local);
    const attemptedFeature = record(attempted.feature);
    const localFeature = record(local.feature);
    const current = record(localFeature.current);
    const target = record(scope.target);
    const list = (value: unknown) => Array.isArray(value) ? value : [];
    const fingerprint = (value: unknown) => digest(value === undefined ? null : value);
    const lines = [
        "Shared completion recovery",
        `Feature: ${String(current.title)} / ${String(localFeature.id)}`,
        `Scope revision: ${String(current.number)} / ${String(current.id)}`,
        `Destination: ${String(target.organization)}/${String(target.project)} / ADO Feature ${String(target.workItemId)}`,
        `Failed execution: ${String(scope.executionId)}`,
        "",
        "Decision",
        "Preserve the exact current local evidence against the saved original claim or attempted completion.",
        "If the shared record changed independently, stop without overwriting it.",
        "This does not validate the evidence, approve a design or other stage, resume the failed run, start paid work, or retry coding/dispatch.",
        "Blocked or wrong-source research remains historical evidence, not an accepted design.",
        "",
        "Exact evidence binding (SHA-256)",
        `Original shared revision: ${String(scope.previousRevision)}`,
        `Original shared snapshot: ${String(scope.previousDigest)}`,
        `Attempted completion: ${fingerprint(attempted)}`,
        `Current local snapshot to preserve: ${fingerprint(local)}`,
        "",
        "Changes from attempted completion to current local evidence",
    ];
    for (const key of ["feature", "configuration", "lock"]) {
        const same = fingerprint(attempted[key]) === fingerprint(local[key]);
        lines.push(`${key}: ${same ? "unchanged" : "CHANGED"}; local SHA-256 ${fingerprint(local[key])}`);
    }
    const before = record(attempted.state);
    const after = record(local.state);
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
        const same = fingerprint(before[key]) === fingerprint(after[key]);
        const entries = Array.isArray(after[key]) ? `; ${list(after[key]).length} local record(s)` : "";
        lines.push(`${key}: ${same ? "unchanged" : "CHANGED"}${entries}; local SHA-256 ${fingerprint(after[key])}`);
    }
    if (fingerprint(attemptedFeature.current) !== fingerprint(localFeature.current)) {
        lines.push("", "Current requirements differ from the attempted completion", readable(localFeature.current));
    }
    lines.push("", "Local evidence inventory");
    const inventory: Record<string, string[]> = {
        artifacts: ["id", "kind", "hash", "requirementsRevision"],
        decisions: ["id", "stage", "targetId", "approved", "scopeHash"],
        operations: ["key", "kind", "itemId", "status"],
        feedback: ["id", "stage", "revision"],
        prBindings: ["planId", "itemId", "number"],
        waivers: ["id", "monitorId", "itemId", "criterion"],
    };
    for (const [section, fields] of Object.entries(inventory)) {
        const entries = list(after[section]);
        lines.push(`${section}: ${entries.length}`);
        for (const raw of entries) {
            const entry = record(raw);
            const content = record(entry.content);
            lines.push(`- ${fields.filter((field) => entry[field] !== undefined).map((field) => `${field}=${JSON.stringify(entry[field])}`).join("; ")}` +
                (typeof content.recommendation === "string" ? `; recommendation=${JSON.stringify(content.recommendation)}` : "") +
                `; record SHA-256 ${fingerprint(entry)}`);
        }
    }
    lines.push(
        "",
        "Full evidence remains available in the exact command preview's scope.attempted and scope.local snapshots and the feature's artifact records.",
        "This review intentionally inventories those immutable records instead of repeating their full contents. Confirmation remains bound to the complete snapshots, not only this presentation.",
    );
    return lines.join("\n");
}

function designApprovalReview(gate: Record<string, unknown>, scoped: Record<string, unknown>): string {
    const packet = record(scoped.packet);
    const design = record(packet.design);
    const sections = record(design.sections);
    const sources = Array.isArray(packet.sourceManifest) ? packet.sourceManifest.map(record) : [];
    const changes = Array.isArray(design.changes) ? design.changes.map(record) : [];
    const reviews = Array.isArray(packet.reviews) ? packet.reviews : [];
    const lines = [
        "Design approval review",
        `Design artifact: ${String(scoped.designId)}`,
        `Requirements revision: ${String(scoped.requirementsRevision)}`,
        `Gate recommendation: ${String(packet.recommendation || "unknown")}`,
        `Exact approval scope SHA-256: ${String(gate.scopeHash || digest(scoped))}`,
        `Packet SHA-256: ${digest(packet)}`,
        ...(sources.length ? ["", "Inspected source manifest", ...sources.map((s) => `- ${String(s.repository)}: ${String(s.revision)}`)] : []),
    ];
    if (Object.keys(sections).length) {
        lines.push("", "Design sections");
        for (const [key, content] of Object.entries(sections)) {
            lines.push("", `### ${key}`, String(content));
        }
    }
    if (changes.length) {
        lines.push("", `Proposed changes (${changes.length})`);
        for (const change of changes) {
            const reqs = Array.isArray(change.requirementIds) ? change.requirementIds.join(", ") : "";
            lines.push(`- ${String(change.id)} [${String(change.classification)} / ${String(change.implementation)}] (${reqs}): ${String(change.requestedDelta)}`);
        }
    }
    for (const [label, key] of [["Assumptions", "assumptions"], ["Open decisions", "openDecisions"], ["Blocking unknowns", "blockingUnknowns"]] as const) {
        const items = Array.isArray(design[key]) ? design[key] : [];
        if (items.length) lines.push("", label, ...items.map((entry, index) => `${index + 1}. ${String(entry)}`));
    }
    lines.push(
        "",
        `Independent review rounds recorded: ${reviews.length} (evidence & review transcripts omitted here for readability; confirmation binds to the complete packet SHA-256 above).`,
    );
    return lines.join("\n");
}

function planApprovalReview(gate: Record<string, unknown>, scoped: Record<string, unknown>): string {
    const plan = record(scoped.plan);
    const items = Array.isArray(plan.items) ? plan.items.map(record) : [];
    const history = Array.isArray(plan.reviewHistory) ? plan.reviewHistory : [];
    const unknowns = Array.isArray(plan.blockingUnknowns) ? plan.blockingUnknowns : [];
    const lines = [
        "Implementation plan approval review",
        `Plan artifact: ${String(scoped.planId)}`,
        `Requirements revision: ${String(scoped.requirementsRevision)}`,
        `Approved design decision: ${String(scoped.designApprovalId)}`,
        `Exact approval scope SHA-256: ${String(gate.scopeHash || digest(scoped))}`,
        ...(typeof plan.summary === "string" && plan.summary ? ["", "Summary", plan.summary] : []),
        "",
        `Planned work items (${items.length})`,
    ];
    for (const item of items) {
        const deps = Array.isArray(item.dependsOn) && item.dependsOn.length ? item.dependsOn.join(", ") : "none";
        const reqs = Array.isArray(item.requirementIds) ? item.requirementIds.join(", ") : "";
        lines.push(
            "",
            `### ${String(item.id)}: ${String(item.title)} (target: ${String(item.repository)}; depends on: ${deps}; covers: ${reqs})`,
            String(item.description || ""),
        );
        if (Array.isArray(item.acceptanceCriteria) && item.acceptanceCriteria.length) {
            lines.push("Acceptance criteria:", ...item.acceptanceCriteria.map((c, idx) => `${idx + 1}. ${String(c)}`));
        }
        if (Array.isArray(item.files) && item.files.length) lines.push(`Files: ${item.files.map(String).join(", ")}`);
        if (Array.isArray(item.tests) && item.tests.length) lines.push(`Tests: ${item.tests.map(String).join("; ")}`);
    }
    if (unknowns.length) lines.push("", "Blocking unknowns", ...unknowns.map((u, idx) => `${idx + 1}. ${String(u)}`));
    lines.push("", `Independent review attempts recorded: ${history.length} (confirmation binds to the complete plan scope SHA-256 above).`);
    return lines.join("\n");
}

function completeApprovalReview(gate: Record<string, unknown>, scoped: Record<string, unknown>): string {
    const monitor = record(scoped.report);
    const items = Array.isArray(monitor.items) ? monitor.items.map(record) : [];
    const prs = Array.isArray(monitor.prs) ? monitor.prs.map(record) : [];
    const validation = Array.isArray(monitor.validation) ? monitor.validation.map(record) : [];
    const waivers = Array.isArray(scoped.acceptedValidationGaps) ? scoped.acceptedValidationGaps.map(record) : [];
    const errors = Array.isArray(monitor.errors) ? monitor.errors : [];
    const lines = [
        "Feature completion approval review",
        `Monitor artifact: ${String(scoped.monitorId)}`,
        `Requirements revision: ${String(scoped.requirementsRevision)}`,
        `Delivered check: ${String(monitor.delivered)}`,
        `Exact approval scope SHA-256: ${String(gate.scopeHash || digest(scoped))}`,
        "",
        `Work items (${items.length})`,
        ...items.map((wi) => `- ${String(wi.key)}: AB#${String(wi.adoId)} [${String(wi.status)}] — ${String(wi.title)}`),
        "",
        `Pull requests (${prs.length})`,
        ...prs.map((pr) => `- ${String(pr.key)}: ${String(pr.repository)}#${String(pr.number)} [${String(pr.status)}, checks: ${String(pr.checks)}] head ${String(pr.head).slice(0, 12)} (${String(pr.url)})`),
        "",
        `Acceptance-criteria validation (${validation.length})`,
        ...validation.map((v) => `- [${String(v.status)}] ${String(v.itemId)}: ${String(v.criterion)} — ${String(v.evidence)}`),
    ];
    if (waivers.length) {
        lines.push("", `Accepted waivers (${waivers.length})`, ...waivers.map((w) => `- ${String(w.itemId)}: ${String(w.criterion)} (${String(w.reason)})`));
    }
    if (errors.length) {
        lines.push("", "Errors", ...errors.map((e) => `- ${String(e)}`));
    }
    return lines.join("\n");
}

function supersedeExecutionReview(value: Record<string, unknown>): string {
    const cmd = record(value.command);
    const execution = record(value.execution);
    const trace = Array.isArray(execution.trace) ? execution.trace : [];
    const lines = [
        "Release stuck execution authority",
        `Feature ID: ${String(cmd.featureId)}`,
        `Execution ID: ${String(execution.id || cmd.executionId)}`,
        `Workflow kind: ${String(execution.kind)}`,
        `Status / phase: ${String(execution.status)} — ${String(execution.phase)}`,
        `Owning session: ${String(execution.sessionId)} (${String(execution.sessionKind || "chat")})`,
        `Native run ID: ${String(execution.runtimeId ?? "none")}`,
        ...(execution.error ? [`Last error: ${String(execution.error)}`] : []),
        `Reason: ${String(cmd.reason)}`,
        "",
        String(value.effect || ""),
        "",
        `Recorded trace entries: ${trace.length} (omitted for readability; execution SHA-256 ${digest(execution)}).`,
    ];
    return lines.join("\n");
}

export function describeDecision(app: Application, command: Record<string, unknown>, scope: Json): DecisionPresentation {
    const feature = typeof command.featureId === "string" ? app.registry.get(command.featureId) : null;
    const value = record(scope);
    const gate = record(value.gate);
    const scoped = record(gate.scope || scope);
    const decisions: Record<string, [string, string, string, string]> = {
        publish_feature: ["Publish this draft?", "Publish to ADO", "", "Your local draft stays intact. This does not approve the scope or start design."],
        release_shared_claim: ["Release shared execution authority", "Release reviewed claim", "Revoke the inspected shared claim after verifying its owner and remote outcomes.", "Does not stop native work or undo provider effects. Reconcile uncertain sends before retrying."],
        reconcile_shared_completion: ["Reconcile shared completion", "Preserve reviewed local state", "Reconcile the reviewed local evidence against the original claim or attempted completion.", "Does not retry coding or dispatch. Independently changed shared records are not overwritten."],
        update_shared_references: ["Update this feature's reference sources", "Publish reference sources", "Publish the reviewed read-only reference-source scope to this ADO Feature.", "Preserves requirements and history. Prior stage approvals do not approve new source scope. Does not start AI work, create PBIs, dispatch coding, or merge."],
        approve_requirements: ["Approve feature scope", "Approve scope", "Use this brief and its success criteria as the approved input to design.", "Does not start AI work, change code, create PBIs, or dispatch coding tasks."],
        create_backlog: ["Create approved backlog", "Create backlog", "Create and read back the approved ADO work items and links.", "No agent workflow or coding task starts."],
        dispatch_work: ["Dispatch approved work", "Dispatch work", "Run the approved dependency-ready coding tasks in local mode by default (with an independent reviewer loop, then branch push and PR creation) or remote dispatch mode.", "Does not merge PRs automatically."],
        configure: ["Save these project connections?", "Save connections", "Remember these repository and ADO mappings for this project.", "No credentials or ADO work items are changed, and no workflow starts. Changed settings may invalidate active runs or scheduled checks."],
        request_revision: ["Request a revision", "Request revision", "Save your feedback and invalidate downstream approval for the changed work.", "Existing remote work is preserved. No new AI work starts."],
        attach_pr: ["Associate a pull request", "Associate PR", "Verify and link this PR to the selected work item.", "Does not edit, approve, or merge the PR."],
        post_pr_feedback: ["Post PR feedback", "Post this feedback", "Publish the reviewed feedback on the current PR head.", "Mentions may start coding-agent work and consume separate usage. Does not approve or merge."],
        accept_validation_gap: ["Accept a validation gap", "Accept this gap", "Record your reason for accepting this one unresolved acceptance criterion.", "Cannot waive unmerged PRs, failed checks, incomplete work items, or provider errors."],
        reconcile_operation: ["Recover an operation", "Recover operation", "Find its existing receipt or repair idempotent links/status.", "Never launches another coding task. Review the original operation and target."],
        confirm_operation_not_sent: ["Confirm that nothing was sent", "Confirm no remote effect", "Record your investigation that the uncertain remote operation never happened.", "A wrong assessment can duplicate work on a later retry. Nothing is retried by this decision alone."],
        supersede_execution: ["Release a stuck run", "Revoke local authority", "Revoke this run's local authority so an explicit successor can be considered.", "Does not prove native cancellation or undo remote work. Paid work may still finish."],
        set_schedule: command.minutes === null
            ? ["Disable scheduled checks", "Disable schedule", "Stop future scheduled monitor admissions for this feature.", "Does not cancel a check already running."]
            : ["Enable scheduled checks", "Enable schedule", `Run local monitoring every ${String(command.minutes)} minutes while the host is online.`, "Repeated checks may consume Copilot usage. No automatic posting or merging."],
        cancel_feature: ["Stop this feature", "Stop feature", "Stop local admissions and request cancellation of owned runs.", "Remote tasks and work items are not deleted or cancelled."],
        import_legacy: ["Import historical features", "Import selected features", "Create reviewed local records from the selected legacy data.", "Historical approvals are not trusted. No workflow or remote work starts."],
        backup_registry: ["Back up the registry", "Create backup", "Create a consistent private snapshot of the local registry.", "No remote work starts and no live data is replaced."],
        restore_backup_clone: ["Restore into a separate registry", "Create restored copy", "Create an isolated restored copy of the reviewed backup.", "Never replaces the live registry. Schedules are disabled; original hosts must be stopped before using the clone."],
    };
    let [title, approveLabel, effect, boundaries] = decisions[String(command.kind)] || ["Review this action", "Confirm action", "Apply the exact reviewed scope.", "No authority beyond this scope is granted."];
    if (command.kind === "approve_stage") {
        const names: Record<string, [string, string, string]> = {
            design: ["Approve design", "Use the reviewed design as the input to planning.", "Does not start planning or authorize code changes."],
            plan: ["Approve implementation plan", "Accept the reviewed work-item plan and dependencies.", "Does not create PBIs, dispatch coding tasks, or start a workflow."],
            backlog: ["Authorize PBI creation", "Allow the separately invoked backlog action to create/reconcile these ADO work items and links.", "This grants remote-write authority for this exact backlog only. Does not dispatch coding tasks."],
            dispatch: ["Authorize coding tasks", "Allow the separately invoked dispatch action to launch the reviewed dependency-ready coding tasks.", "Coding tasks have separate usage. Does not merge PRs or authorize other work."],
            complete: ["Accept feature delivery", "Record your acceptance of the reviewed delivery evidence.", "Does not merge, publish, or deploy anything."],
        };
        const stage = names[String(command.stage)];
        if (stage) [title, effect, boundaries] = stage;
        approveLabel = command.approved === false ? "Reject this scope" : title;
        if (command.approved === false) { title = `Reject ${String(command.stage)} scope`; effect = "Record your rejection of the current reviewed scope."; boundaries = "Does not undo existing remote work or start a revision workflow."; }
    }
    if (command.kind === "start_workflow") {
        const runs: Record<string, [string, string, string]> = {
            intake: ["Draft success criteria with Factory", "Propose draft success criteria and open questions for review.", "Does not edit or approve the brief, change code, or create remote work."],
            design: ["Start design work", "Research, draft and independently review the design for the approved scope.", "Does not change code, create PBIs, dispatch coding tasks, or approve its own design."],
            plan: ["Start implementation planning", "Generate and independently review an implementation plan, then end for human review.", "Does not create PBIs, dispatch coding tasks, or approve its own plan."],
            implement: ["Start Stage 3 coding and independent review", "Run the local builder and independent code reviewer loop, then push the reviewed branch and open the PR.", "Does not merge PRs or bypass acceptance criteria."],
            monitor: ["Check delivery status", "Read provider state and validate acceptance criteria and independent review rounds.", "Does not merge PRs or change code."],
        };
        const run = runs[String(command.workflow)];
        if (run) [title, effect, boundaries] = run;
        if (command.workflow === "implement" && record(value.delivery).mode === "remote") {
            effect = "Dispatch approved remote coding tasks if needed, independently review available PRs, and post feedback when changes are required.";
            boundaries = "Feedback may trigger further coding-agent work. The workflow stops while awaiting a PR or updated head; it does not merge.";
        }
        approveLabel = title;
        effect += " Uses your Copilot allowance and may consume AI credits.";
    }
    if (command.kind === "dispatch_work" && record(value.delivery).mode === "remote") {
        effect = "Dispatch the approved dependency-ready tasks to remote coding agents and mark the work items Active.";
        boundaries = "Does not start independent PR review or merge. Start the implement workflow separately for review and possible feedback.";
    }
    if (command.kind === "control_run") {
        title = `${String(command.action).replace(/^./, (letter) => letter.toUpperCase())} workflow`;
        approveLabel = title;
        effect = command.action === "resume" ? "Continue the saved native run. AI usage and any previously authorized remote work may continue." : `Request ${String(command.action)} on the owning native run.`;
        boundaries = "Does not grant new scope authority or undo existing remote work.";
    }
    const facts: string[] = [];
    if (command.kind === "publish_feature") {
        const target = record(value.target);
        const itemId = target.workItemId;
        const createNew = target.create === true;
        const organization = typeof target.organization === "string" ? target.organization : "";
        const project = typeof target.project === "string" ? target.project : "";
        const location = [organization, project].filter(Boolean).join("/");
        const destination = createNew
            ? `a new ADO Feature${location ? ` in ${location}` : ""}`
            : typeof itemId === "number" ? `ADO Feature ${itemId}${location ? ` (${location})` : ""}` : `the selected ADO Feature${location ? ` (${location})` : ""}`;
        if (command.startDesign === true) {
            title = "Publish, approve scope, and start design?";
            approveLabel = "Publish, approve & start design";
            effect = `Save this draft to ${destination}, approve its scope, and start the metered native design workflow.`;
            boundaries = "Your local draft stays intact. Uses your Copilot allowance and may consume AI credits. Does not change code, create PBIs, or dispatch coding tasks.";
        } else if (command.approve === true) {
            title = "Publish and approve feature scope?";
            approveLabel = "Publish & approve scope";
            effect = `Save this draft to ${destination} and approve its success criteria for design.`;
            boundaries = "Your local draft stays intact. Does not start AI work, change code, create PBIs, or dispatch coding tasks.";
        } else {
            effect = `Save this draft to ${destination} so your team can access it.`;
        }
    }
    if (command.kind === "configure" || command.kind === "update_shared_references") {
        const connections = command.kind === "configure" ? value : record(value.configuration);
        const repositories = record(connections.repositories);
        for (const [alias, raw] of Object.entries(repositories)) {
            const repository = record(raw);
            const host = typeof repository.hostname === "string" ? repository.hostname : "";
            const slug = typeof repository.slug === "string" ? repository.slug : "";
            const branch = typeof repository.baseBranch === "string" ? repository.baseBranch : "";
            const account = typeof repository.account === "string" && repository.account ? repository.account : null;
            const localPath = typeof repository.localPath === "string" ? repository.localPath : "";
            facts.push(`${alias}: ${host}/${slug}; branch ${branch}${account ? `; account ${account}` : ""}; local folder ${localPath}`);
        }
        for (const [alias, raw] of Object.entries(record(connections.referenceRepositories))) {
            const repo = record(raw);
            facts.push(`${alias} (read-only reference, not a delivery target): ${repo.hostname}/${repo.slug}; branch ${repo.baseBranch}; account ${repo.account || "configured Azure identity"}; local folder ${repo.localPath}`);
        }
        const boards = record(connections.boards);
        const organization = typeof boards.organization === "string" ? boards.organization : "";
        const project = typeof boards.project === "string" ? boards.project : "";
        const areaPath = typeof boards.areaPath === "string" ? boards.areaPath : "";
        if (organization || project) facts.push(`ADO backlog: ${organization}/${project}${areaPath ? `; area ${areaPath}` : ""}`);
        if (command.kind === "update_shared_references") {
            const target = record(value.target);
            facts.push(`Shared destination: ${target.organization}/${target.project} / ADO Feature ${target.workItemId}`);
            facts.push(`Expected shared revision: ${value.sharedRevision}; snapshot ${value.sharedDigest}`);
        }
    }
    if (command.kind === "approve_requirements" && feature) {
        facts.push(`${feature.current.requirements.length} success ${feature.current.requirements.length === 1 ? "criterion" : "criteria"}`);
        if (feature.current.designTasks?.length) facts.push(`${feature.current.designTasks.length} engineering tasks assigned to design`);
    }
    if (command.kind === "start_workflow") {
        const limits = record(command.limits);
        if (limits.maxAiCredits === undefined || limits.maxAiCredits === null) facts.push("No AI-credit cap. Copilot usage is still metered.");
        if (Object.keys(limits).length) facts.push(`Requested limits: ${readable(limits).replaceAll("\n", "; ")}`);
        facts.push(record(scoped.executionOwner).kind === "chat"
            ? "Execution runs in the current Copilot chat; dashboard controls route to this chat."
            : "Execution uses a Factory-owned SDK session; app discovery/attachment is not yet verified.");
    }
    if (command.kind === "start_workflow" || command.kind === "dispatch_work" || (command.kind === "approve_stage" && command.stage === "dispatch")) {
        const delivery = record(value.delivery || scoped.delivery);
        if (typeof delivery.mode === "string") {
            facts.push(`Coding mode: ${delivery.mode} (${delivery.mode === "local" ? "default local Copilot CLI/app builder" : "remote provider coding agent"})`);
        }
        if (typeof delivery.maxReviewRounds === "number") {
            const separateReview = delivery.mode === "remote" &&
                (command.kind === "dispatch_work" || (command.kind === "approve_stage" && command.stage === "dispatch"));
            facts.push(separateReview
                ? `Separately confirmed PR review: up to ${String(delivery.maxReviewRounds)} round(s)`
                : `Independent code review loop: up to ${String(delivery.maxReviewRounds)} round(s)`);
        }
    }
    if (command.kind === "attach_pr") facts.push(`PR #${String(command.number)} / work item ${String(command.itemId)}`);
    if (typeof scoped.pr === "object" && scoped.pr !== null) {
        const pr = record(scoped.pr);
        if (typeof pr.url === "string") facts.push(pr.url);
    }
    const brief = feature?.current;
    const review = command.kind === "reconcile_shared_completion" ? recoveryReview(value)
        : command.kind === "supersede_execution" ? supersedeExecutionReview(value)
        : command.kind === "approve_stage" && command.stage === "design" ? designApprovalReview(gate, scoped)
        : command.kind === "approve_stage" && command.stage === "plan" ? planApprovalReview(gate, scoped)
        : command.kind === "approve_stage" && command.stage === "complete" ? completeApprovalReview(gate, scoped)
        : command.kind === "approve_requirements" && brief
        ? [brief.title, `Scope revision ${brief.number}`, ...(brief.description ? ["", "Feature brief", brief.description] : []),
            "", "Success criteria", ...brief.requirements.map((entry, index) => `${index + 1}. ${entry.text}`),
            ...(brief.designTasks?.length ? ["", "Work for the design agents", ...brief.designTasks.map((task, index) => `${index + 1}. ${task}`)] : [])].join("\n")
        : readable(scope);
    const context = feature
        ? command.kind === "publish_feature" ? `Draft revision ${feature.current.number}` : `Scope revision ${feature.current.number}`
        : "Local project";
    return { title, subject: feature?.current.title || "Software Factory", context,
        approveLabel, effect, boundaries, facts, review,
        canRequestChanges: command.kind === "approve_requirements" ||
            (command.kind === "approve_stage" && command.approved === true && ["design", "plan"].includes(String(command.stage))) };
}

export function decisionMessage(decision: DecisionPresentation, afterReview = false): string {
    return [
        `${decision.title} — ${decision.subject} (${decision.context})`,
        `${decision.effect} ${decision.boundaries}`.trim(),
        ...(decision.facts.length ? ["", ...decision.facts.map((fact) => `• ${fact}`)] : []),
        ...(afterReview ? ["", "Full details are shown above in chat."] : []),
    ].join("\n");
}
