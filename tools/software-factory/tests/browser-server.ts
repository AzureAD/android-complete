// Copyright (c) Microsoft Corporation. All rights reserved.

import { Application } from "../core/application.ts";
import { startServer } from "../server/server.ts";
import { TeamProvider, teamConfig } from "./team-provider.ts";

if (!process.env.SOFTWARE_FACTORY_HOME) throw new Error("Browser fixture requires isolated storage.");
const app = new Application({ sharedProvider: new TeamProvider() });
app.lifecycle.configure(teamConfig(), { channel: "web", reference: "browser-fixture" });
const server = await startServer({ application: app, port: 0 });
console.log(`Software Factory: ${server.url}`);
process.on("message", (message) => {
    if (message !== "seed-design") return;
    void (async () => {
        const actor = { channel: "chat" as const, sessionId: "chat-fixture", reference: "browser-seed-design" };
        const submit = async (command: Record<string, unknown>) => {
            const preview = await app.preview(command);
            return app.submit(command, actor, preview.consentKey ? { granted: true, revisionId: preview.consentKey, actor } : undefined);
        };
        const draft = app.execute({ kind: "create_feature", requestId: "readable-design", input: {
            title: "Readable design review", description: "Preserve the response contract.",
            requirements: [{ text: "Existing clients keep the response format." }],
        } }, actor);
        await submit({ kind: "publish_feature", featureId: draft.id, target: { organization: "example", project: "Engineering", workItemId: 456 } });
        const feature = app.registry.list().find((entry) => entry.id !== draft.id && entry.current.title === "Readable design review")!;
        await submit({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const run = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, run);
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning",
            design: { sections: { overview: "Keep the existing response contract intact.\n\nReview the compatibility evidence before continuing." } } });
        await app.shared.finishRun(feature.id, run.id);
        app.lifecycle.store.updateExecution(feature.id, run.id, { status: "completed", phase: "design ready for review" });
        process.send?.({ featureId: feature.id });
    })().catch((error: unknown) => { process.send?.({ error: error instanceof Error ? error.message : String(error) }); });
});
process.once("SIGTERM", () => { void server.close().then(() => app.shutdown()); });
