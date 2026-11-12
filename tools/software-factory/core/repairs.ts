// Copyright (c) Microsoft Corporation. All rights reserved.

import { FactoryError, object } from "./contracts.ts";
import type { FactoryState, LocalPublicationBinding, Operation } from "./lifecycle-contracts.ts";

export function repairHistory(state: FactoryState, publication: Operation): Operation[] {
    const entries = state.operations.filter((entry) => entry.kind === "repair" && entry.repair?.publicationKey === publication.key)
        .sort((a, b) => a.repair!.round - b.repair!.round);
    if (entries.length) {
        const grant = state.decisions.find((decision) => decision.id === publication.approvalId && decision.approved && decision.stage === "dispatch");
        const scope = object(grant?.scope, "Original repair approval");
        const policy = object(scope.observer, "Original observer policy");
        if (policy.engine !== "pr-observer" || !Number.isSafeInteger(policy.maxRepairs) ||
            entries.length > Number(policy.maxRepairs)) {
            throw new FactoryError("repair_history_invalid", "Repair history exceeds its original approved allowance.", 409);
        }
    }
    for (const [round, entry] of entries.entries()) {
        if (entry.repair!.round !== round || entry.planId !== publication.planId ||
            entry.configurationId !== publication.configurationId || entry.itemId !== publication.itemId ||
            entry.approvalId !== publication.approvalId) {
            throw new FactoryError("repair_history_invalid", "Observer repair history does not match the original publication authority.", 409);
        }
    }
    return entries;
}

// Preserve the original source/review receipt; only verified incremental repair receipts advance the observed head.
export function currentPublicationBinding(state: FactoryState, publication: Operation): LocalPublicationBinding {
    if (!publication.publicationBinding) throw new FactoryError("publication_binding_required", "The original publication is missing.", 409);
    let binding = publication.publicationBinding;
    for (const entry of repairHistory(state, publication)) {
        const repair = entry.repair!;
        if (repair.expectedHead !== binding.headSha) {
            throw new FactoryError("repair_history_invalid", "Observer repair parents do not form the recorded publication chain.", 409);
        }
        if (repair.phase !== "pushed") continue;
        const review = object(repair.review, "Independent repair review");
        const receipt = object(entry.receipt, "Repair receipt");
        const original = object(publication.receipt, "Original publication receipt");
        if (!repair.source || repair.source.head !== binding.headSha || !repair.headSha ||
            review.verdict !== "pass" || !Array.isArray(review.findings) || review.findings.length ||
            receipt.headSha !== repair.headSha ||
            !["provider", "mode", "marker", "branch", "prNumber", "url"].every((key) => receipt[key] === original[key])) {
            throw new FactoryError("repair_history_invalid", "The repaired head has no matching source-bound review and publication receipt.", 409);
        }
        binding = { ...binding, headSha: repair.headSha };
    }
    return binding;
}
