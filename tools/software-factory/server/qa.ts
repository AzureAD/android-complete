// Copyright (c) Microsoft Corporation. All rights reserved.

import type { CopilotClient, PermissionHandler, SessionConfig } from "@github/copilot-sdk";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { Application } from "../core/application.ts";
import { FactoryError } from "../core/contracts.ts";
import type { FactoryFeature } from "../core/lifecycle-contracts.ts";

type ArtifactRecord = FactoryFeature["factory"]["artifacts"][number];
import {
    parseQaRequest,
    type FeatureQaContext,
    type FeatureQaContextSummary,
    type FeatureQaEntry,
    type FeatureQaLiveStep,
    type FeatureQaResponse,
    type FeatureQaSessionInfo,
} from "../core/qa-contracts.ts";
import { projectSdkModule, requireRuntime } from "../core/runtime-health.ts";

const CANDIDATE_SUBREPOS = [
    "common",
    "msal",
    "broker",
    "adal",
    "authenticator",
    "design-docs",
    "1ES-Pipelines",
    "tools/software-factory",
] as const;

const QA_TIMEOUT_MS = 180_000;

const QA_SYSTEM_INSTRUCTIONS = [
    "You are the Software Factory Studio Advisor — a strictly read-only engineering & architecture copilot embedded inside the Software Factory dashboard.",
    "Your job is to help the engineer understand, stress-test, and verify requirements, architecture designs, independent reviewer findings, planned PBIs, and code implementations/PRs for this feature WITHOUT disrupting any active Factory workflow.",
    "",
    "Operating rules:",
    "1. Strictly read-only: never attempt to edit files, run shell commands, or mutate Factory/ADO/GitHub state. Use only read-only file/search tools (`view`, `read`, `glob`, `grep`, `rg`) when you need deeper code evidence beyond the Feature Context Pack.",
    "2. Evidence-first: ground claims in the Feature Context Pack and actual repository source files (`path:line` citations when referencing code).",
    "3. Concise & high-signal: lead with the direct answer, explain trade-offs or edge cases clearly, and avoid boilerplate filler.",
    "4. Actionable guidance: if the user spots a gap or asks how to change a design, PBI, or implementation, provide crisp, concrete wording they can apply to revision feedback or PR review comments.",
].join("\n");

export interface QaResponderTurnInput {
    featureId: string;
    sessionId: string;
    turnIndex: number;
    isNewSession: boolean;
    staleContext: boolean;
    question: string;
    context: FeatureQaContext | null;
    prompt: string;
    contextPack: string;
    contextSummary: FeatureQaContextSummary;
    priorEntries: FeatureQaEntry[];
    onDelta: (delta: string) => void;
    onStepStart: (toolCallId: string, toolName: string, target: string) => void;
    onStepComplete: (toolCallId: string, success: boolean) => void;
    onPhase: (phase: string) => void;
}

export type QaResponder = (input: QaResponderTurnInput) => Promise<string>;

interface StoredSessionRow {
    session_id: string;
    feature_id: string;
    context_fingerprint: string;
    turn_count: number;
    active: number;
    created_at: string;
    updated_at: string;
}

interface StoredEntryRow {
    id: string;
    feature_id: string;
    session_id: string;
    turn_index: number;
    question: string;
    context_json: string | null;
    status: "running" | "completed" | "error";
    answer: string;
    error: string | null;
    live_phase: string | null;
    live_steps_json: string;
    duration_ms: number | null;
    created_at: string;
    completed_at: string | null;
}

interface InFlightTurnState {
    entryId: string;
    answer: string;
    livePhase: string;
    stepsByCallId: Map<string, FeatureQaLiveStep>;
    steps: FeatureQaLiveStep[];
    promise: Promise<void>;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return value !== null && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

function asArray(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function truncateText(value: string, maxChars: number): string {
    const trimmed = value.trim();
    if (trimmed.length <= maxChars) return trimmed;
    return `${trimmed.slice(0, maxChars)}\n…[truncated — inspect source directly if needed]`;
}

function selectArtifact(artifacts: ArtifactRecord[], kind: string, preferredId?: string): ArtifactRecord | undefined {
    if (preferredId) {
        const exact = artifacts.find((entry) => entry.id === preferredId && entry.kind === kind);
        if (exact) return exact;
    }
    for (let index = artifacts.length - 1; index >= 0; index -= 1) {
        const candidate = artifacts[index];
        if (candidate?.kind === kind) return candidate;
    }
    return undefined;
}

function discoverReadableRepos(workspace: string): { names: string[]; extraRoots: string[] } {
    const names: string[] = [];
    const extraRoots: string[] = [];
    const seenRealpaths = new Set<string>();
    try {
        seenRealpaths.add(process.platform === "win32" ? realpathSync(workspace).toLowerCase() : realpathSync(workspace));
    } catch {
        // Ignore workspace realpath errors
    }
    for (const rel of CANDIDATE_SUBREPOS) {
        const candidate = path.join(workspace, ...rel.split("/"));
        try {
            if (!existsSync(candidate) || !statSync(candidate).isDirectory()) continue;
            names.push(rel);
            const resolved = realpathSync(candidate);
            const normalized = process.platform === "win32" ? resolved.toLowerCase() : resolved;
            if (!seenRealpaths.has(normalized)) {
                seenRealpaths.add(normalized);
                extraRoots.push(resolved);
            }
        } catch {
            // Skip inaccessible directories
        }
    }
    return { names, extraRoots };
}

export function buildFeatureContextPack(feature: FactoryFeature, workspace: string): {
    packText: string;
    fingerprint: string;
    summary: FeatureQaContextSummary;
} {
    const { names: readableRepos } = discoverReadableRepos(workspace);
    const artifacts = feature.factory.artifacts || [];
    const currentIds = asRecord((feature as unknown as Record<string, unknown>).currentArtifacts);
    const designArtifact = selectArtifact(artifacts, "design", typeof currentIds?.design === "string" ? currentIds.design : undefined);
    const planArtifact = selectArtifact(artifacts, "plan", typeof currentIds?.plan === "string" ? currentIds.plan : undefined);
    const implementationArtifact = selectArtifact(
        artifacts,
        "implementation",
        typeof currentIds?.implementation === "string" ? currentIds.implementation : undefined,
    );
    const monitorArtifact = selectArtifact(artifacts, "monitor", typeof currentIds?.monitor === "string" ? currentIds.monitor : undefined);
    const attemptArtifacts = artifacts.filter((entry) => entry.kind === "implementation_attempt");

    const lines: string[] = [];
    lines.push(`# Software Factory Feature Context Pack`);
    lines.push(`- **Feature Title:** ${feature.current.title}`);
    lines.push(`- **Feature ID:** \`${feature.id}\` (Revision \`${feature.current.id}\`, #${feature.current.number})`);
    if (feature.shared?.target?.workItemId) {
        const { organization, project, workItemId } = feature.shared.target;
        const adoUrl = `https://dev.azure.com/${encodeURIComponent(organization)}/${encodeURIComponent(project)}/_workitems/edit/${workItemId}`;
        lines.push(`- **ADO Feature:** #${workItemId} (${adoUrl})`);
    }
    lines.push(`- **Lifecycle Status:** \`${feature.lifecycleStatus}\``);
    if (feature.currentProgress) {
        lines.push(`- **Current Progress:** stage=\`${feature.currentProgress.stage}\`, status=\`${feature.currentProgress.status}\`${feature.currentProgress.summary ? ` — ${feature.currentProgress.summary}` : ""}`);
    }
    lines.push(`- **Workspace Readable Checkouts:** ${readableRepos.length ? readableRepos.map((r) => `\`${r}\``).join(", ") : "`workspace root`"}`);

    // Stage 0: Requirements & Scope
    lines.push("", "## 1. Approved Scope & Acceptance Criteria");
    if (feature.current.description?.trim()) {
        lines.push("### Problem / Brief", truncateText(feature.current.description, 4_000));
    }
    const requirements = feature.current.requirements || [];
    if (requirements.length) {
        lines.push("### Acceptance Criteria");
        for (const req of requirements) {
            lines.push(`- **[${req.id}]** ${req.text}`);
        }
    }
    if (feature.current.designTasks?.length) {
        lines.push("### Engineering Design Tasks");
        for (const task of feature.current.designTasks) {
            lines.push(`- ${task}`);
        }
    }
    if (feature.current.blockingUnknowns?.length) {
        lines.push("### Initial Blocking Unknowns");
        for (const unknown of feature.current.blockingUnknowns) {
            lines.push(`- ${unknown}`);
        }
    }

    // Stage 2: Design
    let designReviewRounds = 0;
    if (designArtifact) {
        const content = asRecord(designArtifact.content) ?? {};
        const innerDesign = asRecord(content.design) ?? content;
        const sections = asRecord(innerDesign.sections) ?? asRecord(content.sections) ?? {};
        const reviews = asArray(content.reviews);
        designReviewRounds = reviews.length;
        const recLabel = typeof content.recommendation === "string" ? `, recommendation=\`${content.recommendation}\`` : "";
        lines.push("", `## 2. Technical Design (Artifact \`${designArtifact.id}\`, ${designReviewRounds} review round(s)${recLabel})`);
        if (typeof innerDesign.summary === "string" && innerDesign.summary.trim()) {
            lines.push(truncateText(innerDesign.summary, 2_000));
        } else if (typeof content.summary === "string" && content.summary.trim()) {
            lines.push(truncateText(content.summary, 2_000));
        }
        for (const [sectionKey, sectionVal] of Object.entries(sections)) {
            if (typeof sectionVal === "string" && sectionVal.trim()) {
                lines.push(`### Design Section: ${sectionKey}`, truncateText(sectionVal, 3_600));
            }
        }
        const openDecisions = asArray(innerDesign.openDecisions ?? content.openDecisions).filter((v): v is string => typeof v === "string" && Boolean(v.trim()));
        if (openDecisions.length) {
            lines.push("### Open Design Decisions", ...openDecisions.map((d) => `- ${d}`));
        }
        const assumptions = asArray(innerDesign.assumptions ?? content.assumptions).filter((v): v is string => typeof v === "string" && Boolean(v.trim()));
        if (assumptions.length) {
            lines.push("### Design Assumptions", ...assumptions.map((a) => `- ${a}`));
        }
        const evidence = asRecord(content.evidence);
        const references = asArray(evidence?.references ?? content.references);
        if (references.length) {
            lines.push("### Key Source Citations");
            for (const ref of references.slice(0, 25)) {
                const r = asRecord(ref);
                if (!r) continue;
                const loc = [r.repository, r.path, r.line ? `:${String(r.line)}` : ""].filter(Boolean).join("/");
                const note = typeof r.note === "string" ? r.note : typeof r.reason === "string" ? r.reason : "";
                lines.push(`- \`${loc || JSON.stringify(r)}\`${note ? ` — ${note}` : ""}`);
            }
        }
        if (reviews.length) {
            lines.push("### Independent Design Review History");
            reviews.forEach((rawRound, idx) => {
                const roundObj = asRecord(rawRound);
                const innerReview = asRecord(roundObj?.review) ?? roundObj;
                const status = typeof roundObj?.status === "string" ? roundObj.status : typeof innerReview?.status === "string" ? innerReview.status : "reviewed";
                const findings = asArray(innerReview?.findings);
                lines.push(`- **Round ${idx + 1}** (\`${status}\`, ${findings.length} finding(s))`);
                for (const rawFinding of findings.slice(0, 10)) {
                    const f = asRecord(rawFinding);
                    if (!f) continue;
                    const sev = typeof f.severity === "string" ? f.severity.toUpperCase() : "NOTE";
                    const title = typeof f.title === "string" ? f.title : typeof f.section === "string" ? f.section : "Finding";
                    const detail = typeof f.detail === "string" ? f.detail : typeof f.message === "string" ? f.message : "";
                    lines.push(`  - [${sev}] **${title}**: ${truncateText(detail, 450)}`);
                }
            });
        }
    }

    // Stage 3: Implementation Plan & PBIs
    let plannedPbiCount = 0;
    if (planArtifact) {
        const content = asRecord(planArtifact.content) ?? {};
        const items = asArray(content.items);
        plannedPbiCount = items.length;
        lines.push("", `## 3. Implementation Plan & PBIs (Artifact \`${planArtifact.id}\`, ${plannedPbiCount} PBI(s))`);
        if (typeof content.summary === "string" && content.summary.trim()) {
            lines.push(truncateText(content.summary, 2_000));
        }
        for (const rawItem of items) {
            const item = asRecord(rawItem);
            if (!item) continue;
            const id = typeof item.id === "string" ? item.id : "PBI";
            const title = typeof item.title === "string" ? item.title : "";
            const repo = typeof item.repository === "string" ? item.repository : "unknown";
            const reqIds = asArray(item.requirementIds).filter((v): v is string => typeof v === "string");
            const deps = asArray(item.dependsOn).filter((v): v is string => typeof v === "string");
            const files = asArray(item.files).filter((v): v is string => typeof v === "string");
            const tests = asArray(item.tests).filter((v): v is string => typeof v === "string");
            const criteria = asArray(item.acceptanceCriteria).filter((v): v is string => typeof v === "string");
            lines.push(`### ${id}: ${title} (Repo: \`${repo}\`)`);
            if (reqIds.length) lines.push(`- **Covers Requirements:** ${reqIds.map((r) => `[${r}]`).join(", ")}`);
            if (deps.length) lines.push(`- **Depends On:** ${deps.join(", ")}`);
            if (files.length) lines.push(`- **Target Files:** ${files.map((f) => `\`${f}\``).join(", ")}`);
            if (tests.length) lines.push(`- **Planned Tests:** ${tests.map((t) => `\`${t}\``).join(", ")}`);
            if (criteria.length) {
                lines.push(`- **PBI Acceptance Criteria:**`, ...criteria.map((c) => `  - ${c}`));
            }
            if (typeof item.description === "string" && item.description.trim()) {
                lines.push(truncateText(item.description, 2_200));
            }
        }
    }

    // Stage 4–6: Backlog Work Items, Coding Implementation & PRs
    const operations = feature.factory.operations || [];
    const workItemOps = operations.filter((op) => op.kind === "work_item" && op.status === "completed");
    const dispatchOps = operations.filter((op) => op.kind === "dispatch" && op.status === "completed");
    const prBindings = asArray((feature.factory as unknown as Record<string, unknown>).prBindings);
    const implByItem = new Map<string, Record<string, unknown>>();
    if (implementationArtifact) {
        const implContent = asRecord(implementationArtifact.content);
        for (const entry of asArray(implContent?.items)) {
            const rec = asRecord(entry);
            const key = typeof rec?.itemId === "string" ? rec.itemId : typeof rec?.id === "string" ? rec.id : undefined;
            if (rec && key) implByItem.set(key, rec);
        }
    }
    for (const attempt of attemptArtifacts) {
        const rec = asRecord(attempt.content);
        const key = typeof rec?.itemId === "string" ? rec.itemId : typeof rec?.id === "string" ? rec.id : undefined;
        if (rec && key) implByItem.set(key, rec);
    }

    const prUrls = new Set<string>();
    for (const op of dispatchOps) {
        const r = asRecord(op.receipt);
        if (typeof r?.url === "string" && r.url) prUrls.add(r.url);
    }
    for (const binding of prBindings) {
        const b = asRecord(binding);
        if (typeof b?.url === "string" && b.url) prUrls.add(b.url);
    }
    for (const impl of implByItem.values()) {
        const receipt = asRecord(impl.receipt);
        if (typeof receipt?.url === "string" && receipt.url) prUrls.add(receipt.url);
        if (typeof impl.prUrl === "string" && impl.prUrl) prUrls.add(impl.prUrl);
    }

    if (workItemOps.length || implByItem.size || prBindings.length || monitorArtifact) {
        lines.push("", "## 4. Backlog, Coding Implementation & Pull Requests");
        if (workItemOps.length) {
            lines.push("### Provisioned ADO Child Work Items");
            for (const op of workItemOps) {
                const r = asRecord(op.receipt) ?? {};
                lines.push(`- **${String(r.key ?? op.itemId)}** → ADO #${String(r.adoId ?? "?")}${typeof r.title === "string" ? ` (${r.title})` : ""}${typeof r.url === "string" ? ` — ${r.url}` : ""}`);
            }
        }
        for (const [itemId, impl] of implByItem.entries()) {
            const receipt = asRecord(impl.receipt);
            const branch = typeof impl.branch === "string" ? impl.branch : typeof receipt?.branch === "string" ? receipt.branch : "";
            const status = typeof impl.status === "string" ? impl.status : "recorded";
            const observerStatus = typeof impl.observerStatus === "string" ? impl.observerStatus : "";
            const prUrl = typeof receipt?.url === "string" ? receipt.url : typeof impl.prUrl === "string" ? impl.prUrl : "";
            const headSha = typeof receipt?.headSha === "string" ? receipt.headSha : "";
            lines.push(`### Implementation Record: ${itemId} (\`${status}\`${observerStatus ? `, observer=\`${observerStatus}\`` : ""})`);
            if (branch) lines.push(`- **Branch:** \`${branch}\`${headSha ? ` @ \`${headSha}\`` : ""}`);
            if (prUrl) lines.push(`- **Pull Request:** ${prUrl}`);
            const changedFiles = asArray(receipt?.files ?? impl.files);
            if (changedFiles.length) {
                lines.push("- **Changed Files:**");
                for (const rawFile of changedFiles.slice(0, 25)) {
                    if (typeof rawFile === "string") {
                        lines.push(`  - \`${rawFile}\``);
                    } else {
                        const f = asRecord(rawFile);
                        if (f && typeof f.path === "string") {
                            lines.push(`  - \`${f.path}\`${typeof f.action === "string" ? ` (${f.action})` : ""}${typeof f.summary === "string" && f.summary ? ` — ${truncateText(f.summary, 280)}` : ""}`);
                        }
                    }
                }
            }
        }
        if (monitorArtifact) {
            const mon = asRecord(monitorArtifact.content) ?? {};
            const prs = asArray(mon.prs);
            if (prs.length) {
                lines.push("### Latest Stage 6 Monitor Snapshot");
                for (const rawPr of prs) {
                    const pr = asRecord(rawPr);
                    if (!pr) continue;
                    if (typeof pr.url === "string" && pr.url) prUrls.add(pr.url);
                    lines.push(`- **${String(pr.key ?? "PR")}** #${String(pr.number ?? "?")}: status=\`${String(pr.status ?? "?")}\`, checks=\`${String(pr.checks ?? "?")}\`${typeof pr.url === "string" ? ` (${pr.url})` : ""}`);
                }
            }
        }
    }

    const packText = lines.join("\n");
    const fingerprint = createHash("sha256")
        .update(JSON.stringify({
            revisionId: feature.current.id,
            lifecycleStatus: feature.lifecycleStatus,
            artifactIds: artifacts.map((a) => `${a.kind}:${a.id}`),
            prCount: prUrls.size,
        }))
        .digest("hex")
        .slice(0, 16);

    return {
        packText,
        fingerprint,
        summary: {
            requirementsCount: requirements.length,
            hasDesign: Boolean(designArtifact),
            designReviewRounds,
            plannedPbiCount,
            implementedPbiCount: implByItem.size,
            prCount: prUrls.size,
            readableRepos,
        },
    };
}

function formatPinnedContextBlock(context: FeatureQaContext | null): string {
    if (!context) return "";
    const lines = [
        "### Pinned Studio Context",
        `- **Kind:** \`${context.kind}\``,
        `- **Subject:** ${context.title}`,
    ];
    if (context.subtitle) lines.push(`- **Detail:** ${context.subtitle}`);
    if (context.path) lines.push(`- **Path / Reference:** \`${context.path}\``);
    if (context.snippet) {
        lines.push("", "```markdown", context.snippet, "```");
    }
    return lines.join("\n");
}

function summarizeToolTarget(toolName: string, rawArgs: unknown): string {
    const args = asRecord(rawArgs);
    if (!args) return toolName;
    for (const key of ["path", "filePath", "pattern", "query", "glob"]) {
        const val = args[key];
        if (typeof val === "string" && val.trim()) {
            const extra = key === "pattern" && typeof args.paths === "string" ? ` in ${args.paths}` : "";
            return `${val.trim()}${extra}`;
        }
    }
    if (Array.isArray(args.paths) && typeof args.paths[0] === "string") {
        return args.paths[0];
    }
    return toolName;
}

function sanitizedCliEnv(source: NodeJS.ProcessEnv): Record<string, string> {
    const env: Record<string, string> = {};
    for (const [name, value] of Object.entries(source)) {
        if (value !== undefined && !["SESSION_ID", "COPILOT_SDK_PATH", "COPILOT_EXTENSION_PARENT_PID"].includes(name.toUpperCase())) {
            env[name] = value;
        }
    }
    env.GIT_TERMINAL_PROMPT = "0";
    env.GCM_INTERACTIVE = "never";
    return env;
}

export class StudioQaService {
    private readonly application: Application;
    private readonly db: DatabaseSync;
    private readonly customResponder?: QaResponder;
    private readonly inFlight = new Map<string, InFlightTurnState>();
    private clientPromise?: Promise<CopilotClient>;
    private closed = false;

    constructor(options: { application: Application; databasePath?: string; responder?: QaResponder }) {
        this.application = options.application;
        this.customResponder = options.responder;
        const dbPath = options.databasePath ?? path.join(path.dirname(options.application.registry.databasePath), "studio-qa.sqlite");
        this.db = new DatabaseSync(dbPath);
        this.db.exec(`
            PRAGMA foreign_keys = ON;
            PRAGMA busy_timeout = 5000;
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS qa_sessions (
                session_id TEXT PRIMARY KEY,
                feature_id TEXT NOT NULL,
                context_fingerprint TEXT NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_qa_sessions_feature ON qa_sessions(feature_id, active);
            CREATE TABLE IF NOT EXISTS qa_entries (
                id TEXT PRIMARY KEY,
                feature_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                question TEXT NOT NULL,
                context_json TEXT,
                status TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                error TEXT,
                live_phase TEXT,
                live_steps_json TEXT NOT NULL DEFAULT '[]',
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_qa_entries_feature ON qa_entries(feature_id, created_at);
        `);
        const now = new Date().toISOString();
        this.db.prepare(`
            UPDATE qa_entries
               SET status = 'error',
                   error = 'Studio server restarted before this answer finished. Ask your question again to continue.',
                   live_phase = NULL,
                   completed_at = ?
             WHERE status = 'running'
        `).run(now);
    }

    getFeatureQa(featureId: string): FeatureQaResponse {
        const feature = this.application.cachedFeature(featureId);
        const workspace = this.application.registry.project.workspace;
        const { fingerprint, summary } = buildFeatureContextPack(feature, workspace);
        const sessionRow = this.db.prepare(`
            SELECT session_id, feature_id, context_fingerprint, turn_count, active, created_at, updated_at
              FROM qa_sessions
             WHERE feature_id = ? AND active = 1
             ORDER BY updated_at DESC
             LIMIT 1
        `).get(featureId) as unknown as StoredSessionRow | undefined;

        const rows = this.db.prepare(`
            SELECT id, feature_id, session_id, turn_index, question, context_json, status, answer, error,
                   live_phase, live_steps_json, duration_ms, created_at, completed_at
              FROM qa_entries
             WHERE feature_id = ?
             ORDER BY created_at ASC, rowid ASC
             LIMIT 100
        `).all(featureId) as unknown as StoredEntryRow[];

        const running = this.inFlight.get(featureId);
        const entries: FeatureQaEntry[] = rows.map((row) => {
            let parsedContext: FeatureQaContext | null = null;
            if (row.context_json) {
                try { parsedContext = JSON.parse(row.context_json) as FeatureQaContext; } catch { parsedContext = null; }
            }
            let parsedSteps: FeatureQaLiveStep[] = [];
            try { parsedSteps = JSON.parse(row.live_steps_json) as FeatureQaLiveStep[]; } catch { parsedSteps = []; }

            if (running && running.entryId === row.id && row.status === "running") {
                return {
                    id: row.id,
                    featureId: row.feature_id,
                    sessionId: row.session_id,
                    turnIndex: row.turn_index,
                    question: row.question,
                    context: parsedContext,
                    status: "running",
                    answer: running.answer,
                    error: null,
                    livePhase: running.livePhase,
                    liveSteps: running.steps.map((step) => ({ ...step })),
                    durationMs: Date.now() - Date.parse(row.created_at),
                    createdAt: row.created_at,
                    completedAt: null,
                };
            }

            return {
                id: row.id,
                featureId: row.feature_id,
                sessionId: row.session_id,
                turnIndex: row.turn_index,
                question: row.question,
                context: parsedContext,
                status: row.status,
                answer: row.answer,
                error: row.error,
                livePhase: row.live_phase,
                liveSteps: parsedSteps,
                durationMs: row.duration_ms,
                createdAt: row.created_at,
                completedAt: row.completed_at,
            };
        });

        const activeSession: FeatureQaSessionInfo | null = sessionRow
            ? {
                sessionId: sessionRow.session_id,
                turnCount: sessionRow.turn_count,
                contextFingerprint: sessionRow.context_fingerprint,
                staleContext: sessionRow.context_fingerprint !== fingerprint,
                createdAt: sessionRow.created_at,
                updatedAt: sessionRow.updated_at,
            }
            : null;

        return {
            featureId,
            activeSession,
            contextSummary: summary,
            entries,
        };
    }

    handleRequest(featureId: string, body: unknown): { statusCode: 200 | 202; payload: FeatureQaResponse } {
        if (this.closed) {
            throw new FactoryError("server_closing", "Studio Q&A service is shutting down.", 503);
        }
        const feature = this.application.cachedFeature(featureId);
        const parsed = parseQaRequest(body);

        if (this.inFlight.has(featureId)) {
            throw new FactoryError("qa_busy", "Copilot is still answering the current question on this feature.", 409);
        }

        if (parsed.action === "reset") {
            const now = new Date().toISOString();
            this.db.prepare(`UPDATE qa_sessions SET active = 0, updated_at = ? WHERE feature_id = ? AND active = 1`).run(now, featureId);
            return { statusCode: 200, payload: this.getFeatureQa(featureId) };
        }

        const workspace = this.application.registry.project.workspace;
        const { packText, fingerprint, summary } = buildFeatureContextPack(feature, workspace);
        const now = new Date().toISOString();

        if (parsed.resetSession) {
            this.db.prepare(`UPDATE qa_sessions SET active = 0, updated_at = ? WHERE feature_id = ? AND active = 1`).run(now, featureId);
        }

        let sessionRow = this.db.prepare(`
            SELECT session_id, feature_id, context_fingerprint, turn_count, active, created_at, updated_at
              FROM qa_sessions
             WHERE feature_id = ? AND active = 1
             ORDER BY updated_at DESC
             LIMIT 1
        `).get(featureId) as unknown as StoredSessionRow | undefined;

        const isNewSession = !sessionRow || sessionRow.turn_count === 0;
        const staleContext = Boolean(sessionRow && sessionRow.context_fingerprint !== fingerprint);

        if (!sessionRow) {
            const sessionId = `sf-qa-${randomUUID()}`;
            this.db.prepare(`
                INSERT INTO qa_sessions (session_id, feature_id, context_fingerprint, turn_count, active, created_at, updated_at)
                VALUES (?, ?, ?, 0, 1, ?, ?)
            `).run(sessionId, featureId, fingerprint, now, now);
            sessionRow = {
                session_id: sessionId,
                feature_id: featureId,
                context_fingerprint: fingerprint,
                turn_count: 0,
                active: 1,
                created_at: now,
                updated_at: now,
            };
        }

        const priorSnapshot = this.getFeatureQa(featureId).entries.filter((entry) => entry.status === "completed");
        const turnIndex = sessionRow.turn_count + 1;
        const entryId = randomUUID();
        const initialPhase = isNewSession ? "Loading feature artifacts & context…" : "Resuming Q&A session…";

        this.db.prepare(`
            INSERT INTO qa_entries (
                id, feature_id, session_id, turn_index, question, context_json, status, answer, error, live_phase, live_steps_json, duration_ms, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', '', NULL, ?, '[]', NULL, ?, NULL)
        `).run(
            entryId,
            featureId,
            sessionRow.session_id,
            turnIndex,
            parsed.question,
            parsed.context ? JSON.stringify(parsed.context) : null,
            initialPhase,
            now,
        );

        const promptParts: string[] = [];
        if (isNewSession) {
            promptParts.push(
                "Below is the current Software Factory Feature Context Pack. Keep it in working memory for this session and inspect repository files directly with read-only tools whenever deeper source verification helps.",
                "",
                packText,
            );
        } else if (staleContext) {
            promptParts.push(
                "[Updated Feature Context Snapshot — Factory artifacts or lifecycle state advanced since the previous turn]",
                "",
                packText,
            );
        }
        const pinnedBlock = formatPinnedContextBlock(parsed.context);
        if (pinnedBlock) {
            promptParts.push("", pinnedBlock);
        }
        promptParts.push("", "### Engineer Question", parsed.question);
        const fullPrompt = promptParts.join("\n");

        const liveState: InFlightTurnState = {
            entryId,
            answer: "",
            livePhase: initialPhase,
            stepsByCallId: new Map(),
            steps: [],
            promise: Promise.resolve(),
        };

        const startedMs = Date.now();
        const sessionId = sessionRow.session_id;
        liveState.promise = this.executeTurn({
            entryId,
            featureId,
            sessionId,
            turnIndex,
            isNewSession,
            staleContext,
            fingerprint,
            question: parsed.question,
            context: parsed.context,
            prompt: fullPrompt,
            contextPack: packText,
            contextSummary: summary,
            priorEntries: priorSnapshot,
            liveState,
            startedMs,
        });
        this.inFlight.set(featureId, liveState);

        return { statusCode: 202, payload: this.getFeatureQa(featureId) };
    }

    private async executeTurn(params: {
        entryId: string;
        featureId: string;
        sessionId: string;
        turnIndex: number;
        isNewSession: boolean;
        staleContext: boolean;
        fingerprint: string;
        question: string;
        context: FeatureQaContext | null;
        prompt: string;
        contextPack: string;
        contextSummary: FeatureQaContextSummary;
        priorEntries: FeatureQaEntry[];
        liveState: InFlightTurnState;
        startedMs: number;
    }): Promise<void> {
        const { liveState } = params;
        const turnInput: QaResponderTurnInput = {
            featureId: params.featureId,
            sessionId: params.sessionId,
            turnIndex: params.turnIndex,
            isNewSession: params.isNewSession,
            staleContext: params.staleContext,
            question: params.question,
            context: params.context,
            prompt: params.prompt,
            contextPack: params.contextPack,
            contextSummary: params.contextSummary,
            priorEntries: params.priorEntries,
            onDelta: (delta) => {
                if (!delta) return;
                liveState.answer += delta;
                liveState.livePhase = "Drafting answer…";
            },
            onStepStart: (toolCallId, toolName, target) => {
                const step: FeatureQaLiveStep = {
                    toolName,
                    target,
                    status: "running",
                    timestamp: new Date().toISOString(),
                };
                liveState.stepsByCallId.set(toolCallId, step);
                liveState.steps.push(step);
                liveState.livePhase = `Inspecting ${target}…`;
            },
            onStepComplete: (toolCallId, success) => {
                const existing = liveState.stepsByCallId.get(toolCallId);
                if (existing) {
                    existing.status = success ? "completed" : "failed";
                }
            },
            onPhase: (phase) => {
                liveState.livePhase = phase;
            },
        };

        try {
            const responder = this.customResponder ?? ((input) => this.runSdkResponder(input));
            const finalText = (await responder(turnInput)).trim() || liveState.answer.trim();
            if (!finalText) {
                throw new Error("Copilot returned an empty response.");
            }
            if (!this.closed) {
                const completedAt = new Date().toISOString();
                const durationMs = Math.max(1, Date.now() - params.startedMs);
                this.db.prepare(`
                    UPDATE qa_entries
                       SET status = 'completed',
                           answer = ?,
                           error = NULL,
                           live_phase = NULL,
                           live_steps_json = ?,
                           duration_ms = ?,
                           completed_at = ?
                     WHERE id = ?
                `).run(finalText, JSON.stringify(liveState.steps), durationMs, completedAt, params.entryId);
                this.db.prepare(`
                    UPDATE qa_sessions
                       SET turn_count = ?,
                           context_fingerprint = ?,
                           updated_at = ?
                     WHERE session_id = ?
                `).run(params.turnIndex, params.fingerprint, completedAt, params.sessionId);
            }
        } catch (error: unknown) {
            if (!this.closed) {
                const completedAt = new Date().toISOString();
                const durationMs = Math.max(1, Date.now() - params.startedMs);
                const message = error instanceof Error ? error.message : String(error);
                this.db.prepare(`
                    UPDATE qa_entries
                       SET status = 'error',
                           answer = ?,
                           error = ?,
                           live_phase = NULL,
                           live_steps_json = ?,
                           duration_ms = ?,
                           completed_at = ?
                     WHERE id = ?
                `).run(liveState.answer, message, JSON.stringify(liveState.steps), durationMs, completedAt, params.entryId);
            }
        } finally {
            this.inFlight.delete(params.featureId);
        }
    }

    private async ensureClient(): Promise<CopilotClient> {
        if (!this.clientPromise) {
            this.clientPromise = (async () => {
                const runtime = requireRuntime();
                const { CopilotClient: SdkClient, RuntimeConnection }: typeof import("@github/copilot-sdk") = await import(projectSdkModule("."));
                const workspace = this.application.registry.project.workspace;
                return new SdkClient({
                    connection: RuntimeConnection.forStdio({
                        path: runtime.runtimeExecutable,
                        env: sanitizedCliEnv(process.env),
                    }),
                    workingDirectory: workspace,
                    logLevel: "error",
                });
            })().catch((error) => {
                this.clientPromise = undefined;
                throw error;
            });
        }
        return this.clientPromise;
    }

    private readOnlySessionConfig(workspace: string, extraRoots: string[]): SessionConfig {
        const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
        const roots = [workspace, ...extraRoots].map((entry) => normalize(realpathSync(entry)));
        const onPermissionRequest: PermissionHandler = (request) => {
            if (request.kind === "read" && !request.managedApprovalRequired && !request.requestSandboxBypass) {
                try {
                    const candidate = typeof request.path === "string" && request.path.trim() ? request.path : workspace;
                    const resolved = normalize(realpathSync(path.resolve(workspace, candidate)));
                    if (roots.some((root) => resolved === root || resolved.startsWith(root + path.sep))) {
                        return { kind: "approve-once" };
                    }
                } catch {
                    return { kind: "reject", feedback: "Requested path could not be resolved inside the workspace." };
                }
            }
            return {
                kind: "reject",
                feedback: "Studio Q&A is strictly read-only and cannot modify files, execute shell commands, or mutate Software Factory state.",
            };
        };

        return {
            clientName: "software-factory-studio-qa",
            workingDirectory: workspace,
            trustWorkingDirectory: "session",
            enableConfigDiscovery: true,
            enableFileHooks: false,
            requestExtensions: false,
            availableTools: ["builtin:view", "builtin:read", "builtin:glob", "builtin:grep", "builtin:rg"],
            excludedTools: [
                "mcp:*",
                "custom:*",
                "builtin:shell",
                "builtin:powershell",
                "builtin:bash",
                "builtin:write",
                "builtin:edit",
                "builtin:create",
                "builtin:apply_patch",
                "builtin:task",
            ],
            additionalDirectories: extraRoots,
            systemMessage: {
                mode: "append",
                content: QA_SYSTEM_INSTRUCTIONS,
            },
            onPermissionRequest,
        };
    }

    private async runSdkResponder(input: QaResponderTurnInput): Promise<string> {
        const workspace = this.application.registry.project.workspace;
        const { extraRoots } = discoverReadableRepos(workspace);
        const config = this.readOnlySessionConfig(workspace, extraRoots);
        const client = await this.ensureClient();

        let session: Awaited<ReturnType<CopilotClient["createSession"]>>;
        let effectivePrompt = input.prompt;

        if (input.isNewSession) {
            input.onPhase("Starting read-only Copilot session…");
            session = await client.createSession({
                sessionId: input.sessionId,
                ...config,
            });
        } else {
            input.onPhase("Resuming Copilot Q&A session…");
            try {
                session = await client.resumeSession(input.sessionId, config);
            } catch {
                // Session state expired or CLI restarted — seamlessly rebuild with prior turn summary + full context pack
                const fallbackId = `sf-qa-${randomUUID()}`;
                this.db.prepare(`UPDATE qa_sessions SET session_id = ? WHERE session_id = ?`).run(fallbackId, input.sessionId);
                session = await client.createSession({
                    sessionId: fallbackId,
                    ...config,
                });
                const historyLines = input.priorEntries.slice(-6).flatMap((entry) => [
                    `**Prior Q (${entry.turnIndex}):** ${entry.question}`,
                    `**Prior A (${entry.turnIndex}):** ${truncateText(entry.answer, 1_500)}`,
                    "",
                ]);
                effectivePrompt = [
                    "Below is the current Software Factory Feature Context Pack and recent Q&A history from this session.",
                    "",
                    input.contextPack,
                    ...(historyLines.length ? ["", "## Prior Q&A History", ...historyLines] : []),
                    ...(input.context ? ["", formatPinnedContextBlock(input.context)] : []),
                    "",
                    "### Engineer Question",
                    input.question,
                ].join("\n");
            }
        }

        const unsubscribe = session.on((event) => {
            if (event.type === "assistant.message_delta") {
                input.onDelta(event.data.deltaContent);
            } else if (event.type === "tool.execution_start") {
                const target = summarizeToolTarget(event.data.toolName, event.data.arguments);
                input.onStepStart(event.data.toolCallId, event.data.toolName, target);
            } else if (event.type === "tool.execution_complete") {
                input.onStepComplete(event.data.toolCallId, event.data.success);
            }
        });

        try {
            input.onPhase("Thinking…");
            const result = await session.sendAndWait({ prompt: effectivePrompt }, QA_TIMEOUT_MS);
            return result?.data.content ?? "";
        } finally {
            unsubscribe();
            await session.disconnect().catch(() => undefined);
        }
    }

    async close(): Promise<void> {
        this.closed = true;
        const pending = [...this.inFlight.values()].map((entry) => entry.promise.catch(() => undefined));
        if (pending.length) {
            await Promise.race([
                Promise.allSettled(pending),
                new Promise<void>((resolve) => setTimeout(resolve, 2_000)),
            ]);
        }
        if (this.clientPromise) {
            const client = await this.clientPromise.catch(() => undefined);
            if (client) {
                await client.stop().catch(() => client.forceStop().catch(() => undefined));
            }
        }
        try {
            this.db.close();
        } catch {
            // Ignore double-close
        }
    }
}
