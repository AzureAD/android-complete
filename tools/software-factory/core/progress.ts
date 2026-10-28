// Copyright (c) Microsoft Corporation. All rights reserved.

import { exactKeys, FactoryError, object, text, type Feature } from "./contracts.ts";
import type { ChatProgress, Execution, FactoryFeature, FactoryState, GatePreview, ProgressInput, Stage, WorkflowKind } from "./lifecycle-contracts.ts";

export const workflowLabels: Record<WorkflowKind, string> = {
    intake: "Draft success criteria", design: "Create design", lifecycle: "Plan and deliver", monitor: "Check delivery", check: "Runtime check",
};
export const stageLabels = ["Define success", "Design", "Plan", "Backlog", "Implementation", "Verify & finish"];
export interface ActivityEntry { id: string; createdAt: string; title: string; detail: string; source: string }
export interface AvailableGate extends Partial<GatePreview> { stage: Stage; available: boolean; reason?: string }
export interface NextStep {
    stage: number; title: string; description: string;
    action?: { label: string; target?: "edit" | "suggestions" | "runs"; command?: Record<string, unknown> };
    run?: Execution; gate?: AvailableGate; failed?: boolean; report?: ChatProgress;
}

export function parseProgress(input: unknown): ProgressInput {
    const value = object(input, "Progress update");
    exactKeys(value, ["stage", "status", "summary", "expectedOutput", "nextStep", "question"], "Progress update");
    const stages: ProgressInput["stage"][] = ["intake", "research", "design", "plan", "delivery", "monitor"];
    const statuses: ProgressInput["status"][] = ["working", "waiting_for_user", "blocked", "idle"];
    const stage = stages.find((entry) => entry === value.stage);
    const status = statuses.find((entry) => entry === value.status);
    if (!stage || !status) throw new FactoryError("invalid_progress", "Choose a known stage and descriptive working/waiting/blocked/idle status.");
    const bounded = (input: unknown, label: string) => {
        const content = text(input, label);
        if (content.length > 2000) throw new FactoryError("invalid_progress", `${label} must be concise (at most 2000 characters).`);
        return content;
    };
    const progress: ProgressInput = {
        stage, status, summary: bounded(value.summary, "Current work"),
        expectedOutput: bounded(value.expectedOutput, "Expected output"), nextStep: bounded(value.nextStep, "Next step"),
    };
    if (status === "waiting_for_user") {
        const question = object(value.question, "Question and rationale");
        exactKeys(question, ["text", "whyNeeded", "recommendation"], "Question");
        progress.question = {
            text: bounded(question.text, "Question"), whyNeeded: bounded(question.whyNeeded, "Why this needs the user"),
            recommendation: bounded(question.recommendation, "Recommendation"),
        };
    } else if (value.question !== undefined) throw new FactoryError("invalid_progress", "Only a waiting-for-user update may contain a current question.");
    return progress;
}

export function currentChatProgress(feature: Feature, state: FactoryState): ChatProgress | null {
    const progress = state.progress.filter((entry) => entry.requirementsRevision === feature.current.id).at(-1);
    if (!progress || state.cancelled || state.completed?.revision === feature.current.id ||
        progress.auditSequence !== feature.audit.at(-1)?.sequence || progress.decisionId !== (state.decisions.at(-1)?.id || null)) return null;
    return progress;
}

export function featureActivity(feature: Feature, state: FactoryState): ActivityEntry[] {
    const channel = (kind?: string) => kind === "chat" ? "Copilot chat" : kind === "web" ? "Web UI" : "Workflow";
    const entries = feature.audit.map((event) => {
        const revision = feature.history.find((entry) => entry.id === event.revisionId);
        const title = event.kind === "feature_created" ? "Feature captured" : event.kind === "requirements_revised" ? "Brief and criteria updated" : "Approved for design";
        return { id: `audit:${event.sequence}`, createdAt: event.createdAt, title, source: channel(event.actor.channel),
            detail: revision ? [`Revision ${revision.number}`, revision.description || "", ...revision.requirements.map((entry) => entry.text),
                ...revision.blockingUnknowns.map((question) => `Open question: ${question}`)].filter(Boolean).join("\n") : "" };
    });
    for (const progress of state.progress) {
        entries.push({ id: progress.id, createdAt: progress.createdAt,
            title: `${progress.stage === "research" ? "Research" : workflowLabels[progress.stage === "plan" ? "lifecycle" : progress.stage === "delivery" ? "lifecycle" : progress.stage]}: ${progress.status.replaceAll("_", " ")}`,
            source: channel(progress.actor.channel),
            detail: [progress.summary, `Expected output: ${progress.expectedOutput}`, `Next: ${progress.nextStep}`,
                ...(progress.question ? [`Question: ${progress.question.text}`, `Why: ${progress.question.whyNeeded}`, `Recommendation: ${progress.question.recommendation}`] : [])].join("\n") });
    }
    for (const event of state.events) {
        const execution = state.executions.find((entry) => entry.id === event.reference);
        const decision = state.decisions.find((entry) => entry.id === event.reference);
        const artifact = state.artifacts.find((entry) => entry.id === event.reference);
        const status = event.kind === "workflow_requested" ? "requested" : event.message?.match(/^\w+: (\w+) - /)?.[1];
        entries.push({
            id: event.id, createdAt: event.createdAt,
            title: execution ? `${workflowLabels[execution.kind]}${status ? `: ${status === "error" ? "failed" : status}` : ""}` : artifact ? `${artifact.kind === "intake" ? "Draft suggestions" : artifact.kind} saved`
                : event.kind.replaceAll("_", " "),
            source: channel(event.actor?.channel || decision?.actor.channel || execution?.requestedBy?.channel ||
                (execution?.parentSessionId ? "chat" : undefined)),
            detail: event.message || (execution ? `Workflow requested${execution.error ? `\n${execution.error}` : ""}.` : ""),
        });
    }
    for (const execution of state.executions) {
        if (state.events.some((event) => event.reference === execution.id && event.kind === "workflow_progress")) continue;
        if (execution.status === "starting") continue;
        entries.push({ id: `run:${execution.id}`, createdAt: execution.updatedAt, title: `${workflowLabels[execution.kind]}: ${execution.status}`,
            detail: execution.error || execution.phase, source: channel(execution.requestedBy?.channel || (execution.parentSessionId ? "chat" : undefined)) });
    }
    return entries.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function nextStep(feature: FactoryFeature, gates: AvailableGate[]): NextStep {
    const state = feature.factory;
    const command = (kind: string, extra: Record<string, unknown> = {}) => ({ kind, featureId: feature.id, ...extra });
    const start = (workflow: WorkflowKind, label: string) => ({ label, command: command("start_workflow", { workflow }) });
    const currentRuns = state.executions.filter((run) => run.requirementsRevision === feature.current.id && run.status !== "superseded");
    const active = currentRuns.findLast((run) => ["starting", "running", "paused"].includes(run.status));
    const latest = currentRuns.at(-1);
    const report = feature.currentProgress;
    const stage = feature.currentArtifacts.plan ? state.operations.some((op) => op.kind === "dispatch" && op.status === "completed") ? 4 : 2
        : feature.currentArtifacts.design || feature.status === "ready_for_design" ? 1 : 0;
    if (state.cancelled) return { stage, title: "Feature cancelled", description: "History is preserved. Remote work was not cancelled or deleted." };
    if (feature.lifecycleStatus === "completed") return { stage: 5, title: "Feature delivered", description: "Delivery evidence and the completion decision are saved." };
    if (active && ["starting", "running"].includes(active.status)) return {
        stage: active.kind === "intake" ? 0 : stage, title: /owning Factory runtime/.test(active.phase) ? "Waiting for the owning runtime"
            : /owning Copilot chat/.test(active.phase) || feature.sessionOwnerOnline === false ? "Waiting for the owning chat"
            : active.kind === "intake" ? "Factory is drafting your criteria" : "Work in progress",
        description: feature.sessionOwnerOnline === false
            ? `The original chat controller is offline. Reconnect that chat to inspect/continue its run. Last saved phase: ${active.phase}. No replacement was started.`
            : `${active.requestedBy?.channel === "chat" || active.parentSessionId ? "Started in Copilot chat. " : ""}${active.phase}. Updates appear here automatically.`,
        run: active, action: { label: "View run details", target: "runs" },
    };
    if (!active && report && report.status !== "idle" && (!latest || report.createdAt > latest.updatedAt)) {
        const reportStage = { intake: 0, research: 0, design: 1, plan: 2, delivery: 4, monitor: 5 }[report.stage];
        return { stage: reportStage, title: report.status === "waiting_for_user" ? "Chat is waiting for your decision"
            : report.status === "blocked" ? "Chat reported a blocker" : `Chat update: ${report.stage === "research" ? "research and scope" : report.stage}`,
            description: report.summary, report };
    }
    if (latest?.status === "error" && !active) return {
        stage: latest.kind === "intake" ? 0 : stage, title: `${workflowLabels[latest.kind]} failed`,
        description: latest.error || "The workflow failed. No success is assumed.", failed: true, run: latest,
        action: latest.runtimeId ? { label: "Inspect and recover run", target: "runs" } : start(latest.kind, "Try again"),
    };
    if (feature.status !== "ready_for_design") {
        if (feature.currentArtifacts.intake) return { stage: 0, title: "Review Factory's suggestions", description: "Edit the proposed checklist, then save your chosen criteria. Approval stays separate.",
            action: { label: "Review and edit suggestions", target: "suggestions" } };
        if (feature.current.blockingUnknowns.length) return { stage: 0, title: "Review the remaining scope decisions", description: "Only genuine scope choices should block design. Engineering work belongs in design tasks for the agents to resolve.",
            action: { label: "Review scope decisions", target: "edit" } };
        if (!feature.current.requirements.length) return { stage: 0, title: "Let's define what done looks like", description: "Factory can propose a short checklist from your brief, or you can write it yourself. AI drafting asks for confirmation first.",
            action: start("intake", "Draft success criteria with Factory") };
        return { stage: 0, title: "Review your brief and success criteria", description: "Check the saved scope below, then approve it for design. Approval does not start AI work." };
    }
    const order: Stage[] = ["design", "plan", "backlog", "dispatch", "complete"];
    if (active?.status === "paused") {
        const gate = order.map((name) => gates.find((entry) => entry.stage === name)).find((entry) =>
            entry?.available && !entry.approved && active.phase.toLowerCase().includes(entry.stage));
        if (gate) return { stage: order.indexOf(gate.stage) + 1, title: `Review ${gate.stage === "complete" ? "completion" : gate.stage} before continuing`,
            description: gate.ready ? "Review the full output and the exact action below. Approval does not resume the run automatically." : gate.blockers?.join(" ") || gate.reason || "This step is blocked.",
            gate, run: active };
        return { stage, title: "Ready to continue", description: "This run is paused. Continuing uses its original native journal and asks for execution consent.",
            run: active, action: { label: "Continue workflow", command: command("control_run", { executionId: active.id, action: "resume" }) } };
    }
    if (!feature.currentArtifacts.design) return { stage: 1, title: "Ready, not running.", description: "Your criteria are approved. Start design when you are ready; it uses Copilot usage.", action: start("design", "Run guarded design") };
    const design = gates.find((entry) => entry.stage === "design");
    if (design?.available && !design.approved) return { stage: 1, title: "Review the design", description: "Read the design and independent findings before deciding.", gate: design };
    if (!feature.currentArtifacts.plan) return { stage: 2, title: "Turn the design into a plan", description: "Factory will propose repository-specific work items. It will pause before creating anything remotely.", action: start("lifecycle", "Create implementation plan") };
    const gate = order.slice(1).map((name) => gates.find((entry) => entry.stage === name)).find((entry) => entry?.available && !entry.approved && (entry.ready || ["plan", "backlog"].includes(entry.stage)));
    if (gate) return { stage: order.indexOf(gate.stage) + 1, title: gate.ready ? `Review ${gate.stage === "complete" ? "completion" : gate.stage}` : `Resolve ${gate.stage} blockers`,
        description: gate.ready ? "Inspect the exact scope before approving. Remote changes need their own approval." : gate.blockers?.join(" ") || gate.reason || "This step needs your input.", gate };
    const wave = gates.find((entry) => entry.stage === "dispatch" && entry.approved && entry.ready);
    if (wave) return { stage: 4, title: "Dispatch the approved work", description: "This creates coding tasks for the approved dependency-ready wave. No PR is merged automatically.",
        action: start("lifecycle", "Start approved delivery wave") };
    return { stage: 4, title: "Track implementation and delivery", description: "Check work items, PRs and acceptance criteria. Missing evidence stays visible; nothing merges automatically.", action: start("monitor", "Check delivery status") };
}
