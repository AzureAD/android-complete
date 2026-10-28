// Copyright (c) Microsoft Corporation. All rights reserved.

import type { SessionUiApi } from "@github/copilot-sdk";
import { FactoryError } from "../core/contracts.ts";
import { decisionMessage, type DecisionPresentation } from "../core/decisions.ts";

export async function requestDecision(ui: { confirm: SessionUiApi["confirm"]; elicitation?: SessionUiApi["elicitation"] },
    decision: DecisionPresentation, afterReview = false): Promise<"approve" | "review" | "revise" | "not_now"> {
    if (!ui.elicitation) {
        return await ui.confirm(`${decisionMessage(decision)}\n\nFull review\n${decision.review}`) ? "approve" : "not_now";
    }
    const choices = [
        { const: "not_now", title: afterReview ? "Decide later - pause here" : "Not now - leave everything unchanged" },
        ...(!afterReview ? [{ const: "review", title: "Review full details in chat" }] : []),
        ...(decision.canRequestChanges ? [{ const: "revise", title: "Request changes" }] : []),
        { const: "approve", title: decision.approveLabel },
    ];
    const response = await ui.elicitation({
        message: decisionMessage(decision, afterReview),
        requestedSchema: {
            type: "object", required: ["decision"],
            properties: {
                decision: {
                    type: "string", title: afterReview ? "How would you like to proceed?" : "Your decision", default: "not_now",
                    oneOf: choices,
                },
            },
        },
    });
    if (response.action === "decline" || response.action === "cancel") return "not_now";
    const selected = response.content?.decision;
    if (response.action !== "accept" || typeof selected !== "string" || !choices.some((choice) => choice.const === selected)) {
        throw new FactoryError("invalid_confirmation", "The host returned no valid user decision. Nothing was approved.", 409);
    }
    return selected === "approve" ? "approve" : selected === "review" ? "review" : selected === "revise" ? "revise" : "not_now";
}

export async function requestChanges(ui: { elicitation?: SessionUiApi["elicitation"] }, decision: DecisionPresentation): Promise<string | null> {
    if (!ui.elicitation) throw new FactoryError("feedback_unavailable", "This host cannot collect structured revision feedback. Nothing was approved.", 409);
    const response = await ui.elicitation({
        message: `${decision.subject} / ${decision.context}\n\nWhat would you like changed? Your feedback will be returned for a draft revision. Nothing is approved or started.`,
        requestedSchema: {
            type: "object", required: ["feedback"],
            properties: { feedback: { type: "string", title: "Requested changes", minLength: 1, maxLength: 4000 } },
        },
    });
    if (response.action === "decline" || response.action === "cancel") return null;
    const feedback = response.content?.feedback;
    if (response.action !== "accept" || typeof feedback !== "string" || !feedback.trim() || feedback.length > 4000) {
        throw new FactoryError("invalid_feedback", "The host returned no valid revision feedback. Nothing was changed or approved.", 409);
    }
    return feedback.trim();
}
