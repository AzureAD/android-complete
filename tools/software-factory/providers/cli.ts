// Copyright (c) Microsoft Corporation. All rights reserved.

import { spawn } from "node:child_process";
import { FactoryError } from "../core/contracts.ts";

export interface CommandRunner {
    run(command: "gh" | "az" | "git", args: string[], options?: { input?: string; env?: NodeJS.ProcessEnv; cwd?: string; signal?: AbortSignal }): Promise<string>;
}

export const systemRunner: CommandRunner = {
    run: async (command, args, options = {}) => new Promise((resolve, reject) => {
        const windowsAz = process.platform === "win32" && command === "az";
        const child = spawn(windowsAz ? "pwsh" : command, windowsAz
            ? ["-NoProfile", "-NonInteractive", "-Command", "$a = [Console]::In.ReadToEnd() | ConvertFrom-Json; & az @a; exit $LASTEXITCODE"]
            : args, {
            cwd: options.cwd, env: options.env || process.env,
            signal: AbortSignal.any([AbortSignal.timeout(60_000), ...(options.signal ? [options.signal] : [])]),
            windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
        });
        let output = "";
        let bytes = 0;
        const maximum = 4 * 1024 * 1024;
        child.stdout.setEncoding("utf8").on("data", (chunk: string) => {
            bytes += Buffer.byteLength(chunk);
            if (bytes > maximum) child.kill();
            else output += chunk;
        });
        child.stderr.resume();
        child.once("error", (error: NodeJS.ErrnoException) => reject(new FactoryError(
            error.code === "ENOENT" ? "cli_unavailable" : "provider_command_failed",
            `${command} failed or timed out. Check installation/authentication and reconcile any uncertain mutation.`, 503)));
        child.once("close", (code) => {
            if (code !== 0 || bytes > maximum) reject(new FactoryError("provider_command_failed", `${command} failed or returned excessive output. Check authentication, permissions, and CLI capability; no success was assumed.`, 502));
            else resolve(output.trim());
        });
        child.stdin.on("error", (error: NodeJS.ErrnoException) => { if (error.code !== "EPIPE") reject(error); });
        child.stdin.end(windowsAz ? JSON.stringify(args) : options.input);
    }),
};
