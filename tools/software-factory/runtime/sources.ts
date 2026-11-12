// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash } from "node:crypto";
import { existsSync, lstatSync, realpathSync } from "node:fs";
import path from "node:path";
import type { Lifecycle } from "../core/lifecycle.ts";
import { sourceRepositories, type Execution } from "../core/lifecycle-contracts.ts";
import { FactoryError } from "../core/contracts.ts";
import { fileEvidence } from "../core/evidence.ts";
import { systemRunner } from "../providers/cli.ts";

export function repositorySourceRoot(workspace: string, localPath: string): string {
    const root = realpathSync(workspace);
    const location = path.resolve(root, localPath);
    if (!existsSync(path.join(location, ".git"))) {
        throw new FactoryError("source_repository_missing", `The configured repository at ${localPath} has no Git checkout.`, 409);
    }
    const target = realpathSync(location);
    const relative = path.relative(root, target);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
        throw new FactoryError("source_repository_outside_workspace", `The configured repository at ${localPath} resolves outside the workspace.`, 409);
    }
    return target;
}

export async function sourcePin(root: string, pinnedRoots: Set<string>): Promise<string> {
    const head = await systemRunner.run("git", ["rev-parse", "HEAD"], { cwd: root });
    const digest = createHash("sha256").update(await systemRunner.run("git", ["--no-pager", "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD"], { cwd: root }));
    const files = await systemRunner.run("git", ["ls-files", "--others", "--exclude-standard", "-z"], { cwd: root });
    for (const file of files.split("\0").filter(Boolean).sort()) {
        const normalizedFile = file.replaceAll("\\", "/");
        if (normalizedFile === ".software-factory-repairs" || normalizedFile.startsWith(".software-factory-repairs/")) {
            continue;
        }
        const absolute = path.join(root, file);
        digest.update(file);
        if (lstatSync(absolute).isDirectory()) {
            if (!existsSync(path.join(absolute, ".git")) || !pinnedRoots.has(realpathSync(absolute))) {
                throw new FactoryError("source_repository_unpinned", `Untracked directory ${file} is not a configured, independently pinned Git checkout.`, 409);
            }
            digest.update("independently-pinned-repository");
        } else {
            digest.update(fileEvidence(absolute));
        }
    }
    return `${head}:${digest.digest("hex")}`;
}

export async function sourcePins(factory: Lifecycle): Promise<Record<string, string>> {
    const workspace = factory.store.registry.project.workspace;
    const roots = new Set([repositorySourceRoot(workspace, ".")]);
    for (const repo of Object.values(sourceRepositories(factory.store.configuration()?.value || null))) {
        roots.add(repositorySourceRoot(workspace, repo.localPath));
    }
    const pins: Record<string, string> = {};
    for (const root of [...roots].sort()) pins[root] = await sourcePin(root, roots);
    return pins;
}

export async function gitlinks(root: string): Promise<{ path: string; revision: string }[]> {
    const declaration = await systemRunner.run("git", ["ls-tree", "-z", "HEAD", "--", ".gitmodules"], { cwd: root });
    if (!declaration) return [];
    const paths = await systemRunner.run("git", ["config", "--blob", "HEAD:.gitmodules", "--get-regexp", "^submodule\\..*\\.path$"], { cwd: root });
    const declared = paths.split(/\r?\n/).filter(Boolean).map((line) => line.slice(line.indexOf(" ") + 1));
    if (!declared.length) return [];
    const output = await systemRunner.run("git", ["ls-tree", "-z", "HEAD", "--", ...declared], { cwd: root });
    return output.split("\0").filter(Boolean).flatMap((entry) => {
        const match = /^160000 commit ([a-f0-9]{40}(?:[a-f0-9]{24})?)\t(.+)$/.exec(entry);
        return match ? [{ path: match[2]!, revision: match[1]! }] : [];
    });
}

const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
const contains = (root: string, value: string) => normalize(value) === normalize(root) || normalize(value).startsWith(normalize(root) + path.sep);
const emptyFingerprint = createHash("sha256").digest("hex");

export async function sourceReceipt(factory: Lifecycle, execution: Execution, target: string, featureId?: string) {
    const workspaceRoot = realpathSync(factory.store.registry.project.workspace);
    const repairsDir = path.join(workspaceRoot, ".software-factory-repairs");
    if (contains(repairsDir, target)) {
        const ownerFeatureId = featureId || factory.store.registry.list().find((feature) =>
            factory.store.state(feature.id).executions.some((candidate) => candidate.id === execution.id))?.id;
        if (execution.kind !== "implement" || !ownerFeatureId) {
            throw new FactoryError("worker_read_outside_scope", "Repair checkouts are accessible only during an active implementation execution.", 403);
        }
        const state = factory.store.state(ownerFeatureId);
        const matchedOp = state.operations.find((operation) => {
            if (operation.kind !== "repair" || !operation.repair) return false;
            const candidateRoot = path.join(repairsDir, operation.repair.publicationKey, String(operation.repair.round));
            return contains(candidateRoot, target);
        });
        if (!matchedOp?.repair) {
            throw new FactoryError("worker_source_unpinned", "This repair path does not belong to an admitted repair checkout for this feature.", 403);
        }
        const repairRoot = path.join(repairsDir, matchedOp.repair.publicationKey, String(matchedOp.repair.round));
        if (!existsSync(path.join(repairRoot, ".git")) || normalize(realpathSync(repairRoot)) !== normalize(path.resolve(repairRoot))) {
            throw new FactoryError("source_pin_invalid", "The admitted repair checkout has no verifiable Git worktree.", 409);
        }
        let parent = lstatSync(target).isDirectory() ? target : path.dirname(target);
        while (normalize(parent) !== normalize(repairRoot)) {
            if (existsSync(path.join(parent, ".git"))) {
                throw new FactoryError("worker_source_unpinned", "This nested Git checkout inside the repair worktree has no separate admitted source pin.", 409);
            }
            parent = path.dirname(parent);
        }
        const relative = path.relative(repairRoot, target).split(path.sep).filter(Boolean);
        if (relative.length) {
            const prefixes = relative.map((_, index) => relative.slice(0, index + 1).join("/"));
            const tree = await systemRunner.run("git", ["ls-tree", "-z", "HEAD", "--", ...prefixes], { cwd: repairRoot });
            if (tree.split("\0").some((item) => item.startsWith("160000 commit "))) {
                throw new FactoryError("worker_source_unpinned", "The requested repair path is inside a gitlink without a separately admitted checkout pin.", 409);
            }
        }
        const currentPin = await sourcePin(repairRoot, new Set([repairRoot]));
        const revision = currentPin.split(":")[0]!;
        if (revision !== matchedOp.repair.expectedHead) {
            throw new FactoryError("source_drift", "The repair checkout HEAD drifted from its admitted PR head.", 409);
        }
        let repositoryAlias = matchedOp.itemId;
        try {
            repositoryAlias = factory.plan(ownerFeatureId).value.items.find((item) => item.id === matchedOp.itemId)?.repository || matchedOp.itemId;
        } catch {
            // Retain itemId if plan lookup is unavailable.
        }
        const admittedPin = `${matchedOp.repair.expectedHead}:${emptyFingerprint}`;
        return {
            checkoutRoot: repairRoot,
            repositories: [repositoryAlias],
            referenceRepositories: [],
            repairRound: matchedOp.repair.round,
            revision,
            admittedPin,
            currentPin,
            matchesAdmission: revision === matchedOp.repair.expectedHead,
            clean: currentPin.split(":")[1] === emptyFingerprint,
        };
    }
    const entry = Object.entries(execution.sourcePins).filter(([root]) => contains(root, target))
        .sort(([a], [b]) => b.length - a.length)[0];
    if (!entry) return null;
    const [root, admittedPin] = entry;
    if (!/^[a-f0-9]{40}(?:[a-f0-9]{24})?:[a-f0-9]{64}$/.test(admittedPin) ||
        normalize(realpathSync(root)) !== normalize(path.resolve(root)) ||
        !existsSync(path.join(root, ".git"))) {
        throw new FactoryError("source_pin_invalid", "The execution has no verifiable Git snapshot for this source root.", 409);
    }
    let parent = lstatSync(target).isDirectory() ? target : path.dirname(target);
    while (normalize(parent) !== normalize(root)) {
        if (existsSync(path.join(parent, ".git"))) {
            throw new FactoryError("worker_source_unpinned", "This nested Git checkout has no separate admitted source pin. Do not attribute its files to its parent or a sibling repository.", 409);
        }
        parent = path.dirname(parent);
    }
    const relative = path.relative(root, target).split(path.sep).filter(Boolean);
    if (relative.length) {
        const prefixes = relative.map((_, index) => relative.slice(0, index + 1).join("/"));
        const tree = await systemRunner.run("git", ["ls-tree", "-z", "HEAD", "--", ...prefixes], { cwd: root });
        if (tree.split("\0").some((entry) => entry.startsWith("160000 commit "))) {
            throw new FactoryError("worker_source_unpinned", "The requested path is inside a gitlink without a separately admitted checkout pin. Inspect software_factory_sources; a sibling repository is not an implicit substitute.", 409);
        }
    }
    const currentPin = await sourcePin(root, new Set(Object.keys(execution.sourcePins)));
    if (execution.kind !== "implement" && currentPin !== admittedPin) {
        throw new FactoryError("source_drift", "The source snapshot changed after admission. No read is attested to the old revision; inspect a successor run.", 409);
    }
    const configuration = factory.store.configuration()?.value || null;
    const repositories = Object.entries(sourceRepositories(configuration))
        .filter(([, repo]) => normalize(repositorySourceRoot(factory.store.registry.project.workspace, repo.localPath)) === normalize(root))
        .map(([repository]) => repository);
    if (!repositories.length && normalize(root) === normalize(factory.store.registry.project.workspace)) {
        let alias = "android-complete";
        const aliases = Object.keys(sourceRepositories(configuration));
        while (aliases.includes(alias)) alias = `workspace-${alias}`;
        repositories.push(alias);
    }
    return {
        checkoutRoot: root, repositories,
        referenceRepositories: repositories.filter((alias) => Object.hasOwn(configuration?.referenceRepositories || {}, alias)),
        revision: currentPin.split(":")[0]!, admittedPin, currentPin,
        matchesAdmission: currentPin === admittedPin, clean: currentPin.split(":")[1] === emptyFingerprint,
    };
}

export async function sourceInventory(factory: Lifecycle, execution: Execution, featureId?: string) {
    const repositories = [];
    for (const root of Object.keys(execution.sourcePins)) {
        const receipt = await sourceReceipt(factory, execution, root, featureId);
        const dependencies = [];
        for (const link of await gitlinks(root)) {
            const location = path.resolve(root, link.path);
            if (!contains(root, location)) throw new FactoryError("source_mapping_invalid", "A gitlink path escapes its declared checkout.", 409);
            const configured = Object.keys(execution.sourcePins).find((candidate) => normalize(path.resolve(candidate)) === normalize(location));
            const initialized = existsSync(path.join(location, ".git"));
            dependencies.push({ ...link, checkoutRoot: location,
                status: !initialized ? "uninitialized" : configured ? "separately-pinned" : "not-admitted",
                admittedPin: configured ? execution.sourcePins[configured] : null });
        }
        if (JSON.stringify(await sourceReceipt(factory, execution, root, featureId)) !== JSON.stringify(receipt)) {
            throw new FactoryError("source_drift", "Source changed while collecting its Git metadata; no stale inventory was returned.", 409);
        }
        repositories.push({ ...receipt, gitlinks: dependencies });
    }
    const repairCheckouts = [];
    const ownerFeatureId = featureId || (execution.kind === "implement"
        ? factory.store.registry.list().find((feature) =>
            factory.store.state(feature.id).executions.some((candidate) => candidate.id === execution.id))?.id
        : undefined);
    if (execution.kind === "implement" && ownerFeatureId) {
        const repairsDir = path.join(realpathSync(factory.store.registry.project.workspace), ".software-factory-repairs");
        for (const operation of factory.store.state(ownerFeatureId).operations) {
            if (operation.kind !== "repair" || !operation.repair) continue;
            const repairRoot = path.join(repairsDir, operation.repair.publicationKey, String(operation.repair.round));
            if (!existsSync(path.join(repairRoot, ".git"))) continue;
            const receipt = await sourceReceipt(factory, execution, repairRoot, ownerFeatureId);
            if (JSON.stringify(await sourceReceipt(factory, execution, repairRoot, ownerFeatureId)) !== JSON.stringify(receipt)) {
                throw new FactoryError("source_drift", "Repair source changed while collecting its Git metadata; no stale inventory was returned.", 409);
            }
            repairCheckouts.push(receipt);
        }
    }
    return {
        featureSource: "Application-verified execution Git metadata (not repository instructions).",
        executionId: execution.id, workspace: factory.store.registry.project.workspace, repositories,
        ...(repairCheckouts.length ? { repairCheckouts } : {}),
        limitations: "This attests source identity, not functional correctness. A configured sibling is not automatically a parent's consumed gitlink. Read the integration workspace and standalone build mappings before choosing a dependency relationship. Missing/omitted dependencies stay explicit; no checkout is fetched, initialized or substituted.",
    };
}
