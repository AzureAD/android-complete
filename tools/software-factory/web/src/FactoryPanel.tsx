// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useRef, useState } from "react";
import type { ConfigRevision, FactoryFeature, GatePreview, Json } from "../../core/lifecycle-contracts.ts";
import { request } from "./api";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Artifact } from "../../core/lifecycle-contracts.ts";
import { nextStep, stageLabels, type AvailableGate } from "../../core/progress.ts";
import { ScopeSummary } from "./Confirmation";

type Execute = (command: Record<string, unknown>) => Promise<Record<string, Json>>;
type GateResult = AvailableGate;

function ArtifactDocument({ artifact }: { artifact: Artifact }) {
    const body = artifact.content;
    if (!body || typeof body !== "object" || Array.isArray(body)) return <pre>{JSON.stringify(body, null, 2)}</pre>;
    if (artifact.kind === "design" && body.design && typeof body.design === "object" && !Array.isArray(body.design)) {
        const sections = body.design.sections;
        if (sections && typeof sections === "object" && !Array.isArray(sections)) return <article className="design-document">
            {Object.entries(sections).map(([name, content]) => <section key={name}>
                <h3>{name.replace(/([A-Z])/g, " $1")}</h3>
                <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>[Image: {alt || "not loaded"}]</span> }}>
                    {typeof content === "string" ? content : JSON.stringify(content)}
                </Markdown>
            </section>)}
            <details><summary>Full evidence, traceability, and review packet</summary><pre>{JSON.stringify(body, null, 2)}</pre></details>
        </article>;
    }
    if (artifact.kind === "plan" && Array.isArray(body.items)) return <div className="plan-document">
        <p>{typeof body.summary === "string" ? body.summary : ""}</p>
        <div className="plan-table"><table><thead><tr><th>PBI</th><th>Repository</th><th>Dependencies</th><th>Acceptance criteria</th></tr></thead><tbody>
            {body.items.map((raw, index) => {
                if (!raw || typeof raw !== "object" || Array.isArray(raw)) return <tr key={index}><td>Invalid item</td></tr>;
                return <tr key={String(raw.id)}><td><strong>{String(raw.id)}</strong><br />{String(raw.title)}</td><td>{String(raw.repository)}</td>
                    <td>{Array.isArray(raw.dependsOn) && raw.dependsOn.length ? raw.dependsOn.map(String).join(", ") : "Independent"}</td>
                    <td><ul>{Array.isArray(raw.acceptanceCriteria) && raw.acceptanceCriteria.map((criterion, criterionIndex) => <li key={criterionIndex}>{String(criterion)}</li>)}</ul></td></tr>;
            })}
        </tbody></table></div>
        <section aria-label="Dependency graph" className="dependency-graph"><h3>Dependency graph</h3>
            {body.items.map((raw, index) => {
                if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
                return <p key={index}><code>{Array.isArray(raw.dependsOn) && raw.dependsOn.length ? `${raw.dependsOn.map(String).join(" + ")} -> ` : "Start -> "}{String(raw.id)}</code></p>;
            })}
        </section>
        <details><summary>Complete PBI specifications, files, tests, and traceability</summary><pre>{JSON.stringify(body, null, 2)}</pre></details>
    </div>;
    return <pre>{JSON.stringify(body, null, 2)}</pre>;
}

export function FactoryPanel({ feature, execute, onEdit, onReviewSuggestions, onCheckSetup }: {
    feature: FactoryFeature; execute: Execute; onEdit: () => void; onReviewSuggestions: () => void; onCheckSetup: () => void;
}) {
    const [gates, setGates] = useState<GateResult[]>([]);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [gatesLoaded, setGatesLoaded] = useState(false);
    const advanced = useRef<HTMLDetailsElement>(null);
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
    const start = (workflow: string) => {
        try { return run({ kind: "start_workflow", featureId: feature.id, workflow, limits: JSON.parse(limits) }); }
        catch { setError("Execution limit overrides must be valid JSON."); return Promise.resolve(); }
    };
    const progress = nextStep(feature, gates);
    const currentGate = progress.gate;
    const showRuns = () => { setTab("runs"); if (advanced.current) { advanced.current.open = true; advanced.current.scrollIntoView({ block: "nearest", behavior: "smooth" }); } };
    const actNext = () => {
        const action = progress.action;
        if (action?.target === "edit") onEdit();
        else if (action?.target === "suggestions") onReviewSuggestions();
        else if (action?.target === "runs") showRuns();
        else if (action?.command) void run(action.command);
    };
    return <section className="card factory-panel" aria-label="Workflow lifecycle">
        <div className="phase-track" aria-label="Feature progress">{stageLabels.map((label, index) => <div key={label} aria-current={index === progress.stage ? "step" : undefined}
            className={`phase ${index === progress.stage ? "current" : index < progress.stage ? "past" : ""}`}><span>{index + 1}</span><strong>{label}</strong>
            <small>{index === progress.stage ? "CURRENT STEP" : index < progress.stage ? "EARLIER STEP" : "LATER"}</small></div>)}</div>
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
                {progress.stage === 0 && progress.action?.target !== "edit" && !progress.run && !progress.report && <button className="secondary" disabled={busy} onClick={onEdit}>Write or edit criteria myself</button>}
                {progress.failed && <button className="secondary" disabled={busy} onClick={onCheckSetup}>Check setup</button>}
                {progress.failed && progress.run?.runtimeId === null && <span className="field-help">No native run ID was recorded. Retrying creates a new, explicitly confirmed attempt; it never replays remote writes.</span>}
            </div>
        </div>
        {error && <div role="alert" className="message error">{error}</div>}
        {result?.outcome === "not_approved" && <p className="message notice" role="status">Not started. You chose not to approve this action.</p>}
        {currentGate && <section className="current-review" aria-label={`Review ${currentGate.stage}`}>
            {["design", "plan"].includes(currentGate.stage) && latest(currentGate.stage as "design" | "plan") &&
                <ArtifactDocument artifact={latest(currentGate.stage as "design" | "plan")!} />}
            {currentGate.scope && <ScopeSummary scope={currentGate.scope} />}
            <details><summary>Exact approval scope and targets</summary><pre>{JSON.stringify({ scope: currentGate.scope, context: currentGate.context || null }, null, 2)}</pre></details>
            {!currentGate.ready && <p className="message error">{currentGate.blockers?.join(" ")}</p>}
            <div className="factory-actions"><button className="primary" disabled={busy || !currentGate.ready} onClick={() => void run({
                kind: "approve_stage", featureId: feature.id, stage: currentGate.stage, expectedScopeHash: currentGate.scopeHash, approved: true,
            })}>Approve {currentGate.stage === "complete" ? "completion" : currentGate.stage}</button>
                <button className="secondary" disabled={busy} onClick={() => void run({
                    kind: "approve_stage", featureId: feature.id, stage: currentGate.stage, expectedScopeHash: currentGate.scopeHash, approved: false,
                })}>Reject this scope</button></div>
        </section>}
        <details ref={advanced} className="advanced-controls"><summary>Artifacts, run controls and advanced recovery</summary>
        <p className="muted">Saved artifacts and technical controls. Use the guided next step above for ordinary progress.</p>
        <div className="factory-actions">
            <button className="secondary" disabled={busy || feature.status !== "ready_for_design"} onClick={() => void start("design")}>Start a design run</button>
            <button className="secondary" disabled={busy || feature.status !== "ready_for_design"} onClick={() => void start("plan")}>Start a planning run</button>
            <button className="secondary" disabled={busy || !latest("plan")} onClick={() => void start("implement")}>Start a coding &amp; review run</button>
            <button className="secondary" disabled={busy || !latest("plan")} onClick={() => void start("monitor")}>Start a monitoring run</button>
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
        <div className="factory-tabs" role="group" aria-label="Lifecycle details">{(["artifacts", "runs", "operations"] as const).map((value) =>
            <button key={value} className={tab === value ? "secondary active" : "secondary"} onClick={() => setTab(value)}>{value}</button>
        )}</div>
        {tab === "artifacts" && <>
            <div className="factory-artifacts">{feature.factory.artifacts.length ? [...feature.factory.artifacts].reverse().map((artifact) => <details key={artifact.id}>
                <summary>{artifact.kind} / {new Date(artifact.createdAt).toLocaleString()} / {artifact.id}</summary>
                <ArtifactDocument artifact={artifact} />
            </details>) : <p className="muted">No generated artifacts yet. Ask Factory for draft success criteria above, or approve your own criteria before starting design.</p>}</div>
            <label htmlFor={`feedback-${feature.id}`}>Revision feedback</label>
            <textarea id={`feedback-${feature.id}`} value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={3} placeholder="What should change? Approved requirements cannot be rewritten by an agent." />
            <div className="factory-actions">{(["design", "plan"] as const).map((stage) => <button key={stage} className="secondary" disabled={busy || !feedback.trim() || !latest(stage)} onClick={() => void run({
                kind: "request_revision", featureId: feature.id, stage, expectedArtifactId: latest(stage)?.id, feedback,
            })}>Request {stage} revision</button>)}</div>
            <details><summary>Accept an explicitly reviewed validation gap</summary>
                <p className="muted">This records a reasoned human waiver for one criterion in the current report. It cannot waive unmerged PRs, incomplete work items, failed CI, or provider errors.</p>
                <label htmlFor="waiver-item">Work item key</label><input id="waiver-item" value={itemId} onChange={(event) => setItemId(event.target.value)} />
                <label htmlFor="waiver-criterion">Exact criterion text</label><input id="waiver-criterion" value={criterion} onChange={(event) => setCriterion(event.target.value)} />
                <label htmlFor="waiver-reason">Reason for accepting the gap</label><textarea id="waiver-reason" value={waiverReason} onChange={(event) => setWaiverReason(event.target.value)} />
                <button className="secondary" disabled={busy || !latest("monitor") || !itemId || !criterion || !waiverReason} onClick={() => void run({
                    kind: "accept_validation_gap", featureId: feature.id, monitorId: latest("monitor")?.id, itemId, criterion, reason: waiverReason,
                })}>Review &amp; accept this gap</button>
            </details>
        </>}
        {tab === "runs" && <>
            <label htmlFor="resume-limits">Explicit execution/resume-limit overrides (JSON, optional)</label><input id="resume-limits" value={limits} onChange={(event) => setLimits(event.target.value)} />
            <p className="field-help">New runs have no AI-credit cap unless you set one. Copilot usage is still metered. To remove an existing run's credit cap, use {"{\"maxAiCredits\":null}"} in a confirmed resume.</p>
            {feature.factory.executions.length ? [...feature.factory.executions].reverse().map((execution) => <section className="run-card" key={execution.id}>
                <div className="card-heading"><h3>{execution.kind} / {execution.status}</h3><span>{execution.phase}</span></div>
                <dl><dt>Execution</dt><dd>{execution.id}</dd><dt>Owning session</dt><dd>{execution.sessionId}</dd><dt>Native run</dt><dd>{execution.runtimeId || "Awaiting admission"}</dd></dl>
                <p className="field-help">{execution.sessionKind === "chat"
                    ? "Runs in its original Copilot chat. Dashboard controls go to that resident owner; reconnect the original chat if it is offline."
                    : "Runs in a Factory-owned SDK session. App discovery/attachment of this session is not yet verified."}</p>
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
                    <p className="field-help">Native workflow usage only, in the runtime's reported units; remote coding-task usage is separate. Refresh for current measurements. Credit limits are soft, not exact spending guarantees.</p>
                </> : <p className="muted">Native usage has not been observed. Refresh this run; missing measurements do not mean zero usage.</p>}
                {execution.observationError && <p className="message error">{execution.observationError}</p>}
                {execution.error && <p className="message error">{execution.error}</p>}
                {feature.factory.controls.filter((control) => control.executionId === execution.id && control.error).map((control) =>
                    <p className="message error" key={control.id}>{control.action}: {control.error}</p>)}
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
            <details><summary>Associate a canonical PR or post reviewed feedback</summary>
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
        </section>) : <p className="muted">No remote operations. Creating or dispatching work requires its own approved scope.</p>}</div>}
        {result && <details><summary>Technical command result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
        <details><summary>Complete approval and lifecycle audit</summary><pre>{JSON.stringify({
            decisions: feature.factory.decisions, events: feature.factory.events,
            validationWaivers: feature.factory.waivers, canonicalPrBindings: feature.factory.prBindings,
        }, null, 2)}</pre></details>
        </details>
    </section>;
}

const configurationTemplate = {
    repositories: {
        example: { provider: "github", hostname: "github.com", slug: "OWNER/REPOSITORY", baseBranch: "CONFIGURED_BASE", account: "YOUR_ACCOUNT", localPath: "." },
    },
    boards: { organization: "YOUR_ORG", project: "YOUR_PROJECT", workItemType: "Product Backlog Item", areaPath: "CONFIRM_AREA", iterationPath: "CONFIRM_ITERATION", assignee: "", parentId: null },
    delivery: { mode: "local", maxReviewRounds: 3 },
};

export function FactorySettings({ execute }: { execute: Execute }) {
    const [configuration, setConfiguration] = useState(JSON.stringify(configurationTemplate, null, 2));
    const [file, setFile] = useState("");
    const [mappings, setMappings] = useState("[]");
    const [importHash, setImportHash] = useState("");
    const [backupPath, setBackupPath] = useState("");
    const [backupHash, setBackupHash] = useState("");
    const [hostsStopped, setHostsStopped] = useState(false);
    const [result, setResult] = useState<Record<string, Json>>();
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        void request<{ configuration: ConfigRevision | null }>("/api/configuration").then((value) => {
            if (value.configuration) setConfiguration(JSON.stringify(value.configuration.value, null, 2));
        }).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : "Configuration could not be loaded."));
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
    return <section className="card factory-panel" aria-label="Connections and migration">
        <span className="eyebrow">SHARED CHAT / UI CONFIGURATION</span><h2>Connect the right targets.</h2>
        <p className="muted">Provider and hostname are separate. GitHub accounts are scoped to each request; credentials are never stored here. ADO board and repository projects may differ.</p>
        <label htmlFor="configuration-json">Repository and ADO settings</label><textarea id="configuration-json" rows={17} value={configuration} onChange={(event) => setConfiguration(event.target.value)} spellCheck={false} />
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
