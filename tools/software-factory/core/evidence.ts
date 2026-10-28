// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync, readlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { workerGuidanceFiles } from "../runtime/guidance.ts";

export function runtimeFingerprint(): string {
    const root = fileURLToPath(new URL("../", import.meta.url));
    const digest = createHash("sha256");
    const visit = (directory: string) => {
        for (const name of readdirSync(directory).sort()) {
            const file = path.join(directory, name);
            if (lstatSync(file).isDirectory()) visit(file);
            else if (name.endsWith(".ts")) digest.update(file.slice(root.length)).update(readFileSync(file));
        }
    };
    for (const directory of ["core", "chat", "server", "runtime", "providers", "workflows"]) visit(path.join(root, directory));
    digest.update(readFileSync(path.join(root, "package-lock.json")));
    digest.update(readFileSync(path.join(root, "cli.ts")));
    for (const relative of [
        "../../../.github/extensions/DevDesignDoc/workflow.mjs",
        "../../../.github/extensions/SoftwareFactory/extension.mjs",
        ...workerGuidanceFiles.map((file) => `../../../${file}`),
    ]) digest.update(readFileSync(fileURLToPath(new URL(relative, import.meta.url))));
    return digest.digest("hex");
}

export function fileEvidence(file: string): string | Buffer {
    return lstatSync(file).isSymbolicLink() ? readlinkSync(file) : readFileSync(file);
}
