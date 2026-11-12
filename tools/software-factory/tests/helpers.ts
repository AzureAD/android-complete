// Copyright (c) Microsoft Corporation. All rights reserved.

import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { Application } from "../core/application.ts";
import type { Actor, Requirements } from "../core/contracts.ts";
import { ExternalProvider } from "../providers/external.ts";
import type { Json, RepositoryTarget } from "../core/lifecycle-contracts.ts";

export const actor: Actor = { channel: "chat", reference: "tool-call-1", sessionId: "owning-session" };
export const requirements = (): Requirements => ({
    title: "Preserve authentication compatibility",
    requirements: [{ id: "R1", text: "Existing clients keep the current response shape." }],
    blockingUnknowns: [],
});

export function fixture() {
    const directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-test-"));
    const workspace = path.join(directory, "workspace");
    mkdirSync(workspace);
    const options = { database: path.join(directory, "registry.sqlite"), workspace };
    const app = new Application(options);
    return { directory, workspace, options, app, cleanup: () => { app.close(); rmSync(directory, { recursive: true }); } };
}

export function transportFixture() {
    const result = fixture();
    // These unit tests isolate confirmation/UI transport; shared.test.ts exercises the real ADO boundary.
    result.app.preview = async (input) => result.app.prepare(input);
    result.app.submit = (input, actor, consent) => result.app.dispatch(input, actor, consent);
    return result;
}

export function adoObserverFixture(mode: "checkout" | "stale" | "fallback" = "checkout") {
    const repository: RepositoryTarget = { provider: "ado", hostname: "dev.azure.com", slug: "example/Engineering/authenticator",
        baseBranch: "dev", account: "", localPath: "." };
    const head = "a".repeat(40);
    const evaluationId = "01234567-89ab-cdef-0123-456789abcdef";
    const state = {
        head, body: "Human description. Fixes AB#1001", publisher: "fixture-publisher", passed: false,
        loseResponse: "", denyWrite: false, writes: [] as string[],
        build: { id: 77, repository: { id: "repo-id" }, sourceBranch: "refs/pull/42/merge",
            definition: { id: 1, name: "Validation" }, parameters: JSON.stringify({ "system.pullRequest.sourceCommitId": head }) },
        stages: [
            { type: "Stage", id: "success-stage", identifier: "Successful", attempt: 1, state: "completed", result: "succeeded" },
            { type: "Stage", id: "failed-stage", identifier: "Failed", attempt: 1, state: "completed", result: "failed" },
        ],
        policy: { evaluationId, artifactId: "vstfs:///CodeReview/CodeReviewId/project-id/42",
            configuration: { isBlocking: true, type: { id: "0609b952-1397-4640-95ec-e00a01b2c241", displayName: "Build" } },
            status: mode === "stale" ? "approved" : "rejected", context: { buildId: 77, buildIsNotCurrent: mode === "stale" },
            startedDate: "2026-01-01T00:00:00Z", completedDate: "2026-01-01T00:01:00Z" },
    };
    if (mode === "fallback") state.stages[1]!.identifier = "";
    const pr = () => ({
        pullRequestId: 42, status: "active", description: state.body,
        lastMergeSourceCommit: { commitId: state.head }, lastMergeCommit: { commitId: "c".repeat(40) },
        sourceRefName: "refs/heads/factory/test", targetRefName: "refs/heads/dev",
        repository: { id: "repo-id", name: "authenticator", project: { id: "project-id", name: "Engineering" } },
    });
    const provider = new ExternalProvider({ run: async () => "fixture-token" }, async (input, init) => {
        const url = new URL(String(input));
        if (url.hostname !== "dev.azure.com" || !url.pathname.startsWith("/example/")) throw new Error("Observer escaped its configured host/organization.");
        const suffix = url.pathname.split("/_apis/")[1]!;
        if (init?.method === "PATCH") {
            if (state.denyWrite) return new Response("denied", { status: 403 });
            state.writes.push(suffix);
            if (suffix.startsWith("build/builds/77/stages/")) {
                if (url.searchParams.get("api-version") !== "7.0-preview.1") throw new Error("Wrong stage retry API version.");
                if (JSON.stringify(JSON.parse(String(init.body))) !== '{"state":1,"forceRetryAllJobs":false}') throw new Error("Unsafe stage retry payload.");
                const stage = state.stages.find((stage) => stage.identifier === suffix.split("/").at(-1));
                if (!stage || stage.result === "succeeded") throw new Error("Unexpected stage retry.");
                stage.attempt++;
                stage.result = "succeeded"; state.passed = state.stages.every((stage) => stage.result === "succeeded");
            } else if (suffix === `policy/evaluations/${evaluationId}`) {
                if (url.searchParams.get("api-version") !== "7.1-preview.1") throw new Error("Wrong policy API version.");
                state.policy.startedDate = "2026-01-01T01:00:00Z";
                state.policy.context = { buildId: 78, buildIsNotCurrent: false };
                state.policy.status = "approved"; state.passed = true;
            } else if (suffix === "git/repositories/authenticator/pullrequests/42") state.body = JSON.parse(String(init.body)).description;
            else throw new Error(`Unexpected observer write ${suffix}`);
            if (state.loseResponse === suffix) { state.loseResponse = ""; throw new Error("Lost observer mutation response"); }
            return Response.json(suffix.startsWith("git/") ? pr() : { accepted: true });
        }
        if (suffix === "connectionData") return Response.json({ authenticatedUser: { id: state.publisher }, authorizedUser: { id: state.publisher } });
        if (suffix === "git/repositories/authenticator/pullrequests/42") return Response.json(pr());
        if (suffix === "build/builds/77") return Response.json(state.build);
        if (suffix === "build/builds/77/timeline") return Response.json({ records: state.stages });
        if (suffix === "policy/evaluations") {
            if (url.searchParams.get("artifactId") !== state.policy.artifactId) throw new Error("Wrong PR policy artifact.");
            return Response.json({ value: [state.policy] });
        }
        throw new Error(`Unexpected observer read ${suffix}`);
    });
    const snapshot = (): Json => ({
        provider: "ado", number: 42, url: "https://dev.azure.com/example/Engineering/_git/authenticator/pullrequest/42",
        title: "Fixture", description: state.body, state: "active", headRefOid: state.head, headRefName: "factory/test",
        baseRefName: "dev", mergeStateStatus: "succeeded", reviewThreadsComplete: true,
        activeReviewThreads: [], unresolvedReviewThreadCount: 0, statusCheckRollup: [],
        policyEvaluations: [{ evaluationId, name: "Build", typeId: state.policy.configuration.type.id, blocking: true,
            status: state.passed ? "approved" : state.policy.status, buildId: state.policy.context.buildId,
            buildIsNotCurrent: state.policy.context.buildIsNotCurrent, buildSourceHeadSha: state.policy.context.buildIsNotCurrent ? "b".repeat(40) : state.head,
            buildDefinitionId: 1 }],
        prBuilds: mode === "stale" && !state.passed ? [] : [{
            id: state.policy.context.buildId, definition: "Validation", definitionId: 1, sourceHeadSha: state.head,
            status: "completed", result: state.passed ? "succeeded" : "failed",
            failedTasks: state.passed ? [] : [{ name: mode === "checkout" ? "Checkout" : "Build",
                issues: [mode === "checkout" ? "curl 56 Connection reset by peer" : "UnknownHostException: package feed"], excerpt: [] }],
        }],
    });
    return { repository, state, provider, snapshot };
}
