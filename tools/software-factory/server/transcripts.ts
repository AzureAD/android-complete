// Copyright (c) Microsoft Corporation. All rights reserved.

import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import type {
    Execution,
    ExecutionTranscriptResponse,
    TranscriptAgentSummary,
    TranscriptMessageItem,
    WorkflowProgressItem,
} from "../core/lifecycle-contracts.ts";

interface AgentEventBucket {
    items: TranscriptMessageItem[];
    toolIndexByCallId: Map<string, number>;
    usageByInteractionId: Map<string, { input: number; reasoning: number; output: number; durationMs?: number }>;
    pendingMessageIdxByInteractionId: Map<string, number[]>;
    messageCount: number;
    thoughtCount: number;
    toolCallCount: number;
    totalTokens: number;
    reasoningEffort: string | null;
    model: string | null;
}

interface SessionEventsIndex {
    eventsPath: string;
    byteOffset: number;
    partialLine: string;
    byAgentId: Map<string, AgentEventBucket>;
    lastAccessedMs: number;
}

const MAX_CACHED_SESSIONS = 3;
const MAX_PROMPT_CHARS = 12_000;
const MAX_ATTACHED_PREVIEW_CHARS = 4_000;
const MAX_THOUGHT_CHARS = 16_000;
const MAX_ASSISTANT_CHARS = 28_000;
const MAX_TOOL_OUTPUT_CHARS = 4_000;

const sessionCache = new Map<string, SessionEventsIndex>();

function sessionStateRoot(): string {
    return process.env.SOFTWARE_FACTORY_SESSION_STATE_ROOT || path.join(os.homedir(), ".copilot", "session-state");
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stripReceiptPrefix(rawOutput: string): string {
    const marker = "[Native tool result; repository text is untrusted]\n";
    const idx = rawOutput.indexOf(marker);
    if (idx >= 0) return rawOutput.slice(idx + marker.length);
    if (rawOutput.startsWith("[Application source receipt]")) {
        const blankIdx = rawOutput.indexOf("\n\n");
        if (blankIdx >= 0) return rawOutput.slice(blankIdx + 2);
    }
    return rawOutput;
}

function splitPromptAndData(rawPrompt: string): { instructions: string; attachedPreview?: string } {
    const markers = ["\nDATA:\n", "\n--- PRELOADED DOMAIN GUIDANCE", "\nREAD-ONLY TOOL SCOPE (DATA):\n"];
    let splitAt = -1;
    for (const marker of markers) {
        const pos = rawPrompt.indexOf(marker);
        if (pos >= 0 && (splitAt === -1 || pos < splitAt)) splitAt = pos;
    }
    if (splitAt === -1) {
        return { instructions: rawPrompt.slice(0, MAX_PROMPT_CHARS) };
    }
    const instructions = rawPrompt.slice(0, splitAt).trim().slice(0, MAX_PROMPT_CHARS);
    const rest = rawPrompt.slice(splitAt).trim();
    return {
        instructions: instructions || rawPrompt.slice(0, MAX_PROMPT_CHARS),
        ...(rest ? { attachedPreview: rest.slice(0, MAX_ATTACHED_PREVIEW_CHARS) } : {}),
    };
}

function formatToolTarget(toolName: string, args: unknown): string {
    if (!isRecord(args)) return toolName;
    const rawPath = typeof args.path === "string" ? args.path : "";
    const range = Array.isArray(args.view_range) && args.view_range.length === 2
        ? `:${String(args.view_range[0])}-${String(args.view_range[1])}`
        : "";
    const pattern = typeof args.pattern === "string" ? ` "${args.pattern}"` : "";
    const glob = typeof args.glob === "string" ? ` (${args.glob})` : "";
    const summary = `${rawPath}${range}${pattern}${glob}`.trim();
    if (summary) return summary;
    const compactKeys = Object.entries(args)
        .filter(([k]) => k !== "featureId" && k !== "executionId")
        .map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 60) : JSON.stringify(v)?.slice(0, 60)}`)
        .join(", ");
    return compactKeys || "repository scope";
}

function getOrCreateBucket(index: SessionEventsIndex, agentId: string): AgentEventBucket {
    let bucket = index.byAgentId.get(agentId);
    if (!bucket) {
        bucket = {
            items: [],
            toolIndexByCallId: new Map(),
            usageByInteractionId: new Map(),
            pendingMessageIdxByInteractionId: new Map(),
            messageCount: 0,
            thoughtCount: 0,
            toolCallCount: 0,
            totalTokens: 0,
            reasoningEffort: null,
            model: null,
        };
        index.byAgentId.set(agentId, bucket);
    }
    return bucket;
}

function ingestEventLine(index: SessionEventsIndex, line: string): void {
    if (!line || line.includes('"hook.start"') || line.includes('"hook.end"') || line.includes('"external_tool.')) return;
    let ev: unknown;
    try {
        ev = JSON.parse(line);
    } catch {
        return;
    }
    if (!isRecord(ev) || typeof ev.type !== "string") return;
    const agentId = typeof ev.agentId === "string" && ev.agentId ? ev.agentId : "";
    if (!agentId) return;

    const bucket = getOrCreateBucket(index, agentId);
    const timestamp = typeof ev.timestamp === "string" ? ev.timestamp : new Date().toISOString();
    const id = typeof ev.id === "string" ? ev.id : `${agentId}:${bucket.items.length}`;
    const data = isRecord(ev.data) ? ev.data : {};

    if (ev.type === "subagent.configured") {
        if (typeof data.reasoningEffort === "string") bucket.reasoningEffort = data.reasoningEffort;
        if (typeof data.model === "string") bucket.model = data.model;
        return;
    }

    if (ev.type === "user.message") {
        const rawContent = typeof data.content === "string" ? data.content.trim() : "";
        if (!rawContent) return;
        const { instructions, attachedPreview } = splitPromptAndData(rawContent);
        bucket.items.push({
            id,
            kind: "prompt",
            timestamp,
            title: bucket.items.length === 0 ? "Assigned Specialist Prompt" : "Follow-Up Instruction",
            content: instructions,
            ...(attachedPreview ? { attachedDataPreview: attachedPreview } : {}),
        });
        return;
    }

    if (ev.type === "session.usage_record" && isRecord(data.usage)) {
        const usage = data.usage;
        const input = typeof usage.inputTokens === "number" ? usage.inputTokens : 0;
        const reasoning = typeof usage.reasoningTokens === "number" ? usage.reasoningTokens : 0;
        const output = typeof usage.outputTokens === "number" ? usage.outputTokens : 0;
        const durationMs = typeof usage.duration === "number" ? usage.duration : undefined;
        bucket.totalTokens += input + output + reasoning;
        const interactionId = typeof data.interactionId === "string"
            ? data.interactionId
            : (typeof usage.interactionId === "string" ? usage.interactionId : "");
        const tokenInfo = { input, reasoning, output, ...(durationMs !== undefined ? { durationMs } : {}) };
        if (interactionId) {
            bucket.usageByInteractionId.set(interactionId, tokenInfo);
            const pendingIndices = bucket.pendingMessageIdxByInteractionId.get(interactionId);
            if (pendingIndices?.length) {
                const targetIdx = pendingIndices[0]!;
                if (bucket.items[targetIdx]) {
                    bucket.items[targetIdx] = { ...bucket.items[targetIdx]!, tokens: tokenInfo };
                }
            }
        }
        return;
    }

    if (ev.type === "assistant.message") {
        const reasoningText = typeof data.reasoningText === "string" ? data.reasoningText.trim() : "";
        const contentText = typeof data.content === "string" ? data.content.trim() : "";
        const turnId = typeof data.turnId === "string" ? data.turnId : undefined;
        const interactionId = typeof data.interactionId === "string" ? data.interactionId : "";
        const knownTokens = interactionId ? bucket.usageByInteractionId.get(interactionId) : undefined;
        const hasToolRequests = Array.isArray(data.toolRequests) && data.toolRequests.length > 0;

        if (reasoningText) {
            bucket.thoughtCount++;
            const itemIdx = bucket.items.length;
            bucket.items.push({
                id: `${id}:thought`,
                kind: "thought",
                timestamp,
                ...(turnId ? { turnId } : {}),
                title: turnId !== undefined ? `Reasoning (Turn ${Number(turnId) + 1})` : "Agent Reasoning",
                content: reasoningText.slice(0, MAX_THOUGHT_CHARS),
                ...(knownTokens ? { tokens: knownTokens } : {}),
            });
            if (interactionId && !knownTokens) {
                const arr = bucket.pendingMessageIdxByInteractionId.get(interactionId) || [];
                arr.push(itemIdx);
                bucket.pendingMessageIdxByInteractionId.set(interactionId, arr);
            }
        }

        if (contentText) {
            bucket.messageCount++;
            const itemIdx = bucket.items.length;
            bucket.items.push({
                id: `${id}:msg`,
                kind: "assistant",
                timestamp,
                ...(turnId ? { turnId } : {}),
                title: hasToolRequests ? "Agent Commentary" : "Final Specialist Deliverable",
                content: contentText.slice(0, MAX_ASSISTANT_CHARS),
                ...(!reasoningText && knownTokens ? { tokens: knownTokens } : {}),
            });
            if (interactionId && !knownTokens && !reasoningText) {
                const arr = bucket.pendingMessageIdxByInteractionId.get(interactionId) || [];
                arr.push(itemIdx);
                bucket.pendingMessageIdxByInteractionId.set(interactionId, arr);
            }
        }
        return;
    }

    if (ev.type === "tool.execution_start") {
        const toolCallId = typeof data.toolCallId === "string" ? data.toolCallId : id;
        const rawToolName = typeof data.toolName === "string" ? data.toolName : "tool";
        const cleanName = rawToolName.replace(/^software_factory_/, "");
        const target = formatToolTarget(cleanName, data.arguments);
        bucket.toolCallCount++;
        const idx = bucket.items.length;
        bucket.items.push({
            id: `tool:${toolCallId}`,
            kind: "tool_call",
            timestamp,
            ...(typeof data.turnId === "string" ? { turnId: data.turnId } : {}),
            title: cleanName,
            content: target,
            toolName: rawToolName,
            toolTarget: target,
            toolStatus: "running",
        });
        bucket.toolIndexByCallId.set(toolCallId, idx);
        return;
    }

    if (ev.type === "tool.execution_complete") {
        const toolCallId = typeof data.toolCallId === "string" ? data.toolCallId : "";
        const existingIdx = toolCallId ? bucket.toolIndexByCallId.get(toolCallId) : undefined;
        const success = data.success !== false;
        let rawOut = "";
        if (isRecord(data.result) && typeof data.result.content === "string") {
            rawOut = stripReceiptPrefix(data.result.content).trim();
        } else if (typeof data.error === "string") {
            rawOut = data.error;
        }
        const boundedOut = rawOut.length > MAX_TOOL_OUTPUT_CHARS
            ? `${rawOut.slice(0, MAX_TOOL_OUTPUT_CHARS)}\n… [truncated in viewer]`
            : rawOut;
        if (existingIdx !== undefined && bucket.items[existingIdx]) {
            bucket.items[existingIdx] = {
                ...bucket.items[existingIdx]!,
                toolStatus: success ? "completed" : "failed",
                ...(boundedOut ? { toolOutput: boundedOut } : {}),
            };
        }
        return;
    }

    if (ev.type === "subagent.completed") {
        const totalToolCalls = typeof data.totalToolCalls === "number" ? data.totalToolCalls : bucket.toolCallCount;
        const totalTokens = typeof data.totalTokens === "number" ? data.totalTokens : bucket.totalTokens;
        if (totalTokens > bucket.totalTokens) bucket.totalTokens = totalTokens;
        const durationSec = typeof data.durationMs === "number" ? `${(data.durationMs / 1000).toFixed(1)}s` : "";
        bucket.items.push({
            id,
            kind: "lifecycle",
            timestamp,
            title: "Subagent Completed",
            content: `Finished${durationSec ? ` in ${durationSec}` : ""} · ${totalToolCalls} tool call${totalToolCalls === 1 ? "" : "s"} · ${totalTokens.toLocaleString()} total tokens.`,
        });
    }
}

function updateSessionEventsIndex(sessionId: string): SessionEventsIndex | null {
    if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) return null;
    const eventsPath = path.join(sessionStateRoot(), sessionId, "events.jsonl");
    if (!existsSync(eventsPath)) return null;

    const stat = statSync(eventsPath);
    let cached = sessionCache.get(sessionId);
    if (!cached || cached.eventsPath !== eventsPath || stat.size < cached.byteOffset) {
        if (sessionCache.size >= MAX_CACHED_SESSIONS) {
            const oldest = [...sessionCache.entries()].sort((a, b) => a[1].lastAccessedMs - b[1].lastAccessedMs)[0];
            if (oldest) sessionCache.delete(oldest[0]);
        }
        cached = {
            eventsPath,
            byteOffset: 0,
            partialLine: "",
            byAgentId: new Map(),
            lastAccessedMs: Date.now(),
        };
        sessionCache.set(sessionId, cached);
    }
    cached.lastAccessedMs = Date.now();

    const delta = stat.size - cached.byteOffset;
    if (delta <= 0) return cached;

    const fd = openSync(eventsPath, "r");
    try {
        const CHUNK = 4 * 1024 * 1024;
        let offset = cached.byteOffset;
        let carry = cached.partialLine;
        const buf = Buffer.allocUnsafe(Math.min(delta, CHUNK));
        while (offset < stat.size) {
            const toRead = Math.min(buf.length, stat.size - offset);
            const bytesRead = readSync(fd, buf, 0, toRead, offset);
            if (bytesRead <= 0) break;
            offset += bytesRead;
            const textChunk = carry + buf.toString("utf8", 0, bytesRead);
            const lines = textChunk.split(/\r?\n/);
            carry = lines.pop() ?? "";
            for (const line of lines) {
                if (line) ingestEventLine(cached, line);
            }
        }
        cached.byteOffset = offset;
        cached.partialLine = carry;
    } finally {
        closeSync(fd);
    }
    return cached;
}

function parseMilestoneProgress(seq: number, kind: string, textValue: string, recordedAt: number): WorkflowProgressItem {
    const prefix = "[workflow-milestone-v1]";
    if (textValue.startsWith(prefix)) {
        try {
            const parsed = JSON.parse(textValue.slice(prefix.length)) as Record<string, unknown>;
            return {
                seq,
                kind: "milestone",
                title: typeof parsed.title === "string" ? parsed.title : "Milestone",
                ...(typeof parsed.summary === "string" ? { summary: parsed.summary } : {}),
                ...(typeof parsed.outcome === "string" ? { outcome: parsed.outcome } : {}),
                prUrl: typeof parsed.prUrl === "string" ? parsed.prUrl : null,
                recordedAt,
            };
        } catch {
            // Fall through to plain log entry.
        }
    }
    return { seq, kind, title: textValue, recordedAt };
}

function buildTraceFallback(execution: Execution, requestedAgentId?: string): ExecutionTranscriptResponse {
    const trace = execution.trace || [];
    const usageAgents = execution.usage?.agents || [];
    const turns = trace.filter((t) => t.kind === "agent_turn");
    const tools = trace.filter((t) => t.kind === "tool_call");

    const agents: TranscriptAgentSummary[] = usageAgents.length > 0
        ? usageAgents.map((ag, idx) => {
            const occ = usageAgents.slice(0, idx).filter((p) => p.label === ag.label).length;
            const matchingTurns = turns.filter((t) => t.label === ag.label);
            const turn = matchingTurns[occ] || matchingTurns.at(-1) || null;
            return {
                agentId: `trace-agent-${idx}`,
                label: ag.label,
                agentType: turn?.role || "software-factory-worker",
                status: ag.status,
                model: ag.resolvedModel || ag.requestedModel || null,
                reasoningEffort: null,
                startedAtMs: null,
                completedAtMs: null,
                activeMs: ag.activeMs,
                nanoAiu: 0,
                messageCount: turn?.outputPreview ? 1 : 0,
                thoughtCount: 0,
                toolCallCount: idx === 0 ? tools.length : 0,
                totalTokens: 0,
            };
        })
        : turns.map((turn, idx) => ({
            agentId: `trace-turn-${idx}`,
            label: turn.label || turn.role || `Specialist #${idx + 1}`,
            agentType: turn.role || "software-factory-worker",
            status: turn.status,
            model: null,
            reasoningEffort: null,
            startedAtMs: null,
            completedAtMs: null,
            activeMs: turn.durationMs || 0,
            nanoAiu: 0,
            messageCount: turn.outputPreview ? 1 : 0,
            thoughtCount: 0,
            toolCallCount: idx === 0 ? tools.length : 0,
            totalTokens: 0,
        }));

    const selectedAgentId = agents.find((a) => a.agentId === requestedAgentId)?.agentId || agents[0]?.agentId || null;
    const selectedIdx = agents.findIndex((a) => a.agentId === selectedAgentId);
    const matchingTurn = selectedIdx >= 0
        ? (usageAgents.length > 0
            ? (() => {
                const targetLabel = agents[selectedIdx]!.label;
                const occ = agents.slice(0, selectedIdx).filter((p) => p.label === targetLabel).length;
                const matches = turns.filter((t) => t.label === targetLabel);
                return matches[occ] || matches.at(-1) || null;
            })()
            : turns[selectedIdx] || null)
        : null;

    const transcript: TranscriptMessageItem[] = [];
    if (matchingTurn?.detail) {
        transcript.push({
            id: `${matchingTurn.id}:prompt`,
            kind: "prompt",
            timestamp: matchingTurn.timestamp,
            title: "Specialist Objective",
            content: matchingTurn.detail,
        });
    }
    for (const tool of tools) {
        transcript.push({
            id: tool.id,
            kind: "tool_call",
            timestamp: tool.timestamp,
            title: (tool.tool || "read").replace(/^software_factory_/, ""),
            content: tool.target || "",
            toolName: tool.tool,
            toolTarget: tool.target,
            toolStatus: tool.status === "failed" ? "failed" : "completed",
            ...(tool.outputPreview ? { toolOutput: tool.outputPreview } : {}),
        });
    }
    if (matchingTurn?.outputPreview) {
        transcript.push({
            id: `${matchingTurn.id}:assistant`,
            kind: "assistant",
            timestamp: matchingTurn.timestamp,
            title: "Recorded Specialist Deliverable",
            content: matchingTurn.outputPreview,
        });
    }

    return {
        executionId: execution.id,
        runtimeId: execution.runtimeId,
        source: "trace_fallback",
        agents,
        progress: [],
        selectedAgentId,
        transcript,
    };
}

export function readExecutionTranscript(execution: Execution, requestedAgentId?: string): ExecutionTranscriptResponse {
    if (!execution.runtimeId || !execution.sessionId || !/^[a-zA-Z0-9_-]+$/.test(execution.sessionId)) {
        return buildTraceFallback(execution, requestedAgentId);
    }
    const dbPath = path.join(sessionStateRoot(), execution.sessionId, "session.db");
    if (!existsSync(dbPath)) {
        return buildTraceFallback(execution, requestedAgentId);
    }

    try {
        const db = new DatabaseSync(dbPath, { readOnly: true });
        let rawAgents: Array<{
            agent_id: string;
            label: string;
            agent_type: string;
            status: string;
            requested_model: string | null;
            resolved_model: string | null;
            started_at_ms: number | null;
            completed_at_ms: number | null;
            active_ms: number;
            resolved_reasoning_effort: string | null;
            agent_nano_aiu: number;
        }> = [];
        let rawProgress: Array<{ seq: number; kind: string; text: string; recorded_at: number }> = [];

        try {
            const tables = new Set(
                (db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as Array<{ name: string }>)
                    .map((r) => r.name),
            );
            if (tables.has("workflow_agents") && tables.has("workflow_runs")) {
                const hasObs = tables.has("workflow_agent_observability");
                const hasAttr = tables.has("workflow_credit_charge_attribution") && tables.has("workflow_credit_charges");
                rawAgents = db.prepare(`
                    SELECT wa.agent_id, wa.label, wa.agent_type, wa.status, wa.requested_model, wa.resolved_model,
                           wa.started_at_ms, wa.completed_at_ms, wa.active_ms,
                           ${hasObs ? "wao.resolved_reasoning_effort" : "NULL AS resolved_reasoning_effort"},
                           ${hasAttr ? "COALESCE(SUM(wcc.nano_aiu), 0)" : "0"} AS agent_nano_aiu
                    FROM workflow_agents wa
                    JOIN workflow_runs wr ON wr.run_id = wa.run_id
                    ${hasObs ? "LEFT JOIN workflow_agent_observability wao ON wao.run_id = wa.run_id AND wao.agent_id = wa.agent_id" : ""}
                    ${hasAttr ? "LEFT JOIN workflow_credit_charge_attribution wcca ON wcca.run_id = wa.run_id AND wcca.agent_id = wa.agent_id LEFT JOIN workflow_credit_charges wcc ON wcc.run_id = wcca.run_id AND wcc.charge_id = wcca.charge_id" : ""}
                    WHERE wr.root_run_id = ? OR wr.run_id = ?
                    GROUP BY wa.run_id, wa.agent_id
                    ORDER BY COALESCE(wa.started_at_ms, 0) ASC, wa.rowid ASC
                `).all(execution.runtimeId, execution.runtimeId) as typeof rawAgents;
            }
            if (!rawAgents.length && tables.has("factory_agents")) {
                rawAgents = db.prepare(`
                    SELECT agent_id, label, agent_type, status, requested_model, resolved_model,
                           started_at_ms, completed_at_ms, active_ms,
                           NULL AS resolved_reasoning_effort, 0 AS agent_nano_aiu
                    FROM factory_agents
                    WHERE run_id = ?
                    ORDER BY COALESCE(started_at_ms, 0) ASC, rowid ASC
                `).all(execution.runtimeId) as typeof rawAgents;
            }
            if (tables.has("workflow_progress")) {
                rawProgress = db.prepare(`
                    SELECT seq, kind, text, recorded_at
                    FROM workflow_progress
                    WHERE run_id = ?
                    ORDER BY seq ASC
                    LIMIT 200
                `).all(execution.runtimeId) as typeof rawProgress;
            }
        } finally {
            db.close();
        }

        if (!rawAgents.length && !rawProgress.length) {
            return buildTraceFallback(execution, requestedAgentId);
        }

        const eventsIdx = updateSessionEventsIndex(execution.sessionId);
        const agents: TranscriptAgentSummary[] = rawAgents.map((row) => {
            const bucket = eventsIdx?.byAgentId.get(row.agent_id);
            return {
                agentId: row.agent_id,
                label: row.label,
                agentType: row.agent_type,
                status: row.status,
                model: row.resolved_model || row.requested_model || bucket?.model || null,
                reasoningEffort: row.resolved_reasoning_effort || bucket?.reasoningEffort || null,
                startedAtMs: row.started_at_ms,
                completedAtMs: row.completed_at_ms,
                activeMs: row.active_ms,
                nanoAiu: Number(row.agent_nano_aiu || 0),
                messageCount: bucket?.messageCount ?? 0,
                thoughtCount: bucket?.thoughtCount ?? 0,
                toolCallCount: bucket?.toolCallCount ?? 0,
                totalTokens: bucket?.totalTokens ?? 0,
            };
        });

        const selectedAgentId = agents.find((a) => a.agentId === requestedAgentId)?.agentId || agents[0]?.agentId || null;
        const transcript = selectedAgentId && eventsIdx?.byAgentId.has(selectedAgentId)
            ? eventsIdx.byAgentId.get(selectedAgentId)!.items
            : [];

        return {
            executionId: execution.id,
            runtimeId: execution.runtimeId,
            source: "session_events",
            agents,
            progress: rawProgress.map((p) => parseMilestoneProgress(p.seq, p.kind, p.text, p.recorded_at)),
            selectedAgentId,
            transcript,
        };
    } catch {
        return buildTraceFallback(execution, requestedAgentId);
    }
}
