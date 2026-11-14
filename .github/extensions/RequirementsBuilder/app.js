// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

"use strict";
const token = new URL(location.href).searchParams.get("token");
const byId = (id) => document.getElementById(id);
let state;
let selected;
let step = 0;
let timer;
let saving = false;
let busy = false;
let failed = false;
const pending = new Map();

function error(message) {
    byId("error").textContent = message;
    byId("error").hidden = false;
}

async function api(path, body) {
    const response = await fetch(path, {
        method: body ? "POST" : "GET",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Requirements request failed.");
    return result;
}

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function button(text, action, className) {
    const node = element("button", text, className);
    node.type = "button";
    node.addEventListener("click", () => perform(action));
    return node;
}

async function perform(action) {
    if (busy) return;
    busy = true;
    try {
        await flush();
        if (failed) throw new Error("Save did not complete. Your edits remain in the form. Resolve the error before proceeding.");
        byId("error").hidden = true;
        await action();
    } catch (exception) { error(exception.message); }
    finally { busy = false; }
}

function schedule(key, change) {
    pending.set(key, change);
    byId("saved").textContent = "Unsaved changes...";
    clearTimeout(timer);
    timer = setTimeout(() => flush().catch((exception) => error(exception.message)), 700);
}

async function flush() {
    clearTimeout(timer);
    while (saving) await new Promise((resolve) => setTimeout(resolve, 20));
    saving = true;
    try {
        for (const [key, change] of pending) {
            state = await api("/update", { revision: state.draft.revision, change });
            if (pending.get(key) === change) pending.delete(key);
        }
        failed = false;
        overview();
    } catch (exception) {
        failed = true;
        byId("saved").textContent = "Not saved - edits retained in form";
        throw exception;
    } finally { saving = false; }
}

function autoNA(field) {
    const kind = state.draft.metadata.uiKind;
    if (kind === "non-ui" && /3\.[234]/.test(field.heading)) return "Non-UI feature: retain these sections as N/A; describe the caller journey in scenarios and acceptance criteria.";
    if (["non-ui", "other-ui"].includes(kind) && (field.heading.startsWith("3.6") || field.heading === "Acceptance ownership and evidence")) return "No new or changed Authenticator UI is in scope. The section remains in the export with this reason.";
    return null;
}

function overview() {
    byId("draft-name").textContent = `Draft: ${state.draft.id}`;
    byId("saved").textContent = pending.size ? "Unsaved changes..." : `Saved in this session / revision ${state.draft.revision}`;
    byId("progress").textContent = `${state.summary.confirmed} answered fields / ${state.summary.unknown.length} fields with gaps / ${state.summary.proposals} AI proposals to review`;
    byId("gap-count").textContent = `Review ${state.summary.unknown.length} unresolved fields`;
    byId("preview").textContent = state.markdown;
    const gaps = byId("gaps");
    gaps.replaceChildren(...state.summary.unknown.map((gap) => button(gap.label, () => { step = gap.step; selected = gap.id; render(); byId("editor").focus(); })));
    byId("download").disabled = state.summary.proposals > 0 || state.draft.request?.status === "pending";
    const assistance = byId("editor").querySelector(".assistance");
    if (assistance) renderAssistance(assistance, state.fields.find((field) => field.id === selected));
}

function render() {
    const available = state.fields.filter((field) => field.step === step);
    if (!available.some((field) => field.id === selected)) selected = available[0]?.id;
    byId("intro").hidden = step !== 0;
    for (const input of document.querySelectorAll("[data-meta]")) input.value = state.draft.metadata[input.dataset.meta];
    byId("steps").replaceChildren(...state.steps.map((name, index) => {
        const node = button(`${index + 1}. ${name}`, () => { step = index; selected = null; render(); });
        if (index === step) node.setAttribute("aria-current", "step");
        return node;
    }));
    const questions = byId("questions");
    questions.replaceChildren(...available.map((field) => {
        const option = element("option", `${field.label}${autoNA(field) ? " (N/A)" : ""}`);
        option.value = field.id;
        return option;
    }));
    questions.value = selected;
    byId("position").textContent = `${available.findIndex((field) => field.id === selected) + 1} / ${available.length}`;
    renderEditor();
    overview();
}

function renderEditor() {
    const field = state.fields.find((item) => item.id === selected);
    const editor = byId("editor");
    editor.replaceChildren(element("div", `${state.steps[step]} / HUMAN REQUIREMENT`, "eyebrow"), element("h2", field.label));
    const na = autoNA(field);
    if (na) { editor.append(element("p", na)); return; }
    editor.append(element("p", field.question));
    const example = element("details", "", "example");
    example.append(element("summary", "Show an illustrative example"), element("p", field.example), element("p", "Example only - never added to your requirements automatically."));
    editor.append(example);
    const answer = state.draft.answers[field.id];
    let value = structuredClone(answer?.value ?? (field.type === "table" ? field.initialRows : ""));
    const statusLabel = element("label", "How should this answer be treated?");
    const status = element("select");
    for (const [id, text] of [["confirmed", "My answer (gaps may remain)"], ["unknown", "Unknown - needs a decision"], ["na", "Not applicable - explain why"]]) {
        const option = element("option", text); option.value = id; status.append(option);
    }
    status.value = answer?.status ?? "confirmed";
    statusLabel.append(status);
    const reasonLabel = element("label", "Reason this is not applicable");
    const reason = element("textarea"); reason.rows = 2; reason.value = answer?.reason ?? ""; reasonLabel.append(reason);
    reasonLabel.hidden = status.value !== "na";
    editor.append(statusLabel, reasonLabel);
    const changes = () => {
        let mode = status.value;
        if (mode === "confirmed" && (typeof value === "string" ? !value.trim() : !value.some((row) => row.some((cell) => cell.trim())))) mode = "unknown";
        schedule(field.id, { answer: { fieldId: field.id, value: structuredClone(value), status: mode, reason: reason.value } });
    };
    status.addEventListener("change", () => { reasonLabel.hidden = status.value !== "na"; changes(); });
    reason.addEventListener("input", changes);
    if (field.type === "table") {
        const rows = element("div");
        const drawRows = () => {
            rows.replaceChildren();
            value.forEach((row, index) => {
                const baseline = field.heading.startsWith("3.6") && field.initialRows.some((item) => item[0] === row[0]);
                const container = element("details", "", "row");
                container.open = value.length === 1;
                container.append(element("summary", `Row ${index + 1}${row[0] ? `: ${row[0]}` : ""}`));
                field.columns.forEach((column, col) => {
                    const label = element("label", column);
                    const input = element("textarea"); input.rows = 2; input.value = row[col]; input.maxLength = 4000; input.placeholder = "Confirmed answer or Unknown";
                    input.readOnly = baseline && col === 0;
                    input.addEventListener("input", () => { value[index][col] = input.value; changes(); });
                    label.append(input);
                    const hint = field.rowHints?.[index]?.[col];
                    if (hint && !row[col]) label.append(element("p", hint, "quiet"));
                    container.append(label);
                });
                const remove = button("Remove this row", () => {
                    if (value.length === 1) throw new Error("Keep one row and mark it Unknown or N/A instead of silently omitting the section.");
                    value.splice(index, 1); changes(); drawRows();
                });
                remove.disabled = baseline;
                container.append(remove);
                rows.append(container);
            });
        };
        drawRows();
        editor.append(rows, button("Add another row", () => {
            if (value.length >= 50) throw new Error("This draft supports at most 50 rows per field.");
            value.push(field.columns.map(() => "")); changes(); drawRows();
        }));
    } else {
        const label = element("label", "Your answer");
        const input = element("textarea"); input.rows = 5; input.maxLength = 20000; input.value = value; input.placeholder = "Write naturally. You do not need to use template language.";
        input.addEventListener("input", () => { value = input.value; changes(); });
        label.append(input); editor.append(label);
    }
    const assistance = element("section", "", "assistance");
    renderAssistance(assistance, field);
    editor.append(assistance);
}

function renderAssistance(container, field) {
    container.replaceChildren();
    const actions = element("div", "", "actions");
    actions.append(button("Ask AI to help draft this answer", async () => {
        state = await api("/assist", { revision: state.draft.revision, fieldId: field.id }); render();
    }));
    container.append(actions, element("p", "AI assistance submits a request to this Copilot conversation and may consume credits. It drafts from your saved notes; it does not inspect links or run workflows.", "quiet"));
    if (state.draft.request?.status === "pending") {
        container.append(element("p", "AI assistance pending. You can continue editing; edits make late proposals stale.", "quiet"), button("Cancel pending assistance", async () => {
            state = await api("/cancel", { revision: state.draft.revision }); render();
        }));
    } else if (state.draft.request?.status === "failed") container.append(element("p", "AI assistance failed to submit. Your saved answers remain unchanged.", "quiet"));
    const proposal = state.draft.proposals[field.id];
    if (proposal) {
        const box = element("section", "", "proposal");
        box.append(element("h3", "AI proposal - not yet a requirement"), element("pre", typeof proposal.value === "string" ? proposal.value : proposal.value.map((row) => row.join(" | ")).join("\n")));
        if (proposal.stale) box.append(element("p", "Inputs changed after this proposal was generated. Reject it or ask AI for a fresh proposal."));
        const accept = button("Accept as my reviewed answer", async () => {
            state = await api("/accept", { revision: state.draft.revision, fieldId: field.id }); render();
        }, "primary");
        accept.disabled = Boolean(proposal.stale);
        box.append(accept, button("Reject proposal", async () => {
            state = await api("/reject", { revision: state.draft.revision, fieldId: field.id }); render();
        }));
        container.append(box);
    }
}

for (const input of document.querySelectorAll("[data-meta]")) {
    input.addEventListener("input", () => {
        schedule(`meta-${input.dataset.meta}`, { metadata: { [input.dataset.meta]: input.value } });
        if (input.dataset.meta === "uiKind") perform(async () => { render(); });
    });
}
byId("questions").addEventListener("change", () => {
    const id = byId("questions").value;
    perform(() => { selected = id; render(); });
});
for (const [id, delta] of [["previous", -1], ["next", 1]]) byId(id).addEventListener("click", () => perform(() => {
    const index = state.fields.findIndex((field) => field.id === selected);
    const field = state.fields[Math.max(0, Math.min(state.fields.length - 1, index + delta))];
    selected = field.id; step = field.step; render(); byId("editor").focus();
}));
byId("download").addEventListener("click", () => perform(async () => {
    const result = await api("/export", { revision: state.draft.revision });
    const url = URL.createObjectURL(new Blob([result.markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `${state.draft.id}.md`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    byId("export-result").textContent = "Markdown draft saved privately and downloaded. Review unresolved decisions before handing it to design.";
}));
byId("refresh").addEventListener("click", async () => {
    if (busy || saving) return;
    busy = true;
    try {
        // Refresh the base revision without discarding pending form edits.
        state = await api("/state");
        await flush();
        render();
        byId("error").hidden = true;
    } catch (exception) { error(exception.message); }
    finally { busy = false; }
});
window.addEventListener("beforeunload", (event) => {
    if (pending.size || saving) { event.preventDefault(); event.returnValue = ""; }
});
async function load() {
    try { state = await api("/state"); render(); }
    catch (exception) { error(exception.message); }
}
load();
setInterval(async () => {
    if (!state || pending.size || saving || busy || failed) return;
    try {
        const remote = await api("/state");
        if (pending.size || saving || busy) return;
        if (remote.draft.revision !== state.draft.revision) {
            state = remote;
            render();
        }
    } catch (exception) { error(`Could not refresh saved draft: ${exception.message}`); }
}, 2500);
