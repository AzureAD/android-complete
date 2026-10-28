// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
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
