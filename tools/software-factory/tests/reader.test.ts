// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, symlinkSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import type { CopilotSession } from "@github/copilot-sdk";
import {
    createReaderTools, FACTORY_DESIGN_AUTHOR, FACTORY_DESIGN_REVIEWER, FACTORY_PLANNER, FACTORY_RESEARCHER,
    readerAgent, readerAgents, readerToolNames, runReader,
} from "../runtime/reader.ts";
import { blankState } from "../core/lifecycle-store.ts";
import { actor, fixture, requirements } from "./helpers.ts";
import { workerGuidance, workerGuidanceFiles } from "../runtime/guidance.ts";

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
    ]) {
        await runReader(ctx, "feature-id", "execution-id", "Perform the role.", { label });
        assert.equal(agentNames.at(-1), expected);
    }
    for (const name of [FACTORY_RESEARCHER, FACTORY_DESIGN_AUTHOR, FACTORY_DESIGN_REVIEWER]) {
        const worker = readerAgents.find((candidate) => candidate.name === name)!;
        assert.deepEqual(worker.tools, readerToolNames);
        assert.match(worker.prompt!, /scoped (?:native|Factory)/);
    }
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_RESEARCHER)!.prompt!, /codebase-researcher\.agent\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_RESEARCHER)!.prompt!, /codebase-researcher\/references\/research-standards\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_AUTHOR)!.prompt!, /design-writer\.agent\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_AUTHOR)!.prompt!, /design-author\/references\/design-standards\.md/);
    assert.match(readerAgents.find((candidate) => candidate.name === FACTORY_DESIGN_REVIEWER)!.prompt!, /independent-review rubric/);
});

test("workers consume domain guidance, never phase-entry skills or mode-switching instructions", () => {
    for (const [role, name] of [
        ["research", FACTORY_RESEARCHER], ["design", FACTORY_DESIGN_AUTHOR],
        ["review", FACTORY_DESIGN_REVIEWER], ["plan", FACTORY_PLANNER],
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

test("every assigned domain guide changes the runtime fingerprint and a missing guide fails closed", async () => {
    const directory = mkdtempSync(path.join(tmpdir(), "factory-guidance-"));
    try {
        const appRoot = path.join(directory, "tools", "software-factory");
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
            "tools/software-factory/package-lock.json", "tools/software-factory/cli.ts",
            ".github/extensions/DevDesignDoc/workflow.mjs", ".github/extensions/SoftwareFactory/extension.mjs",
        ]) {
            const target = path.join(directory, file);
            mkdirSync(path.dirname(target), { recursive: true });
            writeFileSync(target, "");
        }
        const { runtimeFingerprint } = await import(pathToFileURL(path.join(appRoot, "core", "evidence.ts")).href);
        const initial = runtimeFingerprint();
        for (const file of workerGuidanceFiles) {
            const target = path.join(directory, file);
            const original = readFileSync(target);
            writeFileSync(target, Buffer.concat([original, Buffer.from("\nChanged guidance\n")]));
            assert.notEqual(runtimeFingerprint(), initial, `${file} must invalidate stale execution authority`);
            writeFileSync(target, original);
            assert.equal(runtimeFingerprint(), initial);
        }
        rmSync(path.join(directory, workerGuidanceFiles[0]!));
        assert.throws(() => runtimeFingerprint(), /ENOENT/);
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
