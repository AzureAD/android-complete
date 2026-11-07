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

export function localVerificationSummary(verification) {
    const local = verification?.local?.status || "unverified";
    const summaries = {
        passed: "Local verification passed.",
        failed: "Local verification failed.",
        deferred: "Local verification is incomplete; remaining checks need CI.",
        unverified: "Local verification has not been confirmed.",
    };
    const summary = Object.hasOwn(summaries, local) ? summaries[local] : "Local verification status could not be confirmed.";
    const evidence = Array.isArray(verification?.local?.evidence) ? verification.local.evidence : [];
    const reason = evidence.find((entry) => typeof entry === "string" && entry.trim() &&
        !entry.startsWith("Successful command:"));
    const message = ["failed", "deferred"].includes(local) && reason ? `${summary} ${reason.replace(/\s+/g, " ").trim()}` : summary;
    return message.length > 500 ? `${message.slice(0, 497).trimEnd()}...` : message;
}

export function ciVerificationSummary(verification) {
    const ci = verification?.ci?.status || "not-observed";
    const head = verification?.ci?.headSha;
    if (["passed", "failed"].includes(ci) && (typeof head !== "string" || !head.trim())) {
        return `CI reports a ${ci === "passed" ? "pass" : "failure"}, but its PR head has not been confirmed.`;
    }
    const summary = {
        passed: "CI passed for the current PR head.",
        failed: "CI failed for the current PR head.",
        pending: "Waiting for CI results for the current PR head.",
        "not-observed": "CI has not been checked yet.",
        unknown: "CI results for the current PR head could not be confirmed.",
    }[ci];
    return typeof summary === "string" ? summary : "CI results for the current PR head could not be confirmed.";
}

export function verificationSummary(verification) {
    return `${localVerificationSummary(verification)} ${ciVerificationSummary(verification)}`;
}

export function deliveryOutcome(pullRequests, observations, { published = true } = {}) {
    if (!published) return "prepared-for-review";
    if (!pullRequests.length) return "needs-owner-decision";
    if (observations.length !== pullRequests.filter((pr) => pr.url).length || !observations.length) return "ci-not-observed";
    if (observations.some((item) => item.blockerKind === "ci-infrastructure")) return "infrastructure-blocked";
    if (observations.some((item) => item.ci?.status === "failed")) return "ci-failed";
    if (observations.some((item) => item.statusPublication?.status === "failed")) return "needs-owner-decision";
    if (observations.some((item) => ["max-iterations-reached", "needs-owner-decision"].includes(item.status))) {
        return "needs-owner-decision";
    }
    if (observations.some((item) => item.ci?.status !== "passed")) return "waiting-for-ci";
    if (observations.some((item) => item.status !== "ready-for-human-merge")) return "awaiting-human-review";
    return "ready-for-human-review";
}
