// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Artifact, Configuration, ConfigRevision, FactoryFeature, Json, RepositoryTarget } from "../../core/lifecycle-contracts.ts";
import type { FeatureQaContext } from "../../core/qa-contracts.ts";
import { request } from "./api";
import { nextStep, stageLabels, type AvailableGate } from "../../core/progress.ts";
import { AskCopilotDrawer, type QaFeedbackBridgeAction } from "./AskCopilotDrawer";
import { FeatureScorecardStrip } from "./AnalyticsStudio";
import { ScopeSummary } from "./Confirmation";
import {
    CodeArenaExplorer,
    DeliveryCommandCenter,
    DesignStudioExplorer,
    PlanStudioExplorer,
    SubagentFlightRecorder,
    formatAiUsage,
    formatDuration,
} from "./StudioExplorers";

type Execute = (command: Record<string, unknown>) => Promise<Record<string, Json>>;
type GateResult = AvailableGate;

export function ArtifactDocument({
    artifact,
    allDesignArtifacts,
    allPlanArtifacts,
    feature,
    onAppendFeedback,
    onSelectItemForOps,
    onPrefillWaiver,
    onPrefillPrFeedback,
    onAskAi,
}: {
    artifact: Artifact;
    allDesignArtifacts?: Artifact[];
    allPlanArtifacts?: Artifact[];
    feature?: FactoryFeature;
    onAppendFeedback?: (snippet: string) => void;
    onSelectItemForOps?: (itemId: string) => void;
    onPrefillWaiver?: (itemId: string, criterion: string) => void;
    onPrefillPrFeedback?: (itemId: string, feedbackBody: string, prNumber?: number) => void;
    onAskAi?: (context: FeatureQaContext) => void;
}) {
    const body = artifact.content;
    if (!body || typeof body !== "object" || Array.isArray(body)) {
        return <p className="muted">Structured artifact recorded ({artifact.kind}).</p>;
    }
    if (artifact.kind === "design" && body.design && typeof body.design === "object" && !Array.isArray(body.design)) {
        const sections = body.design.sections;
        if (sections && typeof sections === "object" && !Array.isArray(sections)) {
            return (
                <DesignStudioExplorer
                    artifact={artifact}
                    allDesignArtifacts={allDesignArtifacts}
                    onAppendFeedback={onAppendFeedback}
                    onAskAi={onAskAi}
                />
            );
        }
    }
    if ((artifact.kind === "plan" || artifact.kind === "planning_attempt") && Array.isArray(body.items)) {
        return (
            <PlanStudioExplorer
                artifact={artifact}
                allPlanArtifacts={allPlanArtifacts}
                feature={feature}
                onAppendFeedback={onAppendFeedback}
                onSelectItemForOps={onSelectItemForOps}
                onAskAi={onAskAi}
            />
        );
    }
    if (artifact.kind === "implementation" || artifact.kind === "implementation_attempt") {
        return <CodeArenaExplorer artifact={artifact} onAskAi={onAskAi} />;
    }
    if (artifact.kind === "monitor" && feature) {
        return (
            <DeliveryCommandCenter
                artifact={artifact}
                feature={feature}
                onPrefillWaiver={onPrefillWaiver}
                onPrefillPrFeedback={onPrefillPrFeedback}
                onAskAi={onAskAi}
            />
        );
    }
    return <p className="muted">Artifact {artifact.id.slice(0, 8)} saved ({artifact.kind}).</p>;
}

function summarizeArtifactOption(artifact: Artifact): string {
    const body = artifact.content && typeof artifact.content === "object" && !Array.isArray(artifact.content)
        ? artifact.content as Record<string, Json>
        : {};
    if (artifact.kind === "design") {
        const rec = typeof body.recommendation === "string" ? body.recommendation : "Saved";
        return `${rec} · ${new Date(artifact.createdAt).toLocaleString()}`;
    }
    if (artifact.kind === "plan" || artifact.kind === "planning_attempt") {
        const count = Array.isArray(body.items) ? body.items.length : 0;
        return `${count} PBI${count === 1 ? "" : "s"} · ${new Date(artifact.createdAt).toLocaleString()}`;
    }
    return new Date(artifact.createdAt).toLocaleString();
}

export function FactoryPanel({
    feature,
    execute,
    onEdit,
    onReviewSuggestions,
    onCheckSetup,
    stage1View,
    onActiveStageChange,
    externalFlightOpen,
    onFlightOpenChange,
    externalQaOpen,
    onQaOpenChange,
    qaPinnedContext,
    onPinQaContext,
}: {
    feature: FactoryFeature;
    execute: Execute;
    onEdit: () => void;
    onReviewSuggestions: () => void;
    onCheckSetup: () => void;
    stage1View?: ReactNode;
    onActiveStageChange?: (stageIndex: number) => void;
    externalFlightOpen?: boolean;
    onFlightOpenChange?: (open: boolean) => void;
    externalQaOpen?: boolean;
    onQaOpenChange?: (open: boolean) => void;
    qaPinnedContext?: FeatureQaContext | null;
    onPinQaContext?: (ctx: FeatureQaContext | null) => void;
}) {
    const [gates, setGates] = useState<GateResult[]>([]);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [gatesLoaded, setGatesLoaded] = useState(false);
    const panelRootRef = useRef<HTMLElement>(null);
    const studioViewportRef = useRef<HTMLDivElement>(null);
    const prComposerRef = useRef<HTMLDetailsElement>(null);
    const waiverComposerRef = useRef<HTMLDetailsElement>(null);
    const feedbackInputRef = useRef<HTMLTextAreaElement>(null);

    const [result, setResult] = useState<Record<string, Json>>();
    const [feedback, setFeedback] = useState("");
    const [revisionDrawerOpen, setRevisionDrawerOpen] = useState(false);
    const [prDrawerOpen, setPrDrawerOpen] = useState(false);
    const [waiverDrawerOpen, setWaiverDrawerOpen] = useState(false);
    const [internalFlightOpen, setInternalFlightOpen] = useState(false);
    const flightOpen = externalFlightOpen !== undefined ? externalFlightOpen : internalFlightOpen;
    const setFlightOpen = (next: boolean) => {
        setInternalFlightOpen(next);
        onFlightOpenChange?.(next);
    };
    const [internalQaOpen, setInternalQaOpen] = useState(false);
    const [internalQaContext, setInternalQaContext] = useState<FeatureQaContext | null>(null);
    const qaOpen = externalQaOpen !== undefined ? externalQaOpen : internalQaOpen;
    const setQaOpen = (next: boolean) => {
        setInternalQaOpen(next);
        onQaOpenChange?.(next);
    };
    const activeQaContext = qaPinnedContext !== undefined ? qaPinnedContext : internalQaContext;
    const setQaContext = (ctx: FeatureQaContext | null) => {
        setInternalQaContext(ctx);
        onPinQaContext?.(ctx);
    };
    const openAskAi = (ctx?: FeatureQaContext | null) => {
        if (ctx !== undefined) setQaContext(ctx);
        setQaOpen(true);
    };
    const [minutes, setMinutes] = useState("60");
    const [maxCreditsCap, setMaxCreditsCap] = useState("");
    const [timeoutMinutesCap, setTimeoutMinutesCap] = useState("");
    const [clearCreditCap, setClearCreditCap] = useState(false);
    const [itemId, setItemId] = useState("");
    const [prNumber, setPrNumber] = useState("");
    const [prFeedback, setPrFeedback] = useState("");
    const [criterion, setCriterion] = useState("");
    const [waiverReason, setWaiverReason] = useState("");
    const [recoveryReason, setRecoveryReason] = useState("");
    const feedbackRequest = useRef(crypto.randomUUID());

    const progress = nextStep(feature, gates);
    const currentGate = progress.gate;

    const [selectedStage, setSelectedStage] = useState<number>(progress.stage);
    const [userPinnedStage, setUserPinnedStage] = useState(false);
    const [selectedExecId, setSelectedExecId] = useState<string>();
    const [selectedDesignId, setSelectedDesignId] = useState<string>();
    const [selectedPlanId, setSelectedPlanId] = useState<string>();

    useEffect(() => {
        if (!userPinnedStage) {
            setSelectedStage(progress.stage);
        }
    }, [progress.stage, userPinnedStage]);

    useEffect(() => {
        onActiveStageChange?.(selectedStage);
    }, [selectedStage, onActiveStageChange]);

    useEffect(() => {
        let cancelled = false;
        setGatesLoaded(false);
        void request<{ gates: GateResult[] }>(`/api/gates/${feature.id}`)
            .then((value) => { if (!cancelled) { setGates(value.gates); setGatesLoaded(true); } })
            .catch((failure: unknown) => { if (!cancelled) setError(failure instanceof Error ? failure.message : "Approval previews could not be loaded."); });
        return () => { cancelled = true; };
    }, [feature]);

    function buildLimitOverrides(): Record<string, number | null> {
        const out: Record<string, number | null> = {};
        if (clearCreditCap) {
            out.maxAiCredits = null;
        } else if (maxCreditsCap.trim()) {
            const parsed = Number(maxCreditsCap);
            if (Number.isFinite(parsed) && parsed > 0) out.maxAiCredits = parsed;
        }
        if (timeoutMinutesCap.trim()) {
            const mins = Number(timeoutMinutesCap);
            if (Number.isFinite(mins) && mins > 0) out.timeoutSeconds = Math.round(mins * 60);
        }
        return out;
    }

    async function run(command: Record<string, unknown>) {
        setBusy(true); setError(""); setResult(undefined);
        try { setResult(await execute(command)); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "The command failed; no success was assumed."); }
        finally { setBusy(false); }
    }

    const latest = (kind: "design" | "plan" | "monitor") => feature.factory.artifacts.find((artifact) => artifact.id === feature.currentArtifacts[kind]);
    const latestImpl = () => [...feature.factory.artifacts].reverse().find((a) =>
        (a.kind === "implementation" || a.kind === "implementation_attempt") && a.requirementsRevision === feature.current.id);
    const start = (workflow: string) => run({ kind: "start_workflow", featureId: feature.id, workflow, limits: buildLimitOverrides() });

    const pickStage = (idx: number) => {
        setSelectedStage(idx);
        setUserPinnedStage(idx !== progress.stage);
        if (flightOpen) setFlightOpen(false);
    };

    const returnToActiveStep = () => {
        setSelectedStage(progress.stage);
        setUserPinnedStage(false);
        if (flightOpen) setFlightOpen(false);
    };

    const openFlightForExecution = (execId?: string) => {
        if (execId) setSelectedExecId(execId);
        setFlightOpen(true);
        setTimeout(() => panelRootRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 40);
    };

    const showRuns = () => {
        openFlightForExecution();
    };

    const appendFeedback = (snippet: string) => {
        setFeedback((prev) => (prev.trim() ? `${prev.trim()}\n${snippet}` : snippet));
        setRevisionDrawerOpen(true);
        setTimeout(() => feedbackInputRef.current?.focus(), 60);
    };

    const selectItemForOps = (targetItemId: string) => {
        setItemId(targetItemId);
        setPrDrawerOpen(true);
        pickStage(5);
        setTimeout(() => prComposerRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60);
    };

    const prefillWaiver = (targetItemId: string, targetCriterion: string) => {
        setItemId(targetItemId);
        setCriterion(targetCriterion);
        setWaiverDrawerOpen(true);
        pickStage(5);
        setTimeout(() => waiverComposerRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60);
    };

    const prefillPrFeedback = (targetItemId: string, bodyText: string, detectedPrNumber?: number) => {
        setItemId(targetItemId);
        if (detectedPrNumber && detectedPrNumber > 0) setPrNumber(String(detectedPrNumber));
        setPrFeedback(bodyText);
        feedbackRequest.current = crypto.randomUUID();
        setPrDrawerOpen(true);
        pickStage(5);
        setTimeout(() => prComposerRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60);
    };

    const handleQaBridgeAction = (action: QaFeedbackBridgeAction) => {
        setQaOpen(false);
        if (action.target === "design") {
            pickStage(1);
            appendFeedback(action.snippet);
            return;
        }
        if (action.target === "plan") {
            pickStage(2);
            appendFeedback(action.snippet);
            return;
        }
        const fallbackItemId = (() => {
            const plan = latest("plan");
            const body = plan?.content && typeof plan.content === "object" && !Array.isArray(plan.content)
                ? plan.content as Record<string, Json>
                : null;
            const first = body && Array.isArray(body.items) ? body.items[0] : null;
            return first && typeof first === "object" && !Array.isArray(first) && typeof first.id === "string" ? first.id : "PBI-1";
        })();
        prefillPrFeedback(action.itemId || itemId || fallbackItemId, action.snippet);
    };

    const actNext = () => {
        const action = progress.action;
        if (action?.target === "edit") onEdit();
        else if (action?.target === "suggestions") onReviewSuggestions();
        else if (action?.target === "runs") showRuns();
        else if (action?.command) void run(action.command);
    };

    const allDesignArtifacts = [...feature.factory.artifacts]
        .reverse()
        .filter((a) => a.kind === "design" && a.requirementsRevision === feature.current.id);
    const fallbackDesignArtifacts = allDesignArtifacts.length > 0
        ? allDesignArtifacts
        : [...feature.factory.artifacts].reverse().filter((a) => a.kind === "design");
    const currentDesignArtifact = latest("design");
    const designArtifact = fallbackDesignArtifacts.find((a) => a.id === selectedDesignId) || currentDesignArtifact || fallbackDesignArtifacts[0];

    const allPlanArtifacts = [...feature.factory.artifacts]
        .reverse()
        .filter((a) => (a.kind === "plan" || a.kind === "planning_attempt") && a.requirementsRevision === feature.current.id);
    const currentPlanArtifact = latest("plan");
    const livePlanArtifact = (() => {
        const activePlanExec = [...feature.factory.executions].reverse().find((e) => e.kind === "plan" && (e.status === "running" || e.status === "starting"));
        if (!activePlanExec?.trace?.length) return undefined;
        const turns = activePlanExec.trace.filter((t) => t.kind === "agent_turn" && t.status === "completed" && t.outputPreview);
        const parsedPlans = new Map<number, Record<string, Json>>();
        const parsedReviews = new Map<number, Record<string, Json>>();
        for (const t of turns) {
            const plannerMatch = /^Factory:planner:(\d+)$/.exec(t.label || "");
            const reviewMatch = /^Factory:plan-review:(\d+)$/.exec(t.label || "");
            if (!plannerMatch && !reviewMatch) continue;
            try {
                const json = JSON.parse(t.outputPreview!) as Record<string, Json>;
                if (plannerMatch && Array.isArray(json.items)) parsedPlans.set(Number(plannerMatch[1]), json);
                if (reviewMatch && typeof json.pass === "boolean") parsedReviews.set(Number(reviewMatch[1]), json);
            } catch {
                // Ignore truncated previews.
            }
        }
        const latestRound = Math.max(-1, ...parsedPlans.keys());
        if (latestRound < 0) return undefined;
        const latestPlan = parsedPlans.get(latestRound)!;
        const reviewHistory: Json[] = [];
        for (let i = 0; i <= latestRound; i++) {
            const cand = parsedPlans.get(i);
            const rev = parsedReviews.get(i);
            if (cand || rev) {
                reviewHistory.push({
                    attempt: i,
                    ...(cand ? { candidate: cand } : {}),
                    ...(rev ? { review: rev } : {}),
                });
            }
        }
        return {
            id: `live-${activePlanExec.id}`,
            kind: "planning_attempt",
            requirementsRevision: feature.current.id,
            parentId: currentDesignArtifact?.id || null,
            generation: activePlanExec.generation.plan,
            configurationId: activePlanExec.configurationId,
            analysisHash: "",
            executionId: activePlanExec.id,
            hash: "",
            createdAt: activePlanExec.updatedAt,
            content: { ...latestPlan, reviewHistory, liveInProgress: true },
        } satisfies Artifact;
    })();
    const planArtifact = allPlanArtifacts.find((a) => a.id === selectedPlanId) || currentPlanArtifact || allPlanArtifacts[0] || livePlanArtifact;

    const liveCodeArtifact: Artifact | undefined = (() => {
        const activeImplExec = [...feature.factory.executions].reverse().find(
            (e) => e.kind === "implement" && ["starting", "running", "paused", "error"].includes(e.status),
        );
        if (!activeImplExec) return undefined;
        const turns = (activeImplExec.trace || []).filter((t) => t.kind === "agent_turn" && t.status === "completed" && typeof t.outputPreview === "string");
        const coderTurns = turns.filter((t) => (t.label || "").startsWith("Factory:coder:"));
        const reviewTurns = turns.filter((t) => (t.label || "").startsWith("Factory:code-review:"));
        if (!coderTurns.length && !reviewTurns.length) return undefined;
        const itemTag = coderTurns[0]?.label?.split(":")[2] || reviewTurns[0]?.label?.split(":")[2] || "PBI-1";
        const roundCount = Math.max(coderTurns.length, reviewTurns.length);
        const reviewHistory: Record<string, Json>[] = [];
        for (let idx = 0; idx < roundCount; idx++) {
            let coderObj: Record<string, Json> | null = null;
            let revObj: Record<string, Json> | null = null;
            try { if (coderTurns[idx]?.outputPreview) coderObj = JSON.parse(coderTurns[idx]!.outputPreview!) as Record<string, Json>; } catch { /* ignore */ }
            try { if (reviewTurns[idx]?.outputPreview) revObj = JSON.parse(reviewTurns[idx]!.outputPreview!) as Record<string, Json>; } catch { /* ignore */ }
            const filesList = Array.isArray(coderObj?.files)
                ? (coderObj.files as Json[]).map((f) => (f && typeof f === "object" && !Array.isArray(f) ? String((f as Record<string, Json>).path || "") : String(f))).filter(Boolean)
                : [];
            const testsList = Array.isArray(coderObj?.testsAddedOrUpdated) ? (coderObj.testsAddedOrUpdated as Json[]).map(String) : [];
            reviewHistory.push({
                round: idx + 1,
                builderSummary: typeof coderObj?.summary === "string" ? coderObj.summary : "Coding round completed; awaiting independent code review.",
                files: filesList,
                testsAddedOrUpdated: testsList,
                ...(revObj ? { review: revObj } : {}),
            });
        }
        return {
            id: `live-code-${activeImplExec.id}`,
            kind: "implementation_attempt",
            requirementsRevision: feature.current.id,
            parentId: currentPlanArtifact?.id || null,
            generation: 0,
            configurationId: activeImplExec.configurationId,
            analysisHash: "",
            executionId: activeImplExec.id,
            hash: "",
            createdAt: activeImplExec.updatedAt,
            content: {
                itemId: itemTag,
                mode: "local",
                status: activeImplExec.status === "running" ? "in_progress" : activeImplExec.status,
                roundsCompleted: reviewHistory.length,
                reviewHistory,
            },
        } satisfies Artifact;
    })();
    const codeArtifact = latestImpl() || liveCodeArtifact;
    const monitorArtifact = latest("monitor");

    const provisionedWorkItems = feature.factory.operations
        .filter((op) => op.kind === "work_item" && op.status === "completed" && op.receipt && typeof op.receipt === "object" && !Array.isArray(op.receipt))
        .map((op) => {
            const r = op.receipt as Record<string, Json>;
            const linked = feature.factory.operations.some((l) => l.kind === "links" && l.itemId === op.itemId && l.planId === op.planId && l.status === "completed");
            const disp = feature.factory.operations.find((d) => d.kind === "dispatch" && d.itemId === op.itemId && d.planId === op.planId);
            return {
                key: String(r.key || op.itemId),
                adoId: Number(r.adoId || 0),
                title: String(r.title || op.itemId),
                repository: String(r.repository || ""),
                status: String(r.status || "New"),
                url: typeof r.url === "string" ? r.url : "",
                linked,
                dispatchStatus: disp?.status || null,
                prUrl: disp?.receipt && typeof disp.receipt === "object" && !Array.isArray(disp.receipt) && typeof (disp.receipt as Record<string, Json>).url === "string"
                    ? String((disp.receipt as Record<string, Json>).url)
                    : "",
            };
        });

    const stageStatusSubtitle = (index: number): string => {
        if (index === 0) {
            const count = feature.current.requirements.length;
            return feature.status === "ready_for_design" ? `${count} Criteria Approved` : `${count} Criteria Draft`;
        }
        if (index === 1) {
            if (designArtifact && typeof designArtifact.content === "object" && designArtifact.content && !Array.isArray(designArtifact.content)) {
                const rec = typeof designArtifact.content.recommendation === "string" ? designArtifact.content.recommendation : "Ready";
                return rec === "GO to Planning" ? "Approved Spec" : rec;
            }
            return progress.stage === 1 ? "Current Step" : "Pending";
        }
        if (index === 2) {
            if (planArtifact && typeof planArtifact.content === "object" && planArtifact.content && !Array.isArray(planArtifact.content)) {
                const planObj = planArtifact.content as Record<string, Json>;
                const count = Array.isArray(planObj.items) ? planObj.items.length : 0;
                return `${count} Planned PBI${count === 1 ? "" : "s"}`;
            }
            return progress.stage === 2 ? "Current Step" : "Pending";
        }
        if (index === 3) {
            return provisionedWorkItems.length > 0 ? `${provisionedWorkItems.length} ADO Items` : progress.stage === 3 ? "Current Step" : "Pending";
        }
        if (index === 4) {
            if (codeArtifact && typeof codeArtifact.content === "object" && codeArtifact.content && !Array.isArray(codeArtifact.content)) {
                const items = Array.isArray(codeArtifact.content.items) ? codeArtifact.content.items : [];
                const first = items[0] && typeof items[0] === "object" && !Array.isArray(items[0]) ? items[0] as Record<string, Json> : null;
                if (first?.observerStatus === "ready-for-human-merge") return "PR Ready ✓";
                return `${items.length || 1} PBI Built`;
            }
            return progress.stage === 4 ? "Current Step" : "Pending";
        }
        if (monitorArtifact && typeof monitorArtifact.content === "object" && monitorArtifact.content && !Array.isArray(monitorArtifact.content)) {
            const val = Array.isArray(monitorArtifact.content.validation) ? monitorArtifact.content.validation : [];
            const ok = val.filter((v) => v && typeof v === "object" && !Array.isArray(v) && (v as Record<string, Json>).status === "addressed").length;
            return val.length > 0 ? `${ok}/${val.length} Verified` : "Observed";
        }
        return progress.stage === 5 ? "Current Step" : "Preview";
    };

    const anyRunActive = feature.factory.executions.some((e) => e.status === "running" || e.status === "starting");
    const totalSubagentsAcrossRuns = feature.factory.executions.reduce((sum, e) => sum + (e.usage?.subagents ?? 0), 0);
    const bestExecForKind = (kind: string) =>
        [...feature.factory.executions].reverse().find((e) => e.kind === kind && ((e.usage?.subagents ?? 0) > 0 || e.status === "running"))
        || [...feature.factory.executions].reverse().find((e) => e.kind === kind);
    const designRunForLink = bestExecForKind("design");
    const planRunForLink = bestExecForKind("plan");
    const implRunForLink = bestExecForKind("implement");
    const monitorRunForLink = bestExecForKind("monitor");
    const viewingActiveStep = selectedStage === progress.stage;
    const gateStageIndex = currentGate?.stage === "design" ? 1
        : currentGate?.stage === "plan" ? 2
            : currentGate?.stage === "backlog" ? 3
                : currentGate?.stage === "dispatch" ? 4
                    : currentGate?.stage === "complete" ? 5 : -1;

    return <section ref={panelRootRef} className="card factory-panel" aria-label="Workflow lifecycle">
        <div className="workspace-mode-switcher" role="tablist" aria-label="Feature studio mode">
            <button
                type="button"
                role="tab"
                aria-selected={!flightOpen}
                className={`mode-switch-tab ${!flightOpen ? "active" : ""}`}
                onClick={() => setFlightOpen(false)}
            >
                <span className="mode-switch-icon">🧭</span>
                <div className="mode-switch-copy">
                    <strong>Lifecycle Pipeline</strong>
                    <small>6 Guarded Stages · Stage {selectedStage + 1}: {stageLabels[selectedStage]}</small>
                </div>
            </button>
            <button
                type="button"
                role="tab"
                aria-selected={flightOpen}
                aria-expanded={flightOpen}
                className={`mode-switch-tab flight-tab ${flightOpen ? "active" : ""}`}
                onClick={() => setFlightOpen(!flightOpen)}
            >
                <span className={`status-dot ${anyRunActive ? "pulse" : "ready"}`} />
                <div className="mode-switch-copy">
                    <strong>Subagent Flight Recorder &amp; Runs ({feature.factory.executions.length})</strong>
                    <small>
                        {totalSubagentsAcrossRuns > 0
                            ? `${totalSubagentsAcrossRuns} Specialist Subagents · Full Prompts, Reasoning & Tool Transcripts`
                            : "Inspect Prompts, Agent Reasoning, Messages & Tool Calls"}
                    </small>
                </div>
                <span className="mode-switch-cta">{flightOpen ? "Viewing ▴" : "Open Transcripts →"}</span>
            </button>
            <button
                type="button"
                aria-expanded={qaOpen}
                className={`mode-switch-tab qa-tab ${qaOpen ? "active" : ""}`}
                onClick={() => setQaOpen(!qaOpen)}
            >
                <span className="mode-switch-icon">✦</span>
                <div className="mode-switch-copy">
                    <strong>Ask Copilot · Read-Only Q&amp;A</strong>
                    <small>Ask about design choices, PBIs, diffs &amp; PRs without disrupting runs</small>
                </div>
                <span className="mode-switch-cta">{qaOpen ? "Open ▸" : "Ask AI →"}</span>
            </button>
        </div>

        <div className="phase-track-header">
            <div className="phase-track-header-left">
                <span className="eyebrow">
                    {flightOpen
                        ? `FLIGHT RECORDER ACTIVE · CLICK ANY STAGE BELOW TO RETURN TO PIPELINE`
                        : `LIFECYCLE PIPELINE · SELECT ANY STAGE`}
                </span>
                {!viewingActiveStep && !flightOpen && (
                    <button type="button" className="ghost-chip-btn" onClick={returnToActiveStep}>
                        ← Return to Active Step ({stageLabels[progress.stage]})
                    </button>
                )}
                {flightOpen && (
                    <button type="button" className="ghost-chip-btn" onClick={() => setFlightOpen(false)}>
                        ← Back to {stageLabels[selectedStage]} Stage
                    </button>
                )}
            </div>
            <button
                type="button"
                className="ghost-chip-btn ask-ai-chip"
                onClick={() => openAskAi()}
            >
                ✦ Ask Copilot about {stageLabels[selectedStage]}
            </button>
        </div>

        <div className="phase-track" role="group" aria-label="Feature progress">
            {stageLabels.map((label, index) => {
                const isSelected = !flightOpen && selectedStage === index;
                const isCurrent = index === progress.stage;
                const isPast = index < progress.stage;
                return (
                    <button
                        type="button"
                        key={label}
                        aria-current={isCurrent ? "step" : undefined}
                        aria-pressed={isSelected}
                        onClick={() => pickStage(index)}
                        className={`phase ${isCurrent ? "current" : isPast ? "past" : ""} ${isSelected ? "selected-stage" : ""}`}
                    >
                        <div className="phase-top-row">
                            <span className="phase-step-badge">{isPast ? "✓" : index + 1}</span>
                            <strong>{label}</strong>
                        </div>
                        <small>{stageStatusSubtitle(index)}</small>
                    </button>
                );
            })}
        </div>

        <FeatureScorecardStrip
            feature={feature}
            onOpenFlightRecorder={() => setFlightOpen(true)}
        />

        {flightOpen && (
            <section className="flight-drawer-panel" aria-label="Live subagents and transcripts">
                <div className="flight-drawer-head">
                    <div>
                        <span className="eyebrow">AGENT TELEMETRY, PLAYBOOKS &amp; EXECUTION CONTROLS</span>
                        <h3>Subagent Flight Recorder</h3>
                    </div>
                    <button type="button" className="ghost-chip-btn" onClick={() => setFlightOpen(false)}>
                        Close Flight Recorder ✕
                    </button>
                </div>
                <SubagentFlightRecorder feature={feature} selectedExecutionId={selectedExecId} onSelectExecution={setSelectedExecId} />

                {feature.factory.executions.length > 0 && (
                    <details className="run-controls-drawer">
                        <summary>Workflow Run History, Controls &amp; Optional Guardrails ({feature.factory.executions.length})</summary>
                        <div className="guardrails-row">
                            <label>
                                Max AI Credits (optional)
                                <input
                                    type="number"
                                    min="1"
                                    placeholder="Uncapped by default"
                                    value={maxCreditsCap}
                                    disabled={clearCreditCap}
                                    onChange={(e) => setMaxCreditsCap(e.target.value)}
                                />
                            </label>
                            <label>
                                Timeout in Minutes (optional)
                                <input
                                    type="number"
                                    min="1"
                                    placeholder="Default stage timeout"
                                    value={timeoutMinutesCap}
                                    onChange={(e) => setTimeoutMinutesCap(e.target.value)}
                                />
                            </label>
                            <label className="checkbox-inline">
                                <input
                                    type="checkbox"
                                    checked={clearCreditCap}
                                    onChange={(e) => setClearCreditCap(e.target.checked)}
                                />
                                Clear existing credit cap on resume
                            </label>
                        </div>
                        <div className="runs-compact-list">
                            {[...feature.factory.executions].reverse().map((execution) => {
                                const isActive = execution.status === "running" || execution.status === "starting";
                                const canResume = ["paused", "halted", "error"].includes(execution.status);
                                const execControls = feature.factory.controls.filter((control) => control.executionId === execution.id);
                                const latestControl = execControls.at(-1);
                                return (
                                    <div className="run-compact-card" key={execution.id}>
                                        <div className="run-compact-main">
                                            <div className="studio-badge-row">
                                                <strong>{execution.kind.toUpperCase()}</strong>
                                                <span className={`studio-pill ${execution.status === "completed" ? "emerald" : isActive ? "blue" : execution.status === "error" ? "crimson" : "amber"}`}>
                                                    {execution.status}
                                                </span>
                                                <span className="muted">{execution.phase}</span>
                                            </div>
                                            <div className="run-compact-metrics">
                                                {execution.usage && (
                                                    <>
                                                        <span className="mono-chip">{formatDuration(execution.usage.activeMs)}</span>
                                                        <span className="mono-chip">{execution.usage.subagents} subagents</span>
                                                        <span className="mono-chip emerald">{formatAiUsage(execution.usage.nanoAiu)}</span>
                                                    </>
                                                )}
                                                <span className="muted">{new Date(execution.updatedAt).toLocaleString()}</span>
                                            </div>
                                            {execution.observationError && <p className="message error">{execution.observationError}</p>}
                                            {execution.status === "error" && execution.error && <p className="message error">{execution.error}</p>}
                                            {latestControl?.error && <p className="message error">{latestControl.action}: {latestControl.error}</p>}
                                        </div>
                                        <div className="run-compact-actions">
                                            <button
                                                type="button"
                                                className="ghost-chip-btn"
                                                disabled={busy}
                                                onClick={() => void run({ kind: "control_run", featureId: feature.id, executionId: execution.id, action: "refresh", limits: buildLimitOverrides() })}
                                            >
                                                Refresh
                                            </button>
                                            {isActive && (
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    disabled={busy}
                                                    onClick={() => void run({ kind: "control_run", featureId: feature.id, executionId: execution.id, action: "pause", limits: buildLimitOverrides() })}
                                                >
                                                    Pause
                                                </button>
                                            )}
                                            {canResume && (
                                                <button
                                                    type="button"
                                                    className="secondary"
                                                    disabled={busy}
                                                    onClick={() => void run({ kind: "control_run", featureId: feature.id, executionId: execution.id, action: "resume", limits: buildLimitOverrides() })}
                                                >
                                                    Resume
                                                </button>
                                            )}
                                            {(isActive || execution.status === "paused") && (
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    disabled={busy}
                                                    onClick={() => void run({ kind: "control_run", featureId: feature.id, executionId: execution.id, action: "cancel", limits: buildLimitOverrides() })}
                                                >
                                                    Cancel
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </details>
                )}
            </section>
        )}

        {!flightOpen && (viewingActiveStep || progress.failed || progress.report ? (
            <div className={`next-action ${progress.failed ? "failed" : ""}`} aria-label="Next step">
                <span className="eyebrow">{progress.failed ? "NEEDS ATTENTION" : progress.report ? "CHAT PROGRESS" : progress.run?.status === "running" || progress.run?.status === "starting" ? "IN PROGRESS" : "ACTIVE STEP"}</span>
                <h2>{progress.title}</h2>
                <p role={progress.failed ? "alert" : undefined}>{progress.description}</p>
                {progress.run && (
                    <p className="run-origin">
                        {progress.run.requestedBy?.channel === "chat" || progress.run.parentSessionId
                            ? `${progress.failed ? "Requested" : "Started"} in Copilot chat`
                            : progress.run.requestedBy?.channel === "web"
                                ? "Requested in the dashboard"
                                : "Recorded workflow"} · {new Date(progress.run.updatedAt).toLocaleString()}
                    </p>
                )}
                {progress.report && <section className="chat-progress" aria-label="Current chat progress">
                    <dl><dt>Expected result</dt><dd>{progress.report.expectedOutput}</dd>
                        <dt>Next step</dt><dd>{progress.report.nextStep}</dd></dl>
                    {progress.report.question && <div className="question-context"><h3>{progress.report.question.text}</h3>
                        <p><strong>Why your input matters:</strong> {progress.report.question.whyNeeded}</p>
                        <p><strong>Recommendation:</strong> {progress.report.question.recommendation}</p>
                        <p className="field-help">Answer in the originating Copilot chat. This saved question does not grant approval or start work.</p></div>}
                    <p className="field-help">Last reported in {progress.report.actor.channel === "chat" ? "Copilot chat" : "the dashboard"} at {new Date(progress.report.createdAt).toLocaleString()}.
                        {" "}This is a conversational update, not proof of a running background agent.</p>
                </section>}
                <div className="factory-actions">
                    {progress.action && <button className="primary" disabled={busy || !gatesLoaded} onClick={actNext}>{progress.action.label}</button>}
                    {(progress.run?.status === "running" || progress.run?.status === "starting") && !flightOpen && (
                        <button type="button" className="secondary" onClick={() => {
                            setSelectedExecId(progress.run?.id);
                            setFlightOpen(true);
                        }}>
                            Inspect live subagents ({progress.run.usage?.subagents ?? 0})
                        </button>
                    )}
                    {progress.stage === 0 && progress.action?.target !== "edit" && !progress.run && !progress.report && (
                        <button className="secondary" disabled={busy} onClick={onEdit}>Write or edit criteria myself</button>
                    )}
                    {progress.failed && <button className="secondary" disabled={busy} onClick={onCheckSetup}>Check setup</button>}
                    {progress.failed && progress.run?.runtimeId === null && (
                        <span className="field-help">No native run ID was recorded. Retrying creates a new, explicitly confirmed attempt; it never replays remote writes.</span>
                    )}
                </div>
            </div>
        ) : (
            <div className="compact-active-step-bar" aria-label="Next step">
                <div className="compact-active-step-info">
                    <span className="studio-pill emerald">ACTIVE: {(stageLabels[progress.stage] || "Stage").toUpperCase()}</span>
                    <strong>{progress.title}</strong>
                </div>
                <div className="compact-active-step-actions">
                    {progress.action && (
                        <button className="primary compact-btn" disabled={busy || !gatesLoaded} onClick={actNext}>
                            {progress.action.label}
                        </button>
                    )}
                    <button type="button" className="secondary compact-btn" onClick={returnToActiveStep}>
                        Open Active Step →
                    </button>
                </div>
            </div>
        ))}

        {error && <div role="alert" className="message error">{error}</div>}
        {result?.outcome === "not_approved" && <p className="message notice" role="status">Not started. You chose not to approve this action.</p>}

        {!flightOpen && <div ref={studioViewportRef} className="studio-stage-viewport">
            {selectedStage === 0 && (
                <section className="stage-1-workspace" aria-label="Scope and Criteria Studio">
                    {stage1View}
                </section>
            )}

            {selectedStage === 1 && (
                <section className="studio-spotlight" aria-label={currentGate?.stage === "design" ? "Review design" : "Design Studio"}>
                    {designRunForLink && (
                        <div className="stage-transcript-deeplink">
                            <span>
                                <strong>Subagent Transcripts Available:</strong> Inspect the Researcher, Design Author &amp; Independent Design Reviewer prompts, reasoning thoughts, and source reads.
                            </span>
                            <button type="button" className="ghost-chip-btn" onClick={() => openFlightForExecution(designRunForLink.id)}>
                                🛰️ Inspect Design Subagents ({designRunForLink.usage?.subagents ?? "Run"}) →
                            </button>
                        </div>
                    )}
                    {!currentDesignArtifact && designArtifact && (
                        <div className="studio-iteration-banner">
                            <div>
                                <strong>Revision run in progress — viewing latest saved Design</strong>
                                <p className="muted">
                                    A newer Design run is active. Below is the most recent completed design specification ({new Date(designArtifact.createdAt).toLocaleString()}).
                                </p>
                            </div>
                            {progress.run && (
                                <button type="button" className="secondary" onClick={() => { setSelectedExecId(progress.run?.id); setFlightOpen(true); }}>
                                    Watch live run →
                                </button>
                            )}
                        </div>
                    )}
                    {fallbackDesignArtifacts.length > 1 && (
                        <div className="artifact-version-bar">
                            <label htmlFor="design-version-select" className="eyebrow">DESIGN REVISION</label>
                            <select
                                id="design-version-select"
                                value={designArtifact?.id || ""}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                            >
                                {fallbackDesignArtifacts.map((art, idx) => (
                                    <option key={art.id} value={art.id}>
                                        Revision #{fallbackDesignArtifacts.length - idx} — {summarizeArtifactOption(art)}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    {designArtifact ? (
                        <>
                            <ArtifactDocument
                                artifact={designArtifact}
                                allDesignArtifacts={fallbackDesignArtifacts}
                                feature={feature}
                                onAppendFeedback={appendFeedback}
                                onAskAi={openAskAi}
                            />
                            {currentGate?.stage === "design" && (
                                <div className="gate-decision-bar">
                                    {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                                    {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                                    <div className="factory-actions">
                                        <button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                                            kind: "approve_stage", featureId: feature.id, stage: "design", expectedScopeHash: currentGate.scopeHash, approved: true,
                                        })}>Approve design</button>
                                        <button className="secondary" disabled={busy} onClick={() => void run({
                                            kind: "approve_stage", featureId: feature.id, stage: "design", expectedScopeHash: currentGate.scopeHash, approved: false,
                                        })}>Reject this scope</button>
                                    </div>
                                </div>
                            )}
                            <details className="contextual-action-drawer" open={revisionDrawerOpen} onToggle={(e) => setRevisionDrawerOpen((e.currentTarget as HTMLDetailsElement).open)}>
                                <summary>Request AI Changes to This Design Specification</summary>
                                <div className="drawer-body">
                                    <p className="field-help">Describe architectural changes needed. Tip: click &ldquo;+ Comment on section&rdquo; above to quote a specific section.</p>
                                    <textarea
                                        ref={feedbackInputRef}
                                        id={`design-feedback-${feature.id}`}
                                        value={feedback}
                                        onChange={(event) => setFeedback(event.target.value)}
                                        rows={3}
                                        placeholder="What should the Design Author revise?"
                                    />
                                    <div className="factory-actions">
                                        <button className="secondary" disabled={busy || !feedback.trim() || !latest("design")} onClick={() => void run({
                                            kind: "request_revision", featureId: feature.id, stage: "design", expectedArtifactId: latest("design")?.id, feedback,
                                        })}>Request design revision</button>
                                    </div>
                                </div>
                            </details>
                        </>
                    ) : progress.run?.kind === "design" && (progress.run.status === "running" || progress.run.status === "starting") ? (
                        <div className="studio-empty-lens">
                            <span className="studio-pill blue">STAGE 2 · DESIGN IN PROGRESS</span>
                            <h3>Design Run Active: {progress.run.phase}</h3>
                            <p className="muted">
                                Repository Researcher, Design Author, and Independent Design Reviewer are actively building and auditing the 10-section specification.
                            </p>
                            <button type="button" className="secondary" onClick={() => { setSelectedExecId(progress.run?.id); setFlightOpen(true); }}>
                                Open Flight Recorder →
                            </button>
                        </div>
                    ) : (
                        stage1View || (
                            <div className="studio-empty-lens">
                                <span className="studio-pill blue">STAGE 2 · GUARDED DESIGN</span>
                                <h3>Ready to Author Technical Design</h3>
                                <p className="muted">Click &ldquo;Run guarded design&rdquo; above to dispatch repository research, architecture synthesis, and independent review.</p>
                            </div>
                        )
                    )}
                </section>
            )}

            {selectedStage === 2 && (
                <section className="studio-spotlight" aria-label={currentGate?.stage === "plan" ? "Review plan" : "Plan Studio"}>
                    {planRunForLink && (
                        <div className="stage-transcript-deeplink">
                            <span>
                                <strong>Subagent Transcripts Available:</strong> Read the Feature Planner &amp; Independent Plan Reviewer step-by-step reasoning and review messages.
                            </span>
                            <button type="button" className="ghost-chip-btn" onClick={() => openFlightForExecution(planRunForLink.id)}>
                                🛰️ Inspect Plan Subagents ({planRunForLink.usage?.subagents ?? "Run"}) →
                            </button>
                        </div>
                    )}
                    {allPlanArtifacts.length > 1 && (
                        <div className="artifact-version-bar">
                            <label htmlFor="plan-version-select" className="eyebrow">PLAN REVISION</label>
                            <select
                                id="plan-version-select"
                                value={planArtifact?.id || ""}
                                onChange={(e) => setSelectedPlanId(e.target.value)}
                            >
                                {allPlanArtifacts.map((art, idx) => (
                                    <option key={art.id} value={art.id}>
                                        Revision #{allPlanArtifacts.length - idx} — {summarizeArtifactOption(art)}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    {planArtifact ? (
                        <>
                            <ArtifactDocument
                                artifact={planArtifact}
                                allPlanArtifacts={allPlanArtifacts}
                                feature={feature}
                                onAppendFeedback={appendFeedback}
                                onSelectItemForOps={selectItemForOps}
                                onAskAi={openAskAi}
                            />
                            {currentGate?.stage === "plan" && (
                                <div className="gate-decision-bar">
                                    {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                                    {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                                    <div className="factory-actions">
                                        <button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                                            kind: "approve_stage", featureId: feature.id, stage: "plan", expectedScopeHash: currentGate.scopeHash, approved: true,
                                        })}>Approve plan</button>
                                        <button className="secondary" disabled={busy} onClick={() => void run({
                                            kind: "approve_stage", featureId: feature.id, stage: "plan", expectedScopeHash: currentGate.scopeHash, approved: false,
                                        })}>Reject this scope</button>
                                    </div>
                                </div>
                            )}
                            {latest("plan") && (
                                <details className="contextual-action-drawer" open={revisionDrawerOpen} onToggle={(e) => setRevisionDrawerOpen((e.currentTarget as HTMLDetailsElement).open)}>
                                    <summary>Request AI Changes to This PBI Plan</summary>
                                    <div className="drawer-body">
                                        <p className="field-help">Describe how PBIs, boundaries, or dependencies should be adjusted.</p>
                                        <textarea
                                            ref={feedbackInputRef}
                                            id={`plan-feedback-${feature.id}`}
                                            value={feedback}
                                            onChange={(event) => setFeedback(event.target.value)}
                                            rows={3}
                                            placeholder="What should the Feature Planner revise?"
                                        />
                                        <div className="factory-actions">
                                            <button className="secondary" disabled={busy || !feedback.trim() || !latest("plan")} onClick={() => void run({
                                                kind: "request_revision", featureId: feature.id, stage: "plan", expectedArtifactId: latest("plan")?.id, feedback,
                                            })}>Request plan revision</button>
                                        </div>
                                    </div>
                                </details>
                            )}
                        </>
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill purple">STAGE 3 · IMPLEMENTATION PLAN &amp; DAG</span>
                            <h3>Execution Waves &amp; Repository-Scoped PBIs</h3>
                            <p className="muted">
                                Once design is approved, the Feature Planner and Independent Plan Reviewer decompose the specification into ordered PBIs with target files and test suites.
                            </p>
                        </div>
                    )}
                </section>
            )}

            {selectedStage === 3 && (
                <section className="studio-spotlight" aria-label="Backlog Studio">
                    <div className="scope-inspector-card">
                        <div className="studio-badge-row">
                            <span className="studio-pill purple">STAGE 4 · AZURE DEVOPS BACKLOG</span>
                            <span className="mono-chip">{provisionedWorkItems.length} Provisioned PBI{provisionedWorkItems.length === 1 ? "" : "s"}</span>
                        </div>
                        <h3>Provisioned Azure DevOps Work Items</h3>
                        {currentGate?.stage === "backlog" && (
                            <div className="gate-decision-bar">
                                {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                                {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                                <div className="factory-actions">
                                    <button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                                        kind: "approve_stage", featureId: feature.id, stage: "backlog", expectedScopeHash: currentGate.scopeHash, approved: true,
                                    })}>Approve backlog</button>
                                    <button className="secondary" disabled={busy} onClick={() => void run({
                                        kind: "approve_stage", featureId: feature.id, stage: "backlog", expectedScopeHash: currentGate.scopeHash, approved: false,
                                    })}>Reject this scope</button>
                                </div>
                            </div>
                        )}
                        {provisionedWorkItems.length > 0 ? (
                            <div className="delivery-cards-grid">
                                {provisionedWorkItems.map((wi) => (
                                    <div key={wi.key} className="delivery-pbi-card">
                                        <div className="delivery-pbi-head">
                                            <div className="studio-badge-row">
                                                <span className="mono-chip emerald">{wi.key}</span>
                                                {wi.url ? (
                                                    <a href={wi.url} target="_blank" rel="noreferrer" className="studio-pill purple">
                                                        ADO #{wi.adoId} ↗
                                                    </a>
                                                ) : (
                                                    <span className="mono-chip">ADO #{wi.adoId}</span>
                                                )}
                                                {wi.repository && <span className="mono-chip blue">{wi.repository}</span>}
                                            </div>
                                            <div className="studio-badge-row">
                                                <span className="studio-pill neutral">{wi.status}</span>
                                                <span className={`studio-pill ${wi.linked ? "emerald" : "amber"}`}>
                                                    {wi.linked ? "✓ Linked to Feature & Predecessors" : "Pending links"}
                                                </span>
                                                {wi.prUrl && (
                                                    <a href={wi.prUrl} target="_blank" rel="noreferrer" className="studio-pill emerald">
                                                        PR Opened ↗
                                                    </a>
                                                )}
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn ask-ai-chip"
                                                    onClick={() => openAskAi({
                                                        kind: "pbi",
                                                        title: `${wi.key} (ADO #${wi.adoId}) — ${wi.title}`,
                                                        subtitle: wi.repository || wi.status,
                                                        snippet: `ADO Work Item #${wi.adoId} (${wi.status}) · Repository: ${wi.repository}${wi.prUrl ? ` · PR: ${wi.prUrl}` : ""}`,
                                                    })}
                                                >
                                                    ✦ Ask AI
                                                </button>
                                            </div>
                                        </div>
                                        <h4>{wi.title}</h4>
                                    </div>
                                ))}
                            </div>
                        ) : planArtifact ? (
                            <p className="muted">
                                Plan is ready. Approving and running Backlog Creation provisions these PBIs under your Azure DevOps Feature and links their predecessor dependencies automatically.
                            </p>
                        ) : (
                            <p className="muted">
                                Complete Design (Stage 2) and Plan (Stage 3) first; approved PBIs are then created and linked in Azure DevOps here.
                            </p>
                        )}
                    </div>
                </section>
            )}

            {selectedStage === 4 && (
                <section className="studio-spotlight" aria-label="Code Arena Studio">
                    {implRunForLink && (
                        <div className="stage-transcript-deeplink">
                            <span>
                                <strong>Subagent Transcripts Available:</strong> Inspect Coding Builder, Independent Code Reviewer &amp; PR Observer conversations and tool calls.
                            </span>
                            <button type="button" className="ghost-chip-btn" onClick={() => openFlightForExecution(implRunForLink.id)}>
                                🛰️ Inspect Implementation Subagents ({implRunForLink.usage?.subagents ?? "Run"}) →
                            </button>
                        </div>
                    )}
                    {currentGate?.stage === "dispatch" && (
                        <div className="gate-decision-bar" style={{ marginBottom: 16 }}>
                            {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                            {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                            <div className="factory-actions">
                                <button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                                    kind: "approve_stage", featureId: feature.id, stage: "dispatch", expectedScopeHash: currentGate.scopeHash, approved: true,
                                })}>Approve dispatch</button>
                                <button className="secondary" disabled={busy} onClick={() => void run({
                                    kind: "approve_stage", featureId: feature.id, stage: "dispatch", expectedScopeHash: currentGate.scopeHash, approved: false,
                                })}>Reject this scope</button>
                            </div>
                        </div>
                    )}
                    {codeArtifact ? (
                        <ArtifactDocument artifact={codeArtifact} feature={feature} onAskAi={openAskAi} />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill emerald">STAGE 5 · IMPLEMENTATION &amp; PR OBSERVER</span>
                            <h3>Coding Builder, Independent Code Reviewer &amp; Autonomous PR Observer</h3>
                            <p className="muted">
                                Implements dependency-ready PBIs, verifies every acceptance criterion via independent review, publishes the pull request, and supervises live CI checks and tactical repairs until ready for human merge.
                            </p>
                        </div>
                    )}
                </section>
            )}

            {selectedStage === 5 && (
                <section className="studio-spotlight" aria-label="Delivery Studio">
                    {monitorRunForLink && (
                        <div className="stage-transcript-deeplink">
                            <span>
                                <strong>Subagent Transcripts Available:</strong> Read the Stage 6 Delivery Auditor&apos;s live repository inspection and criteria verification reasoning.
                            </span>
                            <button type="button" className="ghost-chip-btn" onClick={() => openFlightForExecution(monitorRunForLink.id)}>
                                🛰️ Inspect Delivery Auditor ({monitorRunForLink.usage?.subagents ?? "Run"}) →
                            </button>
                        </div>
                    )}
                    {monitorArtifact ? (
                        <ArtifactDocument
                            artifact={monitorArtifact}
                            feature={feature}
                            onPrefillWaiver={prefillWaiver}
                            onPrefillPrFeedback={prefillPrFeedback}
                            onAskAi={openAskAi}
                        />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill amber">STAGE 6 · VERIFY &amp; FINISH</span>
                            <h3>Live PR Status, Acceptance Criteria Audit &amp; Human Sign-Off</h3>
                            <p className="muted">
                                Click &ldquo;Check delivery status&rdquo; above to verify live ADO work items, GitHub/ADO pull requests, CI checks, and acceptance criteria before final completion sign-off.
                            </p>
                        </div>
                    )}

                    {currentGate?.stage === "complete" && (
                        <div className="gate-decision-bar">
                            {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                            {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                            <div className="factory-actions">
                                <button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                                    kind: "approve_stage", featureId: feature.id, stage: "complete", expectedScopeHash: currentGate.scopeHash, approved: true,
                                })}>Approve completion</button>
                                <button className="secondary" disabled={busy} onClick={() => void run({
                                    kind: "approve_stage", featureId: feature.id, stage: "complete", expectedScopeHash: currentGate.scopeHash, approved: false,
                                })}>Reject this scope</button>
                            </div>
                        </div>
                    )}

                    <div className="stage6-tools-grid">
                        <details
                            ref={prComposerRef}
                            className="contextual-action-drawer"
                            open={prDrawerOpen}
                            onToggle={(e) => setPrDrawerOpen((e.currentTarget as HTMLDetailsElement).open)}
                        >
                            <summary>Post Staged PR Review Comment or Link an External PR</summary>
                            <div className="drawer-body">
                                <p className="field-help">
                                    Nothing posts to GitHub or Azure DevOps automatically. Review and edit any staged AI comment below before confirming, or link a manually opened PR to its PBI.
                                </p>
                                <div className="pr-ops-split">
                                    <div className="pr-ops-subcard">
                                        <h4>Post Reviewed Comment to PR</h4>
                                        <label htmlFor="pr-item">PBI Key</label>
                                        <input id="pr-item" placeholder="e.g. PBI-1" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                                        <label htmlFor="pr-feedback">Comment body (editable before posting)</label>
                                        <textarea
                                            id="pr-feedback"
                                            rows={4}
                                            placeholder="Click 'Review & Post PR Comment →' on a PBI above, or write feedback here."
                                            value={prFeedback}
                                            onChange={(event) => { setPrFeedback(event.target.value); feedbackRequest.current = crypto.randomUUID(); }}
                                        />
                                        <div className="factory-actions">
                                            <button type="button" className="secondary" disabled={busy || !itemId} onClick={() => void run({ kind: "review_pr", featureId: feature.id, itemId })}>
                                                Load live PR threads
                                            </button>
                                            <button type="button" className="primary" disabled={busy || !itemId || !prFeedback.trim()} onClick={() => void run({
                                                kind: "post_pr_feedback", featureId: feature.id, itemId, requestId: feedbackRequest.current, body: prFeedback,
                                            })}>
                                                Confirm &amp; post comment
                                            </button>
                                        </div>
                                    </div>

                                    <div className="pr-ops-subcard">
                                        <h4>Link Externally Created PR</h4>
                                        <p className="field-help">Use only if a developer or external agent opened a PR outside this run.</p>
                                        <label htmlFor="pr-link-item">PBI Key</label>
                                        <input id="pr-link-item" placeholder="e.g. PBI-2" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                                        <label htmlFor="pr-number">Pull Request Number</label>
                                        <input id="pr-number" type="number" min="1" placeholder="e.g. 3353" value={prNumber} onChange={(event) => setPrNumber(event.target.value)} />
                                        <div className="factory-actions">
                                            <button type="button" className="secondary" disabled={busy || !itemId || !prNumber} onClick={() => void run({ kind: "attach_pr", featureId: feature.id, itemId, number: Number(prNumber) })}>
                                                Verify &amp; link PR
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </details>

                        {monitorArtifact && (
                            <details
                                ref={waiverComposerRef}
                                className="contextual-action-drawer"
                                open={waiverDrawerOpen}
                                onToggle={(e) => setWaiverDrawerOpen((e.currentTarget as HTMLDetailsElement).open)}
                            >
                                <summary>Sign Off an Intentional Partial Criterion Gap</summary>
                                <div className="drawer-body">
                                    <p className="field-help">
                                        Records an audited human sign-off for one partial criterion in the current verification report (for example, an edge case deferred to a follow-up PBI). Cannot bypass unmerged PRs or failing CI.
                                    </p>
                                    <div className="waiver-fields-grid">
                                        <label htmlFor="waiver-item">PBI Key
                                            <input id="waiver-item" placeholder="e.g. PBI-1" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                                        </label>
                                        <label htmlFor="waiver-criterion">Criterion Text
                                            <input id="waiver-criterion" placeholder="Click 'Sign off gap…' above to prefill" value={criterion} onChange={(event) => setCriterion(event.target.value)} />
                                        </label>
                                    </div>
                                    <label htmlFor="waiver-reason">Engineering Rationale for Sign-Off</label>
                                    <textarea id="waiver-reason" rows={2} placeholder="Why is this partial finding acceptable or where is it tracked?" value={waiverReason} onChange={(event) => setWaiverReason(event.target.value)} />
                                    <div className="factory-actions">
                                        <button type="button" className="secondary" disabled={busy || !latest("monitor") || !itemId || !criterion || !waiverReason.trim()} onClick={() => void run({
                                            kind: "accept_validation_gap", featureId: feature.id, monitorId: latest("monitor")?.id, itemId, criterion, reason: waiverReason,
                                        })}>
                                            Confirm criterion sign-off
                                        </button>
                                    </div>
                                </div>
                            </details>
                        )}
                    </div>
                </section>
            )}

            {gateStageIndex >= 0 && selectedStage !== gateStageIndex && currentGate && (
                <div className="studio-callout info" style={{ marginTop: 14 }}>
                    <strong>Approval ready on Stage {gateStageIndex + 1} ({stageLabels[gateStageIndex]}):</strong>{" "}
                    <button type="button" className="ghost-chip-btn" onClick={() => pickStage(gateStageIndex)}>
                        Review &amp; approve {currentGate.stage} →
                    </button>
                </div>
            )}
        </div>}

        {feature.factory.operations.some((op) => op.status === "reconciliation_required") && (
            <div className="studio-callout warning" style={{ marginTop: 16 }}>
                <strong>Uncertain remote operation needs reconciliation:</strong>
                {feature.factory.operations.filter((op) => op.status === "reconciliation_required").map((operation) => (
                    <div key={operation.key} className="reconcile-inline-row">
                        <span><code>{operation.itemId}</code> · {operation.kind}</span>
                        <button type="button" className="secondary" disabled={busy} onClick={() => void run({ kind: "reconcile_operation", featureId: feature.id, operationKey: operation.key })}>
                            Reconcile without redispatch
                        </button>
                        <input
                            placeholder="Reason if confirmed not sent"
                            value={recoveryReason}
                            onChange={(event) => setRecoveryReason(event.target.value)}
                        />
                        <button type="button" className="ghost-chip-btn" disabled={busy || !recoveryReason.trim()} onClick={() => void run({
                            kind: "confirm_operation_not_sent", featureId: feature.id, operationKey: operation.key, reason: recoveryReason,
                        })}>
                            Attest not sent
                        </button>
                    </div>
                ))}
            </div>
        )}

        <details className="advanced-controls">
            <summary>Advanced Stage Re-runs, Schedule &amp; Export</summary>
            <p className="muted">Manual workflow triggers, periodic delivery polling, and portable feature export.</p>
            <div className="factory-actions">
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || feature.status !== "ready_for_design"} onClick={() => void start("design")}>Re-run Design</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || feature.status !== "ready_for_design"} onClick={() => void start("plan")}>Re-run Planning</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || !latest("plan")} onClick={() => void start("implement")}>Run Next Ready PBI</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || !latest("plan")} onClick={() => void start("monitor")}>Refresh Delivery Audit</button>
                <button className="secondary" disabled={busy} onClick={() => void run({ kind: "export_feature", featureId: feature.id })}>Export Feature Bundle</button>
                <button className="text-button" disabled={busy || feature.factory.cancelled} onClick={() => void run({ kind: "cancel_feature", featureId: feature.id })}>Cancel Feature</button>
            </div>
            <div className="schedule-inline-row">
                <label htmlFor="schedule-minutes">Auto-monitor interval (minutes)</label>
                <input id="schedule-minutes" type="number" min="1" value={minutes} onChange={(event) => setMinutes(event.target.value)} />
                <button className="secondary" disabled={busy} onClick={() => void run({ kind: "set_schedule", featureId: feature.id, minutes: Number(minutes) })}>Enable schedule</button>
                <button className="text-button" disabled={busy || !feature.factory.schedule} onClick={() => void run({ kind: "set_schedule", featureId: feature.id, minutes: null })}>Disable</button>
                {feature.factory.schedule && <span className="muted">Next check: {new Date(feature.factory.schedule.nextAt).toLocaleString()}</span>}
            </div>
        </details>

        <AskCopilotDrawer
            open={qaOpen}
            feature={feature}
            activeStageIndex={selectedStage}
            pinnedContext={activeQaContext}
            onPinContext={setQaContext}
            onClose={() => setQaOpen(false)}
            onUseInFeedback={handleQaBridgeAction}
        />
    </section>;
}

const configurationTemplate: Configuration = {
    repositories: {
        example: { provider: "github", hostname: "github.com", slug: "OWNER/REPOSITORY", baseBranch: "CONFIGURED_BASE", account: "YOUR_ACCOUNT", localPath: "." },
    },
    boards: { organization: "YOUR_ORG", project: "YOUR_PROJECT", workItemType: "Product Backlog Item", areaPath: "CONFIRM_AREA", iterationPath: "CONFIRM_ITERATION", assignee: "", parentId: null },
    delivery: { mode: "local", maxReviewRounds: 3 },
};

export function FactorySettings({ execute }: { execute: Execute }) {
    const [configuration, setConfiguration] = useState(JSON.stringify(configurationTemplate, null, 2));
    const [newAlias, setNewAlias] = useState("");
    const [newRefAlias, setNewRefAlias] = useState("");
    const [file, setFile] = useState("");
    const [mappings, setMappings] = useState("[]");
    const [importHash, setImportHash] = useState("");
    const [backupPath, setBackupPath] = useState("");
    const [backupHash, setBackupHash] = useState("");
    const [hostsStopped, setHostsStopped] = useState(false);
    const [result, setResult] = useState<Record<string, Json>>();
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [loaded, setLoaded] = useState(false);
    useEffect(() => {
        let cancelled = false;
        void request<{ configuration: ConfigRevision | null }>("/api/configuration").then((value) => {
            if (!cancelled && value.configuration) setConfiguration(JSON.stringify(value.configuration.value, null, 2));
        }).catch((failure: unknown) => {
            if (!cancelled) setError(failure instanceof Error ? failure.message : "Configuration could not be loaded.");
        }).finally(() => {
            if (!cancelled) setLoaded(true);
        });
        return () => { cancelled = true; };
    }, []);
    async function run(command: Record<string, unknown>) {
        setBusy(true); setError("");
        try {
            const value = await execute(command); setResult(value);
            if (typeof value.hash === "string") setImportHash(value.hash);
            if (value.backup && typeof value.backup === "object" && !Array.isArray(value.backup)) {
                if (typeof value.backup.path === "string") setBackupPath(value.backup.path);
                if (typeof value.backup.hash === "string") setBackupHash(value.backup.hash);
            }
        } catch (failure) { setError(failure instanceof Error ? failure.message : "Configuration command failed."); }
        finally { setBusy(false); }
    }

    let parsedConfig: Configuration | null = null;
    try {
        const raw = JSON.parse(configuration) as Configuration;
        if (raw && typeof raw === "object" && raw.repositories && raw.boards) parsedConfig = raw;
    } catch {
        parsedConfig = null;
    }

    const updateConfigObject = (mutator: (draft: Configuration) => void) => {
        const base: Configuration = parsedConfig
            ? JSON.parse(JSON.stringify(parsedConfig)) as Configuration
            : JSON.parse(JSON.stringify(configurationTemplate)) as Configuration;
        mutator(base);
        setConfiguration(JSON.stringify(base, null, 2));
    };

    if (!loaded) {
        return <section className="card factory-panel" aria-label="Connections and migration">
            <span className="eyebrow">SHARED CHAT / UI CONFIGURATION</span><h2>Connect the right targets.</h2>
            <p className="muted">Loading saved configuration…</p>
        </section>;
    }

    return <section className="card factory-panel" aria-label="Connections and migration">
        <span className="eyebrow">SHARED CHAT / UI CONFIGURATION</span><h2>Connect the right targets.</h2>
        <p className="muted">Provider and hostname are separate. GitHub accounts are scoped to each request; credentials are never stored here. ADO board and repository projects may differ.</p>

        {parsedConfig && (
            <div className="visual-config-studio">
                <div className="visual-config-section">
                    <div className="studio-section-head">
                        <h3>Target Repositories ({Object.keys(parsedConfig.repositories).length})</h3>
                        <div className="add-repo-inline">
                            <input
                                placeholder="New repo alias (e.g. common)"
                                value={newAlias}
                                onChange={(e) => setNewAlias(e.target.value)}
                                aria-label="New repository alias"
                            />
                            <button
                                type="button"
                                className="secondary"
                                disabled={!newAlias.trim()}
                                onClick={() => {
                                    const key = newAlias.trim().replace(/[^a-zA-Z0-9_-]/g, "");
                                    if (!key) return;
                                    updateConfigObject((cfg) => {
                                        cfg.repositories[key] = {
                                            provider: "github", hostname: "github.com", slug: "AzureAD/repo",
                                            baseBranch: "dev", account: "shjameel_microsoft", localPath: key,
                                        };
                                    });
                                    setNewAlias("");
                                }}
                            >
                                + Add repo
                            </button>
                        </div>
                    </div>
                    <div className="repo-mapper-grid">
                        {Object.entries(parsedConfig.repositories).map(([alias, repo]: [string, RepositoryTarget]) => (
                            <div key={alias} className="repo-mapper-card">
                                <div className="repo-mapper-head">
                                    <span className="mono-chip blue">{alias}</span>
                                    <span className="mono-chip">{repo.provider.toUpperCase()}</span>
                                    {Object.keys(parsedConfig!.repositories).length > 1 && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn"
                                            onClick={() => updateConfigObject((cfg) => { delete cfg.repositories[alias]; })}
                                        >
                                            Remove
                                        </button>
                                    )}
                                </div>
                                <div className="repo-mapper-fields">
                                    <label>Provider
                                        <select
                                            value={repo.provider}
                                            onChange={(e) => updateConfigObject((cfg) => {
                                                const p = e.target.value as "github" | "ado";
                                                cfg.repositories[alias]!.provider = p;
                                                if (p === "ado") {
                                                    cfg.repositories[alias]!.hostname = "dev.azure.com";
                                                    cfg.repositories[alias]!.account = "";
                                                }
                                            })}
                                        >
                                            <option value="github">GitHub / GHE</option>
                                            <option value="ado">Azure DevOps</option>
                                        </select>
                                    </label>
                                    <label>Hostname
                                        <input value={repo.hostname} onChange={(e) => updateConfigObject((cfg) => { cfg.repositories[alias]!.hostname = e.target.value; })} />
                                    </label>
                                    <label>Slug (owner/repo)
                                        <input value={repo.slug} onChange={(e) => updateConfigObject((cfg) => { cfg.repositories[alias]!.slug = e.target.value; })} />
                                    </label>
                                    <label>Base Branch
                                        <input value={repo.baseBranch} onChange={(e) => updateConfigObject((cfg) => { cfg.repositories[alias]!.baseBranch = e.target.value; })} />
                                    </label>
                                    <label>GitHub Account
                                        <input
                                            value={repo.account}
                                            disabled={repo.provider === "ado"}
                                            onChange={(e) => updateConfigObject((cfg) => { cfg.repositories[alias]!.account = e.target.value; })}
                                        />
                                    </label>
                                    <label>Local Path
                                        <input value={repo.localPath} onChange={(e) => updateConfigObject((cfg) => { cfg.repositories[alias]!.localPath = e.target.value; })} />
                                    </label>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="visual-config-section">
                    <div className="studio-section-head">
                        <h3>Read-only Evidence Sources ({Object.keys(parsedConfig.referenceRepositories || {}).length})</h3>
                        <div className="add-repo-inline">
                            <input
                                placeholder="New reference alias (e.g. msal, design-docs)"
                                value={newRefAlias}
                                onChange={(e) => setNewRefAlias(e.target.value)}
                                aria-label="New reference repository alias"
                            />
                            <button
                                type="button"
                                className="secondary"
                                disabled={!newRefAlias.trim()}
                                onClick={() => {
                                    const key = newRefAlias.trim().replace(/[^a-zA-Z0-9_-]/g, "");
                                    if (!key || Object.hasOwn(parsedConfig!.repositories, key)) return;
                                    updateConfigObject((cfg) => {
                                        cfg.referenceRepositories = cfg.referenceRepositories || {};
                                        cfg.referenceRepositories[key] = {
                                            provider: "github", hostname: "github.com", slug: "AzureAD/repo",
                                            baseBranch: "dev", account: "shjameel_microsoft", localPath: key,
                                        };
                                    });
                                    setNewRefAlias("");
                                }}
                            >
                                + Add reference repo
                            </button>
                        </div>
                    </div>
                    <p className="field-help">Pinned caller, dependency, or design-spec checkouts for attested research — never backlog or delivery targets.</p>
                    {Object.keys(parsedConfig.referenceRepositories || {}).length > 0 && (
                        <div className="repo-mapper-grid">
                            {Object.entries(parsedConfig.referenceRepositories || {}).map(([alias, repo]: [string, RepositoryTarget]) => (
                                <div key={alias} className="repo-mapper-card">
                                    <div className="repo-mapper-head">
                                        <span className="mono-chip">{alias}</span>
                                        <span className="mono-chip">{repo.provider.toUpperCase()} · READ-ONLY</span>
                                        <button
                                            type="button"
                                            className="ghost-chip-btn"
                                            onClick={() => updateConfigObject((cfg) => {
                                                if (cfg.referenceRepositories) {
                                                    delete cfg.referenceRepositories[alias];
                                                    if (!Object.keys(cfg.referenceRepositories).length) delete cfg.referenceRepositories;
                                                }
                                            })}
                                        >
                                            Remove
                                        </button>
                                    </div>
                                    <div className="repo-mapper-fields">
                                        <label>Provider
                                            <select
                                                value={repo.provider}
                                                onChange={(e) => updateConfigObject((cfg) => {
                                                    const p = e.target.value as "github" | "ado";
                                                    cfg.referenceRepositories![alias]!.provider = p;
                                                    if (p === "ado") {
                                                        cfg.referenceRepositories![alias]!.hostname = "dev.azure.com";
                                                        cfg.referenceRepositories![alias]!.account = "";
                                                    }
                                                })}
                                            >
                                                <option value="github">GitHub / GHE</option>
                                                <option value="ado">Azure DevOps</option>
                                            </select>
                                        </label>
                                        <label>Hostname
                                            <input value={repo.hostname} onChange={(e) => updateConfigObject((cfg) => { cfg.referenceRepositories![alias]!.hostname = e.target.value; })} />
                                        </label>
                                        <label>Slug (owner/repo)
                                            <input value={repo.slug} onChange={(e) => updateConfigObject((cfg) => { cfg.referenceRepositories![alias]!.slug = e.target.value; })} />
                                        </label>
                                        <label>Base Branch
                                            <input value={repo.baseBranch} onChange={(e) => updateConfigObject((cfg) => { cfg.referenceRepositories![alias]!.baseBranch = e.target.value; })} />
                                        </label>
                                        <label>GitHub Account
                                            <input
                                                value={repo.account}
                                                disabled={repo.provider === "ado"}
                                                onChange={(e) => updateConfigObject((cfg) => { cfg.referenceRepositories![alias]!.account = e.target.value; })}
                                            />
                                        </label>
                                        <label>Local Path
                                            <input value={repo.localPath} onChange={(e) => updateConfigObject((cfg) => { cfg.referenceRepositories![alias]!.localPath = e.target.value; })} />
                                        </label>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                <div className="visual-config-split">
                    <div className="visual-config-section">
                        <h3>Azure DevOps Board Routing</h3>
                        <div className="repo-mapper-fields">
                            <label>Organization
                                <input value={parsedConfig.boards.organization} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.organization = e.target.value; })} />
                            </label>
                            <label>Project
                                <input value={parsedConfig.boards.project} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.project = e.target.value; })} />
                            </label>
                            <label>Work Item Type
                                <input value={parsedConfig.boards.workItemType} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.workItemType = e.target.value; })} />
                            </label>
                            <label>Area Path
                                <input value={parsedConfig.boards.areaPath} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.areaPath = e.target.value; })} />
                            </label>
                            <label>Iteration Path
                                <input value={parsedConfig.boards.iterationPath} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.iterationPath = e.target.value; })} />
                            </label>
                            <label>Assignee
                                <input value={parsedConfig.boards.assignee} onChange={(e) => updateConfigObject((cfg) => { cfg.boards.assignee = e.target.value; })} />
                            </label>
                        </div>
                    </div>

                    <div className="visual-config-section">
                        <h3>Stage 3 Coding &amp; Review Loop</h3>
                        <div className="repo-mapper-fields">
                            <label>Execution Mode
                                <select
                                    value={parsedConfig.delivery?.mode || "local"}
                                    onChange={(e) => updateConfigObject((cfg) => {
                                        cfg.delivery = {
                                            mode: e.target.value as "local" | "remote",
                                            maxReviewRounds: cfg.delivery?.maxReviewRounds ?? 3,
                                        };
                                    })}
                                >
                                    <option value="local">Local Builder + Independent Reviewer</option>
                                    <option value="remote">Remote Coding Agent + PR Reviewer</option>
                                </select>
                            </label>
                            <label>Max Adversarial Review Rounds (1–10)
                                <input
                                    type="number"
                                    min="1"
                                    max="10"
                                    value={parsedConfig.delivery?.maxReviewRounds ?? 3}
                                    onChange={(e) => updateConfigObject((cfg) => {
                                        cfg.delivery = {
                                            mode: cfg.delivery?.mode || "local",
                                            maxReviewRounds: Math.max(1, Math.min(10, Number(e.target.value) || 3)),
                                        };
                                    })}
                                />
                            </label>
                        </div>
                    </div>
                </div>
            </div>
        )}

        <label htmlFor="configuration-json">Repository and ADO settings</label><textarea id="configuration-json" rows={8} value={configuration} onChange={(event) => setConfiguration(event.target.value)} spellCheck={false} />
        <div className="factory-actions"><button className="primary" disabled={busy} onClick={() => {
            try { void run({ kind: "configure", configuration: JSON.parse(configuration) }); }
            catch { setError("Configuration must be valid JSON."); }
        }}>Review &amp; save configuration</button><button className="secondary" disabled={busy} onClick={() => void run({ kind: "preflight" })}>Verify provider connections</button></div>
        <details className="advanced-controls">
            <summary>Migration &amp; Registry Backup Tools</summary>
            <h4>Import historical features</h4><p className="muted">Preview is read-only. Import requires explicit requirements for every selected legacy feature. Historical approvals are never trusted.</p>
            <label htmlFor="legacy-file">Local legacy JSON path</label><input id="legacy-file" value={file} onChange={(event) => { setFile(event.target.value); setImportHash(""); }} />
            <button className="secondary" disabled={busy || !file.trim()} onClick={() => void run({ kind: "preview_import", path: file })}>Preview import</button>
            <label htmlFor="legacy-mappings">Explicit mappings</label><textarea id="legacy-mappings" rows={4} value={mappings} onChange={(event) => setMappings(event.target.value)} />
            <button className="secondary" disabled={busy || !importHash} onClick={() => {
                try { void run({ kind: "import_legacy", path: file, expectedHash: importHash, mappings: JSON.parse(mappings) }); }
                catch { setError("Import mappings must be valid JSON."); }
            }}>Review &amp; import selected features</button>
            <h4>Consistent registry backup and isolated restore</h4>
            <button className="secondary" disabled={busy} onClick={() => void run({ kind: "backup_registry" })}>Create private SQLite backup</button>
            <label htmlFor="backup-path">Backup snapshot path</label><input id="backup-path" value={backupPath} onChange={(event) => { setBackupPath(event.target.value); setBackupHash(""); }} />
            <button className="secondary" disabled={busy || !backupPath} onClick={() => void run({ kind: "inspect_backup", path: backupPath })}>Inspect integrity &amp; hash</button>
            <label><input type="checkbox" checked={hostsStopped} onChange={(event) => setHostsStopped(event.target.checked)} /> Original execution hosts have been stopped.</label>
            <button className="secondary" disabled={busy || !backupHash || !hostsStopped} onClick={() => void run({
                kind: "restore_backup_clone", path: backupPath, expectedHash: backupHash, originalHostsStopped: hostsStopped,
            })}>Review isolated restore</button>
            <p className="field-help">Restore never overwrites the live registry. It disables schedules and returns a new data directory; native journals and ownership are not copied.</p>
        </details>
        {error && <div className="message error" role="alert">{error}</div>}
        {result && typeof result.outcome === "string" && (
            <div className="message notice" role="status">Operation completed: {result.outcome.replaceAll("_", " ")}.</div>
        )}
    </section>;
}
