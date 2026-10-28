// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { parseRequirements, parseSuccessCriteria, type Command, type Feature, type Project, type Requirement, type Requirements } from "../../core/contracts.ts";
import { parseIntakeProposal, type Artifact, type FactoryFeature, type Json } from "../../core/lifecycle-contracts.ts";
import { FactoryPanel, FactorySettings } from "./FactoryPanel";
import { Confirmation, type ConfirmationRequest } from "./Confirmation";
import { nextStep } from "../../core/progress.ts";
import type { runtimeReport } from "../../core/doctor.ts";
import type { DecisionPresentation } from "../../core/contracts.ts";
import { request } from "./api";

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
    const [features, setFeatures] = useState<FactoryFeature[]>([]);
    const [settings, setSettings] = useState(false);
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
    const refreshing = useRef(false);
    const refreshSequence = useRef(0);
    const csrf = useRef("");
    const creationId = useRef(crypto.randomUUID());
    const selected = features.find((feature) => feature.id === selectedId);

    async function refresh() {
        const sequence = ++refreshSequence.current;
        const data = await request<{ project: Project; features: FactoryFeature[] }>("/api/features");
        if (sequence !== refreshSequence.current) return data.features;
        setProject(data.project);
        setFeatures((previous) => JSON.stringify(previous) === JSON.stringify(data.features) ? previous : data.features);
        setSelectedId((previous) => previous || data.features[0]?.id);
        setSyncedAt(Date.now());
        setSyncError("");
        return data.features;
    }

    useEffect(() => {
        let cancelled = false;
        void request<{ csrf: string; project: Project }>("/api/session").then(async (session) => {
            if (cancelled) return;
            csrf.current = session.csrf;
            const items = await refresh();
            if (!cancelled) setSelectedId(items[0]?.id);
        }).catch((failure: unknown) => {
            if (!cancelled) setError(failure instanceof Error ? failure.message : "Could not connect to the local registry.");
        }).finally(() => { if (!cancelled) setBusy(false); });
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        if (!project) return;
        const sync = async () => {
            if (refreshing.current) return;
            refreshing.current = true;
            try { await refresh(); }
            catch (failure) { setSyncError(failure instanceof Error ? failure.message : "Shared state could not be refreshed."); }
            finally { refreshing.current = false; }
        };
        const timer = setInterval(() => { void sync(); }, 2000);
        const focus = () => { void sync(); };
        window.addEventListener("focus", focus);
        return () => { clearInterval(timer); window.removeEventListener("focus", focus); };
    }, [project?.id]);

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
        setSelectedId(id);
        setError("");
        setNotice("");
    }

    function newFeature() {
        if ((editing || creating) && !window.confirm("Discard your unsaved feature edits?")) return;
        creationId.current = crypto.randomUUID();
        setDraft(emptyDraft());
        setCreating(true);
        setEditing(false);
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
        return result;
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

    const workspace = project?.workspace.split(/[\\/]/).filter(Boolean).pop() || "Local workspace";
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

    return <div className="app-shell">
        <aside className="sidebar">
            <a className="brand" href="/" aria-label="Software Factory home"><span className="brand-mark"><FactoryMark /></span><span>Software<br /><strong>Factory</strong></span></a>
            <div className="workspace"><span className="eyebrow">WORKSPACE</span><strong>{workspace}</strong><span>Local, private, and under your control.</span></div>
            <div className="sidebar-heading"><span>FEATURES</span><span>{features.length.toString().padStart(2, "0")}</span></div>
            <button className="new-feature" onClick={newFeature} disabled={busy}><span aria-hidden="true">+</span> New feature</button>
            <nav aria-label="Features" className="feature-nav">
                {features.map((feature) => <button key={feature.id} className={selectedId === feature.id && !creating ? "feature-link selected" : "feature-link"} onClick={() => navigate(feature.id)} disabled={busy}>
                    <span className={feature.status === "ready_for_design" ? "status-dot ready" : "status-dot"} />
                    <span><strong>{feature.current.title}</strong><small>{nextStep(feature, []).title}</small></span>
                </button>)}
                {!busy && features.length === 0 && <p className="sidebar-empty">Your next idea starts here.</p>}
            </nav>
            <div className="chat-note"><span className="eyebrow">CHAT + DASHBOARD</span><p>Saved chat actions appear here automatically. Free-form chat messages are not copied.</p></div>
            <div className="sidebar-footer"><span className="status-dot ready" /> Native Dynamic Workflows <span>0.1</span></div>
        </aside>

        <main>
            <header className="topbar"><span>WORKSPACE <span className="slash">/</span> {workspace} <span className="slash">/</span> Features</span><div>
                <button className="text-button" onClick={() => setSettings(!settings)}>Connections</button>
                <button className="text-button" disabled={busy} onClick={() => void act(async () => { setDoctor(await request("/api/doctor")); })}>Check setup</button>
                <button className="text-button" disabled={busy} onClick={() => void act(async () => { await refresh(); setNotice("Registry refreshed. Unsaved edits are preserved."); })}>Refresh</button>
                <span className={`sync-badge ${syncError ? "offline" : ""}`} role="status">{syncError ? "Updates disconnected" : syncedAt ? `Synced ${new Date(syncedAt).toLocaleTimeString()}` : "Connecting..."}</span>
            </div></header>
            <div className="content">
                <section className="workspace-title"><div><span className="eyebrow">SOFTWARE FACTORY</span><h1>Your feature workspace</h1><p>Pick a feature. Review what changed. Take the next step.</p></div><span>{features.length} {features.length === 1 ? "feature" : "features"}</span></section>
                {syncError && <div role="alert" className="message error">Live updates are disconnected: {syncError} Last saved content is shown; unsaved edits are preserved.</div>}
                {settings && <FactorySettings execute={execute} />}
                {error && <div role="alert" className="message error">{error}</div>}
                {notice && <div role="status" className="message notice">{notice}</div>}
                {doctor !== undefined && <section className="card preflight"><div className="card-heading"><h2>{doctor.runtime.ready ? "Local runtime ready" : "Local runtime needs repair"}</h2><button className="text-button" onClick={() => setDoctor(undefined)}>Close</button></div>
                    <p>{doctor.runtime.message}</p>{doctor.runtime.recoveryCommand && <><p>Run this from the repository root, then explicitly retry. No AI work runs during repair.</p><pre>{doctor.runtime.recoveryCommand}</pre></>}
                    <details><summary>Full setup diagnostics</summary><pre>{JSON.stringify(doctor, null, 2)}</pre></details></section>}

                {!formOpen && selected && <FactoryPanel key={selected.id} feature={selected} execute={execute} onEdit={edit} onReviewSuggestions={reviewSuggestions}
                    onCheckSetup={() => void act(async () => { setDoctor(await request("/api/doctor")); })} />}

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
                </section> : selected ? <div className="detail-grid">
                    <section className="card feature-detail">
                        <div className="card-heading"><span className="eyebrow">FEATURE BRIEF / REVISION {selected.current.number}</span><span className={selected.status === "ready_for_design" ? "tag approved" : "tag"}>{displayStatus(selected)}</span></div>
                        <h2>{selected.current.title}</h2><p className="muted">The saved brief and criteria. Changes from chat appear automatically.</p>
                        {selected.current.description && <section className="feature-brief"><h3>The idea</h3><p>{selected.current.description}</p></section>}
                        <div className="criteria-heading"><h3>What does done look like?</h3><button className="secondary" disabled={busy || intakeRunning || selected.factory.cancelled || selected.lifecycleStatus === "completed"} onClick={() => void act(async () => {
                            const result = await execute({ kind: "start_workflow", featureId: selected.id, workflow: "intake" });
                            if (result.outcome === "workflow_requested") setNotice("Draft requested. Progress and any errors appear above.");
                        })}>{intakeRunning ? "Factory is drafting..." : "Ask Factory for another draft"}</button></div>
                        <p className="field-help">Optional AI help uses Copilot usage and asks for confirmation first. It never approves or replaces your criteria automatically.</p>
                        {intake && <IntakeSuggestion artifact={intake} disabled={busy} onReview={reviewSuggestions} />}
                        {!selected.current.requirements.length && <p className="muted">No success criteria yet. Add your own or ask Factory for a draft. You can keep refining the idea without starting design.</p>}
                        <div className="requirements-list">{selected.current.requirements.map((requirement) => <div key={requirement.id} className="requirement"><span>{requirement.id}</span><p>{requirement.text}</p></div>)}</div>
                        {!!selected.current.designTasks?.length && <section className="design-obligations" aria-label="Design obligations"><h3>Work for the design agents</h3>
                            <ul>{selected.current.designTasks.map((task) => <li key={task}>{task}</li>)}</ul>
                            <p className="field-help">Approval authorizes design of this scope; it does not require these engineering tasks to be solved already. They must be addressed in the design and independent review.</p></section>}
                        <div className="unknowns"><h3>Open questions saved in this brief</h3>{selected.current.blockingUnknowns.length ? <ul>{selected.current.blockingUnknowns.map((unknown, index) => <li key={index}>{unknown}</li>)}</ul> : <p><span className="checkmark" aria-hidden="true">+</span> {selected.currentProgress?.question ? "No additional questions saved in the brief. Chat questions are tracked separately in shared activity." : "No open questions saved in the brief."}</p>}</div>
                        <div className="revision-receipt"><span>REVISION ID</span><code>{selected.current.id}</code><span>FEATURE ID</span><code>{selected.id}</code></div>
                        <div className="form-actions"><button className="secondary" disabled={busy} onClick={edit}>Edit brief and criteria</button>
                            <button className="primary" disabled={busy || selected.status === "ready_for_design" || !selected.current.requirements.length || selected.current.blockingUnknowns.length > 0} onClick={() => approve(selected)}>{selected.status === "ready_for_design" ? "Approved for design" : "Approve for design"}</button>
                        </div>
                        <p className="approval-help">Approval applies only to this revision. It does not start a workflow or authorize remote changes.</p>
                    </section>
                    <aside className="detail-aside"><section className="card activity" aria-label="Feature activity"><span className="eyebrow">SHARED ACTIVITY</span><h3>Chat and UI, one history</h3>
                        <p className="field-help">Saved scope changes, approvals, runs and failures. Not a chat transcript.</p>
                        <ol className="timeline">{selected.activity.slice(0, 20).map((event) => <li key={event.id}><strong>{event.title}</strong>
                            <span>{event.source} / {new Date(event.createdAt).toLocaleString()}</span>
                            {event.detail && <details><summary>What changed</summary><p>{event.detail}</p></details>}</li>)}</ol>
                        {selected.activity.length > 20 && <details><summary>Earlier activity</summary><ol className="timeline">{selected.activity.slice(20).map((event) =>
                            <li key={event.id}><strong>{event.title}</strong><span>{event.source} / {new Date(event.createdAt).toLocaleString()}</span><p>{event.detail}</p></li>)}</ol></details>}
                    </section>
                    </aside>
                </div> : <section className="card empty-state"><span className="empty-mark"><FactoryMark /></span><span className="eyebrow">{busy ? "CONNECTING" : "A GOOD START IS A CLEAR SCOPE"}</span><h2>{busy ? "Opening your local registry..." : "Give your next idea a home."}</h2><p>Create a feature here, or capture its requirements in Copilot chat.<br />The same record will be waiting in both places.</p><button className="primary" onClick={newFeature} disabled={busy}>Create your first feature</button></section>}
                <footer className="page-footer"><span>SOFTWARE FACTORY</span><span>Explicit decisions. Shared context. No silent automation.</span></footer>
            </div>
        </main>
        {confirmation && <Confirmation request={confirmation} finish={finishConfirmation} />}
    </div>;
}
