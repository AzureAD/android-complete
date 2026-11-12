// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, symlinkSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import type { CopilotSession } from "@github/copilot-sdk";
import {
    createReaderTools, FACTORY_CODER, FACTORY_CODE_REVIEWER, FACTORY_DESIGN_AUTHOR, FACTORY_DESIGN_REVIEWER, FACTORY_PLANNER, FACTORY_RESEARCHER,
    readerAgent, readerAgents, readerToolNames, runReader,
} from "../runtime/reader.ts";
import { blankState } from "../core/lifecycle-store.ts";
import { actor, fixture, requirements } from "./helpers.ts";
import { workerGuidance, workerGuidanceFiles } from "../runtime/guidance.ts";
import { Application } from "../core/application.ts";
import { sessionConfiguration } from "../runtime/host.ts";
import { FactoryError } from "../core/contracts.ts";
import { sourcePin, sourcePins } from "../runtime/sources.ts";

const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));

test("workers get only scoped native read tools; host denial is never replaced with filesystem access", async () => {
    const f = fixture();
    const calls: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0][] = [];
    let denied = false;
    let offered = ["view", "glob", "grep"];
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "reader", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "check", actor);
        const session = { sessionId: execution.sessionId, rpc: { tools: {
            initializeAndValidate: async () => ({ toolNames: [] }),
            getCurrentMetadata: async () => ({ tools: offered.map((name) => ({ name, description: "fixture" })) }),
            execute: async (args: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0]) => {
            calls.push(args);
            return denied ? { resultType: "failure" as const, textResultForLlm: "Managed/content policy denied the read." } : "native result";
        } } } };
        const tools = createReaderTools(() => f.app, () => session);
        assert.deepEqual(readerAgent.tools, readerToolNames);
        assert.ok(readerAgents.every((agent) => JSON.stringify(agent.tools) === JSON.stringify(readerToolNames)));
        assert.ok(!readerAgent.tools?.some((name) => /shell|write|approve|command|task/.test(name)));
        const file = path.join(f.workspace, "A.kt");
        writeFileSync(file, "needle");
        const scope = { featureId: feature.id, executionId: execution.id, path: file };
        const read = tools[0]!;
        assert.equal(await read.handler(scope), "native result");
        assert.equal(calls[0]?.name, "view");
        denied = true;
        await assert.rejects(() => read.handler(scope), /Managed\/content policy denied/);
        assert.equal(calls.length, 2, "A denied native read must not fall back or retry.");
        await assert.rejects(() => read.handler({ ...scope, path: f.options.database }), /approved source/);
        await assert.rejects(() => read.handler({ ...scope, view_range: [1, 301] }), /at most 300/);
        await assert.rejects(() => read.handler({ ...scope, forceReadLargeFiles: true }), /Unknown|Unsupported|Unexpected/i);
        await assert.rejects(() => tools[1]!.handler({ ...scope, path: f.workspace, pattern: "..\\*" }), /relative glob/);
        const outside = path.join(f.directory, "outside");
        mkdirSync(outside);
        symlinkSync(outside, path.join(f.workspace, "escape"), process.platform === "win32" ? "junction" : "dir");
        await assert.rejects(() => read.handler({ ...scope, path: path.join(f.workspace, "escape") }), /approved source/);
        session.sessionId = "another-session";
        await assert.rejects(() => read.handler(scope), /another execution session/);
        session.sessionId = execution.sessionId;
        offered = [];
        await assert.rejects(() => read.handler(scope), /does not offer the native/);
        assert.equal(calls.length, 2);
    } finally { f.cleanup(); }
});

test("Factory planner uses its single structured contract without legacy file or handoff requirements", async () => {
    let captured: { prompt: string; options: { agent?: string } } | undefined;
    const ctx = { agent: async (prompt: string, options: { agent?: string }) => {
        captured = { prompt, options };
        return "planned";
    } };
    assert.equal(await runReader(ctx, "feature-id", "execution-id", "Produce the plan.", {
        agent: "feature-planner", schema: { type: "object" },
    }), "planned");
    assert.equal(captured?.options.agent, FACTORY_PLANNER);
    assert.match(captured!.prompt, /"featureId":"feature-id","executionId":"execution-id"/);
    const plannerPrompt = readerAgents.find((candidate) => candidate.name === FACTORY_PLANNER)!.prompt!;
    assert.match(plannerPrompt, /authoritative approved design, requirements, configuration and evidence/);
    assert.match(plannerPrompt, /only JSON matching the supplied schema/);
    assert.match(plannerPrompt, /Do not require a separate local design\/research artifact/);
    assert.match(plannerPrompt, /Do not execute user-facing skills.*create work items, dispatch/);
});

test("packed refs and missing worktree files never fall back to another checkout", async () => {
    const f = fixture();
    let app: Application | undefined;
    const git = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: "pipe" }).trim();
    try {
        git(f.workspace, "init", "--quiet");
        git(f.workspace, "config", "user.name", "Fixture");
        git(f.workspace, "config", "user.email", "fixture@example.invalid");
        writeFileSync(path.join(f.workspace, "README.md"), "Workspace fixture");
        git(f.workspace, "add", "README.md");
        git(f.workspace, "commit", "--quiet", "-m", "Workspace");
        const common = path.join(f.workspace, "common");
        mkdirSync(common);
        git(common, "init", "--quiet", "--initial-branch=dev");
        git(common, "config", "user.name", "Fixture");
        git(common, "config", "user.email", "fixture@example.invalid");
        writeFileSync(path.join(common, "A.kt"), "// different checkout\n");
        git(common, "add", "A.kt");
        git(common, "commit", "--quiet", "-m", "Other checkout");
        const worktree = path.join(f.directory, "isolated");
        git(f.workspace, "worktree", "add", "--detach", worktree, "HEAD");
        const isolatedCommon = path.join(worktree, "common");
        git(worktree, "clone", "--quiet", "--no-hardlinks", common, isolatedCommon);
        git(isolatedCommon, "config", "user.name", "Fixture");
        git(isolatedCommon, "config", "user.email", "fixture@example.invalid");
        writeFileSync(path.join(isolatedCommon, "A.kt"), "// admitted checkout\n");
        git(isolatedCommon, "add", "A.kt");
        git(isolatedCommon, "commit", "--quiet", "-m", "Admitted checkout");
        const head = git(isolatedCommon, "rev-parse", "HEAD");
        git(isolatedCommon, "pack-refs", "--all", "--prune");
        assert.equal(existsSync(path.join(isolatedCommon, ".git", "refs", "heads", "dev")), false);
        assert.equal(existsSync(path.join(common, ".git", "refs", "heads", "dev")), true);
        app = new Application({ database: path.join(f.directory, "isolated.sqlite"), workspace: worktree });
        const feature = app.execute({ kind: "create_feature", requestId: "isolated-reader", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const run = app.lifecycle.begin(feature.id, "check", actor, { [isolatedCommon]: await sourcePin(isolatedCommon, new Set([isolatedCommon])) });
        const calls: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0][] = [];
        const session = { sessionId: run.sessionId, rpc: { tools: {
            initializeAndValidate: async () => ({ toolNames: [] }),
            getCurrentMetadata: async () => ({ tools: ["view", "glob", "grep"].map((name) => ({ name, description: "fixture" })) }),
            execute: async (args: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0]) => { calls.push(args); return "native source"; },
        } } };
        const readers = createReaderTools(() => app!, () => session);
        const scope = { featureId: feature.id, executionId: run.id };
        assert.equal(await readers[0]!.handler({ ...scope, path: "." }), "native source", "root listings cannot advertise repositories from the main checkout");
        await readers[0]!.handler({ ...scope, path: "common\\A.kt" });
        assert.equal(calls.at(-1)!.arguments && objectPath(calls.at(-1)!.arguments), path.join(isolatedCommon, "A.kt"));
        for (const request of [
            "common\\.git\\refs\\heads\\dev",
            path.join(common, "A.kt"),
            path.join(f.workspace, "README.md"),
        ]) {
            await assert.rejects(() => readers[0]!.handler({ ...scope, path: request }),
                (error: unknown) => error instanceof FactoryError &&
                    ["worker_source_missing", "worker_read_outside_scope"].includes(error.code));
        }
        symlinkSync(common, path.join(worktree, "escape"), process.platform === "win32" ? "junction" : "dir");
        await assert.rejects(() => readers[0]!.handler({ ...scope, path: "escape\\A.kt" }), /approved source/);
        const config = sessionConfiguration(worktree, "Isolated worker");
        assert.deepEqual(config.additionalDirectories, []);
        const permission = config.onPermissionRequest;
        assert.ok(permission);
        const allowed = await permission({ kind: "read", path: path.join(isolatedCommon, "A.kt"), intention: "Read approved source" },
            { sessionId: run.sessionId });
        const denied = await permission({ kind: "read", path: path.join(common, "A.kt"), intention: "Read another checkout" },
            { sessionId: run.sessionId });
        assert.equal(allowed.kind, "approve-once");
        assert.equal(denied.kind, "reject");
        assert.equal(calls.length, 2, "missing or rejected paths must never reach an alternate native read");
        assert.equal(git(isolatedCommon, "rev-parse", "HEAD"), head);
    } finally { app?.close(); f.cleanup(); }
});

test("source metadata and native read receipts attest the admitted checkout without inventing dependency equivalence", async () => {
    const f = fixture();
    const git = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: "pipe" }).trim();
    try {
        const init = (root: string, content: string) => {
            git(root, "init", "--quiet");
            git(root, "config", "user.name", "Fixture");
            git(root, "config", "user.email", "fixture@example.invalid");
            writeFileSync(path.join(root, "A.kt"), content);
            git(root, "add", "A.kt"); git(root, "commit", "--quiet", "-m", "Fixture");
            return git(root, "rev-parse", "HEAD");
        };
        init(f.workspace, "// integration root\n");
        const library = path.join(f.workspace, "library");
        mkdirSync(library);
        const revision = init(library, "// configured Common\n");
        mkdirSync(path.join(f.workspace, "common"));
        writeFileSync(path.join(f.workspace, ".gitignore"), "/library/\n");
        writeFileSync(path.join(f.workspace, ".gitmodules"), '[submodule "common"]\n\tpath = common\n\turl = https://example.invalid/common.git\n');
        git(f.workspace, "update-index", "--add", "--cacheinfo", `160000,${revision},common`);
        git(f.workspace, "add", ".gitignore", ".gitmodules"); git(f.workspace, "commit", "--quiet", "-m", "Standalone dependency declaration");
        writeFileSync(path.join(library, "A.kt"), "// newer configured Common\n");
        git(library, "commit", "--quiet", "-am", "New Common");
        const configuredHead = git(library, "rev-parse", "HEAD");
        git(library, "pack-refs", "--all", "--prune");
        f.app.lifecycle.configure({
            repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev", account: "fixture", localPath: "library" } },
            referenceRepositories: { consumer: { provider: "github", hostname: "github.com", slug: "example/consumer", baseBranch: "dev", account: "reader", localPath: "library" } },
            boards: { organization: "example", project: "Engineering", workItemType: "Product Backlog Item",
                areaPath: "", iterationPath: "", assignee: "", parentId: null },
        }, actor);
        const feature = f.app.execute({ kind: "create_feature", requestId: "source-receipts", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const pins = await sourcePins(f.app.lifecycle);
        const run = f.app.lifecycle.begin(feature.id, "check", actor, pins);
        const scope = { featureId: feature.id, executionId: run.id };
        let calls = 0;
        let mutate = false;
        let deny = false;
        const session = { sessionId: run.sessionId, rpc: { tools: {
            initializeAndValidate: async () => ({ toolNames: [] }),
            getCurrentMetadata: async () => ({ tools: ["view", "glob", "grep"].map((name) => ({ name, description: "fixture" })) }),
            execute: async () => {
                calls++;
                if (mutate) writeFileSync(path.join(library, "A.kt"), "// concurrent native read change\n");
                return deny ? { resultType: "failure" as const, textResultForLlm: "Managed content policy denied." } : "// native inspected source";
            },
        } } };
        const tools = createReaderTools(() => f.app, () => session);
        const source = tools.find((tool) => tool.name === "software_factory_sources")!;
        const metadata = JSON.parse(String(await source.handler(scope)));
        assert.equal(calls, 0, "revision metadata does not read code outside the native pipeline");
        const common = metadata.repositories.find((repo: { repositories: string[] }) => repo.repositories.includes("common"));
        assert.equal(common.revision, configuredHead);
        assert.equal(common.matchesAdmission, true);
        assert.equal(common.clean, true);
        assert.deepEqual(common.referenceRepositories, ["consumer"], "read-only aliases must be visible in source receipts");
        const staleCommit = git(library, "rev-parse", "HEAD");
        const integration = metadata.repositories.find((repo: { repositories: string[] }) => repo.repositories.includes("android-complete"));
        assert.deepEqual(integration.gitlinks, [{ path: "common", revision, checkoutRoot: path.join(f.workspace, "common"), status: "uninitialized", admittedPin: null }]);
        assert.notEqual(common.revision, integration.gitlinks[0].revision);
        for (const index of [0, 1, 2]) {
            const result = await tools[index]!.handler({ ...scope, path: index === 0 ? path.join(library, "A.kt") : library,
                ...(index === 0 ? {} : { pattern: index === 1 ? "*.kt" : "native" }) });
            assert.match(String(result), /\[Application source receipt\]/);
            assert.ok(String(result).includes(configuredHead));
            assert.ok(String(result).includes(pins[library]!));
            assert.match(String(result), /"referenceRepositories":\["consumer"\]/);
            assert.match(String(result), /native inspected source/);
        }
        await assert.rejects(() => tools[0]!.handler({ ...scope, path: "common" }), /gitlink without a separately admitted/);
        const previousCalls = calls;
        deny = true;
        await assert.rejects(() => tools[0]!.handler({ ...scope, path: path.join(library, "A.kt") }), /Managed content policy denied/);
        assert.equal(calls, previousCalls + 1, "denial must not retry or attest the inaccessible content");
        deny = false;
        mutate = true;
        await assert.rejects(() => tools[0]!.handler({ ...scope, path: path.join(library, "A.kt") }), /source snapshot changed|Source changed during/);
        mutate = false;
        const callsAfterChange = calls;
        await assert.rejects(() => source.handler(scope), /source snapshot changed/);
        assert.equal(calls, callsAfterChange);
        f.app.lifecycle.store.updateExecution(feature.id, run.id, { status: "completed" });
        const nextRun = f.app.lifecycle.begin(feature.id, "check", actor, await sourcePins(f.app.lifecycle));
        session.sessionId = nextRun.sessionId;
        const dirtyMetadata = JSON.parse(String(await source.handler({ featureId: feature.id, executionId: nextRun.id })));
        const dirtyCommon = dirtyMetadata.repositories.find((repo: { repositories: string[] }) => repo.repositories.includes("common"));
        assert.equal(dirtyCommon.matchesAdmission, true);
        assert.equal(dirtyCommon.clean, false, "a pinned local change must not be presented as a clean commit");
        assert.equal(dirtyCommon.revision, staleCommit);
        const admittedPin = dirtyCommon.admittedPin;
        git(library, "add", "A.kt"); git(library, "commit", "--quiet", "-m", "New source head");
        await assert.rejects(() => source.handler({ featureId: feature.id, executionId: nextRun.id }), /source snapshot changed/);
        assert.equal(f.app.lifecycle.store.execution(feature.id, nextRun.id).sourcePins[library], admittedPin);
        session.sessionId = "wrong-owner";
        await assert.rejects(() => source.handler(scope), /another execution session/);
    } finally { f.cleanup(); }
});

function objectPath(value: unknown): unknown {
    return value !== null && typeof value === "object" && "path" in value ? value.path : undefined;
}

test("Factory design roles load specialist guidance without granting workers mutation or approval", async () => {
    const agentNames: string[] = [];
    const ctx = { agent: async (_prompt: string, options: { agent?: string }) => {
        agentNames.push(options.agent!);
        return "result";
    } };
    for (const [label, expected] of [
        ["Repository evidence", FACTORY_RESEARCHER],
        ["Draft success criteria", FACTORY_RESEARCHER],
        ["Design author", FACTORY_DESIGN_AUTHOR],
        ["Design revision", FACTORY_DESIGN_AUTHOR],
        ["Independent design reviewer", FACTORY_DESIGN_REVIEWER],
        ["Factory:coder:A:round-1", FACTORY_CODER],
        ["Factory:code-reviewer:A:round-1", FACTORY_CODE_REVIEWER],
    ]) {
        await runReader(ctx, "feature-id", "execution-id", "Perform the role.", { label });
        assert.equal(agentNames.at(-1), expected);
    }
    for (const name of [FACTORY_RESEARCHER, FACTORY_DESIGN_AUTHOR, FACTORY_DESIGN_REVIEWER, FACTORY_CODER, FACTORY_CODE_REVIEWER]) {
        const worker = readerAgents.find((candidate) => candidate.name === name)!;
        assert.deepEqual(worker.tools, readerToolNames);
        assert.match(worker.prompt!, /scoped (?:native|Factory)/);
    }
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_RESEARCHER)!.prompt!, /codebase-researcher\.agent\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_RESEARCHER)!.prompt!, /codebase-researcher\/references\/research-standards\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_AUTHOR)!.prompt!, /design-writer\.agent\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_AUTHOR)!.prompt!, /design-author\/references\/design-standards\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_REVIEWER)!.prompt!, /independent-review rubric/);
    for (const name of [FACTORY_CODER, FACTORY_CODE_REVIEWER]) {
        assert.match(readerAgents.find((candidate) => candidate.name === name)!.prompt!,
            /code-author\/references\/implementation-standards\.md/);
    }
});

test("workers consume domain guidance, never phase-entry skills or mode-switching instructions", () => {
    for (const [role, name] of [
        ["research", FACTORY_RESEARCHER], ["design", FACTORY_DESIGN_AUTHOR],
        ["review", FACTORY_DESIGN_REVIEWER], ["plan", FACTORY_PLANNER],
        ["code", FACTORY_CODER], ["codeReview", FACTORY_CODE_REVIEWER],
    ] as const) {
        const prompt = readerAgents.find((candidate) => candidate.name === name)!.prompt!;
        for (const file of workerGuidance[role]) assert.ok(prompt.includes(file), `${name} must load ${file}`);
        assert.doesNotMatch(prompt, /\/SKILL\.md|Factory mode|standalone mode/i);
    }
    for (const file of workerGuidanceFiles) {
        assert.doesNotMatch(file, /\/SKILL\.md$/);
        const content = readFileSync(path.join(repositoryRoot, file), "utf8");
        assert.doesNotMatch(content, /Factory mode|standalone mode|askQuestion|state-utils|gh auth switch|software_factory_command/i, file);
    }
});

test("feature entry skills route only to Factory and legacy registrations/writers stay retired", () => {
    for (const name of ["design-author", "design-reviewer", "feature-planner", "pbi-creator", "pbi-dispatcher"]) {
        const content = readFileSync(path.join(repositoryRoot, ".github", "skills", name, "SKILL.md"), "utf8");
        assert.ok(content.includes(".github/agents/software-factory.agent.md"), name);
        assert.doesNotMatch(content, /Factory mode|standalone mode|askQuestion|state-utils\.js|gh auth switch/i, name);
    }
    for (const file of [
        ".github/agents/feature-orchestrator.agent.md", ".github/agents/pbi-creator.agent.md",
        ".github/agents/agent-dispatcher.agent.md", ".github/hooks/orchestrator.json",
        ".github/hooks/subagent-start.js", ".github/hooks/subagent-stop.js", ".github/hooks/state-utils.js",
        "feature-orchestrator-plugin/.github/plugin/plugin.json", "feature-orchestrator-plugin/hooks/hooks.json",
        "feature-orchestrator-plugin/hooks/state-utils.js", "extensions/feature-orchestrator/package.json",
        "extensions/feature-orchestrator/src/extension.ts",
        ".github/skills/feature-planner/references/dispatch-workflow.md",
    ]) assert.equal(existsSync(path.join(repositoryRoot, file)), false, `${file} must not reactivate the old pipeline`);
});

test("assigned guides and workflow dependencies change the runtime fingerprint and missing files fail closed", async () => {
    const directory = mkdtempSync(path.join(tmpdir(), "factory-guidance-"));
    try {
        const appRoot = path.join(directory, "tools", "software-factory");
        const workflowFiles = [
            ".github/extensions/DevDesignDoc/workflow.mjs",
            ".github/extensions/CodeFeature/workflow.mjs",
            ".github/extensions/CodeFeature/observer.mjs",
            ".github/extensions/CodeFeature/milestones.mjs",
            ".github/extensions/CodeFeature/status.mjs",
            ".github/extensions/CodeFeature/verification.mjs",
            ".github/extensions/CodeFeature/ado-snapshot.ps1",
            ".github/extensions/SoftwareFactory/extension.mjs",
        ];
        for (const name of ["core", "chat", "server", "runtime", "providers", "workflows"]) {
            mkdirSync(path.join(appRoot, name), { recursive: true });
        }
        for (const file of [
            "tools/software-factory/core/evidence.ts", "tools/software-factory/runtime/guidance.ts", ...workerGuidanceFiles,
        ]) {
            const target = path.join(directory, file);
            mkdirSync(path.dirname(target), { recursive: true });
            copyFileSync(path.join(repositoryRoot, file), target);
        }
        for (const file of [
            "tools/software-factory/package-lock.json", "tools/software-factory/cli.ts", "tools/software-factory/mcp.ts", "tools/software-factory/evals/design.mjs",
            ...workflowFiles,
        ]) {
            const target = path.join(directory, file);
            mkdirSync(path.dirname(target), { recursive: true });
            writeFileSync(target, "");
        }
        const { runtimeFingerprint } = await import(pathToFileURL(path.join(appRoot, "core", "evidence.ts")).href);
        const initial = runtimeFingerprint();
        for (const file of [...workerGuidanceFiles, ...workflowFiles]) {
            const target = path.join(directory, file);
            const original = readFileSync(target);
            writeFileSync(target, Buffer.concat([original, Buffer.from("\nChanged guidance\n")]));
            assert.notEqual(runtimeFingerprint(), initial, `${file} must invalidate stale execution authority`);
            writeFileSync(target, original);
            assert.equal(runtimeFingerprint(), initial);
        }
        for (const file of [workerGuidanceFiles[0]!, ".github/extensions/CodeFeature/observer.mjs",
            ".github/extensions/CodeFeature/ado-snapshot.ps1"]) {
            const target = path.join(directory, file);
            const original = readFileSync(target);
            rmSync(target);
            assert.throws(() => runtimeFingerprint(), /ENOENT/);
            writeFileSync(target, original);
        }
    } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("an uncertain native admission cannot automatically become another paid submission", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "uncertain", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "check", actor);
        f.app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "error", admissionClaim: "first-controller" });
        assert.throws(() => f.app.lifecycle.begin(feature.id, "check", actor), /previous submission/i);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 1);
    } finally { f.cleanup(); }
});

test("legacy state upgrades without changing history and only one live controller claims a chat", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "legacy-owner", input: requirements() }, actor);
        const legacy = { ...blankState(), version: 1 };
        f.app.registry.connection.prepare("INSERT INTO factory_state VALUES (?,1,?)").run(feature.id, JSON.stringify(legacy));
        assert.equal(f.app.lifecycle.store.state(feature.id).version, 2);
        assert.equal(JSON.parse(String(f.app.registry.connection.prepare("SELECT content_json FROM factory_state WHERE feature_id=?").get(feature.id)!.content_json)).version, 1);
        f.app.lifecycle.store.change(feature.id, (state) => { state.sessionBinding = { sessionId: "original-chat", kind: "chat", established: true }; });
        assert.equal(f.app.registry.get(feature.id).current.id, feature.current.id);
        const store = f.app.lifecycle.store;
        store.heartbeat("original-chat", "owner-A");
        assert.equal(store.ownerOnline("original-chat"), true);
        assert.equal(store.ownsSession("original-chat", "owner-A"), true);
        assert.throws(() => store.heartbeat("original-chat", "owner-B"), /Another resident controller/);
        store.releaseOwner("original-chat", "owner-A");
        assert.equal(store.ownerOnline("original-chat"), false);
        store.heartbeat("original-chat", "owner-B");
        assert.equal(store.ownsSession("original-chat", "owner-B"), true);
    } finally { f.cleanup(); }
});

test("a changed session owner invalidates execution consent rather than moving the work silently", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "owner-consent", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const command = { kind: "start_workflow", featureId: feature.id, workflow: "design" };
        const before = f.app.prepare(command).consentKey!;
        f.app.lifecycle.store.change(feature.id, (state) => { state.sessionBinding = { sessionId: "original-chat", kind: "chat", established: true }; });
        assert.notEqual(f.app.prepare(command).consentKey, before);
        await assert.rejects(() => f.app.dispatch(command, actor, { granted: true, revisionId: before, actor }), /confirmation|scope/i);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

test("admitted observer repair worktrees under .software-factory-repairs are readable only by active implement executions and excluded from parent sourcePin", async () => {
    const f = fixture();
    try {
        execFileSync("git", ["init", "-b", "dev"], { cwd: f.workspace });
        execFileSync("git", ["config", "user.email", "factory@example.test"], { cwd: f.workspace });
        execFileSync("git", ["config", "user.name", "Factory Fixture"], { cwd: f.workspace });
        writeFileSync(path.join(f.workspace, "A.kt"), "class A { fun call() = 1 }\n");
        execFileSync("git", ["add", "A.kt"], { cwd: f.workspace });
        execFileSync("git", ["commit", "-m", "Base commit"], { cwd: f.workspace });

        const { sourceInventory, sourcePins, sourceReceipt } = await import("../runtime/sources.ts");
        const initialPins = await sourcePins(f.app.lifecycle);

        // Create a PR branch commit and a detached linked repair worktree inside .software-factory-repairs
        execFileSync("git", ["checkout", "-b", "factory/pr-branch"], { cwd: f.workspace });
        writeFileSync(path.join(f.workspace, "A.kt"), "class A { fun call() = 2 }\n");
        execFileSync("git", ["add", "A.kt"], { cwd: f.workspace });
        execFileSync("git", ["commit", "-m", "PR commit"], { cwd: f.workspace });
        const prHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: f.workspace, encoding: "utf8" }).trim();
        execFileSync("git", ["checkout", "dev"], { cwd: f.workspace });

        const repairRoot = path.join(f.workspace, ".software-factory-repairs", "pub-key-1", "0");
        mkdirSync(path.dirname(repairRoot), { recursive: true });
        execFileSync("git", ["worktree", "add", "--detach", repairRoot, prHead], { cwd: f.workspace });

        // Parent workspace sourcePins must stay identical even when .software-factory-repairs exists and is not in .gitignore
        const afterWorktreePins = await sourcePins(f.app.lifecycle);
        assert.deepEqual(afterWorktreePins, initialPins);

        const feature = f.app.execute({ kind: "create_feature", requestId: "repair-attestation", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });

        const designExec = f.app.lifecycle.begin(feature.id, "design", actor, initialPins);
        f.app.lifecycle.store.updateExecution(feature.id, designExec.id, { status: "completed" });

        // Record an active repair operation and an active implement execution with real sourcePins
        const implementExec = { ...designExec, id: "implement-exec-1", kind: "implement" as const, status: "running" as const };
        f.app.lifecycle.store.change(feature.id, (state) => {
            state.executions.push(implementExec);
            state.operations.push({
                key: "repair:pub-key-1:0",
                kind: "repair",
                itemId: "A",
                planId: "plan-1",
                configurationId: "cfg-1",
                claimId: "claim-1",
                status: "inflight",
                approvalId: "approval-1",
                receipt: null,
                error: null,
                updatedAt: new Date().toISOString(),
                repair: {
                    publicationKey: "pub-key-1",
                    round: 0,
                    expectedHead: prHead,
                    phase: "building",
                },
            });
        });

        const repairFile = path.join(repairRoot, "A.kt");
        const receipt = await sourceReceipt(f.app.lifecycle, implementExec, repairFile, feature.id);
        assert.ok(receipt);
        assert.equal(receipt.revision, prHead);
        assert.equal(receipt.repairRound, 0);
        assert.equal(receipt.matchesAdmission, true);
        assert.equal(receipt.clean, true);

        const inventory = await sourceInventory(f.app.lifecycle, implementExec, feature.id);
        assert.equal(inventory.repairCheckouts?.length, 1);
        assert.equal(inventory.repairCheckouts?.[0]?.revision, prHead);

        // Non-implement executions and unrecorded repair paths must be rejected
        await assert.rejects(() => sourceReceipt(f.app.lifecycle, designExec, repairFile, feature.id),
            (error: unknown) => error instanceof FactoryError && error.code === "worker_read_outside_scope");
        const rogueDir = path.join(f.workspace, ".software-factory-repairs", "unrecorded", "0");
        mkdirSync(rogueDir, { recursive: true });
        writeFileSync(path.join(rogueDir, "A.kt"), "rogue\n");
        await assert.rejects(() => sourceReceipt(f.app.lifecycle, implementExec, path.join(rogueDir, "A.kt"), feature.id),
            (error: unknown) => error instanceof FactoryError && error.code === "worker_source_unpinned");

        execFileSync("git", ["worktree", "remove", "--force", repairRoot], { cwd: f.workspace });
    } finally { f.cleanup(); }
});
