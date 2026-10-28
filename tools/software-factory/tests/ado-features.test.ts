// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import { randomUUID } from "node:crypto";
import { AdoFeatureProvider } from "../providers/ado-features.ts";
import { object } from "../core/contracts.ts";
import { actor, fixture, requirements } from "./helpers.ts";

function transport() {
    const identity = { id: "alice", displayName: "Alice", providerDisplayName: "Alice" };
    const revisions = new Map<number, Record<string, unknown>>();
    const attachments = new Map<string, string>();
    let revision = 1;
    let deny = false;
    let loseResponse = false;
    let uploads = 0;
    revisions.set(1, { id: 123, rev: 1, fields: { "System.WorkItemType": "Feature", "System.ChangedBy": identity }, relations: [] });
    const latest = () => revisions.get(revision)!;
    const manualEdit = (change: (item: Record<string, unknown>) => void) => {
        const item = structuredClone(latest());
        change(item); item.rev = ++revision; revisions.set(revision, item);
    };
    const fetcher: typeof fetch = async (input, init) => {
        const url = new URL(String(input));
        assert.equal(url.hostname, "dev.azure.com");
        assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer fixture-token");
        if (url.pathname.endsWith("/connectionData")) return Response.json({ authenticatedUser: identity });
        if (url.pathname.endsWith("/wiql")) return Response.json({ workItems: [{ id: 123 }] });
        if (url.pathname.includes("/attachments")) {
            if (init?.method === "POST") {
                if (deny) return new Response("", { status: 403 });
                const id = randomUUID(); uploads++;
                attachments.set(id, String(init.body));
                return Response.json({ id, url: `https://dev.azure.com/example/Engineering/_apis/wit/attachments/${id}` });
            }
            return new Response(attachments.get(url.pathname.split("/").at(-1)!)!);
        }
        if (init?.method === "PATCH") {
            if (deny) return new Response("", { status: 403 });
            const operations: Record<string, unknown>[] = JSON.parse(String(init.body));
            if (operations[0]!.value !== revision) return new Response("", { status: 412 });
            assert.equal(new Headers(init.headers).get("Content-Type"), "application/json-patch+json");
            manualEdit((item) => {
                const relations = item.relations as unknown[];
                for (const operation of operations.slice(1)) {
                    if (operation.path === "/relations/-") relations.push(operation.value);
                    else if (operation.op === "remove") relations.splice(Number(String(operation.path).split("/").at(-1)), 1);
                    else object(item.fields, "fields")[String(operation.path).replace("/fields/", "")] = operation.value;
                }
                object(item.fields, "fields")["System.ChangedBy"] = identity;
            });
            if (loseResponse) { loseResponse = false; throw new Error("Reply lost after ADO committed."); }
        }
        const historical = /\/revisions\/(\d+)$/.exec(url.pathname);
        return Response.json(historical ? revisions.get(Number(historical[1])) : latest());
    };
    const runner = { async run(command: string, args: string[]) {
        assert.equal(command, "az"); assert.ok(args.includes("499b84ac-1321-427f-aa17-267ca6975798")); return "fixture-token";
    } };
    return { provider: () => new AdoFeatureProvider(runner, fetcher), latest, revisions, manualEdit,
        deny: () => { deny = true; }, loseResponse: () => { loseResponse = true; }, uploads: () => uploads };
}

const target = { organization: "example", project: "Engineering", workItemId: 123 };

test("ADO attachment transport verifies historical writers, CAS, restart and ordinary work item edits", async () => {
    const f = fixture();
    const server = transport();
    const provider = server.provider();
    try {
        f.app.lifecycle.configure({ repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev", account: "local", localPath: "." } },
            boards: { organization: "example", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering", iterationPath: "Engineering", assignee: "", parentId: null } }, actor);
        const identity = await provider.identity(target);
        const author = { ...actor, ado: identity };
        const feature = f.app.execute({ kind: "create_feature", requestId: "root", input: requirements() }, author);
        const first = await provider.write(target, null, f.app.shared.snapshot(f.app, feature.id), identity);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, author,
            { granted: true, revisionId: feature.current.id, actor: author });
        const approved = await provider.write(target, first, f.app.shared.snapshot(f.app, feature.id), identity);
        const restarted = server.provider();
        assert.deepEqual((await restarted.read(target))?.snapshot.feature.approvals, approved.snapshot.feature.approvals);
        server.manualEdit((item) => { object(item.fields, "fields")["System.Title"] = "Ordinary user edit"; });
        assert.equal((await restarted.read(target))!.digest, approved.digest);
        const uploads = server.uploads();
        await assert.rejects(() => provider.write(target, approved, approved.snapshot, identity), /Another user changed/);
        assert.equal(server.uploads(), uploads, "stale CAS must fail before uploading");
        assert.deepEqual(await provider.list("example", "Engineering"), [target]);
        server.manualEdit((item) => { item.relations = structuredClone(server.revisions.get(first.revision)!.relations); });
        await assert.rejects(() => server.provider().read(target), /historical attachment/);
    } finally { f.cleanup(); }
});

test("ADO denied writes and lost responses cannot return a success receipt", async () => {
    for (const mode of ["denied", "lost"] as const) {
        const f = fixture();
        const server = transport();
        const provider = server.provider();
        try {
            f.app.lifecycle.configure({ repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev", account: "local", localPath: "." } },
                boards: { organization: "example", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering", iterationPath: "Engineering", assignee: "", parentId: null } }, actor);
            const identity = await provider.identity(target);
            const feature = f.app.execute({ kind: "create_feature", requestId: "root", input: requirements() }, { ...actor, ado: identity });
            if (mode === "denied") server.deny(); else server.loseResponse();
            await assert.rejects(() => provider.write(target, null, f.app.shared.snapshot(f.app, feature.id), identity),
                mode === "denied" ? /HTTP 403/ : /Reply lost/);
            const remote = await server.provider().read(target);
            assert.equal(!!remote, mode === "lost", "read-back distinguishes denial from uncertain committed writes");
        } finally { f.cleanup(); }
    }
});
