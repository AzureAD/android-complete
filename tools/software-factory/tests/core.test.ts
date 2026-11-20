// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, rmdirSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { createBackup, inspectBackup, restoreClone } from "../core/backups.ts";
import { FactoryError, parseRequirements, validateActor } from "../core/contracts.ts";
import { parseConfiguration, type Operation } from "../core/lifecycle-contracts.ts";
import { LifecycleStore, now } from "../core/lifecycle-store.ts";
import { Registry } from "../core/registry.ts";
import { currentPublicationBinding } from "../core/repairs.ts";
import { ensureSubrepoWorktree, projectSdkRoot, resolveRepositoryRoot } from "../core/runtime-health.ts";
import { systemRunner } from "../providers/cli.ts";
import { actor, fixture, makeExecution, requirements } from "./helpers.ts";

function errorCode(action: () => unknown, code: string) {
    assert.throws(action, (error: unknown) => error instanceof FactoryError && error.code === code);
}

test("creation is durable and same request ID is idempotent, not title-based", () => {
    const f = fixture();
    try {
        const created = f.registry.create("request-1", requirements(), actor);
        assert.equal(f.registry.create("request-1", requirements(), actor).id, created.id);
        assert.notEqual(f.registry.create("request-2", requirements(), actor).id, created.id);
        assert.equal(f.registry.list().length, 2);
        const reopened = new Registry(f.database, f.workspace);
        try {
            assert.deepEqual(reopened.get(created.id), created);
            assert.equal(reopened.project.id, f.registry.project.id);
        } finally {
            reopened.close();
        }
        errorCode(() => f.registry.create("request-1", { ...requirements(), title: "Different" }, actor), "request_conflict");
    } finally {
        f.cleanup();
    }
});

test("project boundaries isolate features even when sharing the registry", () => {
    const f = fixture();
    const otherWorkspace = path.join(f.directory, "other");
    mkdirSync(otherWorkspace);
    const other = new Registry(f.database, otherWorkspace);
    try {
        const feature = f.registry.create("request-1", requirements(), actor);
        errorCode(() => other.get(feature.id), "feature_not_found");
        assert.equal(other.list().length, 0);
        assert.notEqual(other.create("request-1", requirements(), actor).projectId, feature.projectId);
    } finally {
        other.close();
        f.cleanup();
    }
});

test("linked git worktrees share the parent repository project identity, SDK fallback, and subrepo worktree guards", async () => {
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
    const mainReg = new Registry(f.database, mainRepo);
    const wtReg = new Registry(f.database, worktree);
    try {
        assert.equal(resolveRepositoryRoot(worktree), resolveRepositoryRoot(mainRepo));
        assert.equal(wtReg.project.id, mainReg.project.id);
        const created = mainReg.create("shared-across-worktrees", requirements(), actor);
        assert.equal(wtReg.list().length, 1);
        assert.equal(wtReg.get(created.id).id, created.id);
        assert.equal(projectSdkRoot(worktree), mainSdk + path.sep);

        const msalRepo = path.join(mainRepo, "msal");
        const brokerRepo = path.join(mainRepo, "broker");
        mkdirSync(path.join(msalRepo, ".git"), { recursive: true });
        writeFileSync(path.join(msalRepo, "MsalClient.kt"), "class MsalClient\n");
        execFileSync("git", ["init", "--quiet", "-b", "dev", brokerRepo]);
        execFileSync("git", ["-C", brokerRepo, "config", "user.email", "test@example.com"]);
        execFileSync("git", ["-C", brokerRepo, "config", "user.name", "Test"]);
        writeFileSync(path.join(brokerRepo, "Broker.kt"), "class Broker // dev baseline\n");
        execFileSync("git", ["-C", brokerRepo, "add", "Broker.kt"]);
        execFileSync("git", ["-C", brokerRepo, "commit", "--quiet", "-m", "dev baseline"]);
        execFileSync("git", ["-C", brokerRepo, "checkout", "--quiet", "-b", "unrelated-user-branch"]);
        writeFileSync(path.join(brokerRepo, "Broker.kt"), "class Broker // dirty user branch\n");
        execFileSync("git", ["-C", brokerRepo, "commit", "--quiet", "-am", "user branch work"]);

        const brokerTarget = path.join(worktree, "broker");
        mkdirSync(brokerTarget);
        await assert.rejects(() => ensureSubrepoWorktree(worktree, "broker", "dev", systemRunner), /not an independent Git repository/);
        assert.equal(existsSync(brokerTarget), true, "an existing directory must never be replaced");
        rmdirSync(brokerTarget);
        symlinkSync(msalRepo, path.join(worktree, "msal"), process.platform === "win32" ? "junction" : "dir");
        await assert.rejects(() => ensureSubrepoWorktree(worktree, "msal", "dev", systemRunner), /outside the workspace/);
        assert.equal(lstatSync(path.join(worktree, "msal")).isSymbolicLink(), true, "an existing junction must remain untouched");

        execFileSync("git", ["-C", brokerRepo, "worktree", "add", "--quiet", "--detach", brokerTarget, "dev"]);
        const materializedBroker = await ensureSubrepoWorktree(worktree, "broker", "dev", systemRunner);
        assert.equal(materializedBroker, path.join(worktree, "broker"));
        assert.equal(lstatSync(path.join(worktree, "broker")).isSymbolicLink(), false);
        assert.equal(existsSync(path.join(worktree, "broker", ".git")), true);
        assert.match(readFileSync(path.join(worktree, "broker", "Broker.kt"), "utf8"), /dev baseline/);
        const mainBranch = execFileSync("git", ["-C", brokerRepo, "branch", "--show-current"], { encoding: "utf8" }).trim();
        assert.equal(mainBranch, "unrelated-user-branch", "main checkout branch of broker must remain untouched");
    } finally {
        try { unlinkSync(path.join(worktree, "msal")); } catch { /* ignore */ }
        try { execFileSync("git", ["-C", path.join(mainRepo, "broker"), "worktree", "remove", "--force", path.join(worktree, "broker")]); } catch { /* ignore */ }
        wtReg.close();
        mainReg.close();
        f.cleanup();
    }
});

test("revisions, approvals, and input validation enforce exact revision binding and non-empty criteria", () => {
    const f = fixture();
    try {
        for (const input of [
            { ...requirements(), title: " " },
            { ...requirements(), requirements: [requirements().requirements[0], requirements().requirements[0]] },
            { ...requirements(), blockingUnknowns: null },
            { ...requirements(), inventedField: true },
        ]) {
            errorCode(() => parseRequirements(input), "invalid_input");
        }
        errorCode(() => validateActor({ channel: "chat", reference: "forged" }), "invalid_input");

        const draftOnly = f.registry.create("draft-only", parseRequirements({ title: "Draft brief", description: "Plain brief." }), actor);
        errorCode(() => f.registry.approve(draftOnly.id, draftOnly.current.id, actor), "success_criteria_required");

        const blocked = f.registry.create("blocked", parseRequirements({
            ...requirements(),
            blockingUnknowns: ["Which repository owns this API?"],
        }), actor);
        errorCode(() => f.registry.approve(blocked.id, blocked.current.id, actor), "blocking_unknowns");

        const feature = f.registry.create("request-1", requirements(), actor);
        assert.deepEqual(f.registry.revise(feature.id, feature.current.id, requirements(), actor), feature);
        const approved = f.registry.approve(feature.id, feature.current.id, actor);
        assert.equal(approved.status, "ready_for_design");
        assert.equal(approved.approvals.length, 1);
        assert.equal(f.registry.approve(feature.id, feature.current.id, actor).approvals.length, 1);

        const edited = f.registry.revise(feature.id, feature.current.id, { ...requirements(), title: "Revised scope" }, actor);
        assert.equal(edited.status, "requirements_review");
        assert.equal(edited.history.length, 2);
        errorCode(() => f.registry.approve(feature.id, feature.current.id, actor), "stale_revision");

        const restored = f.registry.revise(feature.id, edited.current.id, requirements(), actor);
        assert.equal(restored.current.hash, feature.current.hash);
        assert.notEqual(restored.current.id, feature.current.id);
        assert.equal(restored.status, "requirements_review");
    } finally {
        f.cleanup();
    }
});

test("stored history is immutable, workspace storage is rejected, and backups restore safely into isolated clones", async () => {
    const f = fixture();
    const raw = new DatabaseSync(f.database);
    try {
        errorCode(() => new Registry(path.join(f.workspace, "data", "registry.sqlite"), f.workspace), "storage_in_workspace");

        const feature = f.registry.create("request-1", requirements(), actor);
        const runId = randomUUID();
        f.store.change(feature.id, (state) => {
            state.executions.push(makeExecution(runId, feature.current.id, { status: "running", phase: "drafting" }));
        });

        const snap = await createBackup(f.registry);
        const inspected = await inspectBackup(snap.path);
        assert.equal(inspected.hash, snap.hash);
        const clone = await restoreClone(f.registry, snap.path, snap.hash);
        assert.equal(clone.liveRegistryReplaced, false);
        assert.equal(clone.schedulesDisabled, true);
        const restoredReg = new Registry(clone.registryPath, f.workspace);
        const restoredStore = new LifecycleStore(restoredReg);
        try {
            const restoredExec = restoredStore.execution(feature.id, runId);
            assert.equal(restoredExec.status, "error");
            assert.match(restoredExec.error || "", /Restored snapshot/);
        } finally {
            restoredReg.close();
        }

        assert.throws(() => raw.exec("UPDATE revisions SET input_json = '{}'"), /Immutable history/);
        assert.throws(() => raw.exec("DELETE FROM audit"), /Immutable history/);
        raw.exec("PRAGMA user_version = 99");
        errorCode(() => new Registry(f.database, f.workspace), "storage_version");
        assert.equal(raw.prepare("PRAGMA user_version").get()?.user_version, 99);
    } finally {
        raw.close();
        f.cleanup();
    }
});

test("LifecycleStore validates configuration, artifact authority, and repair publication chains", () => {
    const f = fixture();
    try {
        const config = parseConfiguration({
            boards: {
                organization: "IdentityDivision",
                project: "Engineering",
                workItemType: "Product Backlog Item",
                areaPath: "Engineering\\Android",
                iterationPath: "Engineering",
                assignee: "",
                parentId: null,
            },
            repositories: {
                common: {
                    provider: "github",
                    hostname: "github.com",
                    slug: "AzureAD/microsoft-authentication-library-common-for-android",
                    baseBranch: "dev",
                    localPath: "common",
                    account: "shahzaibj",
                },
            },
        });
        const savedConfig = f.store.configure(config, actor);
        assert.equal(f.store.configuration()?.id, savedConfig.id);

        const feature = f.registry.create("artifact-check", requirements(), actor);
        const design = f.store.artifact(feature.id, "design", feature.current.id, { markdown: "# Design" });
        assert.equal(f.store.latest(feature.id, "design")?.id, design.id);

        const state = f.store.state(feature.id);
        state.decisions.push({
            id: "grant-1",
            stage: "dispatch",
            approved: true,
            targetId: "plan-1",
            scopeHash: "scope-1",
            scope: { observer: { engine: "pr-observer", maxRepairs: 1 } },
            actor,
            createdAt: now(),
        });
        const pub: Operation = {
            key: "pub-1",
            kind: "dispatch",
            status: "completed",
            planId: "plan-1",
            configurationId: savedConfig.id,
            claimId: "claim-1",
            itemId: "item-1",
            approvalId: "grant-1",
            error: null,
            updatedAt: now(),
            receipt: {
                provider: "github",
                mode: "local",
                marker: "sf-1",
                branch: "sf/branch",
                prNumber: 12,
                url: "https://example.test/pr/12",
                headSha: "a".repeat(40),
            },
            publicationBinding: {
                branch: "sf/branch",
                headSha: "a".repeat(40),
                source: { head: "0".repeat(40), indexTree: "1".repeat(40), base: "0".repeat(40), tree: "2".repeat(40) },
                target: {
                    provider: "github",
                    hostname: "github.com",
                    slug: "AzureAD/microsoft-authentication-library-common-for-android",
                    baseBranch: "dev",
                    publisherId: "pub-id-1",
                    publisherAccount: "shahzaibj",
                },
            },
        };
        state.operations.push(pub);
        assert.equal(currentPublicationBinding(state, pub).headSha, "a".repeat(40));
    } finally {
        f.cleanup();
    }
});

test("isolated projects sharing a registry can persist identical portable configurations with project-local mappings", () => {
    const f = fixture();
    const otherWorkspace = path.join(f.directory, "other-project");
    mkdirSync(otherWorkspace);
    execFileSync("git", ["init", "--quiet", "-b", "main", f.workspace]);
    execFileSync("git", ["-C", f.workspace, "remote", "add", "origin", "https://example.test/org/project-a.git"]);
    execFileSync("git", ["init", "--quiet", "-b", "main", otherWorkspace]);
    execFileSync("git", ["-C", otherWorkspace, "remote", "add", "origin", "https://example.test/org/project-b.git"]);

    const regA = new Registry(f.database, f.workspace);
    const regB = new Registry(f.database, otherWorkspace);
    const storeA = new LifecycleStore(regA);
    const storeB = new LifecycleStore(regB);
    try {
        assert.notEqual(regA.project.id, regB.project.id);
        assert.deepEqual(regA.projectIds, [regA.project.id]);
        assert.deepEqual(regB.projectIds, [regB.project.id]);

        const makeConfig = (localPath: string, account: string) => parseConfiguration({
            boards: {
                organization: "IdentityDivision",
                project: "Engineering",
                workItemType: "Product Backlog Item",
                areaPath: "Engineering\\Android",
                iterationPath: "Engineering",
                assignee: "",
                parentId: null,
            },
            repositories: {
                common: {
                    provider: "github",
                    hostname: "github.com",
                    slug: "AzureAD/microsoft-authentication-library-common-for-android",
                    baseBranch: "dev",
                    localPath,
                    account,
                },
            },
        });

        const savedA = storeA.configure(makeConfig("common-a", "account-a"), actor);
        const savedB = storeB.configure(makeConfig("common-b", "account-b"), actor);
        assert.equal(savedA.id, savedB.id, "shared portable configuration hash must match");

        const loadedA = storeA.configuration();
        const loadedB = storeB.configuration();
        assert.ok(loadedA, "first project configuration must load");
        assert.ok(loadedB, "second isolated project configuration must not return null");
        assert.equal(loadedA.id, savedA.id);
        assert.equal(loadedB.id, savedB.id);
        assert.equal(loadedA.value.repositories.common?.localPath, "common-a");
        assert.equal(loadedA.value.repositories.common?.account, "account-a");
        assert.equal(loadedB.value.repositories.common?.localPath, "common-b");
        assert.equal(loadedB.value.repositories.common?.account, "account-b");
        assert.equal(storeB.configuration(savedB.id)?.value.repositories.common?.localPath, "common-b");

        // Also verify identical localPath/account across two isolated projects
        const thirdWorkspace = path.join(f.directory, "third-project");
        mkdirSync(thirdWorkspace);
        execFileSync("git", ["init", "--quiet", "-b", "main", thirdWorkspace]);
        execFileSync("git", ["-C", thirdWorkspace, "remote", "add", "origin", "https://example.test/org/project-c.git"]);
        const regC = new Registry(f.database, thirdWorkspace);
        const storeC = new LifecycleStore(regC);
        try {
            const savedC = storeC.configure(makeConfig("common-a", "account-a"), actor);
            assert.equal(savedC.id, savedA.id);
            assert.equal(storeC.configuration()?.id, savedC.id);
            assert.equal(storeC.configuration(savedC.id)?.value.repositories.common?.account, "account-a");
        } finally {
            regC.close();
        }
    } finally {
        regB.close();
        regA.close();
        f.cleanup();
    }
});

test("lifecycleSchema migrates legacy single-PK factory_configuration tables and repairs orphaned local mappings", () => {
    const f = fixture();
    const legacyDbPath = path.join(f.directory, "legacy.sqlite");
    const workspaceA = path.join(f.directory, "legacy-a");
    const workspaceB = path.join(f.directory, "legacy-b");
    mkdirSync(workspaceA);
    mkdirSync(workspaceB);

    const configA = parseConfiguration({
        boards: {
            organization: "IdentityDivision",
            project: "Engineering",
            workItemType: "Product Backlog Item",
            areaPath: "Engineering\\Android",
            iterationPath: "Engineering",
            assignee: "",
            parentId: null,
        },
        repositories: {
            common: {
                provider: "github",
                hostname: "github.com",
                slug: "AzureAD/microsoft-authentication-library-common-for-android",
                baseBranch: "dev",
                localPath: "common-a",
                account: "account-a",
            },
        },
    });
    const configB = parseConfiguration({
        ...configA,
        repositories: {
            common: {
                ...configA.repositories.common!,
                localPath: "common-b",
                account: "account-b",
            },
        },
    });

    try {
        // Seed a v2 database using the legacy single-column PK where project B's factory_configuration row was dropped by INSERT OR IGNORE.
        const seedRegA = new Registry(legacyDbPath, workspaceA);
        const seedRegB = new Registry(legacyDbPath, workspaceB);
        const projectAId = seedRegA.project.id;
        const projectBId = seedRegB.project.id;
        seedRegB.close();
        seedRegA.close();

        const raw = new DatabaseSync(legacyDbPath);
        try {
            raw.exec(`
                DROP TRIGGER IF EXISTS factory_configuration_immutable_update;
                DROP TRIGGER IF EXISTS factory_configuration_immutable_delete;
                DROP TABLE factory_configuration;
                CREATE TABLE factory_configuration (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    hash TEXT NOT NULL, content_json TEXT NOT NULL, actor_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TRIGGER factory_configuration_immutable_update
                    BEFORE UPDATE ON factory_configuration BEGIN SELECT RAISE(ABORT, 'Immutable configuration history'); END;
                CREATE TRIGGER factory_configuration_immutable_delete
                    BEFORE DELETE ON factory_configuration BEGIN SELECT RAISE(ABORT, 'Immutable configuration history'); END;
            `);
            const cfgId = "legacy-shared-config-id";
            const createdAt = now();
            raw.prepare("INSERT INTO factory_configuration VALUES (?, ?, ?, ?, ?, ?)")
                .run(cfgId, projectAId, cfgId, JSON.stringify(configA), JSON.stringify(actor), createdAt);
            raw.prepare("INSERT INTO factory_local_configuration VALUES (?, ?, ?)")
                .run(projectAId, cfgId, JSON.stringify(configA));
            raw.prepare("INSERT INTO factory_local_configuration VALUES (?, ?, ?)")
                .run(projectBId, cfgId, JSON.stringify(configB));
        } finally {
            raw.close();
        }

        const migratedRegA = new Registry(legacyDbPath, workspaceA);
        const migratedRegB = new Registry(legacyDbPath, workspaceB);
        const migratedStoreA = new LifecycleStore(migratedRegA);
        const migratedStoreB = new LifecycleStore(migratedRegB);
        try {
            assert.equal(migratedStoreA.configuration()?.value.repositories.common?.localPath, "common-a");
            assert.equal(migratedStoreB.configuration()?.value.repositories.common?.localPath, "common-b");
            assert.equal(migratedStoreB.configuration()?.value.repositories.common?.account, "account-b");
            assert.throws(
                () => migratedRegB.connection.prepare("DELETE FROM factory_configuration WHERE project_id = ?").run(projectBId),
                /Immutable configuration history/,
            );
        } finally {
            migratedRegB.close();
            migratedRegA.close();
        }
    } finally {
        f.cleanup();
    }
});
