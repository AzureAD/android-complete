// Copyright (c) Microsoft Corporation. All rights reserved.

import type { Application } from "./application.ts";
import type { Json } from "./lifecycle-contracts.ts";
import type { DecisionPresentation } from "./contracts.ts";
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

export function describeDecision(app: Application, command: Record<string, unknown>, scope: Json): DecisionPresentation {
    const feature = typeof command.featureId === "string" ? app.registry.get(command.featureId) : null;
    const value = record(scope);
    const gate = record(value.gate);
    const scoped = record(gate.scope || scope);
    const decisions: Record<string, [string, string, string, string]> = {
        approve_requirements: ["Approve feature scope", "Approve scope", "Use this brief and its success criteria as the approved input to design.", "Does not start AI work, change code, create PBIs, or dispatch coding tasks."],
        configure: ["Update connections", "Use these connections", "Use the reviewed repository, branch, account and board mappings.", "Does not change credentials, move existing work, or start a workflow."],
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
            backlog: ["Authorize PBI creation", "Allow creation/reconciliation of these ADO work items and links when the workflow continues.", "This grants remote-write authority for this exact backlog only. Does not dispatch coding tasks."],
            dispatch: ["Authorize coding tasks", "Allow the reviewed dependency-ready coding tasks to be launched when the workflow continues.", "Coding tasks have separate usage. Does not merge PRs or authorize other work."],
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
            lifecycle: ["Start planning and delivery", "Generate or reuse the implementation plan, then continue only explicitly approved backlog and dispatch work.", "Previously authorized remote work may run. No new backlog/dispatch authority or merge permission is granted."],
            monitor: ["Check delivery status", "Read provider state and validate acceptance criteria.", "Does not post feedback, merge PRs, or change code."],
        };
        const run = runs[String(command.workflow)];
        if (run) [title, effect, boundaries] = run;
        approveLabel = title;
        effect += " Uses your Copilot allowance and may consume AI credits.";
    }
    if (command.kind === "control_run") {
        title = `${String(command.action).replace(/^./, (letter) => letter.toUpperCase())} workflow`;
        approveLabel = title;
        effect = command.action === "resume" ? "Continue the saved native run. AI usage and any previously authorized remote work may continue." : `Request ${String(command.action)} on the owning native run.`;
        boundaries = "Does not grant new scope authority or undo existing remote work.";
    }
    const facts: string[] = [];
    if (command.kind === "approve_requirements" && feature) {
        facts.push(`${feature.current.requirements.length} success ${feature.current.requirements.length === 1 ? "criterion" : "criteria"}`);
        if (feature.current.designTasks?.length) facts.push(`${feature.current.designTasks.length} engineering tasks assigned to design`);
    }
    if (command.kind === "start_workflow") {
        const limits = record(command.limits);
        if (limits.maxAiCredits === undefined || limits.maxAiCredits === null) facts.push("No AI-credit cap. Copilot usage is still metered.");
        if (Object.keys(limits).length) facts.push(`Requested limits: ${readable(limits).replaceAll("\n", "; ")}`);
        facts.push(record(scoped.executionOwner).kind === "chat"
            ? "Execution stays in its bound Copilot chat; dashboard controls go to that same owner."
            : "Execution uses a Factory-owned SDK session; app discovery/attachment is not yet verified.");
    }
    if (command.kind === "attach_pr") facts.push(`PR #${String(command.number)} / work item ${String(command.itemId)}`);
    if (typeof scoped.pr === "object" && scoped.pr !== null) {
        const pr = record(scoped.pr);
        if (typeof pr.url === "string") facts.push(pr.url);
    }
    const brief = feature?.current;
    const review = command.kind === "approve_requirements" && brief
        ? [brief.title, `Scope revision ${brief.number}`, ...(brief.description ? ["", "Feature brief", brief.description] : []),
            "", "Success criteria", ...brief.requirements.map((entry, index) => `${index + 1}. ${entry.text}`),
            ...(brief.designTasks?.length ? ["", "Work for the design agents", ...brief.designTasks.map((task, index) => `${index + 1}. ${task}`)] : [])].join("\n")
        : readable(scope);
    return { title, subject: feature?.current.title || "Software Factory", context: feature ? `Scope revision ${feature.current.number}` : "Local project",
        approveLabel, effect, boundaries, facts, review,
        canRequestChanges: command.kind === "approve_requirements" ||
            (command.kind === "approve_stage" && command.approved === true && ["design", "plan"].includes(String(command.stage))) };
}

export function decisionMessage(decision: DecisionPresentation, afterReview = false): string {
    return [decision.title, `${decision.subject} / ${decision.context}`, "", decision.effect, "", decision.boundaries,
        ...(decision.facts.length ? ["", ...decision.facts.map((fact) => `- ${fact}`)] : []),
        "", afterReview ? "The full review is shown above. Waiting for your decision: approve, request changes where available, or decide later."
            : "Review full details if needed, or choose your decision below."].join("\n");
}
