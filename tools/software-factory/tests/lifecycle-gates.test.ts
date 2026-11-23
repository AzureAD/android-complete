// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { Application } from "../core/application.ts";
import { dispatch, legacyPreview, prepare } from "../core/commands.ts";
import { FactoryError } from "../core/contracts.ts";
import { decisionMessage, describeDecision } from "../core/decisions.ts";
import { fileEvidence, loadedWorkflowFingerprint, runtimeFingerprint } from "../core/evidence.ts";
import type { Configuration, LocalPublicationBinding, Operation, PlannedItem, PullRequest, WorkItem } from "../core/lifecycle-contracts.ts";
import { now } from "../core/lifecycle-store.ts";
import { reconcileObserverEffect, runObserverEffect } from "../core/observer-effects.ts";
import type { Provider } from "../providers/provider.ts";
import { actor, adoObserverFixture, fixture, requirements, transportFixture } from "./helpers.ts";
import { teamConfig } from "./team-provider.ts";

function assertFactoryError(action: () => unknown, code: string) {
    assert.throws(action, (error: unknown) => error instanceof FactoryError && error.code === code);
}

async function assertAsyncFactoryError(action: () => Promise<unknown>, code: string) {
    await assert.rejects(action, (error: unknown) => error instanceof FactoryError && error.code === code);
}

test("prepare and dispatch enforce exact scope-hash consent gates and reject revision/artifact drift", async () => {
    const f = transportFixture();
    try {
        f.app.lifecycle.configure(teamConfig(), actor);
        const feature = f.app.execute({ kind: "create_feature", requestId: "gate-1", input: requirements() }, actor);

        const approveReqCmd = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        const preparedReq = prepare(f.app, approveReqCmd);
        assert.match(preparedReq.consentKey || "", /^[0-9a-f]{64}$/);
        assert.ok(preparedReq.decision);

        await assertAsyncFactoryError(() => dispatch(f.app, approveReqCmd, actor), "consent_required");
        await assertAsyncFactoryError(
            () => dispatch(f.app, approveReqCmd, actor, { granted: true, revisionId: "wrong-key", actor }),
            "consent_required",
        );

        const revised = f.app.execute({
            kind: "revise_requirements",
            featureId: feature.id,
            expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Updated scope before approval" },
        }, actor);
        await assertAsyncFactoryError(
            () => dispatch(f.app, approveReqCmd, actor, { granted: true, revisionId: preparedReq.consentKey!, actor }),
            "stale_revision",
        );

        const freshApproveCmd = { kind: "approve_requirements", featureId: feature.id, expectedRevision: revised.current.id };
        const freshPreparedReq = prepare(f.app, freshApproveCmd);
        const approvedResult = await dispatch(f.app, freshApproveCmd, actor, {
            granted: true,
            revisionId: freshPreparedReq.consentKey!,
            actor,
        });
        assert.equal(f.app.registry.get(feature.id).status, "ready_for_design");
        assert.ok(approvedResult.feature);
        const repeatApproved = await dispatch(f.app, freshApproveCmd, actor);
        assert.equal(repeatApproved.outcome, "already_approved");

        const blockedDesign = f.app.lifecycle.store.artifact(feature.id, "design", revised.current.id, {
            recommendation: "Hold",
            spec: "# Draft Design",
        });
        assert.equal(f.app.lifecycle.gate(feature.id, "design").ready, false);

        const requestRevisionCmd = {
            kind: "request_revision",
            featureId: feature.id,
            stage: "design",
            expectedArtifactId: blockedDesign.id,
            feedback: "Document rollback isolation before proceeding.",
        };
        const preparedFeedback = prepare(f.app, requestRevisionCmd);
        assert.match(preparedFeedback.consentKey || "", /^[0-9a-f]{64}$/);

        const readyDesign = f.app.lifecycle.store.artifact(feature.id, "design", revised.current.id, {
            recommendation: "GO to Planning",
            spec: "# Ready Design v1",
        });
        await assertAsyncFactoryError(
            () => dispatch(f.app, requestRevisionCmd, actor, { granted: true, revisionId: preparedFeedback.consentKey!, actor }),
            "stale_artifact",
        );

        const designGateV1 = f.app.lifecycle.gate(feature.id, "design");
        assert.equal(designGateV1.ready, true);
        assert.equal(designGateV1.targetId, readyDesign.id);

        const approveDesignV1Cmd = {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "design",
            expectedScopeHash: designGateV1.scopeHash,
            approved: true,
        };
        const preparedDesignV1 = prepare(f.app, approveDesignV1Cmd);
        assert.ok(preparedDesignV1.consentKey);

        f.app.lifecycle.store.artifact(feature.id, "design", revised.current.id, {
            recommendation: "GO to Planning",
            spec: "# Ready Design v2 (drifted after prepare)",
        });
        await assertAsyncFactoryError(
            () => dispatch(f.app, approveDesignV1Cmd, actor, { granted: true, revisionId: preparedDesignV1.consentKey!, actor }),
            "stale_scope",
        );

        const designGateV2 = f.app.lifecycle.gate(feature.id, "design");
        const approveDesignV2Cmd = {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "design",
            expectedScopeHash: designGateV2.scopeHash,
            approved: true,
        };
        const preparedDesignV2 = prepare(f.app, approveDesignV2Cmd);
        await dispatch(f.app, approveDesignV2Cmd, actor, {
            granted: true,
            revisionId: preparedDesignV2.consentKey!,
            actor,
        });
        assert.equal(f.app.lifecycle.gate(feature.id, "design").approved, true);
        const repeatDesign = await dispatch(f.app, approveDesignV2Cmd, actor);
        assert.equal(repeatDesign.outcome, "already_approved");

        const legacyJsonPath = path.join(f.workspace, "legacy-state.json");
        writeFileSync(legacyJsonPath, JSON.stringify({ features: [{ id: "f-1", name: "Legacy Broker Feature" }] }));
        const imported = legacyPreview(legacyJsonPath);
        assert.equal(imported.features.length, 1);
        assert.equal(imported.features[0]?.name, "Legacy Broker Feature");

        const invalidLegacyPath = path.join(f.workspace, "invalid-legacy.json");
        writeFileSync(invalidLegacyPath, JSON.stringify({ invalid: true }));
        assertFactoryError(() => legacyPreview(invalidLegacyPath), "invalid_legacy_state");
    } finally {
        f.cleanup();
    }
});

test("describeDecision, decisionMessage, and stage decisions bind exact plan/backlog/dispatch authority", async () => {
    const f = transportFixture();
    try {
        f.app.lifecycle.configure(teamConfig(), actor);
        const feature = f.app.execute({ kind: "create_feature", requestId: "decisions-1", input: requirements() }, actor);

        const reqDecision = describeDecision(f.app, {
            kind: "approve_requirements",
            featureId: feature.id,
            expectedRevision: feature.current.id,
        }, { revisionId: feature.current.id });
        assert.equal(reqDecision.approveLabel, "Approve scope");
        assert.equal(reqDecision.canRequestChanges, true);
        assert.match(reqDecision.review, /Existing clients keep the current response shape/);
        const fullMsg = decisionMessage(reqDecision, false);
        const compactMsg = decisionMessage(reqDecision, true);
        assert.match(fullMsg, /Approve feature scope/);
        assert.doesNotMatch(fullMsg, /Full details are shown above in chat/);
        assert.match(compactMsg, /Full details are shown above in chat/);

        f.app.execute(
            { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor,
            { granted: true, revisionId: feature.current.id, actor },
        );

        const design = f.app.lifecycle.store.artifact(feature.id, "design", feature.current.id, {
            recommendation: "GO to Planning",
            spec: "# Approved Design",
        });
        const designGate = f.app.lifecycle.gate(feature.id, "design");
        const designApprovePresentation = describeDecision(f.app, {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "design",
            expectedScopeHash: designGate.scopeHash,
            approved: true,
        }, { action: "Approve", gate: JSON.parse(JSON.stringify(designGate)) });
        assert.equal(designApprovePresentation.canRequestChanges, true);
        assert.equal(designApprovePresentation.approveLabel, "Approve design");

        const designRejectPresentation = describeDecision(f.app, {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "design",
            expectedScopeHash: designGate.scopeHash,
            approved: false,
        }, { action: "Reject", gate: JSON.parse(JSON.stringify(designGate)) });
        assert.equal(designRejectPresentation.canRequestChanges, false);
        assert.equal(designRejectPresentation.approveLabel, "Reject this scope");

        f.app.lifecycle.decide(feature.id, "design", designGate.scopeHash, true, actor);

        const plannedItem: PlannedItem = {
            id: "common-1",
            title: "Preserve response shape in Common",
            repository: "common",
            description: "Keep token response fields stable.",
            requirementIds: ["R1"],
            acceptanceCriteria: ["Existing clients keep the current response shape."],
            dependsOn: [],
            files: ["common/src/Response.kt"],
            tests: ["common/test/ResponseTest.kt"],
        };
        f.app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, {
            summary: "Single-repo Common change",
            items: [plannedItem],
            blockingUnknowns: [],
        }, design.id);

        const planGate = f.app.lifecycle.gate(feature.id, "plan");
        assert.equal(planGate.ready, true);
        const planCmd = {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "plan",
            expectedScopeHash: planGate.scopeHash,
            approved: true,
        };
        const preparedPlan = prepare(f.app, planCmd);
        assert.equal(preparedPlan.decision?.canRequestChanges, true);
        assert.match(preparedPlan.decision?.review || "", /common-1/);
        await dispatch(f.app, planCmd, actor, { granted: true, revisionId: preparedPlan.consentKey!, actor });
        assert.equal(f.app.lifecycle.gate(feature.id, "plan").approved, true);

        const backlogGate = f.app.lifecycle.gate(feature.id, "backlog");
        assert.equal(backlogGate.ready, true);
        const backlogPresentation = describeDecision(f.app, {
            kind: "approve_stage",
            featureId: feature.id,
            stage: "backlog",
            expectedScopeHash: backlogGate.scopeHash,
            approved: true,
        }, { action: "Approve", gate: JSON.parse(JSON.stringify(backlogGate)) });
        assert.equal(backlogPresentation.canRequestChanges, false);
        f.app.lifecycle.decide(feature.id, "backlog", backlogGate.scopeHash, true, actor);
        assert.equal(f.app.lifecycle.gate(feature.id, "backlog").approved, true);
    } finally {
        f.cleanup();
    }
});

test("runObserverEffect and reconcileObserverEffect enforce observer authority and reconcile uncertain mutations without resending", async () => {
    const f = fixture();
    const h = adoObserverFixture("checkout");
    const workItems = new Map<string, WorkItem>();
    const publications = new Map<string, {
        provider: string;
        mode: string;
        marker: string;
        branch: string;
        prNumber: number;
        url: string;
        headSha: string;
    }>();

    const mockProvider: Provider = {
        async findWorkItem(_config, marker) {
            return workItems.get(marker) || null;
        },
        async createWorkItem(_config, item, marker) {
            const wi: WorkItem = {
                key: item.id,
                adoId: 1001,
                url: "https://example.test/wi/1001",
                title: item.title,
                repository: item.repository,
                status: "New",
            };
            workItems.set(marker, wi);
            return wi;
        },
        async linkWorkItem() {
            return { linked: 0 };
        },
        async inspectWorkItem(_config, item) {
            return item;
        },
        async dispatch() {
            return { ok: true };
        },
        async findLocalPublication(_config, _item, _workItem, marker) {
            return publications.get(marker) || null;
        },
        prepareObserverEffect: (...args) => h.provider.prepareObserverEffect!(...args),
        sendObserverMutation: (...args) => h.provider.sendObserverMutation!(...args),
        findObserverMutation: (...args) => h.provider.findObserverMutation!(...args),
        async findDispatch() {
            return null;
        },
        async inspectPr(repo, number, itemId): Promise<PullRequest> {
            return {
                key: itemId,
                repository: repo.slug,
                number,
                url: "https://example.test/pr/42",
                status: "open",
                head: h.state.head,
                checks: h.state.passed ? "passing" : "failing",
                title: "Fixture PR",
                baseBranch: repo.baseBranch,
                workItemIds: [1001],
            };
        },
        async findPr() {
            return null;
        },
        async diff() {
            return "";
        },
        async preflight() {
            return { ok: true };
        },
        async findFeedback() {
            return null;
        },
        async postFeedback() {
            return { ok: true };
        },
        async markDispatched() {
            return { ok: true };
        },
        async review() {
            return { pass: true };
        },
    };

    const app = new Application({ ...f.options, provider: mockProvider });
    try {
        const config: Configuration = {
            repositories: { authenticator: h.repository },
            boards: {
                organization: "example",
                project: "Engineering",
                workItemType: "Product Backlog Item",
                areaPath: "Engineering",
                iterationPath: "Engineering",
                assignee: "",
                parentId: null,
            },
            delivery: { mode: "local", maxReviewRounds: 2 },
        };
        const savedConfig = app.lifecycle.configure(config, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "observer-1", input: requirements() }, actor);
        app.execute(
            { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor,
            { granted: true, revisionId: feature.current.id, actor },
        );

        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, {
            recommendation: "GO to Planning",
            spec: "# Observer Design",
        });
        app.lifecycle.decide(feature.id, "design", app.lifecycle.gate(feature.id, "design").scopeHash, true, actor);

        const item: PlannedItem = {
            id: "auth-1",
            title: "Harden token broker IPC",
            repository: "authenticator",
            description: "Add retry resilience.",
            requirementIds: ["R1"],
            acceptanceCriteria: ["Existing clients keep the current response shape."],
            dependsOn: [],
            files: ["src/BrokerIpc.kt"],
            tests: ["test/BrokerIpcTest.kt"],
        };
        const planArtifact = app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, {
            summary: "Authenticator IPC change",
            items: [item],
            blockingUnknowns: [],
        }, design.id);
        app.lifecycle.decide(feature.id, "plan", app.lifecycle.gate(feature.id, "plan").scopeHash, true, actor);
        app.lifecycle.decide(feature.id, "backlog", app.lifecycle.gate(feature.id, "backlog").scopeHash, true, actor);
        await app.lifecycle.backlog(feature.id);

        const dispatchGate = app.lifecycle.gate(feature.id, "dispatch");
        assert.equal(dispatchGate.ready, true);
        const dispatchDecision = app.lifecycle.decide(feature.id, "dispatch", dispatchGate.scopeHash, true, actor);

        const pubKey = "local-pub-1";
        const marker = `software-factory-${pubKey}`;
        const receipt = {
            provider: "ado",
            mode: "local",
            marker,
            branch: "factory/test",
            prNumber: 42,
            url: "https://example.test/pr/42",
            headSha: h.state.head,
        };
        publications.set(marker, receipt);
        const binding: LocalPublicationBinding = {
            branch: "factory/test",
            headSha: h.state.head,
            source: { head: "0".repeat(40), indexTree: "1".repeat(40), base: "0".repeat(40), tree: "2".repeat(40) },
            target: {
                provider: "ado",
                hostname: "dev.azure.com",
                slug: h.repository.slug,
                baseBranch: h.repository.baseBranch,
                publisherId: h.state.publisher,
                publisherAccount: "",
            },
        };
        const publication: Operation = {
            key: pubKey,
            kind: "dispatch",
            dispatchMode: "local",
            itemId: item.id,
            planId: planArtifact.id,
            configurationId: savedConfig.id,
            claimId: "claim-pub-1",
            approvalId: dispatchDecision.id,
            publicationBinding: binding,
            status: "completed",
            receipt,
            error: null,
            updatedAt: now(),
        };
        app.lifecycle.store.change(feature.id, (state) => {
            state.operations.push(publication);
        });

        const workItem: WorkItem = {
            key: item.id,
            adoId: 1001,
            url: "https://example.test/wi/1001",
            title: item.title,
            repository: item.repository,
            status: "Active",
        };

        const run = app.lifecycle.begin(feature.id, "monitor", actor);
        app.lifecycle.store.updateExecution(feature.id, run.id, { status: "running", phase: "observing" });

        // Wrong approvalId on publication must reject before preparing or sending any observer mutation.
        await assertAsyncFactoryError(
            () => runObserverEffect({
                factory: app.lifecycle,
                featureId: feature.id,
                executionId: run.id,
                publication: { ...publication, approvalId: "foreign-approval" },
                item,
                workItem,
                headSha: h.state.head,
                purpose: "checkout",
                identity: { buildId: 77, stage: "Failed" },
                prepare: () => h.provider.prepareObserverEffect!(h.repository, 42, h.snapshot(), { kind: "retry", stageOnly: true }),
                assertCurrent: async () => {},
            }),
            "observer_approval_required",
        );
        assert.equal(h.state.writes.length, 0);

        // Simulate a lost response after the stage retry lands remotely.
        h.state.loseResponse = "build/builds/77/stages/Failed";
        await assert.rejects(
            () => runObserverEffect({
                factory: app.lifecycle,
                featureId: feature.id,
                executionId: run.id,
                publication,
                item,
                workItem,
                headSha: h.state.head,
                purpose: "checkout",
                identity: { buildId: 77, stage: "Failed" },
                prepare: () => h.provider.prepareObserverEffect!(h.repository, 42, h.snapshot(), { kind: "retry", stageOnly: true }),
                assertCurrent: async () => {},
            }),
            /Lost observer mutation response/,
        );
        assert.equal(h.state.writes.length, 1);

        const uncertainOp = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "observer");
        assert.ok(uncertainOp);
        assert.equal(uncertainOp.status, "reconciliation_required");
        assert.equal(uncertainOp.observer?.mutations[0]?.status, "sending");

        // Reconcile confirms the remote stage retry landed without issuing a duplicate PATCH.
        const reconciled = await reconcileObserverEffect(app.lifecycle, feature.id, uncertainOp);
        assert.ok(reconciled);
        assert.equal(h.state.writes.length, 1, "reconcileObserverEffect must never resend a confirmed mutation");
        const completedOp = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.key === uncertainOp.key);
        assert.equal(completedOp?.status, "completed");
        assert.equal(completedOp?.observer?.mutations[0]?.status, "completed");
    } finally {
        app.close();
        f.cleanup();
    }
});

test("runtimeFingerprint and fileEvidence produce deterministic SHA-256 digests and raw file evidence", () => {
    const f = fixture();
    try {
        const fp1 = runtimeFingerprint();
        const fp2 = runtimeFingerprint();
        assert.match(fp1, /^[0-9a-f]{64}$/);
        assert.equal(fp1, fp2);
        assert.equal(fp1, loadedWorkflowFingerprint);

        const sample = path.join(f.directory, "sample.txt");
        writeFileSync(sample, "deterministic-evidence\n");
        const present = fileEvidence(sample);
        assert.ok(Buffer.isBuffer(present));
        assert.equal(present.toString("utf8"), "deterministic-evidence\n");
        assert.deepEqual(present, fileEvidence(sample));

        assert.throws(() => fileEvidence(path.join(f.directory, "missing.txt")));
    } finally {
        f.cleanup();
    }
});
