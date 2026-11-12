// Copyright (c) Microsoft Corporation. All rights reserved.

import { randomUUID } from "node:crypto";
import { FactoryError, object } from "./contracts.ts";
import type { Lifecycle } from "./lifecycle.ts";
import { hash, now } from "./lifecycle-store.ts";
import type { Json, ObserverEffectPlan, Operation, PlannedItem, WorkItem } from "./lifecycle-contracts.ts";

export async function reconcileObserverEffect(factory: Lifecycle, featureId: string, operation: Operation): Promise<Json> {
    const effect = operation.observer;
    if (!effect || !factory.provider.findObserverMutation) throw new FactoryError("observer_reconciliation_unavailable", "This provider cannot reconcile the original observer effect.", 409);
    const publication = factory.store.state(featureId).operations.find((entry) => entry.key === effect.publicationKey);
    if (!publication?.publicationBinding || publication.planId !== factory.plan(featureId).artifact.id ||
        operation.planId !== publication.planId || operation.configurationId !== factory.store.configuration()?.id) {
        throw new FactoryError("stale_scope", "Reconcile this historical observer target explicitly; current authority differs.", 409);
    }
    const item = factory.plan(featureId).value.items.find((item) => item.id === publication.itemId);
    if (!item) throw new FactoryError("stale_scope", "The observer work item no longer belongs to this plan.", 409);
    const repo = factory.configuration().repositories[item.repository]!;
    for (const [index, mutation] of effect.mutations.entries()) {
        if (mutation.status === "completed" || mutation.status === "prepared") continue;
        const receipt = await factory.provider.findObserverMutation(repo, mutation.value);
        if (!receipt) throw new FactoryError("reconciliation_required", "The observer mutation has an uncertain outcome. It was not resent.", 409);
        factory.store.change(featureId, (state) => {
            const current = state.operations.find((entry) => entry.key === operation.key)!;
            if (current.claimId !== operation.claimId || hash(current.observer!.mutations[index]!.value) !== hash(mutation.value)) {
                throw new FactoryError("operation_owner_changed", "The observer effect changed while reconciling its original receipt.", 409);
            }
            current.observer!.mutations[index]!.status = "completed";
            current.observer!.mutations[index]!.receipt = receipt;
            current.updatedAt = now();
        });
        await factory.checkpoint?.(featureId);
    }
    const latest = factory.store.state(featureId).operations.find((entry) => entry.key === operation.key)!;
    if (latest.observer!.mutations.some((mutation) => mutation.status !== "completed")) {
        throw new FactoryError("observer_continuation_required", "Prior effects are reconciled. Resume the owning workflow to send the remaining prepared effects.", 409);
    }
    factory.store.change(featureId, (state) => {
        const current = state.operations.find((entry) => entry.key === operation.key)!;
        current.status = "completed"; current.receipt = effect.result; current.error = null; current.updatedAt = now();
    });
    await factory.checkpoint?.(featureId);
    return effect.result;
}

export async function runObserverEffect(input: {
    factory: Lifecycle; featureId: string; executionId: string; publication: Operation;
    item: PlannedItem; workItem: WorkItem; headSha: string;
    purpose: NonNullable<Operation["observer"]>["purpose"]; identity: Json;
    prepare(): Promise<ObserverEffectPlan>;
    assertCurrent(): Promise<void>;
}): Promise<Json> {
    const { factory, featureId, publication, item, workItem, headSha, purpose } = input;
    const key = hash({ publication: publication.key, headSha, purpose, identity: input.identity });
    const authority = async () => {
        await input.assertCurrent();
        const execution = factory.assertFresh(featureId, input.executionId);
        const grant = factory.requireDispatchDecision(featureId, execution.delivery);
        const policy = object(grant.scope.observer, "Observer approval");
        if (!["starting", "running"].includes(execution.status) || grant.decision.id !== publication.approvalId ||
            policy[purpose === "status" ? "statusPublishing" : "ciRecovery"] !== true ||
            publication.configurationId !== factory.store.configuration()?.id || publication.planId !== factory.plan(featureId).artifact.id) {
            throw new FactoryError("observer_approval_required", "The original dispatch does not authorize this observer effect.", 409);
        }
    };
    const assertTarget = async () => {
        await authority();
        const receipt = object(await factory.verifyRecordedLocalPublication(featureId, item, workItem, publication), "Observer publication");
        if (receipt.headSha !== headSha) throw new FactoryError("observer_evidence_changed", "The observer effect belongs to a different source head.", 409);
        await authority();
    };
    await assertTarget();
    let operation = factory.store.state(featureId).operations.find((entry) => entry.key === key);
    if (!operation) {
        const plan = await input.prepare();
        await assertTarget();
        if (plan.mutations.some((mutation) => mutation.headSha !== headSha ||
            mutation.prNumber !== object(publication.receipt, "Publication receipt").prNumber)) {
            throw new FactoryError("invalid_observer_target", "The effect plan does not match the authorized PR/head.", 409);
        }
        operation = {
            key, kind: "observer", itemId: item.id, planId: publication.planId, configurationId: publication.configurationId,
            approvalId: publication.approvalId, claimId: randomUUID(), status: "inflight", receipt: null, error: null, updatedAt: now(),
            observer: { publicationKey: publication.key, headSha, purpose, identity: input.identity, result: plan.result,
                mutations: plan.mutations.map((value) => ({ value, status: "prepared", receipt: null })) },
        };
        const created = operation;
        factory.store.change(featureId, (state) => {
            if (state.operations.some((entry) => entry.key === key)) throw new FactoryError("operation_owner_changed", "Another admission owns this observer effect.", 409);
            state.operations.push(created);
        });
        await factory.checkpoint?.(featureId);
    }
    if (!operation.observer || operation.approvalId !== publication.approvalId) throw new FactoryError("observer_approval_required", "This observer effect has different saved authority.", 409);
    if (operation.status === "completed") return operation.observer.result;
    const originalClaim = operation.claimId;
    const change = async (update: (current: Operation) => void) => {
        factory.store.change(featureId, (state) => {
            const current = state.operations.find((entry) => entry.key === key)!;
            if (current.claimId !== originalClaim) throw new FactoryError("operation_owner_changed", "Observer effect ownership changed.", 409);
            update(current); current.updatedAt = now();
        });
        await factory.checkpoint?.(featureId);
    };
    try {
        for (const [index, mutation] of operation.observer.mutations.entries()) {
            if (mutation.status === "completed") continue;
            await assertTarget();
            let receipt: Json | null;
            if (mutation.status === "sending") {
                receipt = await factory.provider.findObserverMutation?.(factory.configuration().repositories[item.repository]!, mutation.value) ?? null;
                if (!receipt) throw new FactoryError("reconciliation_required", "The observer effect was not confirmed. No retry was sent.", 409);
            } else {
                if (!factory.provider.sendObserverMutation) throw new FactoryError("observer_provider_unavailable", "The provider cannot send this observer effect.", 409);
                await change((current) => { current.observer!.mutations[index]!.status = "sending"; });
                receipt = await factory.provider.sendObserverMutation(factory.configuration().repositories[item.repository]!,
                    mutation.value, publication.publicationBinding!.target, async () => {
                        await factory.checkpoint?.(featureId);
                        await assertTarget();
                    });
            }
            await change((current) => {
                current.observer!.mutations[index]!.status = "completed";
                current.observer!.mutations[index]!.receipt = receipt;
            });
        }
        await change((current) => { current.status = "completed"; current.receipt = current.observer!.result; current.error = null; });
        await authority();
        return operation.observer.result;
    } catch (error) {
        await change((current) => { current.status = "reconciliation_required"; current.error = error instanceof Error ? error.message : String(error); });
        throw error;
    }
}
