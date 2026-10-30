// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

export const currentStatuses = Object.freeze({
    publishedForObservation: "published-for-observation",
    observing: "observing",
    readyForHumanMerge: "ready-for-human-merge",
    needsOwnerDecision: "needs-owner-decision",
});

export const currentStatusValues = Object.freeze(Object.values(currentStatuses));

export const currentStatusOwners = Object.freeze({
    codeFeature: "code-feature",
    observer: "pr-observer",
    human: "human-reviewer",
});

export const statusMarkers = Object.freeze({
    begin: "<!-- BEGIN code-feature-status -->",
    end: "<!-- END code-feature-status -->",
});

export const deliveryDecisionMarkers = Object.freeze({
    begin: "<!-- BEGIN code-feature-delivery-decisions -->",
    end: "<!-- END code-feature-delivery-decisions -->",
});

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
                    justification: rolloutJustification,
                },
                testCoverage: {
                    tag: testCoverageTag,
                    justification: testCoverageJustification,
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
    summary = "Implementation, validation, and independent review completed.",
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
                summary,
                nextAction,
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
