// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { startServer } from "../server/server.ts";
import { MAX_COMMAND_BYTES } from "../core/contracts.ts";
import { actor, fixture, requirements } from "./helpers.ts";

async function setup() {
    const f = fixture();
    const assets = path.join(f.directory, "assets");
    mkdirSync(assets);
    writeFileSync(path.join(assets, "index.html"), "<!doctype html><title>Software Factory</title>");
    const server = await startServer({ application: f.app, assets });
    const bootstrap = await fetch(`${server.url}/api/session`);
    const cookie = bootstrap.headers.get("set-cookie")?.split(";")[0];
    assert.ok(cookie);
    const session = await bootstrap.json() as { csrf: string };
    const headers = { Cookie: cookie, Origin: server.url, "Content-Type": "application/json", "X-Software-Factory-CSRF": session.csrf };
    return { ...f, server, headers, cleanup: async () => { await server.close(); f.cleanup(); } };
}

test("UI HTTP commands and chat/core reads share exact revisions and approval rules", async () => {
    const f = await setup();
    try {
        const response = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "create_feature", requestId: "web-request", input: requirements() }),
        });
        assert.equal(response.status, 200);
        const feature = f.app.registry.list()[0];
        assert.ok(feature);
        const command = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        const preview = f.app.prepare(command);
        const approval = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: { ...f.headers, "X-Software-Factory-Scope": preview.consentKey! },
            body: JSON.stringify(command),
        });
        assert.equal(approval.status, 200);
        assert.equal(f.app.registry.get(feature.id).status, "ready_for_design");
        f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed in chat" },
        }, actor);
        const stale = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }),
        });
        assert.equal(stale.status, 409);
        const result = await fetch(`${f.server.url}/api/features`, { headers: f.headers });
        const payload = await result.json() as { features: unknown[] };
        assert.deepEqual(payload.features, f.app.registry.list().map((entry) => f.app.lifecycle.store.view(entry.id)));
    } finally { await f.cleanup(); }
});

test("HTTP requires local host/origin, session cookie and anti-forgery token", async () => {
    const f = await setup();
    try {
        const body = JSON.stringify({ kind: "create_feature", requestId: "rejected", input: requirements() });
        const cases = [
            { ...f.headers, Origin: "https://example.com" },
            { ...f.headers, "X-Software-Factory-CSRF": "" },
            { ...f.headers, Cookie: "" },
        ];
        for (const headers of cases) {
            const response = await fetch(`${f.server.url}/api/commands`, { method: "POST", headers, body });
            assert.ok([401, 403].includes(response.status));
        }
        const wrongHost = await new Promise<number>((resolve, reject) => {
            const request = http.request(`${f.server.url}/api/features`, {
                headers: { ...f.headers, Host: "attacker.example" },
            }, (response) => { response.resume(); resolve(response.statusCode || 0); });
            request.on("error", reject);
            request.end();
        });
        assert.equal(wrongHost, 403);
        assert.equal(f.app.registry.list().length, 0);
        const forged = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers, body: JSON.stringify({ kind: "create_feature", requestId: "forged", input: requirements(), actor: { channel: "chat" } }),
        });
        assert.equal(forged.status, 400);
        const malformed = await fetch(`${f.server.url}/api/commands`, { method: "POST", headers: f.headers, body: "{" });
        assert.equal(malformed.status, 400);
        const crossSite = await fetch(`${f.server.url}/api/session`, { headers: { "Sec-Fetch-Site": "cross-site" } });
        assert.equal(crossSite.status, 403);
        const unauthenticated = await fetch(`${f.server.url}/api/features`);
        assert.equal(unauthenticated.status, 401);
        const wrongType = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: { ...f.headers, "Content-Type": "application/jsonp" }, body,
        });
        assert.equal(wrongType.status, 415);
        const oversized = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "create_feature", requestId: "oversized", input: { ...requirements(), title: "x".repeat(MAX_COMMAND_BYTES) } }),
        });
        assert.equal(oversized.status, 413);
        assert.equal(f.app.registry.list().length, 0);
    } finally { await f.cleanup(); }
});
