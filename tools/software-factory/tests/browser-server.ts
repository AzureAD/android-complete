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
    if (!["seed-design", "seed-review-designs", "seed-review-plans"].includes(String(message))) return;
    void (async () => {
        const actor = { channel: "chat" as const, sessionId: "chat-fixture", reference: "browser-seed-design" };
        const submit = async (command: Record<string, unknown>) => {
            const preview = await app.preview(command);
            return app.submit(command, actor, preview.consentKey ? { granted: true, revisionId: preview.consentKey, actor } : undefined);
        };
        if (message === "seed-review-plans") {
            const feature = app.registry.list().find((entry) => entry.current.title === "Historical artifact approvals" && app.lifecycle.store.view(entry.id).shared)!;
            const run = app.lifecycle.begin(feature.id, "plan", actor);
            await app.shared.claimRun(feature.id, run);
            const content = {
                summary: "Current implementation plan",
                items: [{
                    id: "PBI-1", title: "Preserve response compatibility", repository: "common",
                    description: "Keep the response envelope unchanged.", requirementIds: ["R1"],
                    acceptanceCriteria: ["Existing clients keep the response format."],
                    dependsOn: [], files: ["Response.kt"], tests: ["ResponseTest"],
                }],
                blockingUnknowns: [],
            };
            const parent = app.lifecycle.store.view(feature.id).currentArtifacts.design;
            const old = app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { ...content, summary: "Historical implementation plan" }, parent);
            const current = app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, content, parent);
            await app.shared.finishRun(feature.id, run.id);
            app.lifecycle.store.updateExecution(feature.id, run.id, { status: "completed", phase: "plan ready for review" });
            process.send?.({ featureId: feature.id, oldArtifactId: old.id, currentArtifactId: current.id });
            return;
        }
        const historical = message === "seed-review-designs";
        const draft = app.execute({ kind: "create_feature", requestId: historical ? "historical-approvals" : "readable-design", input: {
            title: historical ? "Historical artifact approvals" : "Readable design review", description: "Preserve the response contract.",
            requirements: [{ text: "Existing clients keep the response format." }],
        } }, actor);
        await submit({ kind: "publish_feature", featureId: draft.id, target: { organization: "example", project: "Engineering", workItemId: historical ? 457 : 456 } });
        const feature = app.registry.list().find((entry) => entry.id !== draft.id && entry.current.title === draft.current.title)!;
        await submit({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const run = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, run);
        const old = historical ? app.lifecycle.store.artifact(feature.id, "design", feature.current.id, {
            recommendation: "GO to Planning", design: { sections: { overview: "Historical response design." } },
        }) : null;
        const current = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning",
            design: { sections: { overview: "Keep the existing response contract intact.\n\nReview the compatibility evidence before continuing." } } });
        await app.shared.finishRun(feature.id, run.id);
        app.lifecycle.store.updateExecution(feature.id, run.id, { status: "completed", phase: "design ready for review" });
        process.send?.({ featureId: feature.id, oldArtifactId: old?.id, currentArtifactId: current.id });
    })().catch((error: unknown) => { process.send?.({ error: error instanceof Error ? error.message : String(error) }); });
});
process.once("SIGTERM", () => { void server.close().then(() => app.shutdown()); });
