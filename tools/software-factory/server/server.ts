// Copyright (c) Microsoft Corporation. All rights reserved.

import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import http from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { Application } from "../core/application.ts";
import { FactoryError, MAX_COMMAND_BYTES, type Actor } from "../core/contracts.ts";
import { runtimeReport } from "../core/doctor.ts";
import type { ExecutionTraceEntry, FactoryFeature, Json } from "../core/lifecycle-contracts.ts";

function hydrateStudioFeature(feature: FactoryFeature): FactoryFeature {
    const targetExecs = feature.factory.executions.filter((e) => (e.kind === "plan" || e.kind === "implement") && e.runtimeId && e.sessionId);
    if (!targetExecs.length) return feature;
    const cloned = structuredClone(feature);
    for (const exec of cloned.factory.executions) {
        if ((exec.kind !== "plan" && exec.kind !== "implement") || !exec.runtimeId || !exec.sessionId) continue;
        if (!/^[a-zA-Z0-9_-]+$/.test(exec.sessionId)) continue;
        const sessionDbPath = path.join(os.homedir(), ".copilot", "session-state", exec.sessionId, "session.db");
        if (!existsSync(sessionDbPath)) continue;
        try {
            const db = new DatabaseSync(sessionDbPath, { readOnly: true });
            let rows: { journal_key: string; result_json: string }[] = [];
            try {
                rows = db.prepare("SELECT journal_key, result_json FROM workflow_journal WHERE root_run_id = ? ORDER BY rowid")
                    .all(exec.runtimeId) as { journal_key: string; result_json: string }[];
            } finally {
                db.close();
            }
            if (!rows.length) continue;
            const deduped: string[] = [];
            for (const row of rows) {
                if (typeof row.result_json === "string" && deduped.at(-1) !== row.result_json) {
                    deduped.push(row.result_json);
                }
            }
            if (exec.kind === "implement") {
                const syntheticImplTurns: ExecutionTraceEntry[] = [];
                const roundState = new Map<number, {
                    targetId: string;
                    summaries: string[];
                    filesByPath: Map<string, { path: string; action: string; summary: string }>;
                    tests: Set<string>;
                }>();
                let cIdx = 0;
                let crIdx = 0;
                let lastFilesSig = "";
                for (const rawJson of deduped) {
                    const parsed = JSON.parse(rawJson) as Record<string, Json>;
                    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
                    if (Array.isArray(parsed.targets) && parsed.targets.length > 0) {
                        const t0 = parsed.targets[0];
                        const targetObj = t0 && typeof t0 === "object" && !Array.isArray(t0) ? t0 as Record<string, Json> : null;
                        if (!targetObj || !Array.isArray(targetObj.files)) continue;
                        const filesSig = JSON.stringify(targetObj.files);
                        if (filesSig === lastFilesSig) continue;
                        lastFilesSig = filesSig;
                        const itemTag = typeof targetObj.targetId === "string" && targetObj.targetId
                            ? targetObj.targetId
                            : typeof targetObj.id === "string" && targetObj.id ? targetObj.id : "PBI-1";
                        const compactFiles = targetObj.files
                            .filter((f): f is Record<string, Json> => Boolean(f) && typeof f === "object" && !Array.isArray(f))
                            .map((f) => ({
                                path: typeof f.path === "string" ? f.path : "",
                                action: typeof f.action === "string" ? f.action : "modify",
                                summary: typeof f.summary === "string" ? f.summary : "",
                            }))
                            .filter((f) => Boolean(f.path));
                        const batchSummary = typeof targetObj.summary === "string" && targetObj.summary.trim()
                            ? targetObj.summary.trim()
                            : typeof parsed.summary === "string" && parsed.summary.trim() ? parsed.summary.trim() : "";
                        const batchTests = [
                            ...(Array.isArray(targetObj.testsAddedOrUpdated) ? targetObj.testsAddedOrUpdated : []),
                            ...(Array.isArray(parsed.testsAddedOrUpdated) ? parsed.testsAddedOrUpdated : []),
                        ].filter((t): t is string => typeof t === "string" && Boolean(t.trim()));

                        if (syntheticImplTurns.length > 0 && crIdx < cIdx) {
                            const roundIdx = cIdx - 1;
                            const state = roundState.get(roundIdx)!;
                            if (batchSummary && !state.summaries.includes(batchSummary)) state.summaries.push(batchSummary);
                            for (const f of compactFiles) state.filesByPath.set(f.path, f);
                            for (const t of batchTests) state.tests.add(t);
                            for (const f of compactFiles) {
                                if (/(Test|Tests|Spec)\.(java|kt)$/.test(f.path)) state.tests.add(f.path);
                            }
                            syntheticImplTurns[syntheticImplTurns.length - 1] = {
                                ...syntheticImplTurns[syntheticImplTurns.length - 1]!,
                                timestamp: exec.updatedAt,
                                outputPreview: JSON.stringify({
                                    targetId: state.targetId,
                                    summary: state.summaries.join(" · "),
                                    files: [...state.filesByPath.values()],
                                    testsAddedOrUpdated: [...state.tests],
                                }),
                            };
                        } else {
                            const roundIdx = cIdx;
                            const filesByPath = new Map<string, { path: string; action: string; summary: string }>();
                            const tests = new Set<string>(batchTests);
                            for (const f of compactFiles) {
                                filesByPath.set(f.path, f);
                                if (/(Test|Tests|Spec)\.(java|kt)$/.test(f.path)) tests.add(f.path);
                            }
                            const summaries = batchSummary ? [batchSummary] : [];
                            roundState.set(roundIdx, { targetId: itemTag, summaries, filesByPath, tests });
                            syntheticImplTurns.push({
                                id: `${exec.id}:journal:coder:${itemTag}:${cIdx}`,
                                kind: "agent_turn",
                                timestamp: exec.updatedAt,
                                role: "software-factory-coder",
                                label: `Factory:coder:${itemTag}:${cIdx}`,
                                target: "software-factory-coder",
                                status: "completed",
                                outputPreview: JSON.stringify({
                                    targetId: itemTag,
                                    summary: summaries.join(" · "),
                                    files: [...filesByPath.values()],
                                    testsAddedOrUpdated: [...tests],
                                }),
                            });
                            cIdx++;
                        }
                    } else if (
                        (typeof parsed.pass === "boolean" && Array.isArray(parsed.criteria) && Array.isArray(parsed.findings)) ||
                        (Array.isArray(parsed.acceptanceCriteria) && Array.isArray(parsed.criteria) && Array.isArray(parsed.findings))
                    ) {
                        const firstAc = Array.isArray(parsed.acceptanceCriteria) && parsed.acceptanceCriteria[0] && typeof parsed.acceptanceCriteria[0] === "object" && !Array.isArray(parsed.acceptanceCriteria[0])
                            ? parsed.acceptanceCriteria[0] as Record<string, Json>
                            : null;
                        const itemTag = typeof firstAc?.targetId === "string" && firstAc.targetId ? firstAc.targetId : "PBI-1";
                        let normalizedReview: Record<string, Json> = parsed;
                        if (typeof parsed.pass !== "boolean" && Array.isArray(parsed.acceptanceCriteria)) {
                            const mappedCriteria = parsed.acceptanceCriteria
                                .filter((ac): ac is Record<string, Json> => Boolean(ac) && typeof ac === "object" && !Array.isArray(ac))
                                .map((ac) => ({
                                    criterion: typeof ac.criterion === "string" ? ac.criterion : "",
                                    status: ac.passed === true ? "addressed" : "partial",
                                    evidence: typeof ac.evidence === "string" ? ac.evidence : "",
                                }));
                            const mappedFindings = parsed.findings
                                .filter((fn): fn is Record<string, Json> => Boolean(fn) && typeof fn === "object" && !Array.isArray(fn))
                                .map((fn) => ({
                                    ...fn,
                                    category: typeof fn.category === "string" ? fn.category : typeof fn.rubricId === "string" ? fn.rubricId : "correctness",
                                }));
                            const allRubricPass = parsed.criteria
                                .filter((c): c is Record<string, Json> => Boolean(c) && typeof c === "object" && !Array.isArray(c))
                                .every((c) => c.pass === true);
                            const allAcPass = mappedCriteria.every((c) => c.status === "addressed");
                            const pass = allRubricPass && allAcPass && mappedFindings.length === 0;
                            normalizedReview = {
                                pass,
                                summary: pass
                                    ? `Independent code review passed (${mappedCriteria.length}/${mappedCriteria.length} acceptance criteria verified, 0 findings).`
                                    : `Independent code review requested ${mappedFindings.length} fix(es) (${mappedCriteria.filter((c) => c.status === "addressed").length}/${mappedCriteria.length} acceptance criteria verified).`,
                                criteria: mappedCriteria,
                                findings: mappedFindings,
                            };
                        }
                        syntheticImplTurns.push({
                            id: `${exec.id}:journal:code-review:${itemTag}:${crIdx}`,
                            kind: "agent_turn",
                            timestamp: exec.updatedAt,
                            role: "software-factory-code-reviewer",
                            label: `Factory:code-review:${itemTag}:${crIdx}`,
                            target: "software-factory-code-reviewer",
                            status: "completed",
                            outputPreview: JSON.stringify(normalizedReview),
                        });
                        crIdx++;
                    }
                }
                exec.trace ??= [];
                for (const turn of syntheticImplTurns) {
                    if (!exec.trace.some((t) => t.kind === "agent_turn" && t.label === turn.label)) {
                        exec.trace.push(turn);
                    }
                }
                continue;
            }
            const plans: Record<string, Json>[] = [];
            const reviews: Record<string, Json>[] = [];
            const syntheticTurns: ExecutionTraceEntry[] = [];
            let pIdx = 0;
            let rIdx = 0;
            for (const rawJson of deduped) {
                const parsed = JSON.parse(rawJson) as Record<string, Json>;
                if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
                if (Array.isArray(parsed.items)) {
                    const label = `Factory:planner:${pIdx}`;
                    plans.push(parsed);
                    syntheticTurns.push({
                        id: `${exec.id}:journal:planner:${pIdx}`,
                        kind: "agent_turn",
                        timestamp: exec.updatedAt,
                        role: "software-factory-planner",
                        label,
                        target: "software-factory-planner",
                        status: "completed",
                        outputPreview: rawJson.slice(0, 30_000),
                    });
                    pIdx++;
                } else if (typeof parsed.pass === "boolean") {
                    const label = `Factory:plan-review:${rIdx}`;
                    reviews.push(parsed);
                    syntheticTurns.push({
                        id: `${exec.id}:journal:plan-review:${rIdx}`,
                        kind: "agent_turn",
                        timestamp: exec.updatedAt,
                        role: "software-factory-reader",
                        label,
                        target: "software-factory-reader",
                        status: "completed",
                        outputPreview: rawJson.slice(0, 30_000),
                    });
                    rIdx++;
                }
            }
            exec.trace ??= [];
            for (const turn of syntheticTurns) {
                const existingIdx = exec.trace.findIndex((t) => t.kind === "agent_turn" && t.label === turn.label && t.status === "completed");
                if (existingIdx >= 0) {
                    if (!exec.trace[existingIdx]!.outputPreview || exec.trace[existingIdx]!.outputPreview!.length < (turn.outputPreview?.length || 0)) {
                        exec.trace[existingIdx] = { ...exec.trace[existingIdx]!, outputPreview: turn.outputPreview };
                    }
                } else {
                    exec.trace.unshift(turn);
                }
            }
            for (const art of cloned.factory.artifacts) {
                if ((art.kind === "plan" || art.kind === "planning_attempt") && art.executionId === exec.id &&
                    art.content && typeof art.content === "object" && !Array.isArray(art.content)) {
                    const contentObj = art.content as Record<string, Json>;
                    if (Array.isArray(contentObj.reviewHistory)) {
                        contentObj.reviewHistory = contentObj.reviewHistory.map((entry, idx) => {
                            if (entry && typeof entry === "object" && !Array.isArray(entry) && !("candidate" in entry) && plans[idx]) {
                                return { ...(entry as Record<string, Json>), candidate: plans[idx]! };
                            }
                            return entry;
                        });
                    }
                }
            }
        } catch {
            // Best-effort read-only journal hydration for live UI inspection.
        }
    }
    return cloned;
}

function matches(actual: string | undefined, expected: string): boolean {
    if (typeof actual !== "string") return false;
    const bytes = Buffer.from(actual);
    const expectedBytes = Buffer.from(expected);
    return bytes.length === expectedBytes.length && timingSafeEqual(bytes, expectedBytes);
}

function json(response: ServerResponse, status: number, value: unknown): void {
    response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
    response.end(JSON.stringify(value));
}

function readBody(request: IncomingMessage): Promise<unknown> {
    return new Promise((resolve, reject) => {
        let length = 0;
        const chunks: Buffer[] = [];
        request.on("data", (chunk: Buffer) => {
            length += chunk.length;
            if (length > MAX_COMMAND_BYTES) {
                reject(new FactoryError("command_too_large", `Commands may not exceed ${MAX_COMMAND_BYTES} bytes.`, 413));
                return;
            }
            chunks.push(chunk);
        });
        request.on("error", reject);
        request.on("end", () => {
            if (length > MAX_COMMAND_BYTES) return;
            try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
            catch { reject(new FactoryError("invalid_json", "Request body must be valid JSON.")); }
        });
    });
}

function newestMtime(directory: string): number {
    if (!existsSync(directory)) return 0;
    let latest = 0;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const target = path.join(directory, entry.name);
        latest = Math.max(latest, entry.isDirectory() ? newestMtime(target) : statSync(target).mtimeMs);
    }
    return latest;
}

function ensureStudioBuild(assetDirectory: string): void {
    const webRoot = fileURLToPath(new URL("../web/", import.meta.url));
    const viteCli = path.join(webRoot, "node_modules", "vite", "bin", "vite.js");
    if (!existsSync(viteCli)) return;
    const distIndex = path.join(assetDirectory, "index.html");
    const distTime = existsSync(distIndex) ? statSync(distIndex).mtimeMs : 0;
    const sourceTime = Math.max(
        existsSync(path.join(webRoot, "index.html")) ? statSync(path.join(webRoot, "index.html")).mtimeMs : 0,
        newestMtime(path.join(webRoot, "src")),
        newestMtime(fileURLToPath(new URL("../core/", import.meta.url))),
    );
    if (distTime < sourceTime) {
        console.log("[Software Factory] Rebuilding Feature Studio UI bundle...");
        execFileSync(process.execPath, [viteCli, "build"], { cwd: webRoot, stdio: "inherit" });
    }
}

export async function startServer(options: { application: Application; port?: number; assets?: string }) {
    const assetDirectory = options.assets || fileURLToPath(new URL("../web/dist/", import.meta.url));
    if (!options.assets) ensureStudioBuild(assetDirectory);
    if (!existsSync(path.join(assetDirectory, "index.html"))) {
        throw new FactoryError("ui_not_built", "Build the optional web UI before starting its server: npm --prefix web run build.", 500);
    }
    const assets = realpathSync(assetDirectory);
    const sessionToken = randomBytes(32).toString("hex");
    const csrf = randomBytes(32).toString("hex");
    let origin = "";
    let cookieName = "";

    async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("Referrer-Policy", "no-referrer");
        response.setHeader("Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
        if (request.headers.host !== new URL(origin).host ||
            (request.headers.origin !== undefined && request.headers.origin !== origin) ||
            request.headers["sec-fetch-site"] === "cross-site") {
            throw new FactoryError("origin_forbidden", "Only the local Software Factory origin is allowed.", 403);
        }
        const url = new URL(request.url || "/", origin);
        if (url.pathname === "/api/session" && request.method === "GET") {
            response.setHeader("Set-Cookie", `${cookieName}=${sessionToken}; HttpOnly; SameSite=Strict; Path=/`);
            json(response, 200, { csrf, project: options.application.registry.project });
            return;
        }
        if (url.pathname.startsWith("/api/")) {
            const cookie = request.headers.cookie?.split(";").map((value) => value.trim())
                .find((value) => value.startsWith(`${cookieName}=`))?.slice(cookieName.length + 1);
            if (!matches(cookie, sessionToken)) throw new FactoryError("session_required", "Reload the local application to establish a session.", 401);
            if (request.method === "GET" && url.pathname === "/api/features") {
                const rawFeatures = url.searchParams.get("cached") === "1"
                    ? options.application.registry.list().map((feature) => options.application.cachedFeature(feature.id))
                    : await options.application.features();
                json(response, 200, { project: options.application.registry.project,
                    features: rawFeatures.map(hydrateStudioFeature),
                    cached: url.searchParams.get("cached") === "1" });
            } else if (request.method === "GET" && url.pathname === "/api/doctor") {
                json(response, 200, runtimeReport());
            } else if (request.method === "GET" && url.pathname === "/api/configuration") {
                json(response, 200, { configuration: options.application.lifecycle.store.configuration() });
            } else if (request.method === "GET" && url.pathname.startsWith("/api/gates/")) {
                const featureId = url.pathname.slice("/api/gates/".length);
                const feature = await options.application.feature(featureId);
                json(response, 200, { gates: ["design", "plan", "backlog", "dispatch", "complete"].map((stage) => {
                    if (feature.configurationMismatch) return { available: false, stage, reason: feature.configurationMismatch.message, code: feature.configurationMismatch.code };
                    try { return { available: true, ...options.application.lifecycle.gate(featureId, stage as "design" | "plan" | "backlog" | "dispatch" | "complete") }; }
                    catch (error) {
                        if (!(error instanceof FactoryError)) throw error;
                        return { available: false, stage, reason: error.message, code: error.code };
                    }
                }) });
            } else if (request.method === "POST" && ["/api/commands", "/api/commands/preview"].includes(url.pathname)) {
                if (request.headers.origin !== origin || !matches(request.headers["x-software-factory-csrf"]?.toString(), csrf)) {
                    throw new FactoryError("confirmation_forbidden", "A same-origin application session and anti-forgery token are required.", 403);
                }
                if (request.headers["content-type"]?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
                    throw new FactoryError("invalid_content_type", "Commands must use application/json.", 415);
                }
                const command = await readBody(request);
                const preview = await options.application.preview(command);
                if (url.pathname === "/api/commands/preview") { json(response, 200, preview); return; }
                if (preview.consentKey && request.headers["x-software-factory-scope"] !== preview.consentKey) {
                    throw new FactoryError("scope_confirmation_required", "Confirm the current exact command preview before submitting it.", 409);
                }
                const actor: Actor = { channel: "web", reference: randomUUID() };
                const consent = preview.consentKey
                    ? { granted: true as const, revisionId: preview.consentKey, actor }
                    : undefined;
                json(response, 200, await options.application.submit(command, actor, consent));
            } else {
                throw new FactoryError("route_not_found", "API route or method not found.", 404);
            }
            return;
        }
        if (request.method !== "GET") throw new FactoryError("method_not_allowed", "This route is read-only.", 405);
        let pathname: string;
        try { pathname = decodeURIComponent(url.pathname); }
        catch { throw new FactoryError("invalid_path", "Invalid asset path."); }
        if (pathname.includes("\0")) throw new FactoryError("invalid_path", "Invalid asset path.");
        const candidate = path.resolve(assets, pathname === "/" ? "index.html" : pathname.slice(1));
        if (!candidate.startsWith(assets + path.sep) || !existsSync(candidate) || !statSync(candidate).isFile()) {
            throw new FactoryError("asset_not_found", "Asset not found.", 404);
        }
        const resolved = realpathSync(candidate);
        if (!resolved.startsWith(assets + path.sep)) throw new FactoryError("asset_forbidden", "Asset is outside the UI build.", 403);
        const mime: Record<string, string> = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
        response.writeHead(200, {
            "Content-Type": `${mime[path.extname(resolved)] || "application/octet-stream"}; charset=utf-8`,
            "Cache-Control": "no-store",
        });
        response.end(readFileSync(resolved));
    }

    const server = http.createServer((request, response) => {
        void handle(request, response).catch((error: unknown) => {
            if (error instanceof FactoryError) {
                json(response, error.status, { error: { code: error.code, message: error.message } });
            } else {
                console.error("[Software Factory] Request failed:", error);
                json(response, 500, { error: { code: "internal_error", message: "The operation failed. See the local server error; no success was recorded." } });
            }
        });
    });
    server.requestTimeout = 10_000;
    await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(options.port ?? 0, "127.0.0.1", () => {
            server.removeListener("error", reject);
            resolve();
        });
    });
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("The loopback server did not acquire a port.");
    origin = `http://127.0.0.1:${address.port}`;
    cookieName = `software_factory_${address.port}`;
    return {
        url: origin,
        close: () => new Promise<void>((resolve, reject) => {
            server.close((error) => error ? reject(error) : resolve());
            server.closeAllConnections();
        }),
    };
}
