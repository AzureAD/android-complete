// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fixture } from "./helpers.ts";
import test from "node:test";
import { ExternalProvider } from "../providers/external.ts";
import type { CommandRunner } from "../providers/cli.ts";
import type { Configuration, PlannedItem, WorkItem } from "../core/lifecycle-contracts.ts";

const config = (): Configuration => ({
    repositories: { broker: { provider: "github", hostname: "msft.ghe.com", slug: "example/broker", baseBranch: "dev", account: "fixture-user", localPath: "." } },
    boards: { organization: "boards-org", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering\\Client", iterationPath: "Engineering\\Current", assignee: "", parentId: null },
});
const item: PlannedItem = { id: "A", title: "Compatibility", repository: "broker", description: "Complete implementation details.", requirementIds: ["R1"], acceptanceCriteria: ["Preserve compatibility."], dependsOn: [], files: ["A.kt"], tests: ["Compatibility test"] };
const wi: WorkItem = { key: "A", adoId: 42, title: item.title, repository: "broker", status: "Committed", url: "https://dev.azure.com/boards-org/Engineering/_workitems/edit/42" };

test("GitHub/GHE dispatch uses scoped credentials and stdin, never global account switching", async () => {
    const calls: { command: string; args: string[]; input?: string; env?: NodeJS.ProcessEnv }[] = [];
    const runner: CommandRunner = {
        run: async (command, args, options = {}) => {
            calls.push({ command, args, input: options.input, env: options.env });
            if (args[0] === "auth") return "fixture-token";
            if (args[0] === "api") return JSON.stringify({ name: "dev" });
            return "https://msft.ghe.com/example/broker/pull/9";
        },
    };
    const provider = new ExternalProvider(runner, async () => { throw new Error("No ADO request expected."); });
    await provider.dispatch(config(), item, wi, "operation-marker");
    const dispatch = calls.find((call) => call.args[0] === "agent-task");
    assert.ok(dispatch);
    assert.deepEqual(dispatch.args.slice(0, 4), ["agent-task", "create", "-F", "-"]);
    assert.ok(dispatch.args.includes("msft.ghe.com/example/broker"));
    assert.match(dispatch.input || "", /Fixes AB#42/);
    assert.match(dispatch.input || "", /operation-marker/);
    assert.match(dispatch.input || "", /Compatibility test/);
    assert.equal(dispatch.env?.GH_HOST, "msft.ghe.com");
    assert.equal(dispatch.env?.GH_TOKEN, "fixture-token");
    assert.equal(calls.some((call) => call.args.includes("switch")), false);
    assert.ok(calls.filter((call) => call.args[0] === "auth").every((call) => call.args.includes("fixture-user") && call.args.includes("msft.ghe.com")));
});

test("GitHub Actions check-runs are included even when legacy commit statuses are empty", async () => {
    const provider = new ExternalProvider({
        run: async (_command, args) => {
            if (args[0] === "auth") return "fixture-token";
            const endpoint = args.at(-1)!;
            if (endpoint.includes("check-runs")) return JSON.stringify({ total_count: 1, check_runs: [{ status: "completed", conclusion: "success" }] });
            if (endpoint.endsWith("/status")) return JSON.stringify({ total_count: 0, state: "pending" });
            return JSON.stringify({ title: "Compatibility", html_url: "https://msft.ghe.com/example/broker/pull/9", state: "open", merged_at: null,
                head: { sha: "abc" }, base: { ref: "dev" }, body: "Fixes AB#42, not AB#420." });
        },
    });
    const result = await provider.inspectPr(config().repositories.broker!, 9, "A");
    assert.equal(result.checks, "passing");
    assert.deepEqual(result.workItemIds, [42, 420]);
    assert.equal(result.baseBranch, "dev");
});

test("ADO creation uses the board context, HTML escaping, and a durable correlation tag", async () => {
    let captured: { url: string; init?: RequestInit } | undefined;
    const provider = new ExternalProvider({
        run: async (command, args) => {
            assert.equal(command, "az");
            assert.ok(args.includes("499b84ac-1321-427f-aa17-267ca6975798"));
            return "fixture-ado-token";
        },
    }, async (input, init) => {
        captured = { url: String(input), init };
        return Response.json({ id: 42, fields: { "System.Title": item.title, "System.State": "New" } });
    });
    await provider.createWorkItem(config(), { ...item, description: "<script>not executable</script>" }, "unique-operation");
    assert.ok(captured);
    assert.match(captured.url, /dev\.azure\.com\/boards-org\/Engineering\/_apis\/wit\/workitems/);
    assert.equal(new Headers(captured.init?.headers).get("Content-Type"), "application/json-patch+json");
    const patch: { path: string; value: string }[] = JSON.parse(String(captured.init?.body));
    assert.match(patch.find((entry) => entry.path === "/fields/System.Description")?.value || "", /&lt;script&gt;/);
    assert.match(patch.find((entry) => entry.path === "/fields/System.Tags")?.value || "", /unique-operation/);
});

test("idempotent dispatch-status repair preserves existing tags and never reopens resolved work", async () => {
    let patch: { op: string; path: string; value: unknown }[] = [];
    const provider = new ExternalProvider({ run: async () => "fixture-token" }, async (_input, init) => {
        if (init?.method === "PATCH") { patch = JSON.parse(String(init.body)); return Response.json({ id: 42 }); }
        return Response.json({ id: 42, rev: 7, fields: { "System.Tags": "customer-tag", "System.State": "Resolved" } });
    });
    await provider.markDispatched(config(), wi, "status-marker");
    assert.equal(patch[0]?.op, "test");
    assert.equal(patch[0]?.value, 7);
    assert.equal(patch.find((entry) => entry.path === "/fields/System.State")?.value, "Resolved");
    assert.match(String(patch.find((entry) => entry.path === "/fields/System.Tags")?.value), /customer-tag; agent-dispatched; status-marker/);
});

test("provider rejection is explicit and redirecting authenticated requests is forbidden", async () => {
    const provider = new ExternalProvider({ run: async () => "fixture-token" }, async (_input, init) => {
        assert.equal(init?.redirect, "error");
        return new Response("Not allowed", { status: 403 });
    });
    await assert.rejects(() => provider.createWorkItem(config(), item, "marker"), /HTTP 403/);
});

test("local coding publication writes reviewed files, commits, pushes with scoped credentials, and opens a PR", async () => {
    const f = fixture();
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    const repoDir = path.join(f.workspace, "broker");
    mkdirSync(repoDir, { recursive: true });
    const calls: { command: string; args: string[]; cwd?: string; input?: string }[] = [];
    const runner: CommandRunner = {
        run: async (command, args, options = {}) => {
            calls.push({ command, args, cwd: options.cwd, input: options.input });
            if (command === "gh" && args[0] === "auth") return "scoped-ghe-token";
            if (command === "gh" && args[0] === "api") {
                if (args.includes("POST")) return JSON.stringify({ number: 77, html_url: "https://msft.ghe.com/example/broker/pull/77" });
                return JSON.stringify({ name: "dev" });
            }
            return "";
        },
    };
    try {
        const provider = new ExternalProvider(runner, async () => { throw new Error("No ADO call expected."); });
        const localConfig: Configuration = {
            ...config(),
            repositories: { broker: { ...config().repositories.broker!, localPath: "broker" } },
        };
        const receipt = await provider.publishLocal(localConfig, item, wi, "local-op-marker", {
            branch: "factory/ab-42-a",
            summary: "Implemented compatibility guard locally.",
            files: [{ path: "src/A.kt", action: "write", content: "package example\nclass A\n" }],
            roundsCompleted: 2,
            maxRounds: 3,
            reviewHistory: [],
        });
        assert.deepEqual(receipt, {
            submitted: true, mode: "local", provider: "github", marker: "local-op-marker",
            branch: "factory/ab-42-a", prNumber: 77, url: "https://msft.ghe.com/example/broker/pull/77",
            roundsCompleted: 2, maxRounds: 3,
        });
        assert.ok(calls.some((call) => call.command === "git" && call.args[0] === "checkout" && call.args[2] === "factory/ab-42-a"));
        assert.ok(calls.some((call) => call.command === "git" && call.args[0] === "commit" && call.args[2]?.includes("Fixes AB#42")));
        assert.ok(calls.some((call) => call.command === "git" && call.args.includes("push") && call.args.includes("factory/ab-42-a")));
        const prCall = calls.find((call) => call.command === "gh" && call.args.includes("POST"));
        assert.ok(prCall);
        assert.match(prCall.input || "", /Passed independent AI code review in 2 of 3 round\(s\)/);
        assert.match(prCall.input || "", /local-op-marker/);
    } finally {
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        f.cleanup();
    }
});
