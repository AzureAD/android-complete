// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

export function pendingVerification(reason = "No successful verification receipt is available.") {
    return {
        local: { status: "unverified", commands: [], evidence: [reason] },
        ci: { status: "not-observed", headSha: null, evidence: [] },
    };
}

export function verificationFromGate(gate) {
    const commands = Object.entries(gate.outcomes || {}).flatMap(([status, entries]) =>
        entries.map((entry) => ({ ...entry, status })));
    const failed = commands.some((entry) => entry.status === "failed");
    const deferred = commands.some((entry) => ["blocked", "deferred"].includes(entry.status));
    const passed = commands.length > 0 && commands.every((entry) => entry.status === "passed");
    return {
        ...pendingVerification(),
        local: {
            status: failed ? "failed" : deferred ? "deferred" : passed ? "passed" : "unverified",
            commands,
            evidence: [
                ...(gate.ranCommands || []).map((command) => `Successful command: ${command}`),
                ...(gate.errors || []),
                ...(gate.deferredNotes || []),
            ],
        },
    };
}

export function verificationSummary(verification) {
    const local = verification?.local?.status || "unverified";
    const ci = verification?.ci?.status || "not-observed";
    return `Local verification: ${local}. Current-head CI: ${ci}.`;
}

export function deliveryOutcome(pullRequests, observations, { published = true } = {}) {
    if (!published) return "prepared-for-review";
    if (!pullRequests.length) return "needs-owner-decision";
    if (observations.length !== pullRequests.filter((pr) => pr.url).length || !observations.length) return "ci-not-observed";
    if (observations.some((item) => item.ci?.status === "failed")) return "ci-failed";
    if (observations.some((item) => item.statusPublication?.status === "failed")) return "needs-owner-decision";
    if (observations.some((item) => ["max-iterations-reached", "needs-owner-decision"].includes(item.status))) {
        return "needs-owner-decision";
    }
    if (observations.some((item) => item.ci?.status !== "passed")) return "waiting-for-ci";
    if (observations.some((item) => item.status !== "ready-for-human-merge")) return "awaiting-human-review";
    return "ready-for-human-review";
}
