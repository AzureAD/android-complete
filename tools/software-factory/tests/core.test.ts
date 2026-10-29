// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { Application } from "../core/application.ts";
import { FactoryError, MAX_COMMAND_BYTES } from "../core/contracts.ts";
import { projectSdkRoot, resolveRepositoryRoot } from "../core/runtime-health.ts";
import { actor, fixture, requirements } from "./helpers.ts";

function create(app: Application, requestId = "request-1", input = requirements()) {
    return app.execute({ kind: "create_feature", requestId, input }, actor);
}

function errorCode(action: () => unknown, code: string) {
    assert.throws(action, (error: unknown) => error instanceof FactoryError && error.code === code);
}

test("creation is durable and same request ID is idempotent, not title-based", () => {
    const f = fixture();
    try {
        const created = create(f.app);
        assert.equal(create(f.app).id, created.id);
        assert.notEqual(create(f.app, "request-2").id, created.id);
        assert.equal(f.app.registry.list().length, 2);
        const reopened = new Application(f.options);
        try {
            assert.deepEqual(reopened.registry.get(created.id), created);
            assert.equal(reopened.registry.project.id, f.app.registry.project.id);
        } finally { reopened.close(); }
        errorCode(() => create(f.app, "request-1", { ...requirements(), title: "Different" }), "request_conflict");
    } finally { f.cleanup(); }
});

test("project boundaries isolate features even when sharing the registry", () => {
    const f = fixture();
    const otherWorkspace = path.join(f.directory, "other");
    mkdirSync(otherWorkspace);
    const other = new Application({ ...f.options, workspace: otherWorkspace });
    try {
        const feature = create(f.app);
        errorCode(() => other.registry.get(feature.id), "feature_not_found");
        assert.equal(other.registry.list().length, 0);
        assert.notEqual(create(other).projectId, feature.projectId);
    } finally { other.close(); f.cleanup(); }
});

test("linked git worktrees share the parent repository project identity and SDK fallback", () => {
    const f = fixture();
    const mainRepo = path.join(f.directory, "main-repo");
    const mainGit = path.join(mainRepo, ".git");
    const worktreeGit = path.join(mainGit, "worktrees", "feature-wt");
    const worktree = path.join(f.directory, "worktrees", "feature-wt");
    const mainSdk = path.join(mainRepo, "tools", "software-factory", "node_modules", "@github", "copilot-sdk");
    mkdirSync(worktreeGit, { recursive: true });
    mkdirSync(worktree, { recursive: true });
    mkdirSync(mainSdk, { recursive: true });
    writeFileSync(path.join(worktreeGit, "commondir"), "../..\n");
    writeFileSync(path.join(worktree, ".git"), `gitdir: ${worktreeGit}\n`);
    writeFileSync(path.join(mainSdk, "package.json"), JSON.stringify({ name: "@github/copilot-sdk", version: "1.0.26" }));
    const mainApp = new Application({ database: f.options.database, workspace: mainRepo });
    const wtApp = new Application({ database: f.options.database, workspace: worktree });
    try {
        assert.equal(resolveRepositoryRoot(worktree), resolveRepositoryRoot(mainRepo));
        assert.equal(wtApp.registry.project.id, mainApp.registry.project.id);
        assert.equal(wtApp.registry.project.workspace, resolveRepositoryRoot(worktree) === worktree ? worktree : wtApp.registry.project.workspace);
        const created = create(mainApp, "shared-across-worktrees");
        assert.equal(wtApp.registry.list().length, 1);
        assert.equal(wtApp.registry.get(created.id).id, created.id);
        assert.equal(projectSdkRoot(worktree), mainSdk + path.sep);
    } finally {
        wtApp.close();
        mainApp.close();
        f.cleanup();
    }
});

test("approval requires trusted consent and binds to the exact current revision", () => {
    const f = fixture();
    try {
        const feature = create(f.app);
        const command = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        errorCode(() => f.app.execute(command, actor), "consent_required");
        errorCode(() => f.app.execute({ ...command, approved: true }, actor), "invalid_input");
        const approved = f.app.execute(command, actor, { granted: true, revisionId: feature.current.id, actor });
        assert.equal(approved.status, "ready_for_design");
        assert.equal(approved.approvals.length, 1);
        assert.equal(f.app.execute(command, actor, { granted: true, revisionId: feature.current.id, actor }).approvals.length, 1);
        const edited = f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Revised scope" },
        }, actor);
        assert.equal(edited.status, "requirements_review");
        assert.equal(edited.history.length, 2);
        assert.equal(edited.approvals.length, 1);
        errorCode(() => f.app.execute(command, actor, { granted: true, revisionId: feature.current.id, actor }), "stale_revision");
        assert.equal(f.app.registry.get(feature.id).audit.length, 3);
    } finally { f.cleanup(); }
});

test("concurrent-style revisions reject stale commands and identical edits are no-ops", () => {
    const f = fixture();
    try {
        const feature = create(f.app);
        const command = { kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id, input: requirements() };
        assert.deepEqual(f.app.execute(command, actor), feature);
        f.app.execute({ ...command, input: { ...requirements(), title: "New title" } }, actor);
        errorCode(() => f.app.execute(command, actor), "stale_revision");
        assert.equal(f.app.registry.get(feature.id).history.length, 2);
    } finally { f.cleanup(); }
});

test("returning to previous content creates a fresh revision without resurrecting approval", () => {
    const f = fixture();
    try {
        const feature = create(f.app);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor, { granted: true, revisionId: feature.current.id, actor });
        const next = f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed" },
        }, actor);
        const restored = f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: next.current.id, input: requirements(),
        }, actor);
        assert.equal(restored.current.hash, feature.current.hash);
        assert.notEqual(restored.current.id, feature.current.id);
        assert.equal(restored.status, "requirements_review");
    } finally { f.cleanup(); }
});

test("blocking unknowns prevent approval, and invalid requirements never create features", () => {
    const f = fixture();
    try {
        for (const input of [
            { ...requirements(), title: " " },
            { ...requirements(), requirements: [] },
            { ...requirements(), requirements: [requirements().requirements[0], requirements().requirements[0]] },
            { ...requirements(), blockingUnknowns: null },
            { ...requirements(), inventedField: true },
        ]) {
            errorCode(() => f.app.execute({ kind: "create_feature", requestId: "bad", input }, actor), "invalid_input");
        }
        assert.equal(f.app.registry.list().length, 0);
        const feature = create(f.app, "blocked", { ...requirements(), blockingUnknowns: ["Which repository owns this API?"] });
        errorCode(() => f.app.execute(
            { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor },
        ), "blocking_unknowns");
    } finally { f.cleanup(); }
});

test("unsupported and oversized commands fail explicitly", () => {
    const f = fixture();
    try {
        errorCode(() => f.app.execute({ kind: "dispatch" }, actor), "unsupported_command");
        errorCode(() => create(f.app, "large", { ...requirements(), title: "x".repeat(MAX_COMMAND_BYTES) }), "command_too_large");
        errorCode(() => f.app.execute({ kind: "create_feature", requestId: "no-session", input: requirements() },
            { channel: "chat", reference: "forged" }), "invalid_input");
    } finally { f.cleanup(); }
});

test("stored history is immutable and newer storage schemas fail without being reset", () => {
    const f = fixture();
    const raw = new DatabaseSync(f.options.database);
    try {
        create(f.app);
        assert.throws(() => raw.exec("UPDATE revisions SET input_json = '{}'"), /Immutable history/);
        assert.throws(() => raw.exec("DELETE FROM audit"), /Immutable history/);
        raw.exec("PRAGMA user_version = 99");
        errorCode(() => new Application(f.options), "storage_version");
        assert.equal(raw.prepare("PRAGMA user_version").get()?.user_version, 99);
    } finally { raw.close(); f.cleanup(); }
});

test("runtime storage cannot be placed in the repository checkout", () => {
    const f = fixture();
    try {
        errorCode(() => new Application({ database: path.join(f.workspace, "data", "registry.sqlite"), workspace: f.workspace }), "storage_in_workspace");
    } finally { f.cleanup(); }
});
