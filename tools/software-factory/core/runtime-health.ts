// Copyright (c) Microsoft Corporation. All rights reserved.

import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FactoryError } from "./contracts.ts";
import type { CommandRunner } from "../providers/cli.ts";

export function resolveRepositoryRoot(workspace: string): string {
    const canonical = realpathSync(workspace);
    try {
        const dotGit = path.join(canonical, ".git");
        if (!statSync(dotGit).isFile()) return canonical;
        const match = /^gitdir:\s*(.+)$/m.exec(readFileSync(dotGit, "utf8"));
        if (!match?.[1]) return canonical;
        const gitDir = path.resolve(canonical, match[1].trim());
        const commonFile = path.join(gitDir, "commondir");
        const commonDir = existsSync(commonFile) && statSync(commonFile).isFile()
            ? path.resolve(gitDir, readFileSync(commonFile, "utf8").trim())
            : gitDir;
        if (path.basename(commonDir).toLowerCase() === ".git") {
            return realpathSync(path.dirname(commonDir));
        }
    } catch {
        // Fall back to the canonical workspace path when not a linked git worktree.
    }
    return canonical;
}

export async function ensureSubrepoWorktree(
    workspace: string,
    localPath: string,
    baseBranch: string,
    runner: CommandRunner,
): Promise<string> {
    const canonicalWorkspace = realpathSync(workspace);
    const targetWorktree = path.resolve(canonicalWorkspace, localPath);
    if (existsSync(path.join(targetWorktree, ".git"))) return targetWorktree;
    const mainRoot = resolveRepositoryRoot(canonicalWorkspace);
    if (mainRoot === canonicalWorkspace) return targetWorktree;
    const sourceRepo = path.resolve(mainRoot, localPath);
    if (!existsSync(path.join(sourceRepo, ".git"))) return targetWorktree;
    const canonicalSource = realpathSync(sourceRepo);
    mkdirSync(path.dirname(targetWorktree), { recursive: true });
    let baseRef = "HEAD";
    for (const candidate of [`origin/${baseBranch}`, baseBranch]) {
        try {
            await runner.run("git", ["rev-parse", "--verify", candidate], { cwd: canonicalSource });
            baseRef = candidate;
            break;
        } catch {
            // Try the next candidate ref when the remote-tracking ref is absent.
        }
    }
    await runner.run("git", ["worktree", "add", "--detach", targetWorktree, baseRef], { cwd: canonicalSource });
    return targetWorktree;
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
