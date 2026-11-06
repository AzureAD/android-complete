// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

export const currentStatuses = Object.freeze({
    publishedForObservation: "published-for-observation",
    observing: "observing",
    readyForHumanMerge: "ready-for-human-merge",
    needsOwnerDecision: "needs-owner-decision",
    waitingForCi: "waiting-for-ci",
    ciFailed: "ci-failed",
    awaitingHumanReview: "awaiting-human-review",
    observationExhausted: "max-iterations-reached",
});

export const currentStatusValues = Object.freeze(Object.values(currentStatuses));

export function deliveryStatusSummary(status) {
    const summary = {
        "prepared-for-review": "Source review complete; the PR packet is prepared but has not been published.",
        "published-for-observation": "PR published; CI observation is next.",
        observing: "PR checks are being inspected.",
        "ready-for-human-review": "Implementation handed off for human review.",
        "ready-for-human-merge": "PR handed off for human review and merge approval.",
        "needs-owner-decision": "A developer decision or prerequisite is needed.",
        "waiting-for-ci": "Waiting for CI results.",
        "ci-not-observed": "PR published; CI has not been checked yet.",
        "ci-failed": "CI failed; the reported failures need attention.",
        "awaiting-human-review": "Required human review or repository approvals remain.",
        "infrastructure-blocked": "CI is blocked by its provider or build environment.",
        "max-iterations-reached": "The repair allowance is used up; unresolved findings need a developer decision.",
        "NO-GO": "Work stopped with unresolved issues.",
        "needs clarification": "A developer answer is needed before work can continue.",
        "GO to PR Review": "Source review complete; inspect publication and verification results before review.",
    }[status];
    return typeof summary === "string" ? summary : "The run finished without a recognized delivery outcome; inspect its recorded result.";
}

export function observerNextAction(result) {
    if (result.statusPublication?.status === "failed") return "The developer must restore the PR status update; the published description may be stale.";
    if (result.blockerKind === "ci-infrastructure") return "The CI provider or pipeline owner must resolve the environment blocker before retrying.";
    if (result.blockerKind === "ci-dependencies") return "The feed or pipeline owner must check dependency access and configuration before retrying.";
    if (result.blockerKind === "ci-diagnostics") return "Inspect the failing build's diagnostics before assigning a source repair.";
    if (result.status === currentStatuses.observationExhausted) return "A developer must review the remaining findings and decide how to continue; the repair allowance is used up.";
    if (result.status === currentStatuses.needsOwnerDecision) return "The developer must resolve the recorded blocker or scope decision.";
    if (result.ci?.status === "failed") return "Resolve the reported CI failures before merge.";
    if (result.status === currentStatuses.waitingForCi) return "Continue observing this PR with the remaining repair allowance.";
    if ([currentStatuses.readyForHumanMerge, currentStatuses.awaitingHumanReview].includes(result.status)) {
        return "A human reviewer must complete the required review, approvals, and merge decision.";
    }
    return "Inspect the recorded PR checks and remaining findings before proceeding.";
}

export const currentStatusOwners = Object.freeze({
    codeFeature: "code-feature",
    observer: "pr-observer",
    human: "human-reviewer",
});

export const statusMarkers = Object.freeze({
    begin: "<!-- BEGIN code-feature-status",
    end: "END code-feature-status -->",
});

export const deliveryDecisionMarkers = Object.freeze({
    begin: "<!-- BEGIN code-feature-delivery-decisions",
    end: "END code-feature-delivery-decisions -->",
});

export const verificationMarkers = Object.freeze({
    begin: "<!-- BEGIN code-feature-verification",
    end: "END code-feature-verification -->",
});

function brief(value, maximum = 250) {
    const text = String(value ?? "");
    if (text.length <= maximum) return text;
    return `${text.slice(0, maximum - 40)} [truncated; see workflow receipt]`;
}

export function fitAdoDescription(body, { maximum = 4000, truncateNarrative = false } = {}) {
    const workItems = new Set();
    body = body.replace(/^WiClose:[^\r\n]*(?:\r?\n|$)/gm, (line) => {
        const key = line.trim();
        if (workItems.has(key)) return "";
        workItems.add(key);
        return line;
    });
    if (body.length <= maximum) return body;
    let narrative = body;
    const blocks = [];
    for (const markers of [
        deliveryDecisionMarkers, verificationMarkers, statusMarkers,
        { begin: "<!-- BEGIN pr-telemetry -->", end: "<!-- END pr-telemetry -->" },
    ]) {
        const start = narrative.lastIndexOf(markers.begin);
        const end = narrative.indexOf(markers.end, start);
        if (start < 0 || end < start) continue;
        const block = narrative.slice(start, end + markers.end.length);
        const value = block.slice(markers.begin.length, block.length - markers.end.length).trim();
        let compact = block;
        if (markers.begin !== "<!-- BEGIN pr-telemetry -->") {
            compact = `${markers.begin}\n${JSON.stringify(JSON.parse(value))}\n${markers.end}`;
        }
        blocks.push(compact);
        narrative = `${narrative.slice(0, start)}${narrative.slice(end + markers.end.length)}`;
    }
    const compact = [narrative.trim(), ...blocks].join("\n\n");
    if (compact.length <= maximum) return compact;
    if (!truncateNarrative) throw new Error("ADO PR description exceeds 4000 characters; refusing to discard human-maintained text.");
    const firstLine = narrative.split(/\r?\n/)[0];
    narrative = narrative.slice(firstLine.length).trim();
    const workItemLines = [...narrative.matchAll(/^WiClose:.*$/gm)].map((match) => match[0]);
    narrative = narrative.replace(/^WiClose:.*$/gm, "").trim();
    const notice = "_Additional narrative omitted for ADO's description limit; see the full workflow receipt._";
    const suffix = [notice, ...workItemLines, ...blocks].join("\n\n");
    const room = maximum - firstLine.length - suffix.length - 4;
    if (room < 0) throw new Error("Required PR metadata exceeds the ADO description limit.");
    let excerpt = narrative.slice(0, room);
    if (/[\uD800-\uDBFF]$/.test(excerpt)) excerpt = excerpt.slice(0, -1);
    return [firstLine, excerpt, suffix].join("\n\n");
}

export function renderVerificationBlock(verification) {
    const receipt = {
        local: {
            status: verification?.local?.status || "unverified",
            headSha: verification?.local?.headSha || null,
        },
        ci: {
            status: verification?.ci?.status || "not-observed",
            headSha: verification?.ci?.headSha || null,
        },
    };
    return [verificationMarkers.begin, JSON.stringify(receipt, null, 2), verificationMarkers.end].join("\n");
}

export function renderDeliveryDecisionBlock({
    rolloutTag = "feature-flag",
    rolloutJustification = "Guarded behind default-off flight.",
    testCoverageTag = "unit-tests",
    testCoverageJustification = "Covered by unit tests.",
} = {}) {
    return [
        deliveryDecisionMarkers.begin,
        JSON.stringify(
            {
                rollout: {
                    tag: rolloutTag,
                    justification: brief(rolloutJustification),
                },
                testCoverage: {
                    tag: testCoverageTag,
                    justification: brief(testCoverageJustification),
                },
            },
            null,
            2,
        ),
        deliveryDecisionMarkers.end,
    ].join("\n");
}

export function renderCurrentStatusBlock({
    status = currentStatuses.publishedForObservation,
    owner = currentStatusOwners.observer,
    runId = "local",
    repository = "",
    prUrl = "<pending>",
    summary = "Implementation and source review completed; execution verification must be checked separately.",
    nextAction = "Run pr-observer for this PR.",
} = {}) {
    return [
        statusMarkers.begin,
        JSON.stringify(
            {
                status,
                owner,
                runId,
                repository,
                prUrl,
                summary: brief(summary, 500),
                nextAction: brief(nextAction),
            },
            null,
            2,
        ),
        statusMarkers.end,
    ].join("\n");
}

export function parseMarkerBlock(body, markers) {
    if (typeof body !== "string" || !markers?.begin || !markers?.end) return null;
    const startIdx = body.indexOf(markers.begin);
    const endIdx = body.indexOf(markers.end);
    if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) return null;
    const rawJson = body.slice(startIdx + markers.begin.length, endIdx).trim();
    try {
        return JSON.parse(rawJson);
    } catch {
        return null;
    }
}

export function upsertMarkerBlock(body, markers, renderedBlock) {
    const base = typeof body === "string" ? body : "";
    const startIdx = base.indexOf(markers.begin);
    const endIdx = base.indexOf(markers.end);
    if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
        return `${base.slice(0, startIdx)}${renderedBlock}${base.slice(endIdx + markers.end.length)}`;
    }
    return base.trim().length > 0 ? `${base.trim()}\n\n${renderedBlock}` : renderedBlock;
}
