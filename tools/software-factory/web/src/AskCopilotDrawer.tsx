// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { FactoryFeature, Json } from "../../core/lifecycle-contracts.ts";
import type {
    FeatureQaContext,
    FeatureQaContextKind,
    FeatureQaEntry,
    FeatureQaResponse,
} from "../../core/qa-contracts.ts";
import {
    askFeatureQa,
    fetchFeatureQa,
    resetFeatureQa,
    subscribeStudioEvents,
    type LiveStreamStatus,
} from "./api";

export interface QaFeedbackBridgeAction {
    target: "design" | "plan" | "pr";
    snippet: string;
    itemId?: string;
}

const CONTEXT_KIND_LABELS: Record<FeatureQaContextKind, string> = {
    requirement: "REQUIREMENT",
    "design-section": "DESIGN SECTION",
    "review-finding": "REVIEW FINDING",
    pbi: "PLANNED PBI",
    implementation: "CODE IMPLEMENTATION",
    pr: "PULL REQUEST",
    followup: "FOLLOW-UP",
};

function formatElapsed(ms: number | null, createdAt: string, completedAt: string | null): string {
    const duration = typeof ms === "number" && ms >= 0
        ? ms
        : (completedAt ? Math.max(0, Date.parse(completedAt) - Date.parse(createdAt)) : 0);
    if (!duration) return "";
    if (duration < 1000) return `${duration}ms`;
    return `${(duration / 1000).toFixed(1)}s`;
}

function buildSuggestedPrompts(activeStageIndex: number, pinned: FeatureQaContext | null): string[] {
    if (pinned) {
        switch (pinned.kind) {
            case "design-section":
                return [
                    `Why was this design approach chosen in "${pinned.title}", and what alternatives or tradeoffs exist?`,
                    `Walk through failure modes, edge cases, and backward-compatibility guarantees for "${pinned.title}".`,
                    `Which repository classes and methods are impacted by "${pinned.title}"?`,
                ];
            case "review-finding":
                return [
                    "Explain this reviewer finding against the actual codebase and verify whether it is still an issue.",
                    "Draft concrete revision guidance I can send to resolve this finding cleanly.",
                    "What would happen in production if we left this finding unaddressed?",
                ];
            case "pbi":
                return [
                    `Explain the exact scope, target files, and acceptance criteria coverage of ${pinned.title}.`,
                    `Are there any missing edge-case unit tests or cross-repo contract risks in ${pinned.title}?`,
                    `Why is ${pinned.title} ordered in this wave relative to other PBIs?`,
                ];
            case "implementation":
                return [
                    `Walk me through the key code changes and unit tests implemented for ${pinned.title}.`,
                    "Check this implementation for concurrency, nullability, serialization, or IPC regressions.",
                    "How did the Coding Builder satisfy each PBI acceptance criterion?",
                ];
            case "pr":
                return [
                    `Summarize what ${pinned.title} changes, how CI & Copilot review went, and what I should verify before merging.`,
                    "Are there any subtle edge cases or rollout risks I should double-check in this PR diff?",
                    "Draft a concise reviewer comment if any follow-up refinement is worthwhile.",
                ];
            case "requirement":
                return [
                    `Trace ${pinned.title} end-to-end across Design sections, PBIs, code changes, and tests.`,
                    `Are there any ambiguities or edge cases in ${pinned.title} we should tighten?`,
                ];
            case "followup":
                return [
                    "Show me the exact source file lines and method signatures that back this up.",
                    "Turn that recommendation into concise actionable revision feedback.",
                ];
        }
    }

    if (activeStageIndex === 0) {
        return [
            "Are there any ambiguous requirements, missing edge cases, or hidden cross-repo dependencies in this brief?",
            "Which Android Auth repositories (Common, MSAL, Broker) are likely touched by these requirements?",
            "Suggest concrete acceptance criteria to make this feature testable end-to-end.",
        ];
    }
    if (activeStageIndex === 1) {
        return [
            "Summarize the key architectural choices and tradeoffs in this design.",
            "What did the Independent Design Reviewer flag across rounds, and how did the Design Author resolve it?",
            "Walk through failure handling, rollback safety, and telemetry coverage in this specification.",
        ];
    }
    if (activeStageIndex === 2 || activeStageIndex === 3) {
        return [
            "Trace how the planned PBIs cover 100% of the approved requirements without gaps.",
            "Why were the PBIs split and ordered into these execution waves?",
            "Are the target files and verification test suites complete for each PBI?",
        ];
    }
    if (activeStageIndex === 4) {
        return [
            "Walk me through the actual code changes, design choices, and unit tests built in Stage 5.",
            "Did the Independent Code Reviewer or PR Observer catch any issues, and how were they verified?",
            "Check the implementation against repository coding conventions, thread safety, and error handling.",
        ];
    }
    return [
        "Give me a pre-merge executive summary of the PRs, CI validation, and acceptance criteria audit.",
        "Are there any remaining risks, edge cases, or rollout considerations before human merge?",
        "Trace every requirement from Stage 1 to the exact code and unit tests in the open PR.",
    ];
}

function inferDefaultPbiKey(feature: FactoryFeature, pinned: FeatureQaContext | null): string | undefined {
    if (pinned && (pinned.kind === "pbi" || pinned.kind === "implementation" || pinned.kind === "pr")) {
        const match = /\b(PBI-\d+)\b/i.exec(`${pinned.title} ${pinned.subtitle || ""}`);
        if (match?.[1]) return match[1].toUpperCase();
    }
    const planArt = feature.factory.artifacts.find((a) => a.id === feature.currentArtifacts.plan);
    if (planArt && planArt.content && typeof planArt.content === "object" && !Array.isArray(planArt.content)) {
        const items = (planArt.content as Record<string, Json>).items;
        if (Array.isArray(items) && items[0] && typeof items[0] === "object" && !Array.isArray(items[0])) {
            const id = (items[0] as Record<string, Json>).id;
            if (typeof id === "string" && id.trim()) return id.trim();
        }
    }
    const op = feature.factory.operations.find((o) => o.kind === "work_item" && o.itemId);
    return op?.itemId;
}

export function AskCopilotDrawer({
    open,
    onClose,
    feature,
    activeStageIndex,
    pinnedContext,
    onPinContext,
    onUseInFeedback,
}: {
    open: boolean;
    onClose: () => void;
    feature: FactoryFeature;
    activeStageIndex: number;
    pinnedContext: FeatureQaContext | null;
    onPinContext: (context: FeatureQaContext | null) => void;
    onUseInFeedback?: (action: QaFeedbackBridgeAction) => void;
}) {
    const [qa, setQa] = useState<FeatureQaResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [resetting, setResetting] = useState(false);
    const [question, setQuestion] = useState("");
    const [error, setError] = useState("");
    const [copiedId, setCopiedId] = useState<string | null>(null);
    const [streamStatus, setStreamStatus] = useState<LiveStreamStatus>("connecting");

    const threadRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const stickToBottomRef = useRef(true);

    const hasRunningEntry = Boolean(qa?.entries.some((entry) => entry.status === "running"));
    const hasDesignArtifact = Boolean(feature.currentArtifacts.design || feature.factory.artifacts.some((a) => a.kind === "design"));
    const hasPlanArtifact = Boolean(feature.currentArtifacts.plan || feature.factory.artifacts.some((a) => a.kind === "plan"));
    const defaultPbiKey = useMemo(() => inferDefaultPbiKey(feature, pinnedContext), [feature, pinnedContext]);
    const suggestedPrompts = useMemo(
        () => buildSuggestedPrompts(activeStageIndex, pinnedContext),
        [activeStageIndex, pinnedContext],
    );

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        setLoading(true);
        setError("");
        void fetchFeatureQa(feature.id)
            .then((response) => {
                if (!cancelled) setQa(response);
            })
            .catch((failure: unknown) => {
                if (!cancelled) {
                    setError(failure instanceof Error ? failure.message : "Could not load Studio Q&A history.");
                }
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, [open, feature.id]);

    useEffect(() => {
        if (open) {
            const timer = setTimeout(() => inputRef.current?.focus(), 80);
            return () => clearTimeout(timer);
        }
    }, [open, pinnedContext]);

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        const unsubscribe = subscribeStudioEvents({
            onStatus: (next) => {
                if (!cancelled) setStreamStatus(next);
            },
            onEvent: (ev) => {
                if (cancelled) return;
                if (ev.type === "qa-started" && ev.featureId === feature.id) {
                    setQa((prev) => {
                        if (!prev) return prev;
                        const exists = prev.entries.some((e) => e.id === ev.entryId);
                        if (!exists) {
                            void fetchFeatureQa(feature.id).then((fresh) => {
                                if (!cancelled) setQa(fresh);
                            }).catch(() => {});
                            return prev;
                        }
                        return {
                            ...prev,
                            entries: prev.entries.map((e) =>
                                e.id === ev.entryId
                                    ? { ...e, sessionId: ev.sessionId, turnIndex: ev.turnIndex, livePhase: ev.livePhase }
                                    : e),
                        };
                    });
                } else if (ev.type === "qa-delta" && ev.featureId === feature.id) {
                    setQa((prev) => {
                        if (!prev) return prev;
                        return {
                            ...prev,
                            entries: prev.entries.map((e) =>
                                e.id === ev.entryId
                                    ? { ...e, answer: `${e.answer}${ev.delta}`, livePhase: ev.livePhase }
                                    : e),
                        };
                    });
                } else if (ev.type === "qa-step" && ev.featureId === feature.id) {
                    setQa((prev) => {
                        if (!prev) return prev;
                        return {
                            ...prev,
                            entries: prev.entries.map((e) =>
                                e.id === ev.entryId
                                    ? { ...e, livePhase: ev.livePhase, steps: ev.steps }
                                    : e),
                        };
                    });
                } else if (ev.type === "qa-phase" && ev.featureId === feature.id) {
                    setQa((prev) => {
                        if (!prev) return prev;
                        return {
                            ...prev,
                            entries: prev.entries.map((e) =>
                                e.id === ev.entryId
                                    ? { ...e, livePhase: ev.livePhase }
                                    : e),
                        };
                    });
                } else if ((ev.type === "qa-complete" || ev.type === "qa-reset") && ev.featureId === feature.id) {
                    void fetchFeatureQa(feature.id).then((fresh) => {
                        if (!cancelled) setQa(fresh);
                    }).catch(() => {});
                }
            },
        });
        return () => {
            cancelled = true;
            unsubscribe();
        };
    }, [open, feature.id]);

    useEffect(() => {
        if (!open || !hasRunningEntry || streamStatus === "live") return;
        let cancelled = false;
        const timer = setInterval(() => {
            void fetchFeatureQa(feature.id)
                .then((response) => {
                    if (!cancelled) setQa(response);
                })
                .catch(() => {
                    // Transient polling hiccup; next tick retries quietly.
                });
        }, 2500);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [open, feature.id, hasRunningEntry, streamStatus]);

    useEffect(() => {
        if (!open || !stickToBottomRef.current) return;
        const el = threadRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [open, qa?.entries]);

    useEffect(() => {
        if (!open) return;
        const onKeyDown = (event: KeyboardEvent | globalThis.KeyboardEvent) => {
            if (event.key === "Escape") {
                onClose();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [open, onClose]);

    const handleThreadScroll = () => {
        const el = threadRef.current;
        if (!el) return;
        const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        stickToBottomRef.current = distanceFromBottom < 80;
    };

    async function submitQuestion(rawQuestion = question) {
        const trimmed = rawQuestion.trim();
        if (!trimmed || submitting || hasRunningEntry) return;
        setSubmitting(true);
        setError("");
        stickToBottomRef.current = true;
        try {
            const response = await askFeatureQa(feature.id, trimmed, pinnedContext);
            setQa(response);
            setQuestion("");
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : "Failed to submit question to Copilot.");
        } finally {
            setSubmitting(false);
        }
    }

    function onFormSubmit(event: FormEvent) {
        event.preventDefault();
        void submitQuestion();
    }

    function onTextareaKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            void submitQuestion();
        }
    }

    async function handleResetSession() {
        if (resetting || hasRunningEntry) return;
        setResetting(true);
        setError("");
        try {
            const response = await resetFeatureQa(feature.id);
            setQa(response);
            onPinContext(null);
            inputRef.current?.focus();
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : "Could not start a fresh Q&A session.");
        } finally {
            setResetting(false);
        }
    }

    function handleFollowUp(entry: FeatureQaEntry, turnNumber: number) {
        const answerPreview = entry.answer.length > 900 ? `${entry.answer.slice(0, 900)}…` : entry.answer;
        onPinContext({
            kind: "followup",
            title: `Follow-up on Turn #${turnNumber}`,
            subtitle: entry.question.length > 90 ? `${entry.question.slice(0, 90)}…` : entry.question,
            snippet: `Prior question: ${entry.question}\n\nPrior answer excerpt:\n${answerPreview}`,
        });
        inputRef.current?.focus();
    }

    function handleCopyAnswer(entry: FeatureQaEntry) {
        if (!navigator.clipboard) return;
        void navigator.clipboard.writeText(entry.answer).then(() => {
            setCopiedId(entry.id);
            setTimeout(() => setCopiedId((prev) => (prev === entry.id ? null : prev)), 1800);
        });
    }

    if (!open) return null;

    const summary = qa?.contextSummary;
    const entries = qa?.entries ?? [];

    return (
        <div className="qa-drawer-layer" role="dialog" aria-modal="false" aria-label="Ask Copilot Studio Q&A">
            <div className="qa-drawer-backdrop" onClick={onClose} aria-hidden="true" />
            <aside className="qa-drawer-panel">
                <header className="qa-drawer-header">
                    <div className="qa-drawer-title-group">
                        <span className="eyebrow">STUDIO AI SIDECAR · MULTI-TURN Q&amp;A</span>
                        <div className="qa-drawer-title-row">
                            <h3>✦ Ask Copilot</h3>
                            <span className="studio-pill emerald">🔒 Read-Only</span>
                            {streamStatus === "live" && (
                                <span className="studio-pill blue" title="Real-time SSE token & tool streaming active">
                                    ● Live Stream
                                </span>
                            )}
                            {qa?.activeSession && (
                                <span className="mono-chip blue" title={`Session ${qa.activeSession.sessionId}`}>
                                    Session {qa.activeSession.sessionId.slice(0, 8)} · {qa.activeSession.turnCount} turn{qa.activeSession.turnCount === 1 ? "" : "s"}
                                </span>
                            )}
                        </div>
                        <p className="qa-feature-subtitle" title={feature.current.title}>
                            {feature.current.title}
                        </p>
                    </div>
                    <div className="qa-drawer-header-actions">
                        {(qa?.activeSession || entries.length > 0) && (
                            <button
                                type="button"
                                className="ghost-chip-btn"
                                disabled={resetting || hasRunningEntry}
                                title="Start a fresh multi-turn Copilot session while keeping past Q&A visible above"
                                onClick={() => void handleResetSession()}
                            >
                                {resetting ? "Resetting…" : "New Session ↻"}
                            </button>
                        )}
                        <button type="button" className="ghost-chip-btn" onClick={onClose} aria-label="Close Ask Copilot drawer">
                            Close ✕
                        </button>
                    </div>
                </header>

                <div className="qa-context-strip" aria-label="Loaded read-only context">
                    <span className="qa-safety-note">
                        Inspects artifacts, PBIs, PRs &amp; repo code on a separate session — never alters Factory runs or ADO state.
                    </span>
                    {summary && (
                        <div className="qa-context-pills">
                            <span className="mono-chip">{summary.requirementsCount} Req{summary.requirementsCount === 1 ? "" : "s"}</span>
                            <span className={`mono-chip ${summary.hasDesign ? "emerald" : ""}`}>
                                Design {summary.hasDesign ? `✓ (${summary.designReviewRounds} rev)` : "—"}
                            </span>
                            <span className={`mono-chip ${summary.plannedPbiCount > 0 ? "emerald" : ""}`}>
                                {summary.plannedPbiCount} PBI{summary.plannedPbiCount === 1 ? "" : "s"}
                            </span>
                            {summary.implementedPbiCount > 0 && (
                                <span className="mono-chip blue">{summary.implementedPbiCount} Code Built</span>
                            )}
                            {summary.prCount > 0 && (
                                <span className="mono-chip purple">{summary.prCount} PR{summary.prCount === 1 ? "" : "s"}</span>
                            )}
                            {summary.readableRepos.map((repo) => (
                                <span key={repo} className="mono-chip" title={`Read-only repo access: ${repo}`}>
                                    📂 {repo}
                                </span>
                            ))}
                        </div>
                    )}
                    {qa?.activeSession?.staleContext && (
                        <div className="qa-stale-banner">
                            Artifact revisions updated since the last question — your next question will automatically refresh the context pack.
                        </div>
                    )}
                </div>

                <div ref={threadRef} className="qa-thread" onScroll={handleThreadScroll}>
                    {loading && entries.length === 0 && (
                        <div className="qa-empty-card">
                            <p className="muted">Loading feature context and Q&amp;A history…</p>
                        </div>
                    )}

                    {!loading && entries.length === 0 && (
                        <div className="qa-empty-card">
                            <h4>Ask anything about this feature&apos;s design, PBIs, or code</h4>
                            <p className="muted">
                                Copilot has full read-only context across your approved brief, 10-section design, reviewer objections &amp; author resolutions, planned PBIs, implementation rounds, PR status, and target repositories (<code>view</code>, <code>grep</code>, <code>glob</code>).
                            </p>
                            <p className="field-help">
                                Tip: Click <strong>✦ Ask AI</strong> on any Design section, Reviewer finding, PBI card, or Implementation round in the workspace to pin it here.
                            </p>
                        </div>
                    )}

                    {entries.length === 0 && (
                        <div className="qa-suggestions-block" aria-label="Suggested starter questions">
                            <span className="eyebrow">SUGGESTED QUESTIONS FOR THIS VIEW</span>
                            <div className="qa-suggestion-chips">
                                {suggestedPrompts.map((prompt, idx) => (
                                    <button
                                        key={idx}
                                        type="button"
                                        className="qa-suggestion-chip"
                                        disabled={hasRunningEntry || submitting}
                                        onClick={() => {
                                            setQuestion(prompt);
                                            inputRef.current?.focus();
                                        }}
                                    >
                                        <span>✦</span>
                                        <span>{prompt}</span>
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {entries.map((entry, idx) => {
                        const turnNumber = idx + 1;
                        const elapsed = formatElapsed(entry.durationMs, entry.createdAt, entry.completedAt);
                        return (
                            <article key={entry.id} className="qa-turn" aria-label={`Q&A Turn ${turnNumber}`}>
                                <div className="qa-question-bubble">
                                    <div className="qa-question-meta">
                                        <span className="mono-chip">Turn #{turnNumber}</span>
                                        {entry.context && (
                                            <span className="studio-pill blue">
                                                📌 {CONTEXT_KIND_LABELS[entry.context.kind]}: {entry.context.title}
                                            </span>
                                        )}
                                        <span className="muted">{new Date(entry.createdAt).toLocaleTimeString()}</span>
                                    </div>
                                    <p className="qa-question-text">{entry.question}</p>
                                </div>

                                <div className={`qa-answer-card status-${entry.status}`}>
                                    {entry.status === "running" && (
                                        <div className="qa-live-status" role="status" aria-live="polite">
                                            <span className="status-dot pulse" />
                                            <strong>{entry.livePhase || "Inspecting artifacts and repository source…"}</strong>
                                        </div>
                                    )}

                                    {entry.status === "running" && entry.liveSteps.length > 0 && (
                                        <ul className="qa-live-steps">
                                            {entry.liveSteps.slice(-6).map((step, stepIdx) => (
                                                <li key={`${step.timestamp}-${step.toolName}-${stepIdx}`} className={`qa-step-item ${step.status}`}>
                                                    <span>{step.status === "running" ? "⏳" : step.status === "failed" ? "⚠️" : "✓"}</span>
                                                    <code>{step.toolName}</code>
                                                    <span>{step.target}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    )}

                                    {entry.answer ? (
                                        <div className="markdown-prose compact-prose qa-answer-prose">
                                            <Markdown
                                                remarkPlugins={[remarkGfm]}
                                                skipHtml
                                                components={{ img: ({ alt }) => <span>[Image: {alt || "not loaded"}]</span> }}
                                            >
                                                {entry.answer}
                                            </Markdown>
                                        </div>
                                    ) : entry.status === "running" ? (
                                        <p className="muted qa-thinking-placeholder">Synthesizing grounded answer…</p>
                                    ) : null}

                                    {entry.status === "error" && (
                                        <div className="studio-callout warning">
                                            <strong>Question failed:</strong> {entry.error || "Copilot could not complete this turn."}
                                            <div className="qa-answer-actions">
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    onClick={() => {
                                                        if (entry.context) onPinContext(entry.context);
                                                        setQuestion(entry.question);
                                                        inputRef.current?.focus();
                                                    }}
                                                >
                                                    Edit &amp; retry ↻
                                                </button>
                                            </div>
                                        </div>
                                    )}

                                    {entry.status === "completed" && entry.liveSteps.length > 0 && (
                                        <details className="qa-steps-disclosure">
                                            <summary>
                                                Verified against {entry.liveSteps.length} read-only source probe{entry.liveSteps.length === 1 ? "" : "s"}
                                            </summary>
                                            <ul className="qa-live-steps">
                                                {entry.liveSteps.map((step, stepIdx) => (
                                                    <li key={`${step.timestamp}-${step.toolName}-${stepIdx}`} className={`qa-step-item ${step.status}`}>
                                                        <span>{step.status === "failed" ? "⚠️" : "✓"}</span>
                                                        <code>{step.toolName}</code>
                                                        <span>{step.target}</span>
                                                    </li>
                                                ))}
                                            </ul>
                                        </details>
                                    )}

                                    {entry.status === "completed" && (
                                        <footer className="qa-answer-footer">
                                            <div className="qa-answer-actions">
                                                {elapsed && <span className="mono-chip">{elapsed}</span>}
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    onClick={() => handleFollowUp(entry, turnNumber)}
                                                >
                                                    ↳ Follow up
                                                </button>
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    onClick={() => handleCopyAnswer(entry)}
                                                >
                                                    {copiedId === entry.id ? "Copied ✓" : "Copy"}
                                                </button>
                                            </div>
                                            {onUseInFeedback && (
                                                <div className="qa-bridge-actions">
                                                    {hasDesignArtifact && (
                                                        <button
                                                            type="button"
                                                            className="ghost-chip-btn"
                                                            title="Copy into Stage 2 Design revision composer (does not submit automatically)"
                                                            onClick={() => onUseInFeedback({
                                                                target: "design",
                                                                snippet: `[From Copilot Q&A — ${entry.context?.title || `Turn #${turnNumber}`}]:\n${entry.answer}`,
                                                            })}
                                                        >
                                                            + To Design Revision
                                                        </button>
                                                    )}
                                                    {hasPlanArtifact && (
                                                        <button
                                                            type="button"
                                                            className="ghost-chip-btn"
                                                            title="Copy into Stage 3 Plan revision composer (does not submit automatically)"
                                                            onClick={() => onUseInFeedback({
                                                                target: "plan",
                                                                snippet: `[From Copilot Q&A — ${entry.context?.title || `Turn #${turnNumber}`}]:\n${entry.answer}`,
                                                            })}
                                                        >
                                                            + To Plan Revision
                                                        </button>
                                                    )}
                                                    {defaultPbiKey && (
                                                        <button
                                                            type="button"
                                                            className="ghost-chip-btn"
                                                            title="Stage in Stage 6 PR comment composer for review before posting"
                                                            onClick={() => onUseInFeedback({
                                                                target: "pr",
                                                                itemId: defaultPbiKey,
                                                                snippet: entry.answer,
                                                            })}
                                                        >
                                                            + Stage PR Comment
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </footer>
                                    )}
                                </div>
                            </article>
                        );
                    })}

                    {entries.length > 0 && !hasRunningEntry && (
                        <div className="qa-suggestions-inline">
                            {suggestedPrompts.slice(0, 2).map((prompt, idx) => (
                                <button
                                    key={idx}
                                    type="button"
                                    className="qa-suggestion-chip compact"
                                    disabled={submitting}
                                    onClick={() => {
                                        setQuestion(prompt);
                                        inputRef.current?.focus();
                                    }}
                                >
                                    <span>✦</span>
                                    <span>{prompt}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                <form className="qa-composer" onSubmit={onFormSubmit}>
                    {error && (
                        <div role="alert" className="message error qa-error-banner">
                            {error}
                        </div>
                    )}

                    {pinnedContext && (
                        <div className="qa-pinned-card" aria-label="Pinned question context">
                            <div className="qa-pinned-head">
                                <div className="studio-badge-row">
                                    <span className="studio-pill blue">📌 {CONTEXT_KIND_LABELS[pinnedContext.kind]}</span>
                                    <strong>{pinnedContext.title}</strong>
                                    {pinnedContext.subtitle && <span className="muted">{pinnedContext.subtitle}</span>}
                                </div>
                                <button
                                    type="button"
                                    className="ghost-chip-btn"
                                    onClick={() => onPinContext(null)}
                                    aria-label="Clear pinned context"
                                >
                                    Clear ✕
                                </button>
                            </div>
                            {pinnedContext.path && (
                                <code className="qa-pinned-path">{pinnedContext.path}</code>
                            )}
                            {pinnedContext.snippet && (
                                <details className="qa-pinned-snippet">
                                    <summary>Preview attached excerpt ({pinnedContext.snippet.length.toLocaleString()} chars)</summary>
                                    <pre className="code-snippet-box">{pinnedContext.snippet}</pre>
                                </details>
                            )}
                        </div>
                    )}

                    <label htmlFor={`qa-input-${feature.id}`} className="sr-only">
                        Ask Copilot a question about this feature
                    </label>
                    <textarea
                        ref={inputRef}
                        id={`qa-input-${feature.id}`}
                        rows={3}
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={onTextareaKeyDown}
                        disabled={submitting || hasRunningEntry}
                        placeholder={
                            pinnedContext
                                ? `Ask about "${pinnedContext.title}"… (Enter to send, Shift+Enter for newline)`
                                : "Ask about design choices, tradeoffs, PBIs, reviewer findings, or code… (Enter to send)"
                        }
                    />
                    <div className="qa-composer-footer">
                        <span className="field-help">
                            Multi-turn read-only session · Enter to send, Shift+Enter for newline
                        </span>
                        <button
                            type="submit"
                            className="primary"
                            disabled={submitting || hasRunningEntry || !question.trim()}
                        >
                            {hasRunningEntry ? "Answering…" : submitting ? "Sending…" : "Ask Copilot →"}
                        </button>
                    </div>
                </form>
            </aside>
        </div>
    );
}
