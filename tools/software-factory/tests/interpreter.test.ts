// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { FactoryError } from "../core/contracts.ts";
import { resolveExtensionInterpreter, validateExtensionInterpreter } from "../runtime/interpreter.ts";

test("the verified extension interpreter is a real Node 24+ process, independent of the host", async () => {
    const node = await resolveExtensionInterpreter();
    assert.ok(Number(node.version.split(".")[0]) >= 24);
    const result = spawnSync(node.executable, ["-p", "JSON.stringify({executable:process.execPath,version:process.versions.node})"], {
        encoding: "utf8", windowsHide: true, timeout: 10_000,
    });
    assert.equal(result.status, 0, result.stderr);
    const actual = JSON.parse(result.stdout) as { executable: string; version: string };
    assert.equal(realpathSync(actual.executable), node.executable);
    assert.equal(actual.version, node.version);
});

test("invalid interpreter metadata and unavailable files fail with specific diagnostic codes", () => {
    const cases: [unknown, string][] = [
        [null, "extension_node_invalid"],
        ["not metadata", "extension_node_invalid"],
        [{ executable: "relative-node", version: "24.0.0" }, "extension_node_invalid"],
        [{ executable: process.execPath, version: "not-a-version" }, "extension_node_invalid"],
        [{ executable: process.execPath, version: "20.19.0" }, "extension_node_unsupported"],
        [{ executable: process.execPath }, "extension_node_invalid"],
        [{ executable: path.dirname(process.execPath), version: "24.0.0" }, "extension_node_invalid"],
        [{ executable: path.join(process.execPath, "not-an-executable"), version: "24.0.0" }, "extension_node_invalid"],
    ];
    for (const [input, code] of cases) {
        assert.throws(() => validateExtensionInterpreter(input),
            (error: unknown) => error instanceof FactoryError && error.code === code);
    }
});
