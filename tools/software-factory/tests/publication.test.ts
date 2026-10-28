// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fixture } from "./helpers.ts";

test("public SDK session publication preserves identity and a durable catalog without starting agents", () => {
    const f = fixture();
    try {
        const output = execFileSync(process.execPath, ["--input-type=module", "-e", `
            import assert from "node:assert/strict";
            import { randomUUID } from "node:crypto";
            const { createClient } = await import(process.argv[1]);
            const workspace = process.argv[2];
            const home = process.argv[3];
            const hostOptions = {
                authentication: { mode: "github" }, computeId: randomUUID(),
                localServer: { hostname: "127.0.0.1", requireConnectionToken: true },
                allowCreatingNewSessions: false,
            };
            const config = {
                name: "Factory isolated zero-agent publication regression", workingDirectory: workspace,
                enableConfigDiscovery: false, requestExtensions: false, availableTools: [],
                onPermissionRequest: () => ({kind:"reject",feedback:"No tools or paid agents are allowed."}),
            };
            let client = createClient(workspace, home);
            let host;
            let session;
            let agents = 0;
            try {
                await client.start();
                host = await client.startAhpHost(hostOptions);
                assert.equal(new URL(host.url).hostname, "127.0.0.1");
                assert.ok(host.token, "This fixture must never expose an unauthenticated listener.");
                session = await client.createSession(config);
                session.on("subagent.started", () => { agents++; });
                const id = session.sessionId;
                const published = await host.publishSession(id);
                assert.equal(published.sessionId, id);
                const includes = async () => (await host.listSessions()).sessions
                    .some(entry => entry.resource === published.sessionUri);
                assert.equal(await includes(), true);
                await host.dispose(); host = undefined;
                assert.deepEqual(await client.stop(), []);
                session = undefined;

                client = createClient(workspace, home);
                await client.start();
                host = await client.startAhpHost(hostOptions);
                assert.equal(await includes(), true, "Publication must survive host/runtime restart.");
                session = await client.resumeSession(id, config);
                session.on("subagent.started", () => { agents++; });
                const resumed = await host.publishSession(session.sessionId);
                assert.equal(resumed.sessionId, id);
                assert.equal(resumed.sessionUri, published.sessionUri);
                assert.equal((await host.listSessions()).sessions.filter(entry => entry.resource === published.sessionUri).length, 1);
                await host.unpublishSession(id);
                assert.equal(await includes(), false);
                const events = await session.getEvents();
                assert.ok(events.length > 0, "Withdrawing publication must not delete its conversation history.");
                assert.ok(events.every(event => event.type !== "subagent.started"));
                assert.equal(agents, 0);
                console.log(JSON.stringify({ canonicalIdentity: true, durableCatalog: true, withdrawalPreservesHistory: true,
                    agentsStarted: agents, desktopAttachmentVerified: false }));
            } finally {
                if (host) await host.dispose();
                if (session) await session.disconnect();
                const errors = await client.stop();
                if (errors.length) throw new AggregateError(errors, "Publication fixture cleanup failed.");
            }
        `, new URL("../runtime/host.ts", import.meta.url).href, f.workspace, path.join(f.directory, "factory-home")], {
            cwd: f.workspace, encoding: "utf8", timeout: 90_000, maxBuffer: 64 * 1024,
            env: { ...process.env, COPILOT_HOME: path.join(f.directory, "copilot-home"), SOFTWARE_FACTORY_OWNED_RUNTIME: "1" },
        });
        const result = JSON.parse(output) as { canonicalIdentity: boolean; durableCatalog: boolean; withdrawalPreservesHistory: boolean;
            agentsStarted: number; desktopAttachmentVerified: boolean };
        assert.equal(result.canonicalIdentity, true);
        assert.equal(result.durableCatalog, true);
        assert.equal(result.withdrawalPreservesHistory, true);
        assert.equal(result.agentsStarted, 0);
        assert.equal(result.desktopAttachmentVerified, false, "SDK publication is not proof of desktop discovery or attachment.");
    } finally { f.cleanup(); }
});
