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
}

export interface Invocation {
    sessionId: string;
    toolCallId: string;
    agentId?: string;
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

function schema(properties: Record<string, unknown>) {
    return { type: "object", additionalProperties: false, required: Object.keys(properties), properties };
}

export function createChatTools(application: () => Application, host: () => ChatHost): ChatTool[] {
    const reviews = new Map<string, { command: Record<string, unknown>; scopeHash: string; sessionId: string }>();
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

    function followingDecision(app: Application, prepared: PreparedCommand, result?: Record<string, Json>) {
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
            instruction: "Explain the next checkpoint and present its genuine confirmation now. This handoff is not approval and does not authorize execution.",
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
            if (!host().capabilities.ui?.elicitation) throw new FactoryError("confirmation_unavailable", "This host cannot capture supported consent. No browser redirect or automatic approval is used.", 409);
            note("waiting_for_user", `Waiting for your decision: ${preview.decision!.title}.`, "decision-pending");
            let decision: Awaited<ReturnType<typeof requestDecision>>;
            try { decision = await requestDecision(host().ui, preview.decision!, reviewedScope !== undefined); }
            catch (error) {
                note("blocked", `The decision form could not complete: ${error instanceof Error ? error.message : "The host input failed."} No approval was granted.`, "decision-error");
                throw error;
            }
            if (decision === "not_now") {
                note("idle", "You chose to pause this decision. Nothing new was approved or started.", "decision-paused");
                return { outcome: "not_approved", message: "Paused on the user's choice. State plainly that nothing new was approved or started; do not reopen the form." };
            }
            if (decision === "review") {
                const reviewId = randomUUID();
                reviews.set(reviewId, { command: structuredClone(preview.command), scopeHash: preview.consentKey, sessionId: provenance.sessionId! });
                note("waiting_for_user", "Full review requested. A decision form follows the review; no approval has been granted.", "review-requested");
                return {
                    outcome: "review_required", userInputRequired: true, title: preview.decision!.title, review: preview.decision!.review,
                    nextAction: { tool: "software_factory_decide_after_review", arguments: { reviewId } },
                    message: "Show the complete readable review in chat, state that you are waiting for the user's decision, then invoke nextAction in the same response to present the form. Do not end by asking the user to type an approval phrase. Viewing details grants no approval.",
                };
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
                return feedback ? {
                    outcome: "changes_requested", feedback, reviewedCommand: preview.command,
                    message: "Apply this feedback to an unapproved draft, preserving constraints, then present the next review/decision. No approval or execution was authorized. Any separately protected revision action still needs its own confirmation.",
                } : { outcome: "not_approved", message: "Feedback cancelled. State that the conversation is paused; nothing was changed or approved." };
            }
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

    return [
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
                const review = reviews.get(id);
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
            handler: async () => ({ project: application().registry.project, features: await application().features() }),
        },
        {
            name: "software_factory_get_feature",
            description: "Read the current conversational stage, expected output, pending question/rationale, native runs, requirements, artifacts, history and approvals. Feature IDs, not titles, identify features.",
            parameters: schema({ featureId: string }),
            handler: (args) => application().feature(text(object(args, "Arguments").featureId, "Feature ID")),
        },
        {
            name: "software_factory_report_progress",
            description: "Record the current conversational stage, work summary, expected output and next step so chat and dashboard stay understandable. Before asking the user, record waiting_for_user with the question, why it requires a human and your recommendation. Descriptive only: never starts work, changes requirements, approves or advances gates. Use the current revision and a stable retry requestId.",
            parameters: schema({ featureId: string, expectedRevision: string, requestId: string, progress: progressSchema }),
            handler: (args, invocation) => command({ ...object(args, "Arguments"), kind: "report_progress" }, invocation),
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
            description: "Preview any Software Factory command and its exact approval scope without executing it. Commands: report_progress, configure, start_workflow, approve_stage, request_revision, control_run, reconcile_operation, set_schedule, preview_import, import_legacy, export_feature, preflight, cancel_feature, and the requirements commands. Use stable feature/execution IDs.",
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
            description: "Run a Software Factory command. Save private drafts; publish_feature to an existing ADO Feature, list_shared_features or open_shared_feature for team continuation. Start intake/design/plan/monitor as separate stage workflows that end before human review. Approve exact scopes, then separately create_backlog or dispatch_work. Also revise, recover/release inspected shared claims, control/reconcile runs and import/export. Genuine host confirmation is mandatory for approval, publication and paid work. Use preview and get_feature first.",
            parameters: schema({ command: { type: "object" } }),
            handler: (args, invocation) => command(object(args, "Arguments").command, invocation),
        },
        {
            name: "software_factory_get_gates",
            description: "Read a feature's available exact approval previews, including blockers and scope hashes. Missing artifacts are reported explicitly.",
            parameters: schema({ featureId: string }),
            handler: async (args) => {
                const id = text(object(args, "Arguments").featureId, "Feature ID");
                await application().feature(id);
                return ["design", "plan", "backlog", "dispatch", "complete"].map((stage) => {
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
}
