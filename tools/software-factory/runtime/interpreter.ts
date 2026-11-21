// Copyright (c) Microsoft Corporation. All rights reserved.

import { spawn } from "node:child_process";
import { realpathSync, statSync } from "node:fs";
import path from "node:path";
import { FactoryError } from "../core/contracts.ts";

export interface ExtensionInterpreter { executable: string; version: string }

export function validateExtensionInterpreter(input: unknown): ExtensionInterpreter {
    if (!input || typeof input !== "object" || !("executable" in input) || !("version" in input) ||
        typeof input.executable !== "string" || !path.isAbsolute(input.executable) || typeof input.version !== "string" ||
        !/^\d+\.\d+\.\d+(?:[-+].*)?$/.test(input.version)) {
        throw new FactoryError("extension_node_invalid", "The Node interpreter probe returned an invalid executable/version. No extension was launched.", 503);
    }
    if (Number(input.version.split(".")[0]) < 24) {
        throw new FactoryError("extension_node_unsupported", `The Node interpreter on PATH is ${input.version}; Software Factory extensions require Node.js 24+. The embedded app runtime version is not a substitute.`, 503);
    }
    let executable: string;
    let isFile: boolean;
    try {
        executable = realpathSync(input.executable);
        isFile = statSync(executable).isFile();
    } catch {
        throw new FactoryError("extension_node_invalid", "The probed Node executable could not be inspected. Ensure Node.js 24+ is installed and accessible on PATH. No extension was launched.", 503);
    }
    if (!isFile) throw new FactoryError("extension_node_invalid", "The verified Node interpreter is not an executable file.", 503);
    return { executable, version: input.version };
}

export async function resolveExtensionInterpreter(environment: NodeJS.ProcessEnv = process.env): Promise<ExtensionInterpreter> {
    return new Promise((resolve, reject) => {
        const probe = 'process.stdout.write(JSON.stringify({executable:process.execPath,version:process.versions.node}))';
        const child = spawn("node", ["-e", probe], {
            env: environment, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
            signal: AbortSignal.timeout(10_000),
        });
        let output = "";
        let bytes = 0;
        child.stdout.setEncoding("utf8").on("data", (chunk: string) => {
            bytes += Buffer.byteLength(chunk);
            if (bytes > 4096) child.kill();
            else output += chunk;
        });
        child.stderr.resume();
        child.once("error", () => reject(new FactoryError("extension_node_unavailable",
            "Could not verify Node.js 24+ on the execution host's PATH. This is the extension interpreter, not a missing Copilot SDK package. No agents were started.", 503)));
        child.once("close", (code) => {
            if (code !== 0 || bytes > 4096) {
                reject(new FactoryError("extension_node_unavailable", "The Node interpreter probe failed or returned excessive output. No extension was launched.", 503));
                return;
            }
            try { resolve(validateExtensionInterpreter(JSON.parse(output))); }
            catch (error) {
                reject(error instanceof FactoryError ? error : new FactoryError("extension_node_invalid",
                    "The Node interpreter probe could not be validated. No extension was launched.", 503));
            }
        });
    });
}
