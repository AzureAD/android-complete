// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { computeWorkspaceAnalytics, findSupersededLocalDraftIds } from "../../core/analytics.ts";
import { parseRequirements, parseSuccessCriteria, type Command, type Feature, type Project, type Requirement, type Requirements } from "../../core/contracts.ts";
import { parseIntakeProposal, type Artifact, type FactoryFeature, type Json, type ConfigRevision } from "../../core/lifecycle-contracts.ts";
import type { FeatureQaContext } from "../../core/qa-contracts.ts";
import type { SharedTarget } from "../../core/shared-contracts.ts";
import { WorkspaceAnalyticsView } from "./AnalyticsStudio";
import { FactoryPanel, FactorySettings } from "./FactoryPanel";
import { Confirmation, type ConfirmationRequest } from "./Confirmation";
import { RequirementsRevisionDiffExplorer } from "./RevisionDiffViewer";
import { nextStep } from "../../core/progress.ts";
import type { runtimeReport } from "../../core/doctor.ts";
import type { DecisionPresentation } from "../../core/contracts.ts";
import {
    onSessionRefreshed,
    request,
    setCsrfToken,
    subscribeStudioEvents,
    type FeatureProvenance,
    type LiveStreamStatus,
    type WorktreeInfo,
} from "./api";

interface Draft { title: string; description: string; requirements: string; unknowns: string; designTasks: string }
const emptyDraft = (): Draft => ({ title: "", description: "", requirements: "", unknowns: "", designTasks: "" });
const displayStatus = (feature: Feature) => {
    if ("lifecycleStatus" in feature && typeof feature.lifecycleStatus === "string" &&
        !["requirements_review", "ready_for_design"].includes(feature.lifecycleStatus)) {
        return feature.lifecycleStatus.replaceAll("_", " ");
    }
    return feature.status === "ready_for_design" ? "Ready for design" : "Needs review";
};

function draftFor(feature: Feature): Draft {
    return {
        title: feature.current.title,
        description: feature.current.description || "",
        requirements: feature.current.requirements.map((item) => item.text).join("\n"),
        unknowns: feature.current.blockingUnknowns.join("\n"),
        designTasks: (feature.current.designTasks || []).join("\n"),
    };
}

function requirementsFor(draft: Draft, previous: Requirement[] = []): Requirements {
    return parseRequirements({
        title: draft.title,
        description: draft.description,
        requirements: parseSuccessCriteria(draft.requirements, previous),
        blockingUnknowns: draft.unknowns.split("\n").map((line) => line.trim()).filter(Boolean),
        designTasks: draft.designTasks.split("\n").map((line) => line.trim()).filter(Boolean),
    });
}

function IntakeSuggestion({ artifact, disabled, onReview }: { artifact: Artifact; disabled: boolean; onReview: () => void }) {
    let proposal;
    try { proposal = parseIntakeProposal(artifact.content); }
    catch (failure) { return <div role="alert" className="message error">{failure instanceof Error ? failure.message : "The saved draft could not be read."}</div>; }
    return <section className="intake-proposal" aria-label="Factory suggestions">
        <h3>Factory&apos;s draft suggestions</h3><p>{proposal.summary}</p>
        <ol>{proposal.requirements.map((item) => <li key={item.id}>{item.text}</li>)}</ol>
        {proposal.blockingUnknowns.length > 0 && <><h4>Questions to resolve</h4><ul>{proposal.blockingUnknowns.map((question) => <li key={question}>{question}</li>)}</ul></>}
        {!!proposal.designTasks?.length && <><h4>Work for the design agents</h4><ul>{proposal.designTasks.map((task) => <li key={task}>{task}</li>)}</ul>
            <p className="field-help">These are design obligations, not questions you must answer before design starts. Existing constraints must still be preserved.</p></>}
        <p className="field-help">These are suggestions, not changes to your saved criteria or an approval. Review and edit them before saving.</p>
        <button className="secondary" disabled={disabled} onClick={onReview}>Edit these suggestions</button>
    </section>;
}

function FactoryMark() {
    return <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 26V13l8 5v-6l8 5V6h6v20H5Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /><path d="M10 22h2m5 0h2m5 0h1" stroke="currentColor" strokeWidth="2" /></svg>;
}

export function App() {
    const [project, setProject] = useState<Project>();
    const [worktree, setWorktree] = useState<WorktreeInfo>();
    const [provenanceMap, setProvenanceMap] = useState<Record<string, FeatureProvenance>>({});
    const [features, setFeatures] = useState<FactoryFeature[]>([]);
    const [showSupersededDrafts, setShowSupersededDrafts] = useState(false);
    const [analyticsOpen, setAnalyticsOpen] = useState(false);
    const [streamStatus, setStreamStatus] = useState<LiveStreamStatus>("connecting");
    const [settings, setSettings] = useState(false);
    const [adoFeatureId, setAdoFeatureId] = useState("");
    const [recoveryReason, setRecoveryReason] = useState("");
    const [teamFeatures, setTeamFeatures] = useState<SharedTarget[]>([]);
    const [selectedId, setSelectedId] = useState<string>();
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<Draft>(emptyDraft);
    const [baseRevision, setBaseRevision] = useState<string>();
    const [busy, setBusy] = useState(true);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [doctor, setDoctor] = useState<ReturnType<typeof runtimeReport>>();
    const [confirmation, setConfirmation] = useState<ConfirmationRequest>();
    const [syncedAt, setSyncedAt] = useState<number>();
    const [syncError, setSyncError] = useState("");
    const [theme, setTheme] = useState<"light" | "dark">("dark");
    const [featureFilter, setFeatureFilter] = useState("");
    const [activeStageIdx, setActiveStageIdx] = useState<number>(0);
    const [stage1SubTab, setStage1SubTab] = useState<"brief" | "diff">("brief");
    const [flightOpen, setFlightOpen] = useState(false);
    const [qaOpen, setQaOpen] = useState(false);
    const [qaPinnedContext, setQaPinnedContext] = useState<FeatureQaContext | null>(null);
    const openAskCopilot = (ctx?: FeatureQaContext | null) => {
        if (ctx !== undefined) setQaPinnedContext(ctx);
        setQaOpen(true);
    };
    const refreshing = useRef(false);
    const pendingSseRefresh = useRef(false);
    const refreshSequence = useRef(0);
    const authorityReadAt = useRef(0);
    const csrf = useRef("");
    const creationId = useRef(crypto.randomUUID());

    const supersededDraftIds = useMemo(() => findSupersededLocalDraftIds(features), [features]);
    const visibleFeatures = useMemo(
        () => (showSupersededDrafts ? features : features.filter((f) => !supersededDraftIds.has(f.id))),
        [features, supersededDraftIds, showSupersededDrafts],
    );
    const workspaceAnalytics = useMemo(() => computeWorkspaceAnalytics(features), [features]);
    const selected = visibleFeatures.find((feature) => feature.id === selectedId)
        || features.find((feature) => feature.id === selectedId);

    async function refresh(authoritative = true) {
        const sequence = ++refreshSequence.current;
        if (authoritative) authorityReadAt.current = Date.now();
        const data = await request<{
            project: Project;
            worktree?: WorktreeInfo;
            provenance?: Record<string, FeatureProvenance>;
            features: FactoryFeature[];
        }>(authoritative ? "/api/features" : "/api/features?cached=1");
        if (sequence !== refreshSequence.current) return data.features;
        setProject(data.project);
        if (data.worktree) setWorktree(data.worktree);
        if (data.provenance) setProvenanceMap(data.provenance);
        setFeatures((previous) => JSON.stringify(previous) === JSON.stringify(data.features) ? previous : data.features);
        const hiddenIds = findSupersededLocalDraftIds(data.features);
        const defaultVisible = data.features.filter((f) => !hiddenIds.has(f.id));
        setSelectedId((previous) => {
            if (previous && !hiddenIds.has(previous) && data.features.some((feature) => feature.id === previous)) {
                return previous;
            }
            if (previous && hiddenIds.has(previous)) {
                const oldDraft = data.features.find((f) => f.id === previous);
                const originRevId = oldDraft?.history[0]?.id;
                const publishedSuccessor = originRevId
                    ? data.features.find((f) => Boolean(f.shared) && f.history[0]?.id === originRevId)
                    : undefined;
                if (publishedSuccessor) return publishedSuccessor.id;
            }
            return defaultVisible[0]?.id ?? data.features[0]?.id;
        });
        setSyncedAt(Date.now());
        setSyncError("");
        return data.features;
    }

    useEffect(() => {
        let cancelled = false;
        const unsubscribe = onSessionRefreshed((freshCsrf) => {
            csrf.current = freshCsrf;
            if (!cancelled) setSyncError("");
        });
        void request<{ csrf: string; project: Project; worktree?: WorktreeInfo }>("/api/session").then(async (session) => {
            if (cancelled) return;
            csrf.current = session.csrf;
            setCsrfToken(session.csrf);
            if (session.worktree) setWorktree(session.worktree);
            const items = await refresh();
            const hiddenIds = findSupersededLocalDraftIds(items);
            const firstVisible = items.find((f) => !hiddenIds.has(f.id)) ?? items[0];
            if (!cancelled) setSelectedId(firstVisible?.id);
        }).catch((failure: unknown) => {
            if (!cancelled) setError(failure instanceof Error ? failure.message : "Could not connect to the local registry.");
        }).finally(() => { if (!cancelled) setBusy(false); });
        return () => { cancelled = true; unsubscribe(); };
    }, []);

    useEffect(() => {
        if (!project) return;
        let cancelled = false;
        const sync = async (force = false) => {
            if (refreshing.current) {
                pendingSseRefresh.current = true;
                return;
            }
            refreshing.current = true;
            try {
                await refresh(force || Date.now() - authorityReadAt.current >= 20000);
            } catch (failure) {
                if (!cancelled) setSyncError(failure instanceof Error ? failure.message : "Shared state could not be refreshed.");
            } finally {
                refreshing.current = false;
                if (pendingSseRefresh.current && !cancelled) {
                    pendingSseRefresh.current = false;
                    void sync(false);
                }
            }
        };

        const unsubscribeSse = subscribeStudioEvents({
            onStatus: (next) => {
                if (!cancelled) setStreamStatus(next);
            },
            onEvent: (event) => {
                if (cancelled) return;
                if (event.type === "state-changed") {
                    const needsAuthority = event.reason.startsWith("command:publish_") || event.reason.startsWith("command:open_shared");
                    void sync(needsAuthority);
                }
            },
        });

        const pollIntervalMs = streamStatus === "live" ? 25000 : 2500;
        const timer = setInterval(() => { void sync(); }, pollIntervalMs);
        const focus = () => { void sync(true); };
        window.addEventListener("focus", focus);
        return () => {
            cancelled = true;
            unsubscribeSse();
            clearInterval(timer);
            window.removeEventListener("focus", focus);
        };
    }, [project?.id, streamStatus]);

    function finishConfirmation(approved: boolean) {
        confirmation?.resolve(approved);
        setConfirmation(undefined);
    }

    async function act(action: () => Promise<void>) {
        setBusy(true);
        setError("");
        setNotice("");
        try { await action(); }
        catch (failure) { setError(failure instanceof Error ? failure.message : "The operation failed. Your saved feature was not replaced."); }
        finally { setBusy(false); }
    }

    function navigate(id?: string) {
        if ((editing || creating) && !window.confirm("Discard your unsaved feature edits?")) return;
        setEditing(false);
        setCreating(false);
        setAnalyticsOpen(false);
        setStage1SubTab("brief");
        setSelectedId(id);
        setFlightOpen(false);
        setQaPinnedContext(null);
        setError("");
        setNotice("");
    }

    function newFeature() {
        if ((editing || creating) && !window.confirm("Discard your unsaved feature edits?")) return;
        creationId.current = crypto.randomUUID();
        setDraft(emptyDraft());
        setCreating(true);
        setEditing(false);
        setAnalyticsOpen(false);
        setError("");
        setNotice("");
    }

    async function execute(command: Record<string, unknown>): Promise<Record<string, Json>> {
        const headers = { "Content-Type": "application/json", "X-Software-Factory-CSRF": csrf.current };
        const preview = await request<{ scope: Json; consentKey: string | null; confirmation: string | null; decision: DecisionPresentation | null }>("/api/commands/preview", {
            method: "POST", headers, body: JSON.stringify(command),
        });
        if (preview.consentKey) {
            if (!preview.decision) throw new Error("The action has no readable decision preview. Nothing was approved.");
            if (!await new Promise<boolean>((resolve) => setConfirmation({
                command, scope: preview.scope, consentKey: preview.consentKey!, decision: preview.decision!, resolve,
            }))) return { outcome: "not_approved" };
        }
        const result = await request<Record<string, Json>>("/api/commands", {
            method: "POST",
            headers: { ...headers, ...(preview.consentKey ? { "X-Software-Factory-Scope": preview.consentKey } : {}) },
            body: JSON.stringify(command),
        });
        await refresh();
        if (["publish_feature", "open_shared_feature"].includes(String(command.kind)) && result.feature &&
            typeof result.feature === "object" && !Array.isArray(result.feature) && typeof result.feature.id === "string") {
            setSelectedId(result.feature.id);
        }
        return result;
    }

    async function teamAction(kind: "publish_feature" | "open_shared_feature", workItemId = Number(adoFeatureId), createNew = false) {
        const { configuration } = await request<{ configuration: ConfigRevision | null }>("/api/configuration");
        if (!configuration) throw new Error("Configure repository mappings and the ADO organization/project under Connections first.");
        await execute({ kind, ...(kind === "publish_feature" ? { featureId: selected?.id } : {}),
            target: createNew
                ? { organization: configuration.value.boards.organization, project: configuration.value.boards.project, create: true }
                : { organization: configuration.value.boards.organization, project: configuration.value.boards.project, workItemId } });
    }

    async function send(command: Command) {
        const result = await execute(command);
        if (!result.feature || typeof result.feature !== "object" || Array.isArray(result.feature) ||
            !("id" in result.feature) || typeof result.feature.id !== "string") {
            if (result.outcome === "not_approved") return null;
            throw new Error("The command returned no saved feature.");
        }
        return { feature: { id: result.feature.id } };
    }

    function save(event: FormEvent) {
        event.preventDefault();
        void act(async () => {
            const input = requirementsFor(draft, editing ? selected?.current.requirements : []);
            let command: Command;
            if (creating) command = { kind: "create_feature", requestId: creationId.current, input };
            else {
                if (!selected || !baseRevision) throw new Error("The edited feature or base revision is unavailable. Your draft has been preserved.");
                command = { kind: "revise_requirements", featureId: selected.id, expectedRevision: baseRevision, input };
            }
            const saved = await send(command);
            if (!saved) return;
            const { feature } = saved;
            await refresh();
            setSelectedId(feature.id);
            setCreating(false);
            setEditing(false);
            setNotice("Feature draft saved. Review the success criteria before approving it for design. Saving does not start a workflow.");
        });
    }

    function approve(feature: Feature) {
        void act(async () => {
            const saved = await send({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
            if (!saved) return;
            await refresh();
            setNotice("This brief and its success criteria are approved for design. Approval does not start a workflow; run guarded design when ready.");
        });
    }

    const workspace = worktree?.repositoryName || project?.workspace.split(/[\\/]/).filter(Boolean).pop() || "Local workspace";
    const formOpen = creating || editing;
    const intake = selected?.factory.artifacts.find((artifact) => artifact.id === selected.currentArtifacts.intake);
    const intakeRunning = selected?.factory.executions.some((run) => run.kind === "intake" && ["starting", "running", "paused"].includes(run.status));
    const edit = () => { if (selected) { setDraft(draftFor(selected)); setBaseRevision(selected.current.id); setEditing(true); } };
    const reviewSuggestions = () => {
        if (!selected || !intake) return;
        const proposal = parseIntakeProposal(intake.content);
        setDraft({ ...draftFor(selected), requirements: proposal.requirements.map((item) => item.text).join("\n"),
            unknowns: [...new Set([...selected.current.blockingUnknowns.filter((question) => !proposal.designTasks?.includes(question)), ...proposal.blockingUnknowns])].join("\n"),
            designTasks: [...new Set([...(selected.current.designTasks || []), ...(proposal.designTasks || [])])].join("\n") });
        setBaseRevision(selected.current.id); setEditing(true); setError("");
        setNotice("Review and edit these suggestions. Saving creates a draft revision, not an approval.");
    };

    return <div className="app-shell" data-theme={theme}>
        <aside className="sidebar">
            <div className="sidebar-brand-row">
                <a className="brand" href="/" aria-label="Software Factory home">
                    <span className="brand-mark"><FactoryMark /></span>
                    <span>Software <strong>Factory</strong></span>
                </a>
                <span className="version-chip">0.2</span>
            </div>

            <div className="workspace-compact" title={worktree?.workspace || project?.workspace || workspace}>
                <span className="workspace-repo-name">{workspace}</span>
                {worktree?.branch && (
                    <span className="worktree-context-chip">
                        ⎇ {worktree.branch.length > 26 ? `${worktree.branch.slice(0, 24)}…` : worktree.branch}
                    </span>
                )}
            </div>

            <div className="sidebar-primary-nav" role="navigation" aria-label="Primary workspace views">
                <button
                    type="button"
                    className={`sidebar-view-btn ${!analyticsOpen && !creating ? "active" : ""}`}
                    onClick={() => { setAnalyticsOpen(false); setCreating(false); }}
                >
                    <span>Feature Studio</span>
                    <span className="sidebar-count-pill">{visibleFeatures.length}</span>
                </button>
                <button
                    type="button"
                    className={`sidebar-view-btn ${analyticsOpen ? "active" : ""}`}
                    onClick={() => { setCreating(false); setEditing(false); setAnalyticsOpen(true); }}
                >
                    <span>Scorecard &amp; Analytics</span>
                    <span className="sidebar-count-pill">
                        {workspaceAnalytics.convergence.firstPushCiGreenRatePct !== null
                            ? `${workspaceAnalytics.convergence.firstPushCiGreenRatePct}% CI`
                            : `${workspaceAnalytics.totalExecutions} runs`}
                    </span>
                </button>
            </div>

            <div className="sidebar-heading">
                <span>FEATURES</span>
                <button type="button" className="sidebar-new-btn" onClick={newFeature} disabled={busy}>
                    + New
                </button>
            </div>

            {visibleFeatures.length > 4 && (
                <input
                    className="sidebar-search"
                    type="search"
                    placeholder="Filter features..."
                    aria-label="Filter features"
                    value={featureFilter}
                    onChange={(e) => setFeatureFilter(e.target.value)}
                />
            )}

            <nav aria-label="Features" className="feature-nav">
                {visibleFeatures
                    .filter((f) => !featureFilter.trim() || f.current.title.toLowerCase().includes(featureFilter.toLowerCase()))
                    .map((feature) => {
                        const step = nextStep(feature, []);
                        const isRunning = feature.factory.executions.some((e) => ["starting", "running"].includes(e.status));
                        const prov = provenanceMap[feature.id];
                        const originWorkspace = prov?.codingWorkspace || prov?.originWorkspace;
                        const originLeaf = prov?.codingWorktree || prov?.originWorktree || null;
                        const isCrossWorktree = Boolean(
                            originWorkspace
                            && worktree?.workspace
                            && originWorkspace.toLowerCase() !== worktree.workspace.toLowerCase()
                            && originLeaf
                            && originLeaf.toLowerCase() !== workspace.toLowerCase(),
                        );
                        return <button key={feature.id} className={selectedId === feature.id && !creating && !analyticsOpen ? "feature-link selected" : "feature-link"} onClick={() => navigate(feature.id)} disabled={busy}>
                            <span className={isRunning ? "status-dot pulse" : feature.status === "ready_for_design" ? "status-dot ready" : "status-dot"} />
                            <span className="feature-link-body">
                                <strong>{feature.current.title}</strong>
                                <span className="feature-link-badges">
                                    <span className="mini-stage-pill">S{step.stage + 1}/6 · {step.stage === 5 ? "Verify" : ["Brief", "Design", "Plan", "Backlog", "Code", "Verify"][step.stage]}</span>
                                    {feature.shared
                                        ? <span className="mini-ado-pill">ADO #{feature.shared.target.workItemId}</span>
                                        : <span className="mini-draft-pill">Local draft</span>}
                                    {isCrossWorktree && originLeaf && (
                                        <span
                                            className="mini-worktree-pill"
                                            title={`Origin worktree: ${originWorkspace}${prov?.originBranch ? ` (${prov.originBranch})` : ""}`}
                                        >
                                            ⎇ {originLeaf.length > 14 ? `${originLeaf.slice(-12)}` : originLeaf}
                                        </span>
                                    )}
                                </span>
                            </span>
                        </button>;
                    })}
                {!busy && visibleFeatures.length === 0 && <p className="sidebar-empty">No features yet — click + New above.</p>}
                {supersededDraftIds.size > 0 && (
                    <button
                        type="button"
                        className="superseded-toggle-btn"
                        onClick={() => setShowSupersededDrafts((prev) => !prev)}
                    >
                        {showSupersededDrafts
                            ? `Hide ${supersededDraftIds.size} archived local draft${supersededDraftIds.size === 1 ? "" : "s"}`
                            : `Show ${supersededDraftIds.size} archived pre-publish draft${supersededDraftIds.size === 1 ? "" : "s"}`}
                    </button>
                )}
            </nav>

            <div className="sidebar-footer">
                <span className="status-dot ready" />
                <span>Unified Worktrees · Guarded Workflows</span>
            </div>
        </aside>

        <main>
            <header className="topbar">
                <div className="topbar-left">
                    <div className="topbar-mode-pills" role="tablist" aria-label="Workspace surface">
                        <button
                            type="button"
                            role="tab"
                            aria-selected={!analyticsOpen}
                            className={`topbar-mode-pill ${!analyticsOpen ? "active" : ""}`}
                            onClick={() => setAnalyticsOpen(false)}
                        >
                            Feature Studio
                        </button>
                        <button
                            type="button"
                            role="tab"
                            aria-selected={analyticsOpen}
                            className={`topbar-mode-pill ${analyticsOpen ? "active" : ""}`}
                            onClick={() => { setCreating(false); setEditing(false); setAnalyticsOpen(true); }}
                        >
                            Scorecard &amp; Analytics
                        </button>
                    </div>
                    {!analyticsOpen && selected && !formOpen && (
                        <span className="topbar-crumb" title={selected.current.title}>
                            <span className="slash">/</span>
                            {selected.shared ? `ADO #${selected.shared.target.workItemId}` : "Local Draft"}
                        </span>
                    )}
                </div>

                <div className="topbar-right">
                    {selected && !formOpen && !analyticsOpen && (
                        <button
                            type="button"
                            className={`text-button ask-copilot-topbar-btn ${qaOpen ? "active" : ""}`}
                            onClick={() => setQaOpen(!qaOpen)}
                        >
                            ✦ Ask Copilot
                        </button>
                    )}
                    <button className="text-button" onClick={() => setSettings(!settings)}>
                        {settings ? "Close Connections" : "Connections"}
                    </button>
                    <button className="text-button" disabled={busy} onClick={() => void act(async () => { setDoctor(await request("/api/doctor")); })}>
                        Check setup
                    </button>
                    <button className="text-button" disabled={busy} onClick={() => void act(async () => { await refresh(); setNotice("Synced latest state."); })}>
                        Refresh
                    </button>
                    <button className="text-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
                        {theme === "light" ? "Dark" : "Light"}
                    </button>
                    <span className={`sync-badge ${syncError ? "offline" : streamStatus === "live" ? "live-sse" : ""}`} role="status">
                        {syncError
                            ? "Offline"
                            : streamStatus === "live"
                                ? "● Live"
                                : syncedAt
                                    ? "Synced"
                                    : "Connecting…"}
                    </span>
                </div>
            </header>

            <div className="content">
                {syncError && <div role="alert" className="message notice">Reconnecting live updates ({syncError}). Saved state and edits remain intact.</div>}
                {settings && <FactorySettings execute={execute} />}
                {error && <div role="alert" className="message error">{error}</div>}
                {notice && <div role="status" className="message notice">{notice}</div>}
                {doctor !== undefined && (
                    <section className="card preflight">
                        <div className="card-heading">
                            <h2>{doctor.runtime.ready ? "Local runtime ready" : "Local runtime needs repair"}</h2>
                            <button className="text-button" onClick={() => setDoctor(undefined)}>Close</button>
                        </div>
                        <p>{doctor.runtime.message}</p>
                        {doctor.runtime.recoveryCommand && (
                            <>
                                <p>Run this from the repository root, then explicitly retry. No AI work runs during repair.</p>
                                <pre className="code-snippet-box">{doctor.runtime.recoveryCommand}</pre>
                            </>
                        )}
                    </section>
                )}

                {!formOpen && !analyticsOpen && (
                    <section className="feature-context-bar" aria-label="Selected feature header">
                        <div className="feature-context-main">
                            <div className="feature-context-title-group">
                                <span className="eyebrow">
                                    {selected?.shared
                                        ? `AZURE DEVOPS SHARED FEATURE · REV ${selected.shared.revision}`
                                        : selected
                                            ? "LOCAL FEATURE DRAFT · UNPUBLISHED"
                                            : "SOFTWARE FACTORY"}
                                </span>
                                <h1>{selected ? selected.current.title : "Select or create a feature"}</h1>
                            </div>
                            <div className="feature-context-badges">
                                {selected && (
                                    <span className={selected.status === "ready_for_design" ? "tag approved" : "tag"}>
                                        {displayStatus(selected)}
                                    </span>
                                )}
                                {selected?.shared && (
                                    <a
                                        className="studio-pill purple ado-header-pill"
                                        href={`https://dev.azure.com/${encodeURIComponent(selected.shared.target.organization)}/${encodeURIComponent(selected.shared.target.project)}/_workitems/edit/${selected.shared.target.workItemId}`}
                                        target="_blank"
                                        rel="noreferrer"
                                    >
                                        ADO #{selected.shared.target.workItemId} ↗
                                    </a>
                                )}
                                {selected?.shared?.lock && (
                                    <span className="studio-pill amber">
                                        Lock: {selected.shared.lock.kind} ({selected.shared.lock.actor.displayName})
                                    </span>
                                )}
                            </div>
                        </div>

                        {selected?.shared?.lock && (
                            <details className="ado-switch-drawer" style={{ marginTop: 10 }}>
                                <summary>Shared claim active ({selected.shared.lock.actor.displayName}) — inspect or recover</summary>
                                <div className="ado-switch-body">
                                    <p className="field-help">Stop or inspect the original owner and reconcile uncertain provider sends first. Releasing does not cancel native work.</p>
                                    <label htmlFor="shared-recovery-reason">Investigation and recovery reason</label>
                                    <textarea id="shared-recovery-reason" value={recoveryReason} onChange={(event) => setRecoveryReason(event.target.value)} />
                                    <button className="secondary" disabled={busy || !recoveryReason.trim()} onClick={() => void act(async () => {
                                        await execute({ kind: "release_shared_claim", featureId: selected.id, claimId: selected.shared!.lock!.id, reason: recoveryReason });
                                        setRecoveryReason("");
                                    })}>Review claim release</button>
                                </div>
                            </details>
                        )}

                        <details className="ado-switch-drawer" open={!selected}>
                            <summary>
                                {selected?.shared
                                    ? "Open or import another shared ADO Feature"
                                    : "Publish this draft to Azure DevOps or open an existing ADO Feature"}
                            </summary>
                            <div className="ado-switch-body">
                                {!selected?.shared && (
                                    <p className="field-help" style={{ marginTop: 0 }}>
                                        Publishing binds requirements, artifacts, and stage approvals to an authoritative Azure DevOps Feature work item.
                                    </p>
                                )}
                                <div className="ado-inline-controls">
                                    <input
                                        id="ado-feature-id"
                                        aria-label="ADO Feature work item ID"
                                        inputMode="numeric"
                                        value={adoFeatureId}
                                        onChange={(event) => setAdoFeatureId(event.target.value)}
                                        placeholder="ADO Feature ID (e.g. 3822464)"
                                    />
                                    <button className="secondary" disabled={busy || !adoFeatureId} onClick={() => void act(() => teamAction("open_shared_feature"))}>
                                        Open ADO Feature
                                    </button>
                                    {!selected?.shared && selected && (
                                        <>
                                            <button className="secondary" disabled={busy || !adoFeatureId} onClick={() => void act(() => teamAction("publish_feature"))}>
                                                Link &amp; Publish to ID
                                            </button>
                                            <button className="primary" disabled={busy} onClick={() => void act(() => teamAction("publish_feature", 0, true))}>
                                                Create &amp; Publish New ADO Feature
                                            </button>
                                        </>
                                    )}
                                    <button className="secondary" disabled={busy} onClick={() => void act(async () => {
                                        const result = await execute({ kind: "list_shared_features" });
                                        if (!Array.isArray(result.features)) throw new Error("ADO returned no team feature list.");
                                        setTeamFeatures(result.features.map((entry) => {
                                            if (!entry || typeof entry !== "object" || Array.isArray(entry) || typeof entry.workItemId !== "number" ||
                                                typeof entry.organization !== "string" || typeof entry.project !== "string") throw new Error("Invalid shared feature reference.");
                                            return { organization: entry.organization, project: entry.project, workItemId: entry.workItemId };
                                        }));
                                    })}>
                                        Browse Team Features
                                    </button>
                                </div>
                                {teamFeatures.length > 0 && (
                                    <div className="factory-actions" style={{ marginTop: 8 }}>
                                        {teamFeatures.map((target) => (
                                            <button key={target.workItemId} className="ghost-chip-btn" disabled={busy}
                                                onClick={() => void act(async () => { await execute({ kind: "open_shared_feature", target }); })}>
                                                Open Feature #{target.workItemId}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </details>
                    </section>
                )}

                {formOpen ? <section className="card editor">
                    <div className="card-heading"><div><span className="eyebrow">FEATURE BRIEF</span><h2>{creating ? "Start with the problem." : "Refine the brief and success criteria."}</h2></div><span className="tag">DRAFT</span></div>
                    <p className="muted">Use your own words. You can save the idea first and ask Factory to help define success afterward. Saving does not run agents or approve anything.</p>
                    {editing && selected?.current.id !== baseRevision && <div role="alert" className="message error">This feature changed since you started editing. Your draft is preserved, but its old revision cannot be saved over the new one.</div>}
                    <form onSubmit={save}>
                        <label htmlFor="title">Feature title</label><input id="title" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="What are we making better?" required disabled={busy} />
                        <label htmlFor="description">What are you trying to achieve?</label><p className="field-help">Describe the problem, who it affects, and anything that must stay unchanged. No special format needed.</p>
                        <textarea id="description" rows={5} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="Tell us about the feature in plain language." disabled={busy} />
                        <label htmlFor="requirements">What would success look like? <span className="optional">optional for now</span></label><p className="field-help">A short checklist of outcomes you could check. Write one per line or use bullets; numbers and IDs are not needed. Factory can suggest a draft after you save.</p>
                        <textarea id="requirements" rows={5} value={draft.requirements} onChange={(event) => setDraft({ ...draft, requirements: event.target.value })} placeholder="For example: Existing clients keep the same response format." disabled={busy} />
                        <label htmlFor="unknowns">Scope decisions needed from you <span className="optional">optional</span></label><p className="field-help">Only choices needed to define what to build. Put engineering details for agents to solve in the design work below.</p>
                        <textarea id="unknowns" rows={3} value={draft.unknowns} onChange={(event) => setDraft({ ...draft, unknowns: event.target.value })} placeholder="Which decisions still need an answer?" disabled={busy} />
                        <label htmlFor="design-tasks">Work for the design agents <span className="optional">optional</span></label><p className="field-help">For example: trace IPC compatibility, choose a cache policy, or design context propagation. You do not need to solve these before approving scope.</p>
                        <textarea id="design-tasks" rows={3} value={draft.designTasks} onChange={(event) => setDraft({ ...draft, designTasks: event.target.value })} placeholder="Engineering obligations to resolve during design." disabled={busy} />
                        <div className="form-actions"><button type="button" className="secondary" onClick={() => navigate(selectedId)} disabled={busy}>Cancel</button><button type="submit" className="primary" disabled={busy}>{busy ? "Saving..." : creating ? "Create feature" : "Save new revision"}</button></div>
                    </form>
                </section> : analyticsOpen ? (
                    <WorkspaceAnalyticsView
                        features={features}
                        onSelectFeature={(id) => {
                            setAnalyticsOpen(false);
                            navigate(id);
                        }}
                        onClose={() => setAnalyticsOpen(false)}
                    />
                ) : selected ? <>
                    <FactoryPanel
                        key={selected.id}
                        feature={selected}
                        execute={execute}
                        onEdit={edit}
                        onReviewSuggestions={reviewSuggestions}
                        onCheckSetup={() => void act(async () => { setDoctor(await request("/api/doctor")); })}
                        onActiveStageChange={setActiveStageIdx}
                        externalFlightOpen={flightOpen}
                        onFlightOpenChange={setFlightOpen}
                        externalQaOpen={qaOpen}
                        onQaOpenChange={setQaOpen}
                        qaPinnedContext={qaPinnedContext}
                        onPinQaContext={setQaPinnedContext}
                        stage1View={
                            <section className="feature-detail">
                                <div className="studio-subnav" style={{ marginBottom: 14 }}>
                                    <button
                                        type="button"
                                        className={stage1SubTab === "brief" ? "studio-tab active" : "studio-tab"}
                                        onClick={() => setStage1SubTab("brief")}
                                    >
                                        Brief &amp; Success Criteria ({selected.current.requirements.length})
                                    </button>
                                    <button
                                        type="button"
                                        className={stage1SubTab === "diff" ? "studio-tab active" : "studio-tab"}
                                        onClick={() => setStage1SubTab("diff")}
                                    >
                                        Revision Diff ({selected.history.length})
                                    </button>
                                </div>

                                {stage1SubTab === "diff" ? (
                                    <RequirementsRevisionDiffExplorer
                                        history={selected.history}
                                        onAskAi={openAskCopilot}
                                    />
                                ) : (
                                    <>
                                        <div className="card-heading">
                                            <span className="eyebrow">FEATURE BRIEF · REVISION {selected.current.number}</span>
                                            <span className={selected.status === "ready_for_design" ? "tag approved" : "tag"}>{displayStatus(selected)}</span>
                                        </div>
                                        {selected.current.description && (
                                            <section className="feature-brief">
                                                <h3>Problem &amp; Intent</h3>
                                                <p>{selected.current.description}</p>
                                            </section>
                                        )}
                                        <div className="criteria-heading">
                                            <h3>Success Criteria ({selected.current.requirements.length})</h3>
                                            <button className="secondary" disabled={busy || intakeRunning || selected.factory.cancelled || selected.lifecycleStatus === "completed"} onClick={() => void act(async () => {
                                                const result = await execute({ kind: "start_workflow", featureId: selected.id, workflow: "intake" });
                                                if (result.outcome === "workflow_requested") setNotice("Draft requested. Progress and any errors appear above.");
                                            })}>{intakeRunning ? "Factory is drafting..." : "Suggest criteria with AI"}</button>
                                        </div>
                                        {intake && <IntakeSuggestion artifact={intake} disabled={busy} onReview={reviewSuggestions} />}
                                        {!selected.current.requirements.length && <p className="muted">No success criteria yet. Add your own or ask Factory for a draft.</p>}
                                        <div className="requirements-list">
                                            {selected.current.requirements.map((requirement) => (
                                                <div key={requirement.id} className="requirement">
                                                    <span>{requirement.id}</span>
                                                    <p>{requirement.text}</p>
                                                    <button
                                                        type="button"
                                                        className="ghost-chip-btn ask-ai-chip"
                                                        onClick={() => openAskCopilot({
                                                            kind: "requirement",
                                                            title: requirement.id,
                                                            subtitle: selected.current.title,
                                                            snippet: requirement.text,
                                                        })}
                                                    >
                                                        ✦ Ask AI
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                        {!!selected.current.designTasks?.length && <section className="design-obligations" aria-label="Design obligations">
                                            <h3>Design Obligations ({selected.current.designTasks.length})</h3>
                                            <ul>{selected.current.designTasks.map((task) => <li key={task}>{task}</li>)}</ul>
                                        </section>}
                                        {selected.current.blockingUnknowns.length > 0 && (
                                            <div className="unknowns">
                                                <h3>Blocking Open Questions</h3>
                                                <ul>{selected.current.blockingUnknowns.map((unknown, index) => <li key={index}>{unknown}</li>)}</ul>
                                            </div>
                                        )}
                                        {(() => {
                                            const stage0Approval = [...selected.approvals].reverse().find((a) => a.revisionId === selected.current.id) || selected.approvals.at(-1);
                                            if (!stage0Approval) return null;
                                            return (
                                                <div className="scope-approval-receipt" style={{ marginBottom: 14 }}>
                                                    <strong>✓ Stage 0 Scope Approval Recorded</strong>
                                                    <span>
                                                        Approved by {stage0Approval.actor?.ado?.displayName || stage0Approval.actor?.reference || "authenticated operator"} via {stage0Approval.actor?.channel || "studio"} on{" "}
                                                        {new Date(stage0Approval.createdAt).toLocaleString()}
                                                    </span>
                                                </div>
                                            );
                                        })()}
                                        <div className="revision-receipt">
                                            <span>REVISION</span><code>{selected.current.id.slice(0, 12)}…</code>
                                            <span>FEATURE</span><code>{selected.id.slice(0, 12)}…</code>
                                            {provenanceMap[selected.id]?.originWorkspace && (
                                                <>
                                                    <span>WORKTREE</span>
                                                    <code title={provenanceMap[selected.id]!.originWorkspace}>
                                                        {provenanceMap[selected.id]!.originWorktree}
                                                    </code>
                                                </>
                                            )}
                                        </div>
                                        <div className="form-actions">
                                            <button className="secondary" disabled={busy} onClick={edit}>Edit brief &amp; criteria</button>
                                            <button className="primary" disabled={busy || !selected.shared || selected.status === "ready_for_design" || !selected.current.requirements.length || selected.current.blockingUnknowns.length > 0} onClick={() => approve(selected)}>
                                                {!selected.shared ? "Publish before approving" : selected.status === "ready_for_design" ? "Approved for design" : "Approve for design"}
                                            </button>
                                        </div>
                                    </>
                                )}
                            </section>
                        }
                    />

                    <section className="card activity" aria-label="Feature activity">
                        <details open={activeStageIdx === 0}>
                            <summary className="activity-summary-header">
                                <div>
                                    <span className="eyebrow">SHARED ACTIVITY TIMELINE</span>
                                    <h3>Chat and UI Audit History ({selected.activity.length})</h3>
                                </div>
                                <span className="muted">Toggle timeline ▾</span>
                            </summary>
                            <p className="field-help" style={{ marginTop: 10 }}>Saved scope changes, approvals, runs and failures across Copilot chat and Web Studio.</p>
                            <ol className="timeline">
                                {selected.activity.slice(0, 20).map((event) => (
                                    <li key={event.id}>
                                        <strong>{event.title}</strong>
                                        <span>{event.source} · {new Date(event.createdAt).toLocaleString()}</span>
                                        {event.detail && <details><summary>What changed</summary><p>{event.detail}</p></details>}
                                    </li>
                                ))}
                            </ol>
                            {selected.activity.length > 20 && (
                                <details>
                                    <summary>Earlier activity ({selected.activity.length - 20})</summary>
                                    <ol className="timeline">
                                        {selected.activity.slice(20).map((event) => (
                                            <li key={event.id}>
                                                <strong>{event.title}</strong>
                                                <span>{event.source} · {new Date(event.createdAt).toLocaleString()}</span>
                                                <p>{event.detail}</p>
                                            </li>
                                        ))}
                                    </ol>
                                </details>
                            )}
                        </details>
                    </section>
                </> : <section className="card empty-state"><span className="empty-mark"><FactoryMark /></span><span className="eyebrow">{busy ? "CONNECTING" : "A GOOD START IS A CLEAR SCOPE"}</span><h2>{busy ? "Opening your local registry..." : "Give your next idea a home."}</h2><p>Create a feature here, or capture its requirements in Copilot chat.<br />The same record will be waiting in both places.</p><button className="primary" onClick={newFeature} disabled={busy}>Create your first feature</button></section>}
                <footer className="page-footer"><span>SOFTWARE FACTORY</span><span>Explicit decisions. Shared context. No silent automation.</span></footer>
            </div>
        </main>
        {confirmation && <Confirmation request={confirmation} finish={finishConfirmation} theme={theme} />}
    </div>;
}
