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
