// Copyright (c) Microsoft Corporation. All rights reserved.

import { readFileSync } from "node:fs";
import { Application } from "./core/application.ts";
import { FactoryError } from "./core/contracts.ts";
import { runtimeReport } from "./core/doctor.ts";
import { requireRuntime } from "./core/runtime-health.ts";
import { cases, summarizeGrades } from "./evals/design.mjs";

async function main(): Promise<void> {
    const [command, ...args] = process.argv.slice(2);
    if (command === "doctor") {
        if (args.includes("--require-runtime")) requireRuntime();
        console.log(JSON.stringify(runtimeReport(), null, 2));
        return;
    }
    if (command === "eval-list") {
        console.log(JSON.stringify({ cases }, null, 2));
        return;
    }
    if (command === "eval-summary") {
        if (args.length !== 2 || !args[0] || !args[1]) {
            throw new FactoryError("usage", "eval-summary requires <results.json> <grades.json>.");
        }
        const results: unknown = JSON.parse(readFileSync(args[0], "utf8"));
        const grades: unknown = JSON.parse(readFileSync(args[1], "utf8"));
        console.log(JSON.stringify(summarizeGrades(results, grades), null, 2));
        return;
    }
    if (!["list", "show", "team-list", "team-open", "serve"].includes(command || "")) {
        throw new FactoryError("usage", "Usage: node cli.ts doctor | list | show <feature-id> | team-list | team-open <ADO-feature-id> | eval-list | eval-summary <results.json> <grades.json> | serve [--port <port>]");
    }
    const app = new Application();
    let serving = false;
    try {
        if (command === "list") {
            console.log(JSON.stringify({ project: app.registry.project, features: await app.features() }, null, 2));
        } else if (command === "show") {
            if (args.length !== 1 || !args[0]) throw new FactoryError("usage", "show requires one stable feature ID.");
            console.log(JSON.stringify(await app.feature(args[0]), null, 2));
        } else if (command === "team-list") {
            const boards = app.lifecycle.configuration().boards;
            console.log(JSON.stringify(await app.shared.provider.list(boards.organization, boards.project), null, 2));
        } else if (command === "team-open") {
            if (args.length !== 1 || !/^[1-9]\d*$/.test(args[0]!)) throw new FactoryError("usage", "team-open requires an existing ADO Feature ID.");
            const boards = app.lifecycle.configuration().boards;
            console.log(JSON.stringify(await app.shared.open({ organization: boards.organization, project: boards.project, workItemId: Number(args[0]) }), null, 2));
        } else {
            let port = 0;
            if (args.length) {
                if (args.length !== 2 || args[0] !== "--port" || !args[1] || !/^\d+$/.test(args[1])) {
                    throw new FactoryError("usage", "serve accepts --port <0-65535> only.");
                }
                port = Number(args[1]);
                if (port > 65535) throw new FactoryError("usage", "Port must be between 0 and 65535.");
            }
            const { startServer } = await import("./server/server.ts");
            const server = await startServer({ application: app, port });
            if (app.registry.list().some((feature) => app.lifecycle.store.state(feature.id).schedule)) await app.runtime();
            serving = true;
            console.log(`Software Factory: ${server.url}\nChat and UI share ${app.registry.databasePath}\nNo AI work or remote action starts without explicit approval.`);
            let stopping = false;
            const stop = () => {
                if (stopping) return;
                stopping = true;
                void server.close().then(() => app.shutdown()).catch((error: unknown) => {
                    console.error("[Software Factory] Shutdown failed:", error);
                    process.exitCode = 1;
                });
            };
            process.once("SIGINT", stop);
            process.once("SIGTERM", stop);
        }
    } finally {
        if (!serving) app.close();
    }
}

void main().catch((error: unknown) => {
    console.error(error instanceof FactoryError ? `${error.code}: ${error.message}` : error);
    process.exitCode = 1;
});
