// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useRef } from "react";
import type { Json } from "../../core/lifecycle-contracts.ts";
import type { DecisionPresentation } from "../../core/contracts.ts";

export interface ConfirmationRequest {
    command: Record<string, unknown>; scope: Json; consentKey: string; decision: DecisionPresentation; resolve: (approved: boolean) => void;
}

function record(value: Json | undefined): Record<string, Json> {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function ScopeSummary({ scope }: { scope: Json }) {
    const outer = record(scope);
    const gate = record(outer.gate);
    const details = gate.scope || scope;
    const value = record(details);
    const configuration = record(value.configuration || (value.repositories ? details : undefined));
    const boards = record(configuration.boards);
    const repositories = record(configuration.repositories);
    const entries = Array.isArray(value.eligible) ? value.eligible : Array.isArray(value.items) ? value.items : [];
    return <>
        {typeof value.effect === "string" && <p>{value.effect}</p>}
        {typeof value.body === "string" && <section className="scope-summary"><h3>Feedback to post</h3><p className="feedback-preview">{value.body}</p></section>}
        {Object.keys(boards).length > 0 && <section className="scope-summary"><h3>Azure DevOps destination</h3><dl>
            {Object.entries({ Organization: boards.organization, Project: boards.project, "Work item type": boards.workItemType, Area: boards.areaPath,
                Iteration: boards.iterationPath, Assignee: boards.assignee, Parent: boards.parentId }).map(([label, entry]) =>
                    entry !== null && entry !== undefined && entry !== "" ? <div key={label}><dt>{label}</dt><dd>{String(entry)}</dd></div> : null)}
        </dl></section>}
        {Object.keys(repositories).length > 0 && <section className="scope-summary"><h3>Repositories and accounts</h3><ul>{Object.entries(repositories).map(([alias, raw]) => {
            const repo = record(raw);
            return <li key={alias}><strong>{String(repo.hostname)}/{String(repo.slug)}</strong><br />
                Base: {String(repo.baseBranch)} / Account: {repo.account ? String(repo.account) : "Configured Azure identity"}</li>;
        })}</ul></section>}
        {entries.length > 0 && <section className="scope-summary"><h3>Work in this scope</h3><ul>{entries.map((raw, index) => {
            const item = record(raw);
            return <li key={index}><strong>{String(item.id)}: {String(item.title)}</strong> / {String(item.repository)}
                {Array.isArray(item.acceptanceCriteria) && <ul>{item.acceptanceCriteria.map((criterion, criterionIndex) => <li key={criterionIndex}>{String(criterion)}</li>)}</ul>}</li>;
        })}</ul></section>}
    </>;
}

export function Confirmation({ request, finish }: { request: ConfirmationRequest; finish: (approved: boolean) => void }) {
    const dialog = useRef<HTMLDialogElement>(null);
    useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
    const decision = request.decision;
    const scope = request.scope && typeof request.scope === "object" && !Array.isArray(request.scope) ? request.scope : {};
    const brief = scope.requirements || scope.brief;
    return <dialog ref={dialog} className="confirmation" onCancel={(event) => { event.preventDefault(); finish(false); }} aria-labelledby="confirmation-title">
        <form method="dialog" onSubmit={(event) => { event.preventDefault(); finish(true); }}>
            <span className="eyebrow">YOUR DECISION</span><h2 id="confirmation-title">{decision.title}?</h2>
            <p><strong>{decision.subject}</strong> / {decision.context}</p>
            <p>{decision.effect}</p><p className="decision-boundary">{decision.boundaries}</p>
            {decision.facts.length > 0 && <ul>{decision.facts.map((fact) => <li key={fact}>{fact}</li>)}</ul>}
            {brief && typeof brief === "object" && !Array.isArray(brief) && <details><summary>Review brief and success criteria</summary><section className="confirmation-brief">
                <h3>{typeof brief.title === "string" ? brief.title : "Saved scope"}</h3>
                {typeof brief.description === "string" && <p>{brief.description}</p>}
                {Array.isArray(brief.requirements) && <ul>{brief.requirements.map((entry, index) =>
                    entry && typeof entry === "object" && !Array.isArray(entry) && typeof entry.text === "string" ? <li key={index}>{entry.text}</li> : null)}</ul>}
                {Array.isArray(brief.designTasks) && brief.designTasks.length > 0 && <><h3>Work for the design agents</h3><ul>{brief.designTasks.map((task, index) => <li key={index}>{String(task)}</li>)}</ul></>}
            </section></details>}
            <ScopeSummary scope={request.scope} />
            <details><summary>Technical scope and approval reference</summary><pre>{JSON.stringify({
                scope: request.scope, scopeHash: request.consentKey,
            }, null, 2)}</pre></details>
            <div className="form-actions"><button type="button" className="secondary" autoFocus onClick={() => finish(false)}>Not now</button>
                <button type="submit" className="primary">{decision.approveLabel}</button></div>
        </form>
    </dialog>;
}
