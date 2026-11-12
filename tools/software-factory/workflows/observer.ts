// Copyright (c) Microsoft Corporation. All rights reserved.

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { WorkflowAgentOptions } from "@github/copilot-sdk";
import { FactoryError, object, text } from "../core/contracts.ts";
import { jsonValue, parseCodeImplementation, type Json, type Operation, type PlannedItem, type WorkItem } from "../core/lifecycle-contracts.ts";
import { hash, now } from "../core/lifecycle-store.ts";
import { currentPublicationBinding, repairHistory } from "../core/repairs.ts";
import { runObserverEffect } from "../core/observer-effects.ts";
import type { Lifecycle } from "../core/lifecycle.ts";
import type { ObserverEffectRequest, RepairPublicationInput } from "../providers/provider.ts";
import { ensureSubrepoWorktree } from "../core/runtime-health.ts";
import { systemRunner } from "../providers/cli.ts";
import { workerGuidance } from "../runtime/guidance.ts";
import { applyLocalFiles, type FactoryContext } from "./lifecycle.ts";
import {
    capturePublicationSource, renderPublicationDiff, snapshotWorktree, runLocalVerificationGate,
    verifyPublicationSource, isProtectedPipelineOrQualityPath,
} from "../../../.github/extensions/CodeFeature/workflow.mjs";
import { MAX_OBSERVER_ITERATIONS, selectAddressedReviewThreads, staleAdoBuildPolicies } from "../../../.github/extensions/CodeFeature/observer.mjs";

const strings = { type: "array", items: { type: "string" } };
const builderSchema = {
    type: "object", required: ["verdict", "summary", "evidence", "files"],
    properties: {
        verdict: { type: "string", enum: ["pass", "repair", "blocked"] }, summary: { type: "string" }, evidence: strings,
        files: { type: "array", items: { type: "object", required: ["path", "action", "content"],
            properties: { path: { type: "string" }, action: { type: "string", enum: ["write", "delete"] }, content: { type: "string" } } } },
    },
};

export async function factoryObserver(input: {
    factory: Lifecycle; featureId: string; executionId: string; publication: Operation;
    item: PlannedItem; workItem: WorkItem; ctx: FactoryContext;
    assertCurrent(): Promise<void>;
    agent(prompt: string, options: WorkflowAgentOptions): Promise<unknown>;
}) {
    const { factory, featureId, executionId, publication, item, workItem, ctx } = input;
    const config = factory.configuration();
    const repository = config.repositories[item.repository]!;
    const workspace = factory.store.registry.project.workspace;
    const history = () => repairHistory(factory.store.state(featureId), publication);
    const binding = () => currentPublicationBinding(factory.store.state(featureId), publication);
    const entry = (round: number) => {
        const result = history().find((operation) => operation.repair!.round === round);
        if (!result) throw new FactoryError("repair_history_invalid", "The repair was not reserved before agent work.", 409);
        return result;
    };
    const rootFor = (round: number) => path.join(workspace, ".software-factory-repairs", publication.key, String(round));
    const assertAuthority = async () => {
        ctx.signal?.throwIfAborted();
        await input.assertCurrent();
        const execution = factory.assertFresh(featureId, executionId);
        const grant = factory.requireDispatchDecision(featureId, execution.delivery);
        const policy = object(grant.scope.observer, "Approved observer policy");
        if (!["starting", "running"].includes(execution.status) || grant.decision.id !== publication.approvalId ||
            policy.engine !== "pr-observer" || policy.maxRepairs !== MAX_OBSERVER_ITERATIONS) {
            throw new FactoryError("repair_approval_required", "This publication has no current authority for autonomous observer repairs.", 409);
        }
    };
    const save = async (round: number, update: (operation: Operation) => void) => {
        await assertAuthority();
        const previous = entry(round);
        factory.store.change(featureId, (state) => {
            const current = state.operations.find((operation) => operation.key === previous.key)!;
            if (current.claimId !== previous.claimId) throw new FactoryError("operation_owner_changed", "The repair claim changed.", 409);
            update(current); current.updatedAt = now();
        });
        await factory.checkpoint?.(featureId);
        await assertAuthority();
    };
    const fingerprint = (root: string) => {
        const value = snapshotWorktree(root);
        if (!value) throw new FactoryError("repair_source_changed", "Cannot fingerprint the repair checkout.", 409);
        return value;
    };
    const assertSource = (round: number) => {
        const repair = entry(round).repair!;
        if (!repair.sourceFingerprint || fingerprint(rootFor(round)) !== repair.sourceFingerprint) {
            throw new FactoryError("repair_source_changed", "The repair checkout changed outside its recorded proposal; files were preserved.", 409);
        }
        if (repair.source) verifyPublicationSource(rootFor(round), { targetId: item.id, files: repair.files }, repair.source);
    };
    const assertPr = async () => {
        await assertAuthority();
        await factory.verifyRecordedLocalPublication(featureId, item, workItem, publication);
        await assertAuthority();
    };
    const finishThreads = async (round: number) => {
        const operation = entry(round);
        const repair = operation.repair!;
        for (const [index, raw] of (repair.threads || []).entries()) {
            const thread = object(raw, "Repair thread receipt");
            if (thread.receipt) continue;
            if (!factory.provider.replyRepairThread) throw new FactoryError("repair_provider_unavailable", "This provider cannot reconcile inline repair replies.", 409);
            const retry = thread.sending === true;
            if (!retry) await save(round, (current) => {
                object(current.repair!.threads![index], "Repair thread receipt").sending = true;
            });
            const receipt = await factory.provider.replyRepairThread(repository, Number(object(publication.receipt, "Publication").prNumber),
                jsonValue(thread.thread), text(thread.body, "Repair reply"), `${operation.key}-${index}`, assertPr, retry);
            await save(round, (current) => {
                object(current.repair!.threads![index], "Repair thread receipt").receipt = receipt;
            });
        }
        await save(round, (current) => { current.status = "completed"; current.error = null; });
    };
    await assertAuthority();
    const policy = object(factory.requireDispatchDecision(featureId).scope.observer, "Observer approval");
    const effect = (snapshot: Record<string, unknown>, request: ObserverEffectRequest,
        purpose: NonNullable<Operation["observer"]>["purpose"], identity: Json) => runObserverEffect({
            factory, featureId, executionId, publication, item, workItem,
            headSha: text(snapshot.headRefOid, "Observer head"), purpose, identity,
            assertCurrent: assertAuthority,
            prepare: async () => {
                if (!factory.provider.prepareObserverEffect) throw new FactoryError("observer_provider_unavailable", "This provider cannot safely prepare observer effects.", 409);
                return factory.provider.prepareObserverEffect(repository, Number(object(publication.receipt, "Publication").prNumber), jsonValue(snapshot), request);
            },
        });
    for (const operation of factory.store.state(featureId).operations.filter((entry) =>
        entry.observer?.publicationKey === publication.key && entry.status !== "completed")) {
        const saved = operation.observer!;
        await runObserverEffect({
            factory, featureId, executionId, publication, item, workItem, headSha: saved.headSha,
            purpose: saved.purpose, identity: saved.identity, assertCurrent: assertAuthority,
            prepare: async () => { throw new FactoryError("observer_history_invalid", "The saved observer plan is missing.", 409); },
        });
    }
    // A completed push with interrupted feedback resumes only its original replies, not another coding round.
    for (const operation of history()) {
        if (operation.repair!.phase === "pushed" && operation.status !== "completed") await finishThreads(operation.repair!.round);
    }
    const pending = history().find((operation) => operation.repair!.phase !== "failed" && operation.repair!.phase !== "pushed");
    const firstIteration = pending?.repair!.round ?? history().length;
    if (firstIteration > MAX_OBSERVER_ITERATIONS) throw new FactoryError("repair_history_invalid", "The saved repair allowance is invalid.", 409);
    let activeRoot: string | null = null;

    return {
        effects: {
            ...(policy.ciRecovery === true ? {
                refreshStaleBuilds: async (_pr: unknown, value: unknown) => {
                    const snapshot = object(value, "Observer snapshot");
                    const selected = staleAdoBuildPolicies(snapshot);
                    if (!selected.length) return [];
                    const result = await effect(snapshot, { kind: "refresh" }, "stale-policy",
                        jsonValue(selected.map((policy) => [policy.evaluationId, policy.buildId]).sort()));
                    if (!Array.isArray(result)) throw new FactoryError("observer_history_invalid", "The policy-refresh receipt is invalid.", 409);
                    return result.map((id) => text(id, "Refreshed evaluation"));
                },
                retryFailedBuilds: async (_pr: unknown, value: unknown, options: { stageOnly?: boolean; iteration?: number } = {}) => {
                    const snapshot = object(value, "Observer snapshot");
                    if (repository.provider !== "ado") return false;
                    if (options.stageOnly) {
                        const previous = factory.store.state(featureId).operations.find((entry) =>
                            entry.observer?.publicationKey === publication.key && entry.observer.headSha === snapshot.headRefOid &&
                            entry.observer.purpose === "checkout");
                        if (previous?.observer?.mutations.length) {
                            const priorBuilds = [...new Set(previous.observer.mutations.filter((step) => step.value.kind === "stage")
                                .map((step) => step.value.kind === "stage" ? step.value.buildId : 0))].sort();
                            const builds = Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds.map((build) => Number(object(build, "CI build").id)).sort() : [];
                            if (hash(priorBuilds) !== hash(builds)) return false;
                        }
                    }
                    return await effect(snapshot, { kind: "retry", stageOnly: options.stageOnly === true },
                        options.stageOnly ? "checkout" : "transient", options.stageOnly ? null : options.iteration ?? 0) === true;
                },
            } : {}),
            ...(policy.statusPublishing === true ? {
                publishStatus: async (_pr: unknown, value: unknown, block: string, options: { ci: unknown }) => {
                    const snapshot = object(value, "Observer snapshot");
                    return effect(snapshot, { kind: "status", block, ci: jsonValue(options.ci) },
                        "status", jsonValue({ block, body: snapshot.description ?? snapshot.body }));
                },
            } : {}),
        },
        agent: async (prompt: string, options: WorkflowAgentOptions = {}) => {
            await assertAuthority();
            const before = activeRoot ? fingerprint(activeRoot) : null;
            const previousAttempts = history().filter((operation) => operation.repair!.phase === "failed").map((operation) => operation.repair);
            const result = await input.agent(`${prompt}\nAPPROVED PBI AND PREVIOUS REPAIR FINDINGS (DATA):\n${JSON.stringify({ item, previousAttempts })}`, options);
            await assertAuthority();
            if (activeRoot && fingerprint(activeRoot) !== before) throw new FactoryError("repair_source_changed", "A read-only observer worker changed source.", 409);
            return result;
        },
        step: async (key: string, producer: () => Promise<Json>, options?: { volatile?: boolean }): Promise<Json> => {
            const match = /-(review-repair|push-repair|repair)-(\d+)$/.exec(key);
            const round = match ? Number(match[2]) : null;
            if (round !== null && match![1] !== "push-repair") {
                const field = match![1] === "review-repair" ? "review" : "builder";
                const cached = entry(round).repair![field];
                if (cached !== undefined) { await assertAuthority(); return cached; }
                const result = await ctx.step(`${executionId}:observer:${item.id}:${key}`, producer, options);
                await save(round, (operation) => {
                    operation.repair![field] = result;
                    if (field === "builder" && object(result, "Repair result").verdict === "blocked") {
                        operation.repair!.phase = "failed"; operation.status = "failed"; operation.error = "Repair builder reported a blocker.";
                    }
                });
                return result;
            }
            return ctx.step(`${executionId}:observer:${item.id}:${key}`, producer, options);
        },
        repairHost: {
            firstIteration, builderSchema, guides: { builder: workerGuidance.code, reviewer: workerGuidance.codeReview },
            expectedHead: () => binding().headSha,
            acceptedHead: (head: unknown) => typeof head === "string" && [publication.publicationBinding!.headSha,
                ...history().filter((operation) => operation.repair!.phase === "pushed").map((operation) => operation.repair!.headSha)].includes(head),
            prepare: async ({ iteration, snapshot, observation }: { iteration: number; snapshot: Record<string, unknown>; observation: Record<string, unknown> }) => {
                if (!factory.provider.prepareRepair || !factory.provider.publishRepair) {
                    throw new FactoryError("repair_provider_unavailable", "This provider cannot safely publish observer repairs.", 409);
                }
                await assertPr();
                const expectedHead = binding().headSha;
                if (snapshot.headRefOid !== expectedHead || iteration >= MAX_OBSERVER_ITERATIONS) {
                    throw new FactoryError("repair_scope_changed", "The repair snapshot or remaining allowance changed.", 409);
                }
                if (!history().some((operation) => operation.repair!.round === iteration)) {
                    if (history().length !== iteration) throw new FactoryError("repair_history_invalid", "A repair round cannot skip its predecessor.", 409);
                    factory.store.change(featureId, (state) => state.operations.push({
                        key: hash({ publication: publication.key, round: iteration }), kind: "repair", itemId: item.id,
                        planId: publication.planId, configurationId: publication.configurationId, approvalId: publication.approvalId,
                        claimId: randomUUID(), status: "inflight", receipt: null, error: null, updatedAt: now(),
                        repair: { publicationKey: publication.key, round: iteration, expectedHead, phase: "building" },
                    }));
                    await factory.checkpoint?.(featureId);
                    await assertAuthority();
                }
                const root = rootFor(iteration);
                const sourceRoot = await ensureSubrepoWorktree(workspace, repository.localPath, repository.baseBranch, systemRunner);
                if (!existsSync(root)) {
                    const parent = path.dirname(root);
                    mkdirSync(parent, { recursive: true });
                    if (realpathSync(parent) !== path.resolve(parent)) throw new FactoryError("repair_path_changed", "The repair directory must not be a link.", 409);
                    if (factory.provider.fetchRepairRefs) {
                        await factory.provider.fetchRepairRefs(repository, sourceRoot, [publication.publicationBinding!.branch, repository.baseBranch]);
                    } else {
                        await systemRunner.run("git", ["fetch", "--no-tags", "origin", `refs/heads/${publication.publicationBinding!.branch}`, `+refs/heads/${repository.baseBranch}:refs/remotes/origin/${repository.baseBranch}`], { cwd: sourceRoot });
                    }
                    await assertPr();
                    await systemRunner.run("git", ["-c", "core.hooksPath=", "worktree", "add", "--detach", root, expectedHead], { cwd: sourceRoot });
                    if (/conflict/i.test(String(observation.repairKind))) {
                        const base = await systemRunner.run("git", ["rev-parse", `origin/${repository.baseBranch}`], { cwd: sourceRoot });
                        let output: string;
                        try { output = execFileSync("git", ["merge-tree", "--write-tree", "--name-only", expectedHead, base], { cwd: root, encoding: "utf8" }); }
                        catch (error) {
                            const result = error as { status?: number; stdout?: string };
                            if (result.status !== 1 || typeof result.stdout !== "string") throw error;
                            output = result.stdout;
                        }
                        const tree = output.split(/\r?\n/)[0]!;
                        const conflicts = output.split(/\r?\n\r?\n/)[0]!.split(/\r?\n/).slice(1).filter(Boolean);
                        if (!/^[0-9a-f]{40}(?:[0-9a-f]{24})?$/.test(tree)) throw new FactoryError("merge_preparation_failed", "Git did not produce a merge tree.", 409);
                        await systemRunner.run("git", ["read-tree", "--reset", "-u", tree], { cwd: root });
                        await save(iteration, (operation) => { operation.repair!.mergeParent = base; operation.repair!.mergeConflicts = conflicts; });
                    }
                    await save(iteration, (operation) => { operation.repair!.sourceFingerprint = fingerprint(root); });
                }
                if (realpathSync(root) !== path.resolve(root) ||
                    await systemRunner.run("git", ["rev-parse", "HEAD"], { cwd: root }) !== expectedHead ||
                    await systemRunner.run("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: root }) !==
                    await systemRunner.run("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: sourceRoot })) {
                    throw new FactoryError("repair_path_changed", "The isolated repair checkout no longer matches the approved repository/head.", 409);
                }
                assertSource(iteration);
                activeRoot = root;
                return root;
            },
            apply: async ({ iteration, repairResult }: { iteration: number; repairResult: unknown }) => {
                await assertAuthority();
                assertSource(iteration);
                const result = object(repairResult, "Observer repair");
                const files = parseCodeImplementation({ summary: result.summary, files: result.files, blockingUnknowns: [] }).files;
                if (entry(iteration).repair!.mergeConflicts?.some((file) => !files.some((proposal) => proposal.path === file))) {
                    throw new FactoryError("merge_conflicts_unresolved", "Every merge-conflicted file must be explicitly resolved by the reviewed proposal.", 409);
                }
                for (const file of files) {
                    if (isProtectedPipelineOrQualityPath(file.path) || /(^|\/)(local\.properties|gradle\.properties)$/i.test(file.path) ||
                        /^(<{7}|={7}|>{7})(?: |$)/m.test(file.content)) {
                        throw new FactoryError("repair_scope_changed", `The repair contains protected configuration or unresolved conflict markers: ${file.path}.`, 409);
                    }
                }
                applyLocalFiles(rootFor(iteration), files, new Map());
                const source = capturePublicationSource(rootFor(iteration), { id: item.id, baseBranch: repository.baseBranch }, { files });
                await save(iteration, (operation) => {
                    operation.repair!.files = files; operation.repair!.source = source;
                    operation.repair!.sourceFingerprint = fingerprint(rootFor(iteration));
                });
            },
            verify: async (args: Parameters<typeof runLocalVerificationGate>[0]) => {
                const round = history().findLast((operation) => operation.repair!.phase === "building")!.repair!.round;
                assertSource(round);
                await assertAuthority();
                const result = runLocalVerificationGate(args);
                assertSource(round);
                await save(round, (operation) => { operation.repair!.verification = jsonValue(result); });
                return result;
            },
            reviewContext: async (round: number) => {
                assertSource(round);
                const repair = entry(round).repair!;
                return { sourceBinding: repair.source, diff: renderPublicationDiff(rootFor(round), repair.source), verification: repair.verification };
            },
            verifyReview: async ({ iteration }: { iteration: number }) => { await assertAuthority(); assertSource(iteration); },
            finishAttempt: async (round: number, reason: string) => save(round, (operation) => {
                operation.repair!.phase = "failed"; operation.status = "failed"; operation.error = reason;
            }),
            push: async ({ iteration, snapshot, observation, repairResult, reviewResult }: {
                iteration: number; snapshot: Record<string, unknown>; observation: unknown; repairResult: unknown; reviewResult: unknown;
            }) => {
                const review = object(reviewResult, "Independent observer review");
                if (review.verdict !== "pass" || !Array.isArray(review.findings) || review.findings.length) {
                    throw new FactoryError("repair_review_required", "The observer repair needs a passing independent review.", 409);
                }
                assertSource(iteration);
                await assertPr();
                const repair = entry(iteration).repair!;
                const proposal = object(repairResult, "Repair proposal");
                const publicationInput: RepairPublicationInput = {
                    localPath: path.relative(workspace, rootFor(iteration)), expectedHead: repair.expectedHead,
                    source: repair.source!, files: repair.files!, summary: text(proposal.summary, "Repair summary"),
                    ...(repair.mergeParent ? { mergeParent: repair.mergeParent } : {}),
                };
                const headSha = await factory.provider.prepareRepair!(repository, publicationInput);
                const observedThreads = (repository.provider === "github" ? snapshot.inlineReviewComments : snapshot.activeReviewThreads) ?? [];
                if (!Array.isArray(observedThreads)) throw new FactoryError("invalid_review_snapshot", "The observer omitted its review thread list.", 409);
                const threads = selectAddressedReviewThreads({
                    threads: observedThreads.map((thread) => object(thread, "Observed review thread")),
                    observation: object(observation, "Observer classification"), repairResult: proposal, changedFiles: repair.files!.map((file) => file.path),
                });
                await save(iteration, (operation) => {
                    operation.repair!.phase = "prepared"; operation.repair!.headSha = headSha;
                    operation.repair!.review = jsonValue(reviewResult);
                    operation.repair!.threads = threads.map((thread: unknown) => jsonValue({
                        thread, body: `Addressed in ${headSha}: ${proposal.summary}`, sending: false, receipt: null,
                    }));
                });
                try {
                    const receipt = await factory.provider.publishRepair!(config, item, workItem, `software-factory-${publication.key}`,
                        publication.publicationBinding!, publicationInput, headSha, async () => {
                            await factory.checkpoint?.(featureId);
                            await assertAuthority();
                            assertSource(iteration);
                        });
                    await save(iteration, (operation) => { operation.repair!.phase = "pushed"; operation.receipt = receipt; });
                    binding();
                    await finishThreads(iteration);
                    return { pushedSha: headSha };
                } catch (error) {
                    factory.store.change(featureId, (state) => {
                        const current = state.operations.find((operation) => operation.key === entry(iteration).key)!;
                        current.status = "reconciliation_required"; current.error = error instanceof Error ? error.message : String(error);
                    });
                    await factory.checkpoint?.(featureId);
                    throw error;
                }
            },
        },
    };
}
