// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { adoObserverFixture, fixture } from "./helpers.ts";
import test from "node:test";
import { ExternalProvider } from "../providers/external.ts";
import { systemRunner, type CommandRunner } from "../providers/cli.ts";
import type { Configuration, Json, PlannedItem, RepositoryTarget, WorkItem } from "../core/lifecycle-contracts.ts";
import type { LocalImplementationInput, RepairPublicationInput } from "../providers/provider.ts";
import { FactoryError, object } from "../core/contracts.ts";
import { capturePublicationSource } from "../../../.github/extensions/CodeFeature/workflow.mjs";

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

function git(cwd: string, ...args: string[]): string {
    return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }).trim();
}

function publicationFixture(repo: RepositoryTarget, objectFormat: "sha1" | "sha256" = "sha1") {
    const f = fixture();
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    const repoDir = path.join(f.workspace, "broker");
    const bare = path.join(f.directory, "remote.git");
    const origin = repo.provider === "github"
        ? `https://${repo.hostname}/${repo.slug}.git`
        : `https://dev.azure.com/boards-org/Engineering/_git/broker`;
    mkdirSync(repoDir);
    git(repoDir, "init", "--quiet", `--object-format=${objectFormat}`);
    git(repoDir, "config", "user.name", "Factory Test");
    git(repoDir, "config", "user.email", "factory@example.invalid");
    git(repoDir, "config", "core.autocrlf", "false");
    writeFileSync(path.join(repoDir, "A.kt"), "// baseline\n");
    git(repoDir, "add", "A.kt");
    git(repoDir, "commit", "--quiet", "-m", "Fixture");
    git(repoDir, "remote", "add", "origin", origin);
    git(repoDir, "update-ref", "refs/remotes/origin/dev", "HEAD");
    git(f.directory, "init", "--bare", "--quiet", `--object-format=${objectFormat}`, bare);
    git(repoDir, "push", "--quiet", bare, "HEAD:refs/heads/dev");
    const content = "class A { fun value() = \"reviewed\" }\n";
    writeFileSync(path.join(repoDir, "A.kt"), content);
    const files: LocalImplementationInput["files"] = [{ path: "A.kt", action: "write", content }];
    const sourceBinding = capturePublicationSource(repoDir, { id: item.id, baseBranch: repo.baseBranch }, { targetId: item.id, files });
    const implementation: LocalImplementationInput = {
        branch: "factory/ab-42-a", summary: "Implemented the reviewed compatibility guard.",
        files, roundsCompleted: 2, maxRounds: 3, reviewHistory: [], sourceBinding,
        verification: { passed: ["fixture test"], failed: [], blocked: [], deferred: [], unrun: [] },
    };
    const localConfig: Configuration = { ...config(), repositories: { broker: { ...repo, localPath: "broker" } } };
    return {
        f, repoDir, bare, origin, implementation, localConfig,
        marker: `software-factory-${"a".repeat(64)}`,
        cleanup: () => {
            if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE;
            else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
            f.cleanup();
        },
    };
}

interface GitHubPr {
    number: number;
    html_url: string;
    state: string;
    merged_at: string | null;
    head: { ref: string; sha: string; repo: { full_name: string } };
    base: { ref: string; repo: { full_name: string } };
    user: { id: number; login: string };
    body: string;
}

function githubPublicationFixture(objectFormat: "sha1" | "sha256" = "sha1") {
    const local = publicationFixture(config().repositories.broker!, objectFormat);
    const state: {
        base: string; publisherId: number; publisherLogin: string; pr: GitHubPr | null;
        lookup: "normal" | "ambiguous" | "incomplete"; failAfterCreate: boolean;
        pushes: number; posts: number; body: string;
    } = {
        base: local.implementation.sourceBinding.base, publisherId: 7, publisherLogin: "fixture-user",
        pr: null, lookup: "normal", failAfterCreate: false, pushes: 0, posts: 0, body: "",
    };
    const runner: CommandRunner = {
        run: async (command, args, options = {}) => {
            if (command === "git") {
                if (args[0]?.startsWith("--git-dir=") && args[1] === "push") {
                    state.pushes++;
                    const remoteIndex = args.indexOf(local.origin);
                    assert.ok(remoteIndex >= 0, "the only push must target the approved origin");
                    const rewritten = [...args];
                    rewritten[remoteIndex] = local.bare;
                    const result = await systemRunner.run(command, rewritten, options);
                    if (state.pr) state.pr.head.sha = git(local.repoDir, `--git-dir=${local.bare}`, "rev-parse", `refs/heads/${state.pr.head.ref}`);
                    return result;
                }
                return systemRunner.run(command, args, options);
            }
            if (command !== "gh") throw new Error("Unexpected ADO command.");
            if (args[0] === "auth") return "fixture-token";
            assert.equal(args[0], "api");
            const method = args[4];
            const endpoint = args[5]!;
            if (endpoint === "user") return JSON.stringify({ id: state.publisherId, login: state.publisherLogin });
            if (endpoint === "repos/example/broker") return JSON.stringify({ full_name: "example/broker" });
            if (endpoint === "repos/example/broker/branches/dev") return JSON.stringify({ commit: { sha: state.base } });
            if (endpoint === "repos/example/broker/pulls" && method === "POST") {
                state.posts++;
                const submitted = JSON.parse(options.input || "{}") as { head: string; base: string; body: string };
                const sha = git(local.repoDir, `--git-dir=${local.bare}`, "rev-parse", `refs/heads/${submitted.head}`);
                state.body = submitted.body;
                state.pr = {
                    number: 77, html_url: "https://msft.ghe.com/example/broker/pull/77", state: "open", merged_at: null,
                    head: { ref: submitted.head, sha, repo: { full_name: "example/broker" } },
                    base: { ref: submitted.base, repo: { full_name: "example/broker" } },
                    user: { id: state.publisherId, login: state.publisherLogin }, body: submitted.body,
                };
                if (state.failAfterCreate) throw new Error("Connection lost after PR creation.");
                return JSON.stringify({ number: 77 });
            }
            if (endpoint.startsWith("repos/example/broker/pulls?")) {
                if (state.lookup === "incomplete") return JSON.stringify(Array.from({ length: 100 }, () => ({ number: 77 })));
                if (state.lookup === "ambiguous") return JSON.stringify([{ number: 77 }, { number: 78 }]);
                return JSON.stringify(state.pr ? [{ number: state.pr.number }] : []);
            }
            if (endpoint === "repos/example/broker/pulls/77") return JSON.stringify(state.pr);
            throw new Error(`Unexpected GitHub endpoint: ${endpoint}`);
        },
    };
    return { ...local, state, provider: new ExternalProvider(runner, async () => { throw new Error("Unexpected HTTP request."); }) };
}

test("local publication pushes exactly the reviewed Git tree with an empty-ref lease", async () => {
    for (const format of ["sha1", "sha256"] as const) {
        const h = githubPublicationFixture(format);
        try {
            const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
            assert.equal(binding.headSha.length, format === "sha1" ? 40 : 64);
            assert.equal(git(h.repoDir, "rev-parse", `${binding.headSha}^{tree}`), h.implementation.sourceBinding.tree);
            assert.equal(git(h.repoDir, "rev-list", "--parents", "-n", "1", binding.headSha), `${binding.headSha} ${h.implementation.sourceBinding.base}`);
            const receipt = await h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding);
            assert.deepEqual(receipt, {
                submitted: true, mode: "local", provider: "github", marker: h.marker, branch: binding.branch,
                headSha: binding.headSha, prNumber: 77, url: "https://msft.ghe.com/example/broker/pull/77",
            });
            assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${binding.branch}`), binding.headSha);
            assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "show", `${binding.headSha}:A.kt`), h.implementation.files[0]!.content.trim());
            assert.equal(git(h.repoDir, "rev-parse", "HEAD"), h.implementation.sourceBinding.head, "publication must not move the checkout");
            assert.match(git(h.repoDir, "show", "-s", "--format=%B", binding.headSha), /Co-authored-by: Copilot App/);
            assert.match(h.state.body, /Passed independent AI code review in 2 of 3 round\(s\)/);
            assert.match(h.state.body, /fixture test/);
            assert.equal(h.state.pushes, 1);
            assert.equal(h.state.posts, 1);
        } finally { h.cleanup(); }
    }
});

test("local publication rejects source, target, publisher, and base drift before pushing", async () => {
    const h = githubPublicationFixture();
    try {
        const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
        for (const changed of [{ account: "other-publisher" }, { slug: "example/other" }, { baseBranch: "main" }]) {
            const changedConfig = { ...h.localConfig, repositories: { broker: { ...h.localConfig.repositories.broker!, ...changed } } };
            await assert.rejects(() => h.provider.publishLocal(changedConfig, item, wi, h.marker, h.implementation, binding),
                (error: unknown) => error instanceof FactoryError && error.code === "publication_identity_changed");
        }
        writeFileSync(path.join(h.repoDir, "A.kt"), "// unreviewed change\n");
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding), /changed|review/i);
        writeFileSync(path.join(h.repoDir, "A.kt"), h.implementation.files[0]!.content);
        h.state.publisherId = 8;
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding),
            (error: unknown) => error instanceof FactoryError && error.code === "publication_identity_changed");
        h.state.publisherId = 7;
        h.state.base = "b".repeat(binding.headSha.length);
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding),
            (error: unknown) => error instanceof FactoryError && error.code === "local_base_changed");
        assert.equal(h.state.pushes, 0);
        assert.equal(h.state.posts, 0);
    } finally { h.cleanup(); }
});

test("observer publication uses the reviewed incremental tree, exact-head lease and original publisher", async () => {
    for (const race of [false, true]) {
        const h = githubPublicationFixture();
        try {
            const original = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
            await h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, original);
            const root = path.join(h.f.workspace, "broker-repair");
            git(h.repoDir, "worktree", "add", "--detach", root, original.headSha);
            const files: RepairPublicationInput["files"] = [{ path: "A.kt", action: "write", content: "class A { fun value() = \"repaired\" }\n" }];
            writeFileSync(path.join(root, "A.kt"), files[0]!.content);
            const input: RepairPublicationInput = {
                localPath: "broker-repair", expectedHead: original.headSha, summary: "Repair the compatibility guard",
                files, source: capturePublicationSource(root, { id: "A", baseBranch: "dev" }, { files }),
            };
            const head = await h.provider.prepareRepair(h.localConfig.repositories.broker!, input);
            assert.equal(git(root, "rev-list", "--parents", "-n", "1", head), `${head} ${original.headSha}`);
            h.state.publisherId = 99;
            await assert.rejects(() => h.provider.publishRepair(h.localConfig, item, wi, h.marker, original, input, head, async () => {}),
                /publisher/i);
            assert.equal(h.state.pushes, 1);
            h.state.publisherId = 7;
            await assert.rejects(() => h.provider.publishRepair(h.localConfig, item, wi, h.marker, original, input, head,
                async () => { throw new Error("Dispatch approval revoked"); }), /Dispatch approval revoked/);
            assert.equal(h.state.pushes, 1, "authority must be checked immediately before the push");
            writeFileSync(path.join(root, "A.kt"), "// changed after independent review\n");
            await assert.rejects(() => h.provider.publishRepair(h.localConfig, item, wi, h.marker, original, input, head, async () => {}),
                /changed|review/i);
            writeFileSync(path.join(root, "A.kt"), files[0]!.content);
            let admitted = 0;
            let competing = "";
            const send = () => h.provider.publishRepair(h.localConfig, item, wi, h.marker, original, input, head, async () => {
                admitted++;
                if (race) {
                    competing = git(root, "commit-tree", input.source.tree, "-p", original.headSha, "-m", "Concurrent contributor");
                    git(root, "push", h.bare, `${competing}:refs/heads/${original.branch}`);
                }
            });
            if (race) {
                await assert.rejects(send, (error: unknown) => error instanceof FactoryError && error.code === "provider_command_failed");
                assert.equal(git(root, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${original.branch}`), competing);
            } else {
                const receipt = await send();
                assert.equal(object(receipt, "Repair receipt").headSha, head);
                assert.equal(git(root, `--git-dir=${h.bare}`, "show", `${head}:A.kt`), files[0]!.content.trim());
            }
            assert.equal(admitted, 1);
            assert.equal(h.state.posts, 1, "repair must not create a replacement PR");
            assert.equal(git(h.repoDir, "rev-parse", "HEAD"), h.implementation.sourceBinding.head);
            assert.equal(git(h.repoDir, "write-tree"), h.implementation.sourceBinding.indexTree);
        } finally { h.cleanup(); }
    }
});

test("GitHub observer replies reconcile lost responses and resolve only unchanged, exact-PR threads", async () => {
    for (const scenario of ["lost", "changed", "wrong-pr", "unsent"] as const) {
        const originalComments = [{ databaseId: 10, body: "Fix A.kt compatibility." }];
        const comments = [...originalComments];
        let resolved = false;
        let posts = 0;
        let resolves = 0;
        const provider = new ExternalProvider({
            run: async (_command, args, options = {}) => {
                if (args[0] === "auth") return "fixture-token";
                const data = JSON.parse(options.input || "{}");
                if (args[5] === "graphql") {
                    if (data.query.startsWith("mutation")) {
                        resolves++; resolved = true;
                        return JSON.stringify({ data: { resolveReviewThread: { thread: { isResolved: true } } } });
                    }
                    if (scenario === "changed" && posts && comments.length === 2) comments.push({ databaseId: 99, body: "New concern after the repair." });
                    return JSON.stringify({ data: { node: {
                        id: "PRRT_fixture", isResolved: resolved,
                        pullRequest: { number: scenario === "wrong-pr" ? 999 : 77, repository: { nameWithOwner: "example/broker" } },
                        comments: { nodes: comments, pageInfo: { hasNextPage: false } },
                    } } });
                }
                assert.equal(args[5], "repos/example/broker/pulls/77/comments/10/replies");
                posts++; comments.push({ databaseId: 11, body: data.body });
                if (scenario === "lost") throw new Error("Lost reply response");
                return JSON.stringify({ id: 11 });
            },
        });
        const thread = { id: 10, threadId: "PRRT_fixture", path: "A.kt", comments: originalComments, commentsComplete: true };
        const send = (retry: boolean) => provider.replyRepairThread(config().repositories.broker!, 77, thread,
            "Addressed in the reviewed repair.", "repair-marker", async () => {}, retry);
        if (scenario === "lost") {
            await assert.rejects(() => send(false), /Lost reply response/);
            assert.deepEqual(await send(true), { threadId: "PRRT_fixture", replied: true, resolved: true });
            await send(true);
            assert.equal(posts, 1);
            assert.equal(resolves, 1);
        } else {
            await assert.rejects(() => send(scenario === "unsent"), /changed|different PR|not reposted/);
            assert.equal(resolves, 0);
            assert.equal(posts, scenario === "changed" ? 1 : 0);
        }
    }
});

test("ADO observer replies use original threads and reconcile without duplicate posts", async () => {
    const original = [{ id: 1, content: "Fix A.kt compatibility." }];
    const comments = [...original];
    let posts = 0;
    let status = 1;
    const provider = new ExternalProvider({ run: async () => "fixture-token" }, async (url, init) => {
        assert.match(String(url), /pullrequests\/77\/threads\/5/);
        if (init?.method === "POST") {
            posts++;
            comments.push({ id: 2, content: JSON.parse(String(init.body)).content });
            throw new Error("Lost ADO reply response");
        }
        if (init?.method === "PATCH") status = JSON.parse(String(init.body)).status;
        return Response.json({ id: 5, comments, status });
    });
    const repo: RepositoryTarget = { provider: "ado", hostname: "dev.azure.com", slug: "boards-org/Engineering/broker", baseBranch: "dev", localPath: ".", account: "" };
    const send = (retry: boolean) => provider.replyRepairThread(repo, 77, { id: 5, comments: original }, "Addressed by repair.", "ado-repair-marker", async () => {}, retry);
    await assert.rejects(() => send(false), /Lost ADO reply response/);
    assert.deepEqual(await send(true), { threadId: "5", replied: true, resolved: true });
    await send(true);
    assert.equal(posts, 1);
    assert.equal(status, 2);
});

test("observer CI recovery rejects foreign, stale and incomplete build evidence before any write", async () => {
    for (const drift of ["head", "repository", "branch", "parameters", "attempt", "stage", "publisher", "policy"]) {
        const h = adoObserverFixture(drift === "policy" ? "stale" : "checkout");
        const snapshot = h.snapshot();
        const plan = await h.provider.prepareObserverEffect(h.repository, 42, snapshot,
            drift === "policy" ? { kind: "refresh" } : { kind: "retry", stageOnly: true });
        assert.equal(plan.mutations.length, 1);
        const publisher = { ...h.repository, publisherId: h.state.publisher, publisherAccount: "" };
        const change = () => {
            if (drift === "head") h.state.head = "b".repeat(40);
            if (drift === "repository") h.state.build.repository.id = "another-repository";
            if (drift === "branch") h.state.build.sourceBranch = "refs/pull/999/merge";
            if (drift === "parameters") h.state.build.parameters = "{invalid";
            if (drift === "attempt") h.state.stages[1]!.attempt++;
            if (drift === "stage") h.state.stages[1]!.identifier = "different-stage";
            if (drift === "publisher") h.state.publisher = "another-publisher";
            if (drift === "policy") h.state.policy.context.buildId = 999;
        };
        await assert.rejects(() => h.provider.sendObserverMutation(h.repository, plan.mutations[0]!, publisher, async () => change()),
            (error: unknown) => error instanceof FactoryError &&
                ["publication_identity_changed", "observer_evidence_changed", "observer_evidence_incomplete"].includes(error.code), drift);
        assert.equal(h.state.writes.length, 0, drift);
    }
});

test("observer status rendering and publication preserve human text and reconcile the original exact body", async () => {
    for (const providerKind of ["github", "ado"] as const) {
        const h = adoObserverFixture();
        h.state.passed = true;
        const githubRepo: RepositoryTarget = { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev",
            localPath: ".", account: "fixture-user" };
        let changedHead = false;
        let publisher = "fixture-publisher";
        const github = new ExternalProvider({
            run: async (_command, args, options = {}) => {
                if (args[0] === "auth") {
                    assert.ok(args.includes("fixture-user"));
                    return "fixture-token";
                }
                if (args[5] === "user") return JSON.stringify({ id: publisher });
                assert.equal(args[5], "repos/example/common/pulls/42");
                if (args[4] === "PATCH") {
                    h.state.body = JSON.parse(options.input!).body;
                    h.state.writes.push("github-status");
                    if (h.state.loseResponse) { h.state.loseResponse = ""; throw new Error("Lost status response"); }
                }
                return JSON.stringify({ number: 42, state: "open", merged_at: null, body: h.state.body,
                    head: { sha: changedHead ? "b".repeat(40) : h.state.head, ref: "factory/test", repo: { full_name: githubRepo.slug } },
                    base: { ref: "dev", repo: { full_name: githubRepo.slug } } });
            },
        });
        const repo = providerKind === "github" ? githubRepo : h.repository;
        const provider = providerKind === "github" ? github : h.provider;
        const snapshot = { ...object(h.snapshot(), "Snapshot"), provider: providerKind, body: h.state.body };
        const request = { kind: "status" as const, block: "Current status: ready-for-human-merge",
            ci: { status: "passed", headSha: h.state.head, evidence: ["All current-head checks passed."] } };
        const plan = await provider.prepareObserverEffect(repo, 42, snapshot as Json, request);
        const mutation = plan.mutations[0]!;
        assert.equal(mutation.kind, "status");
        if (mutation.kind !== "status") throw new Error("Wrong fixture plan");
        assert.match(mutation.body, /^Human description\. Fixes AB#1001/);
        assert.match(mutation.body, /ready-for-human-merge/);
        const binding = { ...repo, publisherId: "fixture-publisher", publisherAccount: repo.account };
        await assert.rejects(() => provider.sendObserverMutation(repo, mutation, binding, async () => { h.state.body += "\nConcurrent human edit"; }),
            /changed/);
        assert.equal(h.state.writes.length, 0);
        assert.match(h.state.body, /Concurrent human edit/);
        h.state.body = mutation.before;
        if (providerKind === "github") {
            changedHead = true;
            await assert.rejects(() => provider.sendObserverMutation(repo, mutation, binding, async () => {}), /head changed/);
            changedHead = false;
            publisher = "different-publisher";
            await assert.rejects(() => provider.sendObserverMutation(repo, mutation, binding, async () => {}), /publisher changed/);
            publisher = "fixture-publisher";
        }
        h.state.loseResponse = providerKind === "github" ? "status" : "git/repositories/authenticator/pullrequests/42";
        await assert.rejects(() => provider.sendObserverMutation(repo, mutation, binding, async () => {}), /Lost/);
        const writes = h.state.writes.length;
        assert.ok(await provider.findObserverMutation(repo, mutation));
        assert.equal(h.state.writes.length, writes);
        h.state.body += "\nA subsequent human update";
        await assert.rejects(() => provider.findObserverMutation(repo, mutation), /edited after an uncertain status update/);
        assert.equal(h.state.writes.length, writes);
    }
});

test("an existing branch is never overwritten by local publication", async () => {
    const h = githubPublicationFixture();
    try {
        const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
        git(h.repoDir, `--git-dir=${h.bare}`, "update-ref", `refs/heads/${binding.branch}`, binding.source.base);
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding));
        assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${binding.branch}`), binding.source.base);
        assert.equal(h.state.posts, 0);
    } finally { h.cleanup(); }
});

test("lost GitHub receipts reconcile only the unique original PR and publisher", async () => {
    const h = githubPublicationFixture();
    try {
        const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
        h.state.failAfterCreate = true;
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding), /Connection lost/);
        const original = h.state.pr!;
        const teammate = { ...h.localConfig, repositories: { broker: { ...h.localConfig.repositories.broker!, account: "teammate" } } };
        const receipt = await h.provider.findLocalPublication(teammate, item, wi, h.marker, binding);
        assert.deepEqual(receipt, {
            submitted: true, mode: "local", provider: "github", marker: h.marker, branch: binding.branch,
            headSha: binding.headSha, prNumber: 77, url: original.html_url,
        });
        for (const changed of [
            { ...original, user: { ...original.user, id: 8 } },
            { ...original, user: { ...original.user, login: "different" } },
            { ...original, head: { ...original.head, sha: "b".repeat(binding.headSha.length) } },
            { ...original, base: { ...original.base, ref: "main" } },
            { ...original, body: original.body.replace("Fixes AB#42", "Fixes AB#420") },
            { ...original, body: original.body.replace(h.marker, `${h.marker}-extra`) },
            { ...original, html_url: "https://msft.ghe.com/example/other/pull/77" },
        ]) {
            h.state.pr = changed;
            await assert.rejects(() => h.provider.findLocalPublication(teammate, item, wi, h.marker, binding),
                (error: unknown) => error instanceof FactoryError && error.code === "publication_identity_changed");
        }
        h.state.pr = original;
        for (const lookup of ["ambiguous", "incomplete"] as const) {
            h.state.lookup = lookup;
            await assert.rejects(() => h.provider.findLocalPublication(teammate, item, wi, h.marker, binding),
                (error: unknown) => error instanceof FactoryError && error.code === "reconciliation_required");
        }
        h.state.lookup = "normal";
        h.state.pr = null;
        assert.equal(await h.provider.findLocalPublication(teammate, item, wi, h.marker, binding), null);
        assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${binding.branch}`), binding.headSha,
            "a negative PR lookup does not prove that the branch was never pushed");
        assert.equal(h.state.pushes, 1);
        assert.equal(h.state.posts, 1);
    } finally { h.cleanup(); }
});

interface AdoPr {
    pullRequestId: number;
    status: string;
    repository: { id: string };
    createdBy: { id: string };
    sourceRefName: string;
    targetRefName: string;
    lastMergeSourceCommit: { commitId: string };
    description: string;
}

function adoPublicationFixture() {
    const local = publicationFixture({
        provider: "ado", hostname: "dev.azure.com", slug: "boards-org/Engineering/broker",
        baseBranch: "dev", account: "", localPath: ".",
    });
    const publisher = "01234567-89ab-cdef-0123-456789abcdef";
    const otherPublisher = "fedcba98-7654-3210-fedc-ba9876543210";
    const state: {
        base: string; authenticated: string; authorized: string; pr: AdoPr | null; links: number[];
        lookup: "normal" | "ambiguous" | "incomplete"; failAfterCreate: boolean; failAfterPush: boolean;
        switchPublisherAfterPush: boolean; pushes: number; posts: number; postedWorkItems: unknown;
    } = {
        base: local.implementation.sourceBinding.base, authenticated: publisher, authorized: publisher, pr: null, links: [42],
        lookup: "normal", failAfterCreate: false, failAfterPush: false, switchPublisherAfterPush: false,
        pushes: 0, posts: 0, postedWorkItems: null,
    };
    const runner: CommandRunner = {
        run: async (command, args, options = {}) => {
            if (command === "az") return "fixture-ado-token";
            if (command !== "git") throw new Error(`Unexpected command: ${command}`);
            if (args[0]?.startsWith("--git-dir=") && args[1] === "push") {
                state.pushes++;
                const remoteIndex = args.indexOf(local.origin);
                assert.ok(remoteIndex >= 0);
                const rewritten = [...args];
                rewritten[remoteIndex] = local.bare;
                const output = await systemRunner.run(command, rewritten, options);
                if (state.switchPublisherAfterPush) state.authenticated = state.authorized = otherPublisher;
                if (state.failAfterPush) throw new Error("Connection lost after branch creation.");
                return output;
            }
            return systemRunner.run(command, args, options);
        },
    };
    const fetcher: typeof fetch = async (input, init) => {
        const url = new URL(String(input));
        const method = init?.method || "GET";
        assert.equal(init?.redirect, "error");
        assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer fixture-ado-token");
        if (url.pathname === "/boards-org/_apis/connectionData") {
            return Response.json({ authenticatedUser: { id: state.authenticated }, authorizedUser: { id: state.authorized } });
        }
        const prefix = "/boards-org/Engineering/_apis/git/repositories/broker";
        if (url.pathname === prefix && method === "GET") {
            return Response.json({ id: "repository-id", name: "broker", project: { name: "Engineering" } });
        }
        if (url.pathname === `${prefix}/refs`) {
            return Response.json({ value: [{ name: "refs/heads/dev", objectId: state.base }] });
        }
        if (url.pathname === `${prefix}/pullrequests` && method === "POST") {
            state.posts++;
            const submitted = JSON.parse(String(init?.body)) as {
                sourceRefName: string; targetRefName: string; description: string; workItemRefs: unknown;
            };
            const sha = git(local.repoDir, `--git-dir=${local.bare}`, "rev-parse", submitted.sourceRefName);
            state.postedWorkItems = submitted.workItemRefs;
            state.pr = {
                pullRequestId: 77, status: "active", repository: { id: "repository-id" },
                createdBy: { id: state.authorized.toLowerCase() }, sourceRefName: submitted.sourceRefName,
                targetRefName: submitted.targetRefName, lastMergeSourceCommit: { commitId: sha },
                description: submitted.description,
            };
            if (state.failAfterCreate) throw new Error("Connection lost after ADO PR creation.");
            return Response.json({ pullRequestId: 77 });
        }
        if (url.pathname === `${prefix}/pullrequests`) {
            if (state.lookup === "incomplete") return Response.json({ value: Array.from({ length: 100 }, () => ({ pullRequestId: 77 })) });
            if (state.lookup === "ambiguous") return Response.json({ value: [{ pullRequestId: 77 }, { pullRequestId: 78 }] });
            return Response.json({ value: state.pr ? [{ pullRequestId: state.pr.pullRequestId }] : [] });
        }
        if (url.pathname === `${prefix}/pullrequests/77`) return Response.json(state.pr);
        if (url.pathname === `${prefix}/pullrequests/77/workitems`) return Response.json({ value: state.links.map((id) => ({ id })) });
        throw new Error(`Unexpected ADO endpoint: ${method} ${url.pathname}`);
    };
    return { ...local, state, publisher, otherPublisher, provider: new ExternalProvider(runner, fetcher) };
}

test("ADO publication rejects impersonation and binds the reviewed commit to its creator", async () => {
    const h = adoPublicationFixture();
    try {
        h.state.authorized = h.otherPublisher;
        await assert.rejects(() => h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation),
            (error: unknown) => error instanceof FactoryError && error.code === "ado_impersonation_unsupported");
        assert.equal(h.state.pushes, 0);
        assert.equal(h.state.posts, 0);

        h.state.authorized = h.publisher.toUpperCase();
        const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
        const receipt = await h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding);
        assert.deepEqual(receipt, {
            submitted: true, mode: "local", provider: "ado", marker: h.marker, branch: binding.branch,
            headSha: binding.headSha, prNumber: 77,
            url: "https://dev.azure.com/boards-org/Engineering/_git/broker/pullrequest/77",
        });
        assert.deepEqual(h.state.postedWorkItems, [{ id: "42" }]);
        assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${binding.branch}`), binding.headSha);
        assert.equal(git(h.repoDir, "rev-parse", `${binding.headSha}^{tree}`), h.implementation.sourceBinding.tree);
        assert.equal(h.state.pushes, 1);
        assert.equal(h.state.posts, 1);
    } finally { h.cleanup(); }
});

test("lost ADO receipts reconcile only the unique original PR, creator, and work-item link", async () => {
    const h = adoPublicationFixture();
    try {
        const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
        h.state.failAfterCreate = true;
        await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding), /Connection lost/);
        const original = h.state.pr!;
        assert.deepEqual(await h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding), {
            submitted: true, mode: "local", provider: "ado", marker: h.marker, branch: binding.branch,
            headSha: binding.headSha, prNumber: 77,
            url: "https://dev.azure.com/boards-org/Engineering/_git/broker/pullrequest/77",
        });
        for (const changed of [
            { ...original, createdBy: { id: h.otherPublisher } },
            { ...original, repository: { id: "different-repository" } },
            { ...original, status: "completed" },
            { ...original, sourceRefName: "refs/heads/different" },
            { ...original, targetRefName: "refs/heads/main" },
            { ...original, lastMergeSourceCommit: { commitId: "b".repeat(binding.headSha.length) } },
            { ...original, description: original.description.replace(h.marker, `${h.marker}-extra`) },
        ]) {
            h.state.pr = changed;
            await assert.rejects(() => h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding),
                (error: unknown) => error instanceof FactoryError && error.code === "publication_identity_changed");
        }
        h.state.pr = original;
        h.state.links = [420];
        await assert.rejects(() => h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding),
            (error: unknown) => error instanceof FactoryError && error.code === "publication_identity_changed");
        h.state.links = [42];
        for (const lookup of ["ambiguous", "incomplete"] as const) {
            h.state.lookup = lookup;
            await assert.rejects(() => h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding),
                (error: unknown) => error instanceof FactoryError && error.code === "reconciliation_required");
        }
        h.state.lookup = "normal";
        h.state.pr = null;
        assert.equal(await h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding), null);
        assert.equal(h.state.pushes, 1);
        assert.equal(h.state.posts, 1);
    } finally { h.cleanup(); }
});

test("ADO branch creation remains uncertain if the push receipt is lost or the publisher changes", async () => {
    for (const cause of ["lost-push-receipt", "publisher-changed"] as const) {
        const h = adoPublicationFixture();
        try {
            const binding = await h.provider.prepareLocalPublication(h.localConfig, item, wi, h.marker, h.implementation);
            h.state.failAfterPush = cause === "lost-push-receipt";
            h.state.switchPublisherAfterPush = cause === "publisher-changed";
            await assert.rejects(() => h.provider.publishLocal(h.localConfig, item, wi, h.marker, h.implementation, binding),
                (error: unknown) => cause === "lost-push-receipt"
                    ? error instanceof Error && /Connection lost/.test(error.message)
                    : error instanceof FactoryError && error.code === "publication_identity_changed");
            assert.equal(git(h.repoDir, `--git-dir=${h.bare}`, "rev-parse", `refs/heads/${binding.branch}`), binding.headSha);
            assert.equal(await h.provider.findLocalPublication(h.localConfig, item, wi, h.marker, binding), null);
            assert.equal(h.state.pushes, 1);
            assert.equal(h.state.posts, 0);
        } finally { h.cleanup(); }
    }
});

test("ExternalProvider strips ambient GH_TOKEN/GHE tokens, isolates host credentials, checks reference repositories, and authenticates repair fetches", async () => {
    const previousEnv = {
        GH_TOKEN: process.env.GH_TOKEN,
        GITHUB_TOKEN: process.env.GITHUB_TOKEN,
        GH_ENTERPRISE_TOKEN: process.env.GH_ENTERPRISE_TOKEN,
        GH_HOST: process.env.GH_HOST,
    };
    process.env.GH_TOKEN = "ambient-emu-token";
    process.env.GITHUB_TOKEN = "ambient-actions-token";
    process.env.GH_ENTERPRISE_TOKEN = "ambient-ghe-token";
    process.env.GH_HOST = "wrong.host.example";
    try {
        const calls: { command: string; args: string[]; env?: NodeJS.ProcessEnv }[] = [];
        const gheRepo: RepositoryTarget = {
            provider: "github", hostname: "msft.ghe.com", slug: "security/ad-accounts-for-android",
            baseBranch: "dev", localPath: ".", account: "shjameel",
        };
        const ghRepo: RepositoryTarget = {
            provider: "github", hostname: "github.com", slug: "AzureAD/microsoft-authentication-library-common-for-android",
            baseBranch: "dev", localPath: ".", account: "shahzaibj",
        };
        const refRepo: RepositoryTarget = {
            provider: "github", hostname: "github.com", slug: "AzureAD/microsoft-authentication-library-for-android",
            baseBranch: "dev", localPath: ".", account: "shjameel_microsoft",
        };
        let wrongLogin = false;
        const provider = new ExternalProvider({
            run: async (command, args, options = {}) => {
                calls.push({ command, args, env: options.env });
                if (command === "az") return "ado-token";
                if (command === "gh" && args[0] === "auth" && args[1] === "token") {
                    assert.equal(options.env?.GH_TOKEN, undefined, "ambient GH_TOKEN must not poison gh auth token");
                    assert.equal(options.env?.GITHUB_TOKEN, undefined);
                    assert.equal(options.env?.GH_ENTERPRISE_TOKEN, undefined);
                    assert.equal(options.env?.GH_HOST, undefined);
                    const user = args[args.indexOf("--user") + 1];
                    return `keyring-token-for-${user}`;
                }
                if (command === "gh" && args[0] === "api") {
                    const host = args[args.indexOf("--hostname") + 1];
                    if (host === "msft.ghe.com") {
                        assert.equal(options.env?.GH_ENTERPRISE_TOKEN, "keyring-token-for-shjameel");
                        assert.equal(options.env?.GH_TOKEN, "keyring-token-for-shjameel");
                        assert.equal(options.env?.GH_HOST, "msft.ghe.com");
                    } else {
                        assert.match(String(options.env?.GH_TOKEN), /^keyring-token-for-(shahzaibj|shjameel_microsoft)$/);
                        assert.equal(options.env?.GH_ENTERPRISE_TOKEN, undefined);
                        assert.equal(options.env?.GH_HOST, "github.com");
                    }
                    const endpoint = args.at(-1)!;
                    if (endpoint === "user") {
                        const login = host === "msft.ghe.com"
                            ? (wrongLogin ? "shjameel_microsoft" : "shjameel")
                            : (options.env?.GH_TOKEN === "keyring-token-for-shahzaibj" ? "shahzaibj" : "shjameel_microsoft");
                        return JSON.stringify({ id: 42, login });
                    }
                    if (endpoint.endsWith("/branches/dev")) {
                        return JSON.stringify({ commit: { sha: "f547c546bbedc9c0e3091c0d4d2ecf0fffbcac63" } });
                    }
                    return JSON.stringify({ full_name: endpoint.replace(/^repos\//, "") });
                }
                if (command === "git") {
                    if (args[0] === "remote") return "https://msft.ghe.com/security/ad-accounts-for-android.git";
                    assert.equal(options.env?.GIT_TERMINAL_PROMPT, "0");
                    assert.match(String(options.env?.GIT_CONFIG_VALUE_0), /^AUTHORIZATION: basic /);
                    assert.ok(args.includes("fetch"));
                    assert.ok(args.includes("+refs/heads/dev:refs/remotes/origin/dev"));
                    return "";
                }
                throw new Error(`Unexpected command ${command} ${args.join(" ")}`);
            },
        }, async () => Response.json({ id: "proj-1", name: "Engineering" }));

        const cfg: Configuration = {
            ...config(),
            repositories: { broker: gheRepo, common: ghRepo },
            referenceRepositories: { msal: refRepo },
        };
        const preflight = object(await provider.preflight(cfg), "Preflight");
        assert.equal(Array.isArray(preflight.repositories) && preflight.repositories.length, 2);
        assert.equal(Array.isArray(preflight.referenceRepositories) && preflight.referenceRepositories.length, 1);

        wrongLogin = true;
        await assert.rejects(() => provider.preflight(cfg), /authenticated as shjameel_microsoft, not configured account shjameel/);
        wrongLogin = false;

        await provider.verifyLocalReadiness(gheRepo, "f547c546bbedc9c0e3091c0d4d2ecf0fffbcac63");
        await assert.rejects(() => provider.verifyLocalReadiness(gheRepo, "0".repeat(40)),
            (error: unknown) => error instanceof FactoryError && error.code === "local_base_changed");

        await provider.fetchRepairRefs(gheRepo, process.cwd(), ["copilot/fix-1", "dev"]);
        assert.ok(calls.some((call) => call.command === "git" && call.args.includes("https://msft.ghe.com/security/ad-accounts-for-android.git") && call.args.includes("refs/heads/copilot/fix-1")));

        const adoAuthenticatorRepo: RepositoryTarget = {
            provider: "ado", hostname: "dev.azure.com", slug: "msazure/One/AD-MFA-phonefactor-phoneApp-android",
            baseBranch: "working", localPath: "authenticator", account: "",
        };
        const adoProvider = new ExternalProvider({
            run: async (command, args, options = {}) => {
                if (command === "az") return "ado-bearer-token";
                if (command === "git" && args[0] === "remote") {
                    return "https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android";
                }
                if (command === "git" && args[0] === "fetch") {
                    assert.equal(options.env?.GIT_CONFIG_VALUE_0, "AUTHORIZATION: bearer ado-bearer-token");
                    assert.ok(args.includes("https://dev.azure.com/msazure/One/_git/AD-MFA-phonefactor-phoneApp-android"));
                    assert.ok(args.includes("+refs/heads/working:refs/remotes/origin/working"));
                    return "";
                }
                throw new Error(`Unexpected ADO git command ${command} ${args.join(" ")}`);
            },
        });
        await adoProvider.fetchRepairRefs(adoAuthenticatorRepo, process.cwd(), ["copilot/fix-auth", "working"]);
    } finally {
        for (const [key, value] of Object.entries(previousEnv)) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
    }
});
