// Copyright (c) Microsoft Corporation. All rights reserved.

import { createRequire } from "node:module";
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FactoryError } from "./contracts.ts";
import type { CommandRunner } from "../providers/cli.ts";

export interface WorktreeInfo {
    workspace: string;
    repositoryRoot: string;
    worktreeName: string;
    repositoryName: string;
    isLinkedWorktree: boolean;
    branch: string | null;
    remoteOrigin: string | null;
}

export function normalizeGitRemote(rawUrl: string): string | null {
    const trimmed = rawUrl.trim();
    if (!trimmed) return null;
    const sshMatch = /^(?:ssh:\/\/)?(?:[^@]+@)?([a-zA-Z0-9._-]+)[:/]([^/].*?)(?:\.git)?\/?$/.exec(trimmed);
    if (sshMatch?.[1] && sshMatch[2] && !trimmed.startsWith("http://") && !trimmed.startsWith("https://") && !trimmed.startsWith("file://")) {
        return `${sshMatch[1].toLowerCase()}/${sshMatch[2].replace(/^\/+/, "").toLowerCase()}`;
    }
    try {
        const parsed = new URL(trimmed);
        if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "ssh:") {
            const cleanPath = parsed.pathname.replace(/^\/+/, "").replace(/\.git\/?$/i, "").replace(/\/+$/, "");
            if (parsed.hostname && cleanPath) {
                return `${parsed.hostname.toLowerCase()}/${cleanPath.toLowerCase()}`;
            }
        }
    } catch {
        // Non-URL local path remote; ignore unless canonicalizable.
    }
    return null;
}

function resolveGitDirs(canonicalWorkspace: string): { gitDir: string; commonDir: string } | null {
    try {
        const dotGit = path.join(canonicalWorkspace, ".git");
        if (!existsSync(dotGit)) return null;
        const stat = statSync(dotGit);
        if (stat.isDirectory()) {
            return { gitDir: dotGit, commonDir: dotGit };
        }
        if (!stat.isFile()) return null;
        const match = /^gitdir:\s*(.+)$/m.exec(readFileSync(dotGit, "utf8"));
        if (!match?.[1]) return null;
        const gitDir = path.resolve(canonicalWorkspace, match[1].trim());
        const commonFile = path.join(gitDir, "commondir");
        const commonDir = existsSync(commonFile) && statSync(commonFile).isFile()
            ? path.resolve(gitDir, readFileSync(commonFile, "utf8").trim())
            : gitDir;
        return { gitDir, commonDir };
    } catch {
        return null;
    }
}

function readGitBranchFromDir(gitDir: string): string | null {
    try {
        const headFile = path.join(gitDir, "HEAD");
        if (!existsSync(headFile)) return null;
        const head = readFileSync(headFile, "utf8").trim();
        if (!head) return null;
        const refMatch = /^ref:\s*refs\/heads\/(.+)$/.exec(head);
        if (refMatch?.[1]) return refMatch[1].trim();
        const anyRef = /^ref:\s*(.+)$/.exec(head);
        if (anyRef?.[1]) return anyRef[1].trim();
        return `detached@${head.slice(0, 8)}`;
    } catch {
        return null;
    }
}

function readOriginRemoteFromCommonDir(commonDir: string): string | null {
    try {
        const configFile = path.join(commonDir, "config");
        if (!existsSync(configFile)) return null;
        const lines = readFileSync(configFile, "utf8").split(/\r?\n/);
        let inOrigin = false;
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
                inOrigin = /^\[remote\s+"origin"\]$/i.test(trimmed);
                continue;
            }
            if (inOrigin) {
                const urlMatch = /^url\s*=\s*(.+)$/i.exec(trimmed);
                if (urlMatch?.[1]) {
                    return normalizeGitRemote(urlMatch[1]);
                }
            }
        }
    } catch {
        // Ignore unreadable git config.
    }
    return null;
}

export function resolveRepositoryRoot(workspace: string): string {
    const canonical = realpathSync(workspace);
    const dirs = resolveGitDirs(canonical);
    if (dirs && path.basename(dirs.commonDir).toLowerCase() === ".git") {
        try {
            return realpathSync(path.dirname(dirs.commonDir));
        } catch {
            return canonical;
        }
    }
    return canonical;
}

export function resolveWorktreeInfo(workspace: string): WorktreeInfo {
    const canonical = existsSync(workspace) ? realpathSync(workspace) : path.resolve(workspace);
    const repoRoot = existsSync(workspace) ? resolveRepositoryRoot(canonical) : canonical;
    const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
    const dirs = existsSync(workspace) ? resolveGitDirs(canonical) : null;
    return {
        workspace: canonical,
        repositoryRoot: repoRoot,
        worktreeName: path.basename(canonical) || canonical,
        repositoryName: path.basename(repoRoot) || repoRoot,
        isLinkedWorktree: normalize(canonical) !== normalize(repoRoot),
        branch: dirs ? readGitBranchFromDir(dirs.gitDir) : null,
        remoteOrigin: dirs ? readOriginRemoteFromCommonDir(dirs.commonDir) : null,
    };
}

export function linkedWorktreePaths(repositoryRoot: string): string[] {
    const results: string[] = [];
    try {
        const wtDir = path.join(repositoryRoot, ".git", "worktrees");
        if (!existsSync(wtDir) || !statSync(wtDir).isDirectory()) return results;
        for (const entry of readdirSync(wtDir)) {
            const gitdirFile = path.join(wtDir, entry, "gitdir");
            if (!existsSync(gitdirFile) || !statSync(gitdirFile).isFile()) continue;
            const rawTarget = readFileSync(gitdirFile, "utf8").trim();
            if (!rawTarget) continue;
            const resolvedGitFile = path.resolve(path.join(wtDir, entry), rawTarget);
            const wtPath = path.basename(resolvedGitFile).toLowerCase() === ".git"
                ? path.dirname(resolvedGitFile)
                : resolvedGitFile;
            results.push(existsSync(wtPath) ? realpathSync(wtPath) : wtPath);
        }
    } catch {
        // Ignore unreadable .git/worktrees metadata.
    }
    return results;
}

export async function ensureSubrepoWorktree(
    workspace: string,
    localPath: string,
    _baseBranch: string,
    runner: CommandRunner,
): Promise<string> {
    const canonicalWorkspace = realpathSync(workspace);
    const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
    const target = path.resolve(canonicalWorkspace, localPath);
    if (!existsSync(target)) {
        throw new FactoryError("local_repository_missing", `Prepare an isolated Git checkout at ${localPath} before local delivery.`, 409);
    }
    const canonicalTarget = realpathSync(target);
    const root = normalize(canonicalWorkspace);
    if (normalize(canonicalTarget) !== root && !normalize(canonicalTarget).startsWith(root + path.sep)) {
        throw new FactoryError("local_repository_outside_workspace", `The checkout at ${localPath} resolves outside the workspace; prepare an isolated checkout inside it.`, 409);
    }
    if (!existsSync(path.join(canonicalTarget, ".git"))) {
        throw new FactoryError("local_repository_missing", `The checkout at ${localPath} is not an independent Git repository.`, 409);
    }
    const gitRoot = await runner.run("git", ["rev-parse", "--show-toplevel"], { cwd: canonicalTarget });
    if (normalize(realpathSync(gitRoot)) !== normalize(canonicalTarget)) {
        throw new FactoryError("local_repository_invalid", `The configured localPath ${localPath} is not the Git checkout root.`, 409);
    }
    return canonicalTarget;
}

export function projectSdkRoot(checkoutRoot?: string): string {
    // Chat hosts may redirect bare SDK specifiers to their own bundled extension SDK.
    const localRoot = checkoutRoot
        ? path.join(realpathSync(checkoutRoot), "tools", "software-factory", "node_modules", "@github", "copilot-sdk") + path.sep
        : fileURLToPath(new URL("../node_modules/@github/copilot-sdk/", import.meta.url));
    if (existsSync(path.join(localRoot, "package.json"))) return localRoot;
    const workspace = checkoutRoot ? realpathSync(checkoutRoot) : fileURLToPath(new URL("../../../", import.meta.url));
    const mainRoot = resolveRepositoryRoot(workspace);
    if (mainRoot !== realpathSync(workspace)) {
        const sharedSdk = path.join(mainRoot, "tools", "software-factory", "node_modules", "@github", "copilot-sdk") + path.sep;
        if (existsSync(path.join(sharedSdk, "package.json"))) return sharedSdk;
    }
    return localRoot;
}

export function projectSdkModule(name: "." | "./extension", root = projectSdkRoot()): string {
    const metadata: unknown = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
    if (!metadata || typeof metadata !== "object" || !("exports" in metadata) ||
        !metadata.exports || typeof metadata.exports !== "object" || !(name in metadata.exports)) {
        throw new FactoryError("sdk_layout_invalid", "The project SDK does not expose its required public entry points. Reinstall Software Factory dependencies.", 503);
    }
    const target: unknown = Reflect.get(metadata.exports, name);
    const condition: unknown = target && typeof target === "object" && "import" in target ? target.import : target;
    const entry: unknown = condition && typeof condition === "object" && "default" in condition ? condition.default : condition;
    if (typeof entry !== "string" || !entry.startsWith("./")) throw new FactoryError("sdk_layout_invalid", "The installed project SDK has an unsupported public module entry.", 503);
    const location = path.resolve(root, entry);
    const relative = path.relative(root, location);
    if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative) || !statSync(location).isFile()) {
        throw new FactoryError("sdk_layout_invalid", "The installed project SDK module is outside its package or missing.", 503);
    }
    return pathToFileURL(location).href;
}

export function runtimeHealth(sdkRoot?: string) {
    const recoveryCommand = "npm --prefix .\\tools\\software-factory ci --include=optional --include=dev --no-audit --no-fund";
    try {
        if (Number(process.versions.node.split(".")[0]) < 24) throw new Error("Node.js 24 or newer is required.");
        const root = sdkRoot || projectSdkRoot();
        const sdk = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8")) as { version: string; optionalDependencies: Record<string, string> };
        let platform = process.platform as string;
        if (platform === "linux") {
            const report = process.report.getReport();
            if (typeof report === "object" && report !== null && "header" in report &&
                typeof report.header === "object" && report.header !== null && !("glibcVersionRuntime" in report.header)) platform = "linuxmusl";
        }
        const target = `${platform}-${process.arch}`;
        const packageName = `@github/copilot-sdk-${target}`;
        const expected = sdk.optionalDependencies[packageName];
        if (!expected) throw new Error(`The installed Copilot SDK does not declare a runtime for ${target}.`);
        const require = createRequire(path.join(root, "package.json"));
        const manifest = require.resolve(`${packageName}/package.json`);
        const runtime = JSON.parse(readFileSync(manifest, "utf8")) as { version: string };
        if (runtime.version !== expected) throw new Error(`${packageName} must match SDK runtime version ${expected}, not ${runtime.version}.`);
        const directory = path.join(path.dirname(manifest), "prebuilds", target);
        for (const file of [platform === "win32" ? "copilot-runtime.exe" : "copilot-runtime", "runtime.node"]) {
            if (!statSync(path.join(directory, file)).isFile() || statSync(path.join(directory, file)).size === 0) {
                throw new Error(`${packageName} contains an incomplete runtime: ${file}.`);
            }
        }
        return { ready: true as const, code: "runtime_ready", sdkRoot: root, platformPackage: packageName, hostExecutable: process.execPath,
            runtimeExecutable: path.join(directory, platform === "win32" ? "copilot-runtime.exe" : "copilot-runtime"),
            message: `Copilot SDK ${sdk.version} and its ${target} runtime are installed.`, recoveryCommand: null };
    } catch (error) {
        return { ready: false as const, code: "runtime_unavailable", sdkRoot: sdkRoot || projectSdkRoot(), runtimeExecutable: null, hostExecutable: process.execPath,
            message: `The local Copilot runtime is not ready: ${error instanceof Error ? error.message : "Installation could not be verified."}`,
            recoveryCommand };
    }
}

export function requireRuntime() {
    const health = runtimeHealth();
    if (!health.ready) throw new FactoryError(health.code, `${health.message}\nRepair from the repository root:\n${health.recoveryCommand}\nThen retry explicitly. No workflow was launched.`, 503);
    return health;
}
