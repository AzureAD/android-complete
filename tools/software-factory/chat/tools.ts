// Copyright (c) Microsoft Corporation. All rights reserved.

import { Application } from "../core/application.ts";
import { randomUUID } from "node:crypto";
import { exactKeys, FactoryError, object, text, type Actor } from "../core/contracts.ts";
import { runtimeReport } from "../core/doctor.ts";
import type { SessionUiApi } from "@github/copilot-sdk";
import { requestChanges, requestDecision } from "./confirmation.ts";
import { hash } from "../core/lifecycle-store.ts";
import { nextStep, type AvailableGate } from "../core/progress.ts";
import type { PreparedCommand } from "../core/commands.ts";
import type { Json, ProgressInput } from "../core/lifecycle-contracts.ts";

export interface ChatHost {
    sessionId: string;
    capabilities: { ui?: { elicitation?: boolean } };
    ui: { confirm: SessionUiApi["confirm"]; elicitation?: SessionUiApi["elicitation"] };
    send?(options: { prompt: string }): Promise<unknown>;
    rpc?: { mode?: { get(): Promise<unknown> } };
    getMode?(): Promise<string | undefined> | string | undefined;
}

export interface Invocation {
    sessionId: string;
    toolCallId: string;
    agentId?: string;
    signal?: AbortSignal;
}

const AUTOPILOT_ELIGIBLE_COMMANDS = new Set([
    "configure",
    "publish_feature",
    "update_shared_references",
    "approve_requirements",
    "approve_stage",
    "request_revision",
    "reconcile_shared_completion",
    "start_workflow",
    "control_run",
    "create_backlog",
    "dispatch_work",
    "post_pr_feedback",
    "reconcile_operation",
    "attach_pr",
    "set_schedule",
]);

async function resolveHostMode(currentHost: ChatHost): Promise<string | undefined> {
    try {
        const raw = currentHost.getMode ? await currentHost.getMode() : await currentHost.rpc?.mode?.get();
        if (typeof raw === "string") return raw;
        if (raw && typeof raw === "object" && "mode" in raw && typeof (raw as { mode?: unknown }).mode === "string") {
            return (raw as { mode: string }).mode;
        }
    } catch {
        // Fall back to interactive confirmation when session mode cannot be queried.
    }
    return undefined;
}

interface ChatTool {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    handler(args: unknown, invocation: Invocation): unknown | Promise<unknown>;
}

const string = { type: "string" };
const requirementsSchema = {
    type: "object",
    additionalProperties: false,
    required: ["title", "requirements", "blockingUnknowns"],
    properties: {
        title: string,
        description: { ...string, description: "The engineer's plain-language feature brief. May be saved before success criteria are known." },
        designTasks: { type: "array", items: string, description: "Engineering work the design agents must resolve. These tasks do not block scope approval or design startup; hard constraints must remain explicit." },
        requirements: {
            type: "array", items: {
                type: "object", additionalProperties: false, required: ["text"],
                properties: { id: string, text: string },
            },
        },
        blockingUnknowns: { type: "array", items: string },
    },
};
const progressSchema = {
    type: "object", additionalProperties: false, required: ["stage", "status", "summary", "expectedOutput", "nextStep"],
    properties: {
        stage: { type: "string", enum: ["intake", "research", "design", "plan", "delivery", "monitor"] },
        status: { type: "string", enum: ["working", "waiting_for_user", "blocked", "idle"] },
        summary: string, expectedOutput: string, nextStep: string,
        question: { type: "object", additionalProperties: false, required: ["text", "whyNeeded", "recommendation"],
            properties: { text: string, whyNeeded: string, recommendation: string } },
    },
};

function compactFeatureForChat(feature: unknown): unknown {
    if (!feature || typeof feature !== "object" || Array.isArray(feature)) return feature;
    if (Buffer.byteLength(JSON.stringify(feature), "utf8") <= 16_000) return feature;
    const cloned = structuredClone(feature) as Record<string, unknown>;
    const currentIds = new Set(
        cloned.currentArtifacts && typeof cloned.currentArtifacts === "object" && !Array.isArray(cloned.currentArtifacts)
            ? Object.values(cloned.currentArtifacts as Record<string, unknown>).filter((v): v is string => typeof v === "string")
            : [],
    );
    const factory = cloned.factory && typeof cloned.factory === "object" && !Array.isArray(cloned.factory)
        ? cloned.factory as Record<string, unknown>
        : null;
    if (factory && Array.isArray(factory.executions)) {
        for (const exec of factory.executions) {
            if (exec && typeof exec === "object" && !Array.isArray(exec) && "trace" in exec) {
                const traceArr = Array.isArray((exec as { trace?: unknown }).trace) ? (exec as { trace: unknown[] }).trace : [];
                delete (exec as Record<string, unknown>).trace;
                (exec as Record<string, unknown>).traceCount = traceArr.length;
            }
        }
    }
    if (factory && Array.isArray(factory.artifacts)) {
        for (const art of factory.artifacts) {
            if (!art || typeof art !== "object" || Array.isArray(art)) continue;
            const rec = art as Record<string, unknown>;
            const isCurrent = typeof rec.id === "string" && currentIds.has(rec.id);
            const content = rec.content;
            if (!content || typeof content !== "object" || Array.isArray(content)) continue;
            const body = content as Record<string, unknown>;
            if (!isCurrent) {
                rec.content = {
                    summarizedForChat: true,
                    ...(body.recommendation !== undefined ? { recommendation: body.recommendation } : {}),
                    ...(Array.isArray(body.reasons) ? { reasons: body.reasons } : {}),
                };
            } else {
                for (const heavyKey of ["evidence", "availableEvidence", "referenceDiscoveries", "reviews", "reviewHistory", "codeFeature"]) {
                    if (heavyKey in body) delete body[heavyKey];
                }
            }
        }
    }
    return cloned;
}

function schema(properties: Record<string, unknown>) {
    return { type: "object", additionalProperties: false, required: Object.keys(properties), properties };
}

export function createChatTools(
    application: () => Application,
    host: () => ChatHost,
    options: { decisionWaitMs?: number; structuredErrors?: boolean } = {},
): ChatTool[] {
    const reviews = new Map<string, { command: Record<string, unknown>; scopeHash: string; sessionId: string }>();
    let activeDecisionSeq = 0;
    function actor(invocation: Invocation): Actor {
        if (host().sessionId !== invocation.sessionId) {
            throw new FactoryError("wrong_session", "The tool call does not belong to this owning session.", 403);
        }
        return { channel: "chat", reference: text(invocation.toolCallId, "Tool call ID"), sessionId: invocation.sessionId };
    }

    function noteDecision(app: Application, prepared: PreparedCommand, provenance: Actor, status: ProgressInput["status"], summary: string, suffix: string,
        requirementsRevision: string | null, question?: ProgressInput["question"]) {
        if (typeof prepared.command.featureId !== "string") return;
        const feature = app.registry.get(prepared.command.featureId);
        if (feature.current.id !== requirementsRevision) return;
        const state = app.lifecycle.store.state(feature.id);
        if (state.cancelled || state.completed?.revision === feature.current.id) return;
        const stage = prepared.command.kind === "approve_requirements" ? "intake"
            : prepared.command.stage === "design" ? "design" : prepared.command.stage === "plan" ? "plan"
            : prepared.command.kind === "start_workflow" && prepared.command.workflow === "design" ? "design" : "delivery";
        const progress: ProgressInput = {
            stage, status, summary,
            expectedOutput: status === "working" ? "An unapproved draft reflecting the requested changes."
                : status === "idle" ? "A recorded decision outcome or an intentional pause, not an unexplained input request."
                : status === "blocked" ? "Recovery from the reported host/action failure; no form response is currently being awaited."
                : "An explicit decision in the displayed form; viewing details is never approval.",
            nextStep: status === "idle" ? "Paused or handed off; no question is left for you to guess. Continue when you choose."
                : status === "working" ? "Apply the requested changes to a draft, then present it for review."
                : status === "blocked" ? "Explain the blocker and safe recovery. Do not imply that work is running or that an unseen confirmation is pending."
                : "Present the review and the actionable decision form; do not require a typed approval phrase.",
            ...(status === "waiting_for_user" ? { question: question || {
                text: `${prepared.decision!.title}: ${prepared.decision!.subject}?`,
                whyNeeded: prepared.decision!.effect,
                recommendation: "Review the scope, then choose its explicit approval action, request changes where available, or decide later.",
            } } : {}),
        };
        app.lifecycle.reportProgress(feature.id, feature.current.id, `${provenance.reference}:${suffix}`, progress, provenance);
    }

    function followingDecision(app: Application, prepared: PreparedCommand, result?: Record<string, Json>, autopilot = false) {
        const publishedId = prepared.command.kind === "publish_feature" && prepared.command.approve === true &&
            prepared.command.startDesign !== true && result?.feature && typeof result.feature === "object" &&
            !Array.isArray(result.feature) && typeof result.feature.id === "string"
            ? result.feature.id : null;
        if (!publishedId && (!["approve_requirements", "approve_stage"].includes(String(prepared.command.kind)) ||
            prepared.command.approved === false || typeof prepared.command.featureId !== "string")) return null;
        const id = publishedId || String(prepared.command.featureId);
        const gates: AvailableGate[] = (["design", "plan", "backlog", "dispatch", "complete"] as const).map((stage) => {
            try { return { available: true, ...app.lifecycle.gate(id, stage) }; }
            catch (error) {
                if (!(error instanceof FactoryError) || error.status >= 500) throw error;
                return { available: false, stage, reason: error.message };
            }
        });
        const step = nextStep(app.lifecycle.store.view(id), gates);
        const next = step.action?.command || (step.gate?.ready && !step.gate.approved ? {
            kind: "approve_stage", featureId: id, stage: step.gate.stage, expectedScopeHash: step.gate.scopeHash, approved: true,
        } : null);
        return next ? {
            tool: "software_factory_command", arguments: { command: next },
            instruction: autopilot
                ? "Autopilot mode is active: explain the next checkpoint briefly and invoke nextAction directly."
                : "Explain the next checkpoint and present its genuine confirmation now. This handoff is not approval and does not authorize execution.",
        } : null;
    }

    async function command(input: unknown, invocation: Invocation, reviewedScope?: string) {
        const app = application();
        const provenance = actor(invocation);
        const preview = await app.preview(input);
        const progressRevision = typeof preview.command.featureId === "string" ? app.registry.get(preview.command.featureId).current.id : null;
        const note = (status: ProgressInput["status"], summary: string, suffix: string, question?: ProgressInput["question"]) =>
            noteDecision(app, preview, provenance, status, summary, suffix, progressRevision, question);
        if (reviewedScope && hash(preview.scope) !== reviewedScope) throw new FactoryError("stale_review", "The reviewed scope changed. Present the updated review before requesting another decision.", 409);
        if (invocation.agentId && preview.command.kind === "report_progress") {
            throw new FactoryError("worker_progress_forbidden", "Native workers report through their workflow. Conversational progress belongs to the top-level chat.", 403);
        }
        if (preview.consentKey) {
            if (invocation.agentId) throw new FactoryError("worker_approval_forbidden", "Background workers cannot authorize Software Factory actions.", 403);
            const sessionMode = await resolveHostMode(host());
            if (sessionMode === "autopilot" && AUTOPILOT_ELIGIBLE_COMMANDS.has(String(preview.command.kind))) {
                const autopilotActor: Actor = { ...provenance, reference: `autopilot:${provenance.reference}` };
                let submitted: Record<string, Json>;
                try {
                    submitted = await app.submit(input, autopilotActor, { granted: true, revisionId: preview.consentKey!, actor: autopilotActor });
                } catch (error) {
                    if (preview.decision) note("blocked", `The action did not complete: ${error instanceof Error ? error.message : "Unknown action failure."} Inspect recorded outcomes before retrying.`, "action-error");
                    throw error;
                }
                if (preview.decision) note("idle", `Autopilot confirmed ${preview.decision.title}.`, "autopilot-confirmed");
                const nextAction = followingDecision(app, preview, submitted, true);
                return {
                    ...submitted,
                    ...(preview.command.kind === "approve_requirements" && !submitted.outcome ? { outcome: "approved" } : {}),
                    autopilotApproved: true,
                    approvedDecision: preview.decision!.title,
                    ...(nextAction ? { nextAction } : {}),
                };
            }
            if (!host().capabilities.ui?.elicitation) throw new FactoryError("confirmation_unavailable", "This host cannot capture supported consent. No browser redirect or automatic approval is used.", 409);
            note("waiting_for_user", `Waiting for your decision: ${preview.decision!.title}.`, "decision-pending");
            const decisionSeq = ++activeDecisionSeq;
            const handleDecision = async (decision: Awaited<ReturnType<typeof requestDecision>>, background = false): Promise<Record<string, unknown>> => {
                if (decision === "not_now") {
                    note("idle", "You chose to pause this decision. Nothing new was approved or started.", "decision-paused");
                    return { outcome: "not_approved", message: "Paused on the user's choice. State plainly that nothing new was approved or started; do not reopen the form." };
                }
                if (decision === "review") {
                    const reviewId = randomUUID();
                    const clonedCommand = structuredClone(preview.command);
                    reviews.set(reviewId, { command: clonedCommand, scopeHash: preview.consentKey!, sessionId: provenance.sessionId! });
                    try { app.lifecycle.store.saveReview(reviewId, provenance.sessionId!, preview.consentKey!, clonedCommand); } catch {}
                    note("waiting_for_user", "Full review requested. A decision form follows the review; no approval has been granted.", "review-requested");
                    const reviewOutcome = {
                        outcome: "review_required", userInputRequired: true, title: preview.decision!.title, review: preview.decision!.review,
                        nextAction: { tool: "software_factory_decide_after_review", arguments: { reviewId } },
                        message: "Show the complete readable review in chat, state that you are waiting for the user's decision, then invoke nextAction in the same response to present the form. Do not end by asking the user to type an approval phrase. Viewing details grants no approval.",
                    };
                    if (background && host().send) {
                        await host().send!({
                            prompt: `The user selected "Review full details in chat" for "${preview.decision!.title}". Present this complete review in chat:\n\n${preview.decision!.review}\n\nThen immediately invoke software_factory_decide_after_review with reviewId "${reviewId}".`,
                        }).catch(() => {});
                    }
                    return reviewOutcome;
                }
                if (decision === "revise") {
                    note("waiting_for_user", "You requested changes. Waiting for the feedback form, not approval.", "feedback-pending", {
                        text: "What would you like changed?",
                        whyNeeded: "Your requested changes are needed before preparing a new unapproved draft.",
                        recommendation: "Describe the changes and any constraints to preserve. Nothing will be approved by submitting feedback.",
                    });
                    let feedback: string | null;
                    try { feedback = await requestChanges(host().ui, preview.decision!); }
                    catch (error) {
                        note("blocked", `The revision-feedback form could not complete: ${error instanceof Error ? error.message : "The host input failed."} Nothing was approved.`, "feedback-error");
                        throw error;
                    }
                    note(feedback ? "working" : "idle", feedback
                        ? "The user requested changes. Revise the draft before seeking approval again."
                        : "Revision feedback was cancelled. No scope was changed or approved.", "changes-requested");
                    if (feedback && background && host().send) {
                        await host().send!({
                            prompt: `The user requested changes on "${preview.decision!.title}":\n${feedback}\nApply this feedback to an unapproved draft, preserving constraints, then present the updated review/decision.`,
                        }).catch(() => {});
                    }
                    return feedback ? {
                        outcome: "changes_requested", feedback, reviewedCommand: preview.command,
                        message: "Apply this feedback to an unapproved draft, preserving constraints, then present the next review/decision. No approval or execution was authorized. Any separately protected revision action still needs its own confirmation.",
                    } : { outcome: "not_approved", message: "Feedback cancelled. State that the conversation is paused; nothing was changed or approved." };
                }
                let submitted: Record<string, Json>;
                try {
                    submitted = await app.submit(input, provenance, { granted: true, revisionId: preview.consentKey!, actor: provenance });
                } catch (error) {
                    if (preview.decision) note("blocked", `The action did not complete: ${error instanceof Error ? error.message : "Unknown action failure."} Inspect recorded outcomes before retrying.`, "action-error");
                    throw error;
                }
                if (preview.decision) note("idle", "The decision was handled. Native execution and recorded results remain authoritative; no hidden input request is pending.", "decision-handled");
                const nextAction = followingDecision(app, preview, submitted);
                if (background && host().send && preview.command.kind !== "start_workflow") {
                    await host().send!({
                        prompt: `The user confirmed "${preview.decision!.title}" in the decision form and the action completed (${String(submitted.outcome || preview.command.kind)}). Continue the Software Factory flow from the updated feature state.`,
                    }).catch(() => {});
                }
                return { ...submitted,
                    ...(preview.command.kind === "approve_requirements" && !submitted.outcome ? { outcome: "approved" } : {}),
                    ...(nextAction ? { nextAction } : {}) };
            };

            const decisionPromise = requestDecision(host().ui, preview.decision!, reviewedScope !== undefined);
            const waitMs = options.decisionWaitMs;
            let timer: NodeJS.Timeout | undefined;
            let onAbort: (() => void) | undefined;
            const races: Promise<
                | { kind: "resolved"; value: Awaited<ReturnType<typeof requestDecision>> }
                | { kind: "rejected"; error: unknown }
                | { kind: "deferred" }
                | { kind: "aborted" }
            >[] = [
                decisionPromise.then((value) => ({ kind: "resolved" as const, value }))
                    .catch((error: unknown) => ({ kind: "rejected" as const, error })),
            ];
            if (waitMs !== undefined) {
                races.push(new Promise<{ kind: "deferred" }>((resolve) => {
                    timer = setTimeout(() => resolve({ kind: "deferred" }), waitMs);
                    timer.unref?.();
                }));
            }
            if (invocation.signal) {
                races.push(new Promise<{ kind: "aborted" }>((resolve) => {
                    if (invocation.signal!.aborted) {
                        resolve({ kind: "aborted" });
                        return;
                    }
                    onAbort = () => resolve({ kind: "aborted" });
                    invocation.signal!.addEventListener("abort", onAbort, { once: true });
                }));
            }
            const waitOutcome = await Promise.race(races).finally(() => {
                if (timer) clearTimeout(timer);
                if (onAbort && invocation.signal) invocation.signal.removeEventListener("abort", onAbort);
            });

            if (waitOutcome.kind === "aborted") {
                decisionPromise.catch(() => {});
                note("idle", "The decision prompt was cancelled before completion. Nothing was approved or started.", "decision-cancelled");
                throw new FactoryError("decision_cancelled", "The decision prompt was cancelled or interrupted before completion. Reopen the confirmation when ready; nothing was approved.", 409);
            }
            if (waitOutcome.kind === "deferred") {
                void decisionPromise.then(async (lateDecision) => {
                    if (decisionSeq !== activeDecisionSeq) return;
                    await handleDecision(lateDecision, true);
                }).catch(() => {});
                return {
                    outcome: "decision_pending",
                    userInputRequired: true,
                    title: preview.decision!.title,
                    message: "The decision form remains open in the chat with no expiration. Do not report a timeout or blocker; tell the user they can respond in the form or reply in chat whenever they return (even hours or days later), and end your turn.",
                };
            }
            if (waitOutcome.kind === "rejected") {
                const message = waitOutcome.error instanceof Error ? waitOutcome.error.message : "The host input failed.";
                note("blocked", `The decision form could not complete: ${message} No approval was granted.`, "decision-error");
                throw waitOutcome.error;
            }
            return handleDecision(waitOutcome.value, false);
        }
        let result: Record<string, Json>;
        try {
            result = await app.submit(input, provenance, preview.consentKey
                ? { granted: true, revisionId: preview.consentKey, actor: provenance } : undefined);
        } catch (error) {
            if (preview.decision) note("blocked", `The action did not complete: ${error instanceof Error ? error.message : "Unknown action failure."} Inspect recorded outcomes before retrying.`, "action-error");
            throw error;
        }
        if (preview.decision) note("idle", "The decision was handled. Native execution and recorded results remain authoritative; no hidden input request is pending.", "decision-handled");
        const nextAction = followingDecision(app, preview, result);
        return { ...result,
            ...(preview.command.kind === "approve_requirements" && !result.outcome ? { outcome: "approved" } : {}),
            ...(nextAction ? { nextAction } : {}) };
    }

    const tools: ChatTool[] = [
        {
            name: "software_factory_decide_after_review",
            description: "After presenting a review_required result's complete review in chat, call this immediately to show Approve / Request changes / Decide later. No typed trigger phrase is needed. The reviewId only reopens a genuine, same-session, exact-scope form; it never grants approval. Do not call before showing the review.",
            parameters: schema({ reviewId: string }),
            handler: async (args, invocation) => {
                const provenance = actor(invocation);
                if (invocation.agentId) throw new FactoryError("worker_approval_forbidden", "Background workers cannot present user decision forms.", 403);
                const input = object(args, "Arguments");
                exactKeys(input, ["reviewId"], "Arguments");
                const id = text(input.reviewId, "Review ID");
                let persisted: { command: Record<string, unknown>; scopeHash: string; sessionId: string } | null = null;
                try { persisted = application().lifecycle.store.consumeReview(id, provenance.sessionId!); } catch {}
                const review = reviews.get(id) || persisted;
                if (!review || review.sessionId !== provenance.sessionId) throw new FactoryError("review_expired", "This review is unavailable in the current chat. Present a fresh review; no approval was granted.", 409);
                reviews.delete(id);
                return command(review.command, invocation, review.scopeHash);
            },
        },
        {
            name: "software_factory_doctor",
            description: "Inspect Software Factory's current chat-host capabilities and implementation limits. Does not launch workflows or open a browser.",
            parameters: schema({}),
            handler: () => runtimeReport({
                sessionId: host().sessionId, elicitation: host().capabilities.ui?.elicitation === true,
            }),
        },
        {
            name: "software_factory_list_features",
            description: "List this project's Software Factory features, exact requirements revisions, approvals, and history from the shared registry.",
            parameters: schema({}),
            handler: async () => ({ project: application().registry.project, features: (await application().features()).map(compactFeatureForChat) }),
        },
        {
            name: "software_factory_get_feature",
            description: "Read the current conversational stage, expected output, pending question/rationale, native runs, requirements, artifacts, history and approvals. Feature IDs, not titles, identify features.",
            parameters: schema({ featureId: string }),
            handler: async (args) => compactFeatureForChat(await application().feature(text(object(args, "Arguments").featureId, "Feature ID"))),
        },
        {
            name: "software_factory_report_progress",
            description: "Record the current conversational stage, work summary, expected output and next step so chat and dashboard stay understandable. Before asking the user, record waiting_for_user with the question, why it requires a human and your recommendation. Descriptive only: never starts work, changes requirements, approves or advances gates. Use the current revision and a stable retry requestId.",
            parameters: schema({ featureId: string, expectedRevision: string, requestId: string, progress: progressSchema }),
            handler: async (args, invocation) => {
                const res = await command({ ...object(args, "Arguments"), kind: "report_progress" }, invocation);
                if (res && typeof res === "object" && "feature" in res) {
                    return { ...res, feature: compactFeatureForChat((res as { feature: unknown }).feature) };
                }
                return res;
            },
        },
        {
            name: "software_factory_create_feature",
            description: "Save a local feature from a plain-language description. Success criteria may be empty while drafting; omit IDs to assign them automatically. Reuse the same requestId on retries. Does not run agents or approve anything.",
            parameters: schema({ requestId: string, input: requirementsSchema }),
            handler: (args, invocation) => {
                const input = object(args, "Arguments");
                return application().execute({ ...input, kind: "create_feature" }, actor(invocation));
            },
        },
        {
            name: "software_factory_revise_requirements",
            description: "Create an immutable requirements revision using the current expectedRevision. Previous approval does not approve changed requirements.",
            parameters: schema({ featureId: string, expectedRevision: string, input: requirementsSchema }),
            handler: (args, invocation) => command({ ...object(args, "Arguments"), kind: "revise_requirements" }, invocation),
        },
        {
            name: "software_factory_preview",
            description: "Preview any Software Factory command and its exact approval scope without executing it. Commands: report_progress, configure, update_shared_references, start_workflow, approve_stage, request_revision, control_run, reconcile_operation, set_schedule, preview_import, import_legacy, export_feature, preflight, cancel_feature, and the requirements commands. Use stable feature/execution IDs.",
            parameters: schema({ command: { type: "object" } }),
            handler: async (args) => {
                try {
                    return await application().preview(object(args, "Arguments").command);
                } catch (error) {
                    if (!(error instanceof FactoryError)) throw error;
                    return {
                        outcome: "blocked",
                        executed: false,
                        error: { code: error.code, message: error.message },
                        nextStep: error.code === "configuration_required"
                            ? "Read software_factory_configuration, gather the missing repository and board mappings, and preview a configure command. Do not retry this preview until configuration is confirmed."
                            : "Resolve the reported blocker, then prepare a fresh preview. No action was executed.",
                    };
                }
            },
        },
        {
            name: "software_factory_command",
            description: "Run a Software Factory command. Save private drafts; publish_feature to an existing ADO Feature, list_shared_features or open_shared_feature for team continuation. Start intake/design/plan/implement/monitor as separate stage workflows that end before human review. Approve exact scopes, then separately create_backlog or dispatch_work (which defaults to local coding with an independent reviewer loop in this Copilot CLI/app session). Remote dispatch only submits coding tasks; start the implement workflow separately to review available PRs and possibly post feedback. Also revise, recover/release inspected shared claims, control/reconcile runs and import/export. Genuine host confirmation is mandatory for approval, publication and paid work. Use preview and get_feature first.",
            parameters: schema({ command: { type: "object" } }),
            handler: (args, invocation) => command(object(args, "Arguments").command, invocation),
        },
        {
            name: "software_factory_get_gates",
            description: "Read a feature's available exact approval previews, including blockers and scope hashes. Missing artifacts are reported explicitly.",
            parameters: schema({ featureId: string }),
            handler: async (args) => {
                const id = text(object(args, "Arguments").featureId, "Feature ID");
                const feature = await application().feature(id);
                return ["design", "plan", "backlog", "dispatch", "complete"].map((stage) => {
                    if (feature.configurationMismatch) return { available: false, stage, reason: feature.configurationMismatch.message, code: feature.configurationMismatch.code };
                    try { return { available: true, ...application().lifecycle.gate(id, stage as "design" | "plan" | "backlog" | "dispatch" | "complete") }; }
                    catch (error) {
                        if (!(error instanceof FactoryError)) throw error;
                        return { available: false, stage, reason: error.message };
                    }
                });
            },
        },
        {
            name: "software_factory_configuration",
            description: "Read this project's configuration status and configured provider, hostname, repository, branch, account, and ADO board mappings. If unconfigured, returns the missing setup steps. No credentials are returned.",
            parameters: schema({}),
            handler: () => {
                const configuration = application().lifecycle.store.configuration();
                return configuration
                    ? { configured: true, configuration }
                    : {
                        configured: false,
                        configuration: null,
                        message: "Software Factory has no repository or ADO board configuration in this local registry.",
                        nextStep: "Research the current repository and ask only for missing or ambiguous repository, account, branch, or ADO values. Then preview and request genuine confirmation for a configure command without a separate preliminary yes/no prompt. Do not infer missing account or branch values or publish before configuration succeeds.",
                    };
            },
        },
        {
            name: "software_factory_approve_requirements",
            description: "Ask the actual user in the current chat host to approve an exact requirements revision. Approval cannot be supplied in tool arguments. Does not start design or authorize remote work.",
            parameters: schema({ featureId: string, expectedRevision: string }),
            handler: (args, invocation) => command({ ...object(args, "Arguments"), kind: "approve_requirements" }, invocation),
        },
    ];
    return options.structuredErrors ? tools.map((tool) => ({ ...tool, handler: async (args, invocation) => {
        try { return await tool.handler(args, invocation); }
        catch (error) {
            if (!(error instanceof FactoryError)) throw error;
            return { outcome: "blocked", error: { code: error.code, message: error.message },
                nextStep: "Inspect this reported blocker and the feature's recorded state before retrying. No success or new approval is implied." };
        }
    } })) : tools;
}
