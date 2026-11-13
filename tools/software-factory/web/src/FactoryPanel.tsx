// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useRef, useState } from "react";
import type { Artifact, Configuration, ConfigRevision, FactoryFeature, Json, RepositoryTarget } from "../../core/lifecycle-contracts.ts";
import { request } from "./api";
import { nextStep, stageLabels, type AvailableGate } from "../../core/progress.ts";
import { ScopeSummary } from "./Confirmation";
import {
    CodeArenaExplorer,
    DeliveryCommandCenter,
    DesignStudioExplorer,
    PlanStudioExplorer,
    SubagentFlightRecorder,
} from "./StudioExplorers";

type Execute = (command: Record<string, unknown>) => Promise<Record<string, Json>>;
type GateResult = AvailableGate;

export function ArtifactDocument({
    artifact,
    feature,
    onAppendFeedback,
    onSelectItemForOps,
    onPrefillWaiver,
    onPrefillPrFeedback,
}: {
    artifact: Artifact;
    feature?: FactoryFeature;
    onAppendFeedback?: (snippet: string) => void;
    onSelectItemForOps?: (itemId: string) => void;
    onPrefillWaiver?: (itemId: string, criterion: string) => void;
    onPrefillPrFeedback?: (itemId: string, feedbackBody: string) => void;
}) {
    const body = artifact.content;
    if (!body || typeof body !== "object" || Array.isArray(body)) return <pre>{JSON.stringify(body, null, 2)}</pre>;
    if (artifact.kind === "design" && body.design && typeof body.design === "object" && !Array.isArray(body.design)) {
        const sections = body.design.sections;
        if (sections && typeof sections === "object" && !Array.isArray(sections)) {
            return <DesignStudioExplorer artifact={artifact} onAppendFeedback={onAppendFeedback} />;
        }
    }
    if ((artifact.kind === "plan" || artifact.kind === "planning_attempt") && Array.isArray(body.items)) {
        return (
            <PlanStudioExplorer
                artifact={artifact}
                feature={feature}
                onAppendFeedback={onAppendFeedback}
                onSelectItemForOps={onSelectItemForOps}
            />
        );
    }
    if (artifact.kind === "implementation" || artifact.kind === "implementation_attempt") {
        return <CodeArenaExplorer artifact={artifact} />;
    }
    if (artifact.kind === "monitor" && feature) {
        return (
            <DeliveryCommandCenter
                artifact={artifact}
                feature={feature}
                onPrefillWaiver={onPrefillWaiver}
                onPrefillPrFeedback={onPrefillPrFeedback}
            />
        );
    }
    return <pre>{JSON.stringify(body, null, 2)}</pre>;
}

function summarizeArtifactCard(artifact: Artifact): {
    stageBadge: string;
    pillColor: "blue" | "purple" | "emerald" | "amber" | "red";
    title: string;
    verdict: string;
    verdictColor: "emerald" | "amber" | "red" | "blue";
    summary: string;
    targetLens?: "scope" | "design" | "plan" | "code" | "delivery";
} {
    const body = artifact.content && typeof artifact.content === "object" && !Array.isArray(artifact.content)
        ? artifact.content as Record<string, Json>
        : {};
    if (artifact.kind === "design") {
        const rec = typeof body.recommendation === "string" ? body.recommendation : "Saved";
        const designObj = body.design && typeof body.design === "object" && !Array.isArray(body.design) ? body.design as Record<string, Json> : {};
        const sectionsCount = designObj.sections && typeof designObj.sections === "object" && !Array.isArray(designObj.sections)
            ? Object.keys(designObj.sections).length
            : 0;
        const evidenceObj = body.evidence && typeof body.evidence === "object" && !Array.isArray(body.evidence) ? body.evidence as Record<string, Json> : {};
        const refsCount = Array.isArray(evidenceObj.references) ? evidenceObj.references.length : 0;
        const reviewsCount = Array.isArray(body.reviews) ? body.reviews.length : 0;
        const verdictColor = rec === "GO to Planning" ? "emerald" : rec === "NO-GO" ? "red" : "amber";
        return {
            stageBadge: "STAGE 2 · DESIGN",
            pillColor: "blue",
            title: `Technical Design Specification (Gen ${(artifact.generation ?? 0) + 1})`,
            verdict: rec,
            verdictColor,
            summary: `${sectionsCount} architecture sections · ${refsCount} pinned source refs · ${reviewsCount} adversarial review round${reviewsCount === 1 ? "" : "s"}`,
            targetLens: "design",
        };
    }
    if (artifact.kind === "plan" || artifact.kind === "planning_attempt") {
        const itemsCount = Array.isArray(body.items) ? body.items.length : 0;
        const summaryText = typeof body.summary === "string" && body.summary ? body.summary : `${itemsCount} planned backlog item(s)`;
        const isFinal = artifact.kind === "plan";
        return {
            stageBadge: "STAGE 3 · PLAN",
            pillColor: "purple",
            title: isFinal ? `Implementation Plan & PBI DAG (Gen ${(artifact.generation ?? 0) + 1})` : "Planning Draft Attempt",
            verdict: isFinal ? `${itemsCount} PBI${itemsCount === 1 ? "" : "s"} Ready` : "Needs Revision",
            verdictColor: isFinal ? "emerald" : "amber",
            summary: summaryText,
            targetLens: "plan",
        };
    }
    if (artifact.kind === "implementation" || artifact.kind === "implementation_attempt") {
        const items = Array.isArray(body.items) ? body.items : [];
        const isFinal = artifact.kind === "implementation";
        return {
            stageBadge: "STAGE 5 · CODE",
            pillColor: "emerald",
            title: isFinal ? "Implementation & Code Review Bundle" : "Coding Attempt Snapshot",
            verdict: isFinal ? "Reviewed" : "In Progress / Attempt",
            verdictColor: isFinal ? "emerald" : "amber",
            summary: `${items.length} implemented work item${items.length === 1 ? "" : "s"} recorded`,
            targetLens: "code",
        };
    }
    if (artifact.kind === "monitor") {
        const items = Array.isArray(body.items) ? body.items : [];
        return {
            stageBadge: "STAGE 6 · DELIVERY",
            pillColor: "amber",
            title: "Delivery & PR Verification Report",
            verdict: "Snapshot",
            verdictColor: "blue",
            summary: `${items.length} tracked PBI/PR delivery item${items.length === 1 ? "" : "s"}`,
            targetLens: "delivery",
        };
    }
    if (artifact.kind === "intake") {
        const draftReqs = Array.isArray(body.requirements) ? body.requirements.length : 0;
        return {
            stageBadge: "STAGE 1 · SCOPE",
            pillColor: "emerald",
            title: "AI-Drafted Success Criteria Suggestions",
            verdict: `${draftReqs} Suggested Criteria`,
            verdictColor: "blue",
            summary: "Draft scope criteria and open questions proposed for human review",
            targetLens: "scope",
        };
    }
    return {
        stageBadge: artifact.kind.toUpperCase(),
        pillColor: "blue",
        title: `${artifact.kind} artifact`,
        verdict: "Recorded",
        verdictColor: "blue",
        summary: `Revision ${artifact.requirementsRevision.slice(0, 8)}`,
    };
}

type StudioLens = "gate" | "scope" | "design" | "plan" | "backlog" | "code" | "delivery" | "flight";

const stageIndexToLens: StudioLens[] = ["scope", "design", "plan", "backlog", "code", "delivery"];

export function FactoryPanel({ feature, execute, onEdit, onReviewSuggestions, onCheckSetup }: {
    feature: FactoryFeature; execute: Execute; onEdit: () => void; onReviewSuggestions: () => void; onCheckSetup: () => void;
}) {
    const [gates, setGates] = useState<GateResult[]>([]);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [gatesLoaded, setGatesLoaded] = useState(false);
    const deliverablesRef = useRef<HTMLElement>(null);
    const studioViewportRef = useRef<HTMLDivElement>(null);
    const [result, setResult] = useState<Record<string, Json>>();
    const [feedback, setFeedback] = useState("");
    const [minutes, setMinutes] = useState("60");
    const [limits, setLimits] = useState("{}");
    const [itemId, setItemId] = useState("");
    const [prNumber, setPrNumber] = useState("");
    const [prFeedback, setPrFeedback] = useState("");
    const [criterion, setCriterion] = useState("");
    const [waiverReason, setWaiverReason] = useState("");
    const [recoveryReason, setRecoveryReason] = useState("");
    const feedbackRequest = useRef(crypto.randomUUID());
    const [tab, setTab] = useState<"artifacts" | "runs" | "operations">("artifacts");
    const [studioLens, setStudioLens] = useState<StudioLens>("gate");
    const [selectedExecId, setSelectedExecId] = useState<string>();
    const [selectedDesignId, setSelectedDesignId] = useState<string>();
    const [selectedPlanId, setSelectedPlanId] = useState<string>();
    const feedbackInputRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        let cancelled = false;
        setGatesLoaded(false);
        void request<{ gates: GateResult[] }>(`/api/gates/${feature.id}`)
            .then((value) => { if (!cancelled) { setGates(value.gates); setGatesLoaded(true); } })
            .catch((failure: unknown) => { if (!cancelled) setError(failure instanceof Error ? failure.message : "Approval previews could not be loaded."); });
        return () => { cancelled = true; };
    }, [feature]);

    async function run(command: Record<string, unknown>) {
        setBusy(true); setError(""); setResult(undefined);
        try { setResult(await execute(command)); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "The command failed; no success was assumed."); }
        finally { setBusy(false); }
    }

    const latest = (kind: "design" | "plan" | "monitor") => feature.factory.artifacts.find((artifact) => artifact.id === feature.currentArtifacts[kind]);
    const latestImpl = () => [...feature.factory.artifacts].reverse().find((a) =>
        (a.kind === "implementation" || a.kind === "implementation_attempt") && a.requirementsRevision === feature.current.id);
    const start = (workflow: string) => {
        try { return run({ kind: "start_workflow", featureId: feature.id, workflow, limits: JSON.parse(limits) }); }
        catch { setError("Execution limit overrides must be valid JSON."); return Promise.resolve(); }
    };
    const progress = nextStep(feature, gates);
    const currentGate = progress.gate;
    const showRuns = () => {
        setTab("runs");
        deliverablesRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    const appendFeedback = (snippet: string) => {
        setFeedback((prev) => (prev.trim() ? `${prev.trim()}\n${snippet}` : snippet));
        setTab("artifacts");
        setTimeout(() => feedbackInputRef.current?.focus(), 50);
    };
    const selectItemForOps = (targetItemId: string) => {
        setItemId(targetItemId);
        setTab("operations");
        deliverablesRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    const prefillWaiver = (targetItemId: string, targetCriterion: string) => {
        setItemId(targetItemId);
        setCriterion(targetCriterion);
        setTab("artifacts");
        deliverablesRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    const prefillPrFeedback = (targetItemId: string, bodyText: string) => {
        setItemId(targetItemId);
        setPrFeedback(bodyText);
        feedbackRequest.current = crypto.randomUUID();
        setTab("operations");
        deliverablesRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
                // Ignore truncated or partial output previews.
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

    const stage0Approval = [...feature.approvals].reverse().find((a) => a.revisionId === feature.current.id) || feature.approvals.at(-1);
    const backlogOperations = feature.factory.operations.filter((op) => op.kind === "work_item" || op.kind === "links" || op.kind === "dispatch");
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
    const inspectedStageIndex = stageIndexToLens.indexOf(studioLens);

    const openArtifactInStudio = (artifact: Artifact) => {
        const meta = summarizeArtifactCard(artifact);
        if (artifact.kind === "design") setSelectedDesignId(artifact.id);
        if (artifact.kind === "plan" || artifact.kind === "planning_attempt") setSelectedPlanId(artifact.id);
        if (meta.targetLens) {
            setStudioLens(meta.targetLens);
            studioViewportRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }
    };

    return <section className="card factory-panel" aria-label="Workflow lifecycle">
        <div className="phase-track-header">
            <span className="eyebrow">LIFECYCLE STAGES · CLICK ANY STEP TO INSPECT</span>
            {studioLens !== "gate" && (
                <button type="button" className="ghost-chip-btn" onClick={() => setStudioLens("gate")}>
                    ← Back to Active Step ({stageLabels[progress.stage]})
                </button>
            )}
        </div>
        <div className="phase-track" role="group" aria-label="Feature progress">
            {stageLabels.map((label, index) => {
                const targetLens: StudioLens = stageIndexToLens[index] || "gate";
                const isInspected = inspectedStageIndex === index;
                const isCurrent = index === progress.stage;
                const isPast = index < progress.stage;
                return (
                    <button
                        type="button"
                        key={label}
                        aria-current={isCurrent ? "step" : undefined}
                        aria-pressed={isInspected}
                        onClick={() => setStudioLens(isInspected ? "gate" : targetLens)}
                        className={`phase ${isCurrent ? "current" : isPast ? "past" : ""} ${isInspected ? "inspecting" : ""}`}
                    >
                        <span>{isPast ? "✓" : index + 1}</span>
                        <strong>{label}</strong>
                        <small>{isInspected ? "INSPECTING" : isCurrent ? "CURRENT STEP" : isPast ? "COMPLETED · VIEW" : "PREVIEW"}</small>
                    </button>
                );
            })}
        </div>

        <div className={`next-action ${progress.failed ? "failed" : ""}`} aria-label="Next step">
            <span className="eyebrow">{progress.failed ? "NEEDS ATTENTION" : progress.report ? "CHAT PROGRESS" : progress.run?.status === "running" || progress.run?.status === "starting" ? "IN PROGRESS" : "NEXT STEP"}</span>
            <h2>{progress.title}</h2><p role={progress.failed ? "alert" : undefined}>{progress.description}</p>
            {progress.run && <p className="run-origin">{progress.run.requestedBy?.channel === "chat" || progress.run.parentSessionId ? `${progress.failed ? "Requested" : "Started"} in Copilot chat` : progress.run.requestedBy?.channel === "web" ? "Requested in the dashboard" : "Recorded workflow"} / {new Date(progress.run.updatedAt).toLocaleString()}</p>}
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
                {(progress.run?.status === "running" || progress.run?.status === "starting") && studioLens !== "flight" && (
                    <button type="button" className="secondary" onClick={() => {
                        setSelectedExecId(progress.run?.id);
                        setStudioLens("flight");
                    }}>
                        Inspect live subagents ({progress.run.usage?.subagents ?? 0})
                    </button>
                )}
                {progress.stage === 0 && progress.action?.target !== "edit" && !progress.run && !progress.report && <button className="secondary" disabled={busy} onClick={onEdit}>Write or edit criteria myself</button>}
                {progress.failed && <button className="secondary" disabled={busy} onClick={onCheckSetup}>Check setup</button>}
                {progress.failed && progress.run?.runtimeId === null && <span className="field-help">No native run ID was recorded. Retrying creates a new, explicitly confirmed attempt; it never replays remote writes.</span>}
            </div>
        </div>
        {error && <div role="alert" className="message error">{error}</div>}
        {result?.outcome === "not_approved" && <p className="message notice" role="status">Not started. You chose not to approve this action.</p>}

        <div ref={studioViewportRef} className="studio-workspace-shell">
            <div className="studio-lens-bar" role="navigation" aria-label="Feature studio workspaces">
                <span className="eyebrow">INSPECT WORKSPACE VIEW</span>
                <div className="studio-lens-buttons">
                    <button type="button" className={studioLens === "gate" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("gate")}>
                        Active Gate &amp; Next Step
                    </button>
                    <button type="button" className={studioLens === "scope" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("scope")}>
                        1. Scope &amp; Criteria {feature.status === "ready_for_design" ? "✓" : ""}
                    </button>
                    <button type="button" className={studioLens === "design" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("design")}>
                        Design Studio {designArtifact ? "✓" : ""}
                    </button>
                    <button type="button" className={studioLens === "plan" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("plan")}>
                        Plan &amp; DAG Studio {planArtifact ? "✓" : ""}
                    </button>
                    <button type="button" className={studioLens === "code" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("code")}>
                        Code Builder vs. Reviewer {codeArtifact ? "✓" : ""}
                    </button>
                    <button type="button" className={studioLens === "delivery" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("delivery")}>
                        Delivery &amp; PR Center {monitorArtifact ? "✓" : ""}
                    </button>
                    <button type="button" className={studioLens === "flight" ? "lens-btn active" : "lens-btn"} onClick={() => setStudioLens("flight")}>
                        Subagent Flight Recorder ({feature.factory.executions.length})
                    </button>
                </div>
            </div>

            {studioLens === "scope" && (
                <section className="studio-spotlight" aria-label="Scope and Criteria Studio">
                    <div className="scope-inspector-card">
                        <div className="studio-header-top">
                            <div>
                                <div className="studio-badge-row">
                                    <span className="studio-pill emerald">STAGE 1 · DEFINE SUCCESS</span>
                                    <span className={`studio-pill ${feature.status === "ready_for_design" ? "emerald" : "amber"}`}>
                                        {feature.status === "ready_for_design" ? "Scope Approved ✓" : "Draft / Awaiting Approval"}
                                    </span>
                                    <span className="mono-chip">Revision #{feature.current.number}</span>
                                </div>
                                <h3>{feature.current.title}</h3>
                            </div>
                            <button type="button" className="secondary" disabled={busy} onClick={onEdit}>
                                Edit brief &amp; criteria
                            </button>
                        </div>
                        {feature.current.description && (
                            <div className="scope-inspector-block">
                                <span className="eyebrow">PROBLEM STATEMENT &amp; GOAL</span>
                                <p>{feature.current.description}</p>
                            </div>
                        )}
                        <div className="scope-inspector-block">
                            <span className="eyebrow">APPROVED SUCCESS CRITERIA ({feature.current.requirements.length})</span>
                            {feature.current.requirements.length > 0 ? (
                                <div className="requirements-list">
                                    {feature.current.requirements.map((req) => (
                                        <div key={req.id} className="requirement">
                                            <span>{req.id}</span>
                                            <p>{req.text}</p>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <p className="muted">No success criteria saved yet.</p>
                            )}
                        </div>
                        {!!feature.current.designTasks?.length && (
                            <div className="scope-inspector-block">
                                <span className="eyebrow">ENGINEERING OBLIGATIONS FOR DESIGN AGENTS ({feature.current.designTasks.length})</span>
                                <ul className="studio-bullet-list">
                                    {feature.current.designTasks.map((task) => <li key={task}>{task}</li>)}
                                </ul>
                            </div>
                        )}
                        {stage0Approval && (
                            <div className="scope-approval-receipt">
                                <strong>✓ Stage 0 Scope Approval Recorded</strong>
                                <span>
                                    Approved by {stage0Approval.actor?.ado?.displayName || stage0Approval.actor?.reference || "authenticated operator"} via {stage0Approval.actor?.channel || "studio"} on{" "}
                                    {new Date(stage0Approval.createdAt).toLocaleString()}
                                </span>
                            </div>
                        )}
                    </div>
                </section>
            )}

            {studioLens === "design" && currentGate?.stage !== "design" && (
                <section className="studio-spotlight" aria-label="Design Studio">
                    {!currentDesignArtifact && designArtifact && (
                        <div className="studio-iteration-banner">
                            <div>
                                <strong>Revision run in progress — viewing saved Design Iteration</strong>
                                <p className="muted">
                                    A newer Design run is currently active. Below is the most recent completed design packet ({new Date(designArtifact.createdAt).toLocaleString()}) including its full 10-section spec and reviewer findings.
                                </p>
                            </div>
                            {progress.run && (
                                <button type="button" className="secondary" onClick={() => { setSelectedExecId(progress.run?.id); setStudioLens("flight"); }}>
                                    Watch live run →
                                </button>
                            )}
                        </div>
                    )}
                    {fallbackDesignArtifacts.length > 1 && (
                        <div className="artifact-version-bar">
                            <label htmlFor="design-version-select" className="eyebrow">DESIGN ITERATION</label>
                            <select
                                id="design-version-select"
                                value={designArtifact?.id || ""}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                            >
                                {fallbackDesignArtifacts.map((art, idx) => {
                                    const meta = summarizeArtifactCard(art);
                                    return (
                                        <option key={art.id} value={art.id}>
                                            Iteration #{fallbackDesignArtifacts.length - idx} — {meta.verdict} · {new Date(art.createdAt).toLocaleString()} ({art.id.slice(0, 8)})
                                        </option>
                                    );
                                })}
                            </select>
                        </div>
                    )}
                    {designArtifact ? (
                        <ArtifactDocument artifact={designArtifact} feature={feature} onAppendFeedback={appendFeedback} />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill blue">STAGE 2 · GUARDED DESIGN STUDIO</span>
                            <h3>{progress.run?.kind === "design" && progress.run.status === "running" ? `Design Run Active: ${progress.run.phase}` : "Design Author vs. Independent Reviewer Arena"}</h3>
                            <p className="muted">
                                {progress.run?.kind === "design" && progress.run.status === "running"
                                    ? "The guarded design workflow is actively researching repository evidence, authoring the 10-section architecture specification, and running adversarial design reviews. As soon as the run settles, the complete interactive specification appears here."
                                    : "Once your feature brief and success criteria are approved, starting a Guarded Design run dispatches the Repository Researcher, Design Author, and Independent Design Reviewer across up to 3 adversarial rounds, producing a full 10-section specification, Requirement Traceability Matrix, and verified Source Evidence Ledger."}
                            </p>
                            {progress.run?.kind === "design" && progress.run.status === "running" && (
                                <button type="button" className="secondary" onClick={() => { setSelectedExecId(progress.run?.id); setStudioLens("flight"); }}>
                                    Open Subagent Flight Recorder →
                                </button>
                            )}
                        </div>
                    )}
                </section>
            )}

            {studioLens === "plan" && currentGate?.stage !== "plan" && (
                <section className="studio-spotlight" aria-label="Plan Studio">
                    {allPlanArtifacts.length > 1 && (
                        <div className="artifact-version-bar">
                            <label htmlFor="plan-version-select" className="eyebrow">PLAN ITERATION</label>
                            <select
                                id="plan-version-select"
                                value={planArtifact?.id || ""}
                                onChange={(e) => setSelectedPlanId(e.target.value)}
                            >
                                {allPlanArtifacts.map((art, idx) => {
                                    const meta = summarizeArtifactCard(art);
                                    return (
                                        <option key={art.id} value={art.id}>
                                            Plan #{allPlanArtifacts.length - idx} — {meta.verdict} · {new Date(art.createdAt).toLocaleString()} ({art.id.slice(0, 8)})
                                        </option>
                                    );
                                })}
                            </select>
                        </div>
                    )}
                    {planArtifact ? (
                        <ArtifactDocument artifact={planArtifact} feature={feature} onAppendFeedback={appendFeedback} onSelectItemForOps={selectItemForOps} />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill purple">STAGE 3 · PLAN &amp; DAG STUDIO</span>
                            <h3>Interactive Execution Wave DAG &amp; PBI Inspector</h3>
                            <p className="muted">
                                After design approval, the <strong>Feature Planner</strong> and <strong>Independent Plan Reviewer</strong> decompose the architecture into repository-targeted PBIs with a verified dependency DAG, target file list, and unit/instrumented test suite.
                            </p>
                        </div>
                    )}
                </section>
            )}

            {studioLens === "backlog" && (
                <section className="studio-spotlight" aria-label="Backlog Studio">
                    <div className="scope-inspector-card">
                        <div className="studio-badge-row">
                            <span className="studio-pill purple">STAGE 4 · BACKLOG &amp; WORK ITEMS</span>
                            <span className="mono-chip">{provisionedWorkItems.length} ADO PBI(s) · {backlogOperations.length} recorded operation(s)</span>
                        </div>
                        <h3>Azure DevOps Backlog &amp; PBI Provisioning</h3>
                        {provisionedWorkItems.length > 0 && (
                            <div className="delivery-cards-grid" style={{ marginBottom: 14 }}>
                                {provisionedWorkItems.map((wi) => (
                                    <div key={wi.key} className="delivery-pbi-card">
                                        <div className="delivery-pbi-head">
                                            <div className="studio-badge-row">
                                                <strong>{wi.key}</strong>
                                                {wi.url ? (
                                                    <a href={wi.url} target="_blank" rel="noreferrer" className="studio-pill purple">
                                                        ADO #{wi.adoId} ↗
                                                    </a>
                                                ) : (
                                                    <span className="mono-chip">ADO #{wi.adoId}</span>
                                                )}
                                                {wi.repository && <span className="studio-pill blue">repo: {wi.repository}</span>}
                                            </div>
                                            <div className="studio-badge-row">
                                                <span className="studio-pill neutral">{wi.status}</span>
                                                <span className={`studio-pill ${wi.linked ? "emerald" : "amber"}`}>
                                                    {wi.linked ? "✓ Linked to Feature & DAG" : "Pending links"}
                                                </span>
                                                {wi.prUrl && (
                                                    <a href={wi.prUrl} target="_blank" rel="noreferrer" className="studio-pill emerald">
                                                        PR Opened ↗
                                                    </a>
                                                )}
                                            </div>
                                        </div>
                                        <h4>{wi.title}</h4>
                                    </div>
                                ))}
                            </div>
                        )}
                        {backlogOperations.length > 0 ? (
                            <details>
                                <summary>Raw provider operation receipts ({backlogOperations.length})</summary>
                                <div className="artifact-cards-list" style={{ marginTop: 10 }}>
                                    {backlogOperations.map((op) => (
                                        <div key={op.key} className="run-card">
                                            <div className="card-heading">
                                                <strong>{op.itemId} · {op.kind}</strong>
                                                <span className={`studio-pill ${op.status === "completed" ? "emerald" : "amber"}`}>{op.status}</span>
                                            </div>
                                            <code>{op.key}</code>
                                            <details><summary>Provider receipt</summary><pre>{JSON.stringify(op.receipt, null, 2)}</pre></details>
                                        </div>
                                    ))}
                                </div>
                            </details>
                        ) : planArtifact ? (
                            <p className="muted">
                                Plan is drafted ({Array.isArray((planArtifact.content as Record<string, Json>)?.items) ? ((planArtifact.content as Record<string, Json>).items as Json[]).length : 0} PBIs). Once the plan gate is approved, confirming Backlog Creation creates and links the ADO Product Backlog Items here.
                            </p>
                        ) : (
                            <p className="muted">
                                Complete Design (Stage 2) and Implementation Planning (Stage 3) first; approved PBIs are then created in Azure DevOps under Stage 4.
                            </p>
                        )}
                    </div>
                </section>
            )}

            {studioLens === "code" && (
                <section className="studio-spotlight" aria-label="Code Arena Studio">
                    {codeArtifact ? (
                        <ArtifactDocument artifact={codeArtifact} feature={feature} />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill emerald">STAGE 5 · CODE BUILDER VS. REVIEWER ARENA</span>
                            <h3>Multi-Round Coding Builder vs. Independent Code Reviewer</h3>
                            <p className="muted">
                                During Stage 5 implementation, the <strong>Coding Builder</strong> and <strong>Independent Code Reviewer</strong> iterate across rounds until every acceptance criterion is verified and zero blocker/major/minor findings remain before opening a PR.
                            </p>
                        </div>
                    )}
                </section>
            )}
            {studioLens === "delivery" && (
                <section className="studio-spotlight" aria-label="Delivery Studio">
                    {monitorArtifact ? (
                        <ArtifactDocument artifact={monitorArtifact} feature={feature} onPrefillWaiver={prefillWaiver} onPrefillPrFeedback={prefillPrFeedback} />
                    ) : (
                        <div className="studio-empty-lens">
                            <span className="studio-pill amber">STAGE 6 · DELIVERY &amp; PR COMMAND CENTER</span>
                            <h3>Live ADO Work Item, PR Status &amp; Acceptance Criteria Verification</h3>
                            <p className="muted">
                                Tracks live Azure DevOps PBIs, GitHub/ADO Pull Requests, CI status checks, and AI review rounds with one-click PR feedback composition and reasoned human gap waivers.
                            </p>
                        </div>
                    )}
                </section>
            )}
            {studioLens === "flight" && (
                <section className="studio-spotlight" aria-label="Flight Recorder Studio">
                    <SubagentFlightRecorder feature={feature} selectedExecutionId={selectedExecId} onSelectExecution={setSelectedExecId} />
                </section>
            )}

            {studioLens === "gate" && !currentGate && progress.run?.kind === "plan" && planArtifact && (
                <section className="studio-spotlight" aria-label="Live Plan Studio">
                    <ArtifactDocument artifact={planArtifact} feature={feature} onAppendFeedback={appendFeedback} onSelectItemForOps={selectItemForOps} />
                </section>
            )}
            {studioLens === "gate" && !currentGate && progress.run?.kind === "implement" && codeArtifact && (
                <section className="studio-spotlight" aria-label="Live Code Arena Studio">
                    <ArtifactDocument artifact={codeArtifact} feature={feature} />
                </section>
            )}

            {(studioLens === "gate" || (currentGate && studioLens === currentGate.stage)) && currentGate && (
                <section className="current-review" aria-label={`Review ${currentGate.stage}`}>
                    {["design", "plan"].includes(currentGate.stage) && latest(currentGate.stage as "design" | "plan") &&
                        <ArtifactDocument
                            artifact={latest(currentGate.stage as "design" | "plan")!}
                            feature={feature}
                            onAppendFeedback={appendFeedback}
                            onSelectItemForOps={selectItemForOps}
                        />}
                    {currentGate.stage === "complete" && monitorArtifact && (
                        <ArtifactDocument
                            artifact={monitorArtifact}
                            feature={feature}
                            onPrefillWaiver={prefillWaiver}
                            onPrefillPrFeedback={prefillPrFeedback}
                        />
                    )}
                    {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
                    <details><summary>Exact approval scope and targets</summary><pre>{JSON.stringify({ scope: currentGate.scope, context: currentGate.context || null }, null, 2)}</pre></details>
                    {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
                    <div className="factory-actions"><button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                        kind: "approve_stage", featureId: feature.id, stage: currentGate.stage, expectedScopeHash: currentGate.scopeHash, approved: true,
                    })}>Approve {currentGate.stage === "complete" ? "completion" : currentGate.stage}</button>
                        <button className="secondary" disabled={busy} onClick={() => void run({
                            kind: "approve_stage", featureId: feature.id, stage: currentGate.stage, expectedScopeHash: currentGate.scopeHash, approved: false,
                        })}>Reject this scope</button></div>
                </section>
            )}
        </div>

        <section ref={deliverablesRef} className="deliverables-hub" aria-label="Saved deliverables and run controls">
            <div className="deliverables-hub-header">
                <div>
                    <span className="eyebrow">SAVED DELIVERABLES &amp; EXECUTION HISTORY</span>
                    <h3>Artifacts, Workflow Runs &amp; Remote Operations</h3>
                </div>
                <div className="factory-tabs" role="group" aria-label="Lifecycle details">
                    <button type="button" className={tab === "artifacts" ? "secondary active" : "secondary"} onClick={() => setTab("artifacts")}>
                        Saved Artifacts ({feature.factory.artifacts.length})
                    </button>
                    <button type="button" className={tab === "runs" ? "secondary active" : "secondary"} onClick={() => setTab("runs")}>
                        Workflow Runs ({feature.factory.executions.length})
                    </button>
                    <button type="button" className={tab === "operations" ? "secondary active" : "secondary"} onClick={() => setTab("operations")}>
                        Backlog &amp; PR Ops ({feature.factory.operations.length})
                    </button>
                </div>
            </div>

            {tab === "artifacts" && <>
                <div className="factory-artifacts">
                    {feature.factory.artifacts.length ? [...feature.factory.artifacts].reverse().map((artifact) => {
                        const meta = summarizeArtifactCard(artifact);
                        return (
                            <article key={artifact.id} className="artifact-summary-card">
                                <div className="artifact-card-top">
                                    <div>
                                        <div className="studio-badge-row">
                                            <span className={`studio-pill ${meta.pillColor}`}>{meta.stageBadge}</span>
                                            <span className={`studio-pill ${meta.verdictColor}`}>{meta.verdict}</span>
                                            <span className="mono-chip">#{artifact.id.slice(0, 8)}</span>
                                        </div>
                                        <h4>{meta.title}</h4>
                                        <p className="muted">{meta.summary}</p>
                                    </div>
                                    <div className="artifact-card-actions">
                                        <span className="artifact-timestamp">{new Date(artifact.createdAt).toLocaleString()}</span>
                                        {meta.targetLens && (
                                            <button type="button" className="secondary" onClick={() => openArtifactInStudio(artifact)}>
                                                Open in Studio ↑
                                            </button>
                                        )}
                                    </div>
                                </div>
                                <details className="artifact-inline-expand">
                                    <summary>Expand full artifact inline</summary>
                                    <ArtifactDocument
                                        artifact={artifact}
                                        feature={feature}
                                        onAppendFeedback={appendFeedback}
                                        onSelectItemForOps={selectItemForOps}
                                        onPrefillWaiver={prefillWaiver}
                                        onPrefillPrFeedback={prefillPrFeedback}
                                    />
                                </details>
                            </article>
                        );
                    }) : (
                        <p className="muted">No generated artifacts yet. Ask Factory for draft success criteria above, or approve your own criteria before starting design.</p>
                    )}
                </div>

                {(designArtifact || planArtifact) && (
                    <div className="revision-composer-card">
                        <label htmlFor={`feedback-${feature.id}`}>Request a Design or Plan Revision</label>
                        <p className="field-help">Describe what should change in the architecture or plan. Approved Stage 0 requirements remain immutable unless you edit the brief.</p>
                        <textarea ref={feedbackInputRef} id={`feedback-${feature.id}`} value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={3} placeholder="What should change? Tip: click '+ Add to revision feedback' on any reviewer finding in Design Studio." />
                        <div className="factory-actions">{(["design", "plan"] as const).map((stage) => <button key={stage} className="secondary" disabled={busy || !feedback.trim() || !latest(stage)} onClick={() => void run({
                            kind: "request_revision", featureId: feature.id, stage, expectedArtifactId: latest(stage)?.id, feedback,
                        })}>Request {stage} revision</button>)}</div>
                    </div>
                )}

                {monitorArtifact && (
                    <details><summary>Accept an explicitly reviewed validation gap</summary>
                        <p className="muted">This records a reasoned human waiver for one criterion in the current report. It cannot waive unmerged PRs, incomplete work items, failed CI, or provider errors.</p>
                        <label htmlFor="waiver-item">Work item key</label><input id="waiver-item" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                        <label htmlFor="waiver-criterion">Exact criterion text</label><input id="waiver-criterion" value={criterion} onChange={(event) => setCriterion(event.target.value)} />
                        <label htmlFor="waiver-reason">Reason for accepting the gap</label><textarea id="waiver-reason" value={waiverReason} onChange={(event) => setWaiverReason(event.target.value)} />
                        <button className="secondary" disabled={busy || !latest("monitor") || !itemId || !criterion || !waiverReason} onClick={() => void run({
                            kind: "accept_validation_gap", featureId: feature.id, monitorId: latest("monitor")?.id, itemId, criterion, reason: waiverReason,
                        })}>Review &amp; accept this gap</button>
                    </details>
                )}
            </>}
            {tab === "runs" && <>
                <SubagentFlightRecorder feature={feature} selectedExecutionId={selectedExecId} onSelectExecution={setSelectedExecId} />
                <label htmlFor="resume-limits">Explicit execution/resume-limit overrides (JSON, optional)</label><input id="resume-limits" value={limits} onChange={(event) => setLimits(event.target.value)} />
                <p className="field-help">New runs have no AI-credit cap unless you set one. Copilot usage is still metered. To remove an existing run&apos;s credit cap, use {"{\"maxAiCredits\":null}"} in a confirmed resume.</p>
                {feature.factory.executions.length ? [...feature.factory.executions].reverse().map((execution) => <section className="run-card" key={execution.id}>
                    <div className="card-heading"><h3>{execution.kind} / {execution.status}</h3><span>{execution.phase}</span></div>
                    <dl><dt>Execution</dt><dd>{execution.id}</dd><dt>Owning session</dt><dd>{execution.sessionId}</dd><dt>Native run</dt><dd>{execution.runtimeId || "Awaiting admission"}</dd></dl>
                    <p className="field-help">{execution.sessionKind === "chat"
                        ? "Runs in its original Copilot chat. Dashboard controls go to that resident owner; reconnect the original chat if it is offline."
                        : "Runs in a Factory-owned SDK session. App discovery/attachment of this session is not yet verified."}</p>
                    {feature.factory.sessionBinding?.sessionId === execution.sessionId && execution.sessionKind !== "chat" && <details>
                        <summary>App handoff status</summary>
                        {feature.factory.sessionBinding.publicationError
                            ? <p className="message error">{feature.factory.sessionBinding.publicationError}</p>
                            : <p className="field-help">Host catalog publication does not prove app discovery or attachment. Use only supported host connection controls; this is not an app deep link.</p>}
                        <dl><dt>Stable host identity</dt><dd>{feature.factory.sessionBinding.hostComputeId || "Not published"}</dd>
                            <dt>Native session resource</dt><dd>{feature.factory.sessionBinding.publishedSessionUri || "Unavailable"}</dd></dl>
                    </details>}
                    {execution.launchContext && <details><summary>Runtime and extension interpreter</summary><dl>
                        <dt>Chat host</dt><dd>{execution.launchContext.hostExecutable}</dd>
                        <dt>Extension Node</dt><dd>{execution.launchContext.extensionNode} ({execution.launchContext.nodeVersion})</dd>
                        <dt>Native runtime</dt><dd>{execution.launchContext.nativeRuntime}</dd>
                    </dl></details>}
                    {execution.registration && <details><summary>Workflow registration verified in this owning session</summary>
                        <p className="field-help">Session: {execution.registration.sessionId}. This verifies registration, not design quality or delivery.</p>
                        <ul>{execution.registration.workflowNames.map((name) => <li key={name}>{name}</li>)}</ul>
                        <code>{execution.registration.runtimeFingerprint}</code>
                    </details>}
                    {execution.usage ? <>
                        <dl><dt>Active runtime</dt><dd>{(execution.usage.activeMs / 1000).toFixed(1)} seconds</dd>
                            <dt>Workflow subagents</dt><dd>{execution.usage.subagents}</dd>
                            <dt>AI usage (nano-AIU)</dt><dd>{execution.usage.nanoAiu.toLocaleString()}</dd>
                            <dt>Measured</dt><dd>{new Date(execution.usage.observedAt).toLocaleString()}</dd></dl>
                        <details><summary>Native resource ceilings and effective agent models</summary><pre>{JSON.stringify({
                            declaredLimits: execution.usage.declaredLimits, approvedLimits: execution.usage.approvedLimits, agents: execution.usage.agents,
                        }, null, 2)}</pre></details>
                        <p className="field-help">Native workflow usage only, in the runtime&apos;s reported units; remote coding-task usage is separate. Refresh for current measurements. Credit limits are soft, not exact spending guarantees.</p>
                    </> : <p className="muted">Native usage has not been observed. Refresh this run; missing measurements do not mean zero usage.</p>}
                    {execution.observationError && <p className="message error">{execution.observationError}</p>}
                    {execution.status === "error" && execution.error && <p className="message error">{execution.error}</p>}
                    {(() => {
                        const execControls = feature.factory.controls.filter((control) => control.executionId === execution.id);
                        const latestControl = execControls.at(-1);
                        return latestControl?.error ? <p className="message error" key={latestControl.id}>{latestControl.action}: {latestControl.error}</p> : null;
                    })()}
                    <div className="factory-actions">{(["refresh", "pause", "resume", "cancel"] as const).map((action) => <button className="secondary" disabled={busy} key={action} onClick={() => {
                        try {
                            const overrides: unknown = JSON.parse(limits);
                            void run({ kind: "control_run", featureId: feature.id, executionId: execution.id, action, limits: overrides });
                        } catch { setError("Resume limit overrides must be valid JSON."); }
                    }}>{action}</button>)}</div>
                    {execution.outcome !== null && <details><summary>Full outcome</summary><pre>{JSON.stringify(execution.outcome, null, 2)}</pre></details>}
                    {!["completed", "cancelled", "superseded"].includes(execution.status) && <details><summary>Operator recovery when native ownership cannot be recovered</summary>
                        <p className="muted">Revoke local authority without pretending to cancel or resume the native run. Reconcile all in-flight effects before a successor; paid work may still finish.</p>
                        <label htmlFor={`supersede-${execution.id}`}>Recovery reason</label><textarea id={`supersede-${execution.id}`} value={recoveryReason} onChange={(event) => setRecoveryReason(event.target.value)} />
                        <button className="secondary" disabled={busy || !recoveryReason.trim()} onClick={() => void run({
                            kind: "supersede_execution", featureId: feature.id, executionId: execution.id, reason: recoveryReason,
                        })}>Review local authority revocation</button>
                    </details>}
                </section>) : <p className="muted">No workflow has been started. No agents or credits have been consumed for this feature.</p>}
                <label htmlFor="schedule-minutes">Local monitor cadence, in minutes</label><input id="schedule-minutes" type="number" min="1" value={minutes} onChange={(event) => setMinutes(event.target.value)} />
                <p className="field-help">Opt-in schedules may consume AI credits and run only while an owning host is online.</p>
                <div className="factory-actions"><button className="secondary" disabled={busy} onClick={() => void run({ kind: "set_schedule", featureId: feature.id, minutes: Number(minutes) })}>Enable schedule</button>
                    <button className="text-button" disabled={busy || !feature.factory.schedule} onClick={() => void run({ kind: "set_schedule", featureId: feature.id, minutes: null })}>Disable schedule</button></div>
                {feature.factory.schedule && <p className="muted">Next local check: {new Date(feature.factory.schedule.nextAt).toLocaleString()}</p>}
            </>}
            {tab === "operations" && <div className="factory-artifacts">
                <details open={Boolean(itemId || prFeedback)}><summary>Associate a canonical PR or post reviewed feedback</summary>
                    <label htmlFor="pr-item">Planned work item key</label><input id="pr-item" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                    <label htmlFor="pr-number">PR number in its configured repository</label><input id="pr-number" type="number" min="1" value={prNumber} onChange={(event) => setPrNumber(event.target.value)} />
                    <button className="secondary" disabled={busy || !itemId || !prNumber} onClick={() => void run({ kind: "attach_pr", featureId: feature.id, itemId, number: Number(prNumber) })}>Verify &amp; associate PR</button>
                    <button className="secondary" disabled={busy || !itemId} onClick={() => void run({ kind: "review_pr", featureId: feature.id, itemId })}>Load all review feedback</button>
                    <label htmlFor="pr-feedback">Exact feedback body</label><textarea id="pr-feedback" rows={5} value={prFeedback} onChange={(event) => { setPrFeedback(event.target.value); feedbackRequest.current = crypto.randomUUID(); }} />
                    <p className="field-help">Monitor first to bind feedback to the reviewed head. Mentions may activate a coding agent. Nothing posts automatically.</p>
                    <button className="secondary" disabled={busy || !itemId || !prFeedback} onClick={() => void run({
                        kind: "post_pr_feedback", featureId: feature.id, itemId, requestId: feedbackRequest.current, body: prFeedback,
                    })}>Review &amp; post exact feedback</button>
                </details>
                {feature.factory.operations.length ? feature.factory.operations.map((operation) => <section key={operation.key} className="run-card">
                    <h3>{operation.itemId} / {operation.kind} / {operation.status}</h3><code>{operation.key}</code>
                    {operation.error && <p className="message error">{operation.error}</p>}
                    <details><summary>Remote receipt</summary><pre>{JSON.stringify(operation.receipt, null, 2)}</pre></details>
                    {operation.status === "reconciliation_required" && <>
                        <button className="secondary" disabled={busy} onClick={() => void run({ kind: "reconcile_operation", featureId: feature.id, operationKey: operation.key })}>Reconcile without redispatch</button>
                        <details><summary>Operator recovery when the provider proves nothing was sent</summary>
                            <p className="muted">This is an explicit human attestation, not automatic retry. A wrong assessment can duplicate remote work.</p>
                            <label htmlFor={`recovery-${operation.key}`}>Investigation and reason</label><textarea id={`recovery-${operation.key}`} value={recoveryReason} onChange={(event) => setRecoveryReason(event.target.value)} />
                            <button className="secondary" disabled={busy || !recoveryReason.trim()} onClick={() => void run({
                                kind: "confirm_operation_not_sent", featureId: feature.id, operationKey: operation.key, reason: recoveryReason,
                            })}>Review no-effect attestation</button>
                        </details>
                    </>}
                </section>) : <p className="muted">No remote operations. Creating or dispatching work requires its own approved scope.</p>}
            </div>}
            {result && <details><summary>Technical command result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
        </section>

        <details className="advanced-controls">
            <summary>Manual stage triggers, additional gates &amp; audit trail</summary>
            <p className="muted">Low-level manual overrides and full lifecycle audit JSON. Use the Next Step card above for standard progression.</p>
            <div className="factory-actions">
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || feature.status !== "ready_for_design"} onClick={() => void start("design")}>Start a design run</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || feature.status !== "ready_for_design"} onClick={() => void start("plan")}>Start a planning run</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || !latest("plan")} onClick={() => void start("implement")}>Start a coding &amp; review run</button>
                <button className="secondary" disabled={busy || Boolean(feature.configurationMismatch || feature.recovery) || !latest("plan")} onClick={() => void start("monitor")}>Start a monitoring run</button>
                <button className="secondary" disabled={busy} onClick={() => void run({ kind: "export_feature", featureId: feature.id })}>Export feature</button>
                <button className="text-button" disabled={busy || feature.factory.cancelled} onClick={() => void run({ kind: "cancel_feature", featureId: feature.id })}>Cancel feature</button>
            </div>
            <div className="gate-grid">{gates.filter((gate) => gate.available && gate.stage !== currentGate?.stage).map((gate) => <section key={gate.stage} className="gate-card">
                <h3>{gate.stage === "complete" ? "Completion" : gate.stage} approval</h3>
                {!gate.available ? <p>{gate.reason}</p> : <>
                    <p>{gate.approved ? "Approved for this exact scope." : gate.ready ? "Ready for your decision." : gate.blockers?.join(" ")}</p>
                    <details><summary>Review complete scope</summary><pre>{JSON.stringify({ scope: gate.scope, context: gate.context || null }, null, 2)}</pre></details>
                    <div className="factory-actions">
                        <button className="secondary" disabled={busy || !gate.ready || gate.approved} onClick={() => void run({
                            kind: "approve_stage", featureId: feature.id, stage: gate.stage, expectedScopeHash: gate.scopeHash, approved: true,
                        })}>Approve {gate.stage}</button>
                        <button className="text-button" disabled={busy} onClick={() => void run({
                            kind: "approve_stage", featureId: feature.id, stage: gate.stage, expectedScopeHash: gate.scopeHash, approved: false,
                        })}>Reject</button>
                    </div>
                </>}
            </section>)}</div>
            <details><summary>Complete approval and lifecycle audit</summary><pre>{JSON.stringify({
                decisions: feature.factory.decisions, events: feature.factory.events,
                validationWaivers: feature.factory.waivers, canonicalPrBindings: feature.factory.prBindings,
            }, null, 2)}</pre></details>
        </details>
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

        <h3>Read-only evidence sources</h3>
        <p className="field-help">Add optional <code>referenceRepositories</code> in the settings JSON using the same repository fields. They are pinned research sources, never backlog or coding targets. Saving changed source scope requires confirmation and invalidates prior run scope.</p>
        {parsedConfig?.referenceRepositories && <ul>{Object.entries(parsedConfig.referenceRepositories).map(([alias, repo]) =>
            <li key={alias}><strong>{alias}</strong>: {repo.hostname}/{repo.slug} ({repo.localPath}) — evidence only</li>)}</ul>}
        <label htmlFor="configuration-json">Repository and ADO settings</label><textarea id="configuration-json" rows={12} value={configuration} onChange={(event) => setConfiguration(event.target.value)} spellCheck={false} />
        <div className="factory-actions"><button className="primary" disabled={busy} onClick={() => {
            try { void run({ kind: "configure", configuration: JSON.parse(configuration) }); }
            catch { setError("Configuration must be valid JSON."); }
        }}>Review &amp; save configuration</button><button className="secondary" disabled={busy} onClick={() => void run({ kind: "preflight" })}>Verify provider connections</button></div>
        <h3>Import historical features</h3><p className="muted">Preview is read-only. Import requires explicit requirements for every selected legacy feature. Historical approvals are never trusted.</p>
        <label htmlFor="legacy-file">Local legacy JSON path</label><input id="legacy-file" value={file} onChange={(event) => { setFile(event.target.value); setImportHash(""); }} />
        <button className="secondary" disabled={busy || !file.trim()} onClick={() => void run({ kind: "preview_import", path: file })}>Preview import</button>
        <label htmlFor="legacy-mappings">Explicit mappings: [{"{ legacyId, input: { title, requirements, blockingUnknowns } }"}]</label><textarea id="legacy-mappings" rows={5} value={mappings} onChange={(event) => setMappings(event.target.value)} />
        <button className="secondary" disabled={busy || !importHash} onClick={() => {
            try { void run({ kind: "import_legacy", path: file, expectedHash: importHash, mappings: JSON.parse(mappings) }); }
            catch { setError("Import mappings must be valid JSON."); }
        }}>Review &amp; import selected features</button>
        <h3>Consistent registry backup and isolated restore</h3>
        <button className="secondary" disabled={busy} onClick={() => void run({ kind: "backup_registry" })}>Create private SQLite backup</button>
        <label htmlFor="backup-path">Backup snapshot path</label><input id="backup-path" value={backupPath} onChange={(event) => { setBackupPath(event.target.value); setBackupHash(""); }} />
        <button className="secondary" disabled={busy || !backupPath} onClick={() => void run({ kind: "inspect_backup", path: backupPath })}>Inspect integrity &amp; hash</button>
        <label><input type="checkbox" checked={hostsStopped} onChange={(event) => setHostsStopped(event.target.checked)} /> Original execution hosts have been stopped.</label>
        <button className="secondary" disabled={busy || !backupHash || !hostsStopped} onClick={() => void run({
            kind: "restore_backup_clone", path: backupPath, expectedHash: backupHash, originalHostsStopped: hostsStopped,
        })}>Review isolated restore</button>
        <p className="field-help">Restore never overwrites the live registry. It disables schedules and returns a new data directory; native journals and ownership are not copied.</p>
        {error && <div className="message error" role="alert">{error}</div>}
        {result && <details open><summary>Complete result / preview</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
    </section>;
}
