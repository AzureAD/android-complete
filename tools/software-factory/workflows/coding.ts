// Copyright (c) Microsoft Corporation. All rights reserved.

import type { WorkflowAgentOptions } from "@github/copilot-sdk";
import { FactoryError, object, text } from "../core/contracts.ts";
import {
    jsonValue, parseCodeImplementation, type CodeImplementationFile, type CodeReviewResult,
    type Json, type PlannedItem, type PublicationSource, type RepositoryTarget,
} from "../core/lifecycle-contracts.ts";
import { MAX_FIX_ITERATIONS, parseAllowedGradleVerificationCommand, prepareCodeFeature, runLocalVerificationGate, validateReview } from "../../../.github/extensions/CodeFeature/workflow.mjs";
import type { FactoryContext } from "./lifecycle.ts";
import { workerGuidance } from "../runtime/guidance.ts";

export interface CodingPreparation {
    engine: "code-feature";
    summary: string;
    files: CodeImplementationFile[];
    roundsCompleted: number;
    sourceBinding: PublicationSource;
    verification: Json;
    reviewHistory: Json[];
    packet: Json;
}

export async function prepareFactoryCode(input: {
    ctx: FactoryContext;
    executionId: string;
    workspace: string;
    item: PlannedItem;
    repository: RepositoryTarget;
    workItemId: number;
    revision: string;
    approvalReference: string;
    requirements: { id: string; text: string }[];
    design: Json;
    dependencyContext: Json;
    maxRounds: number;
    assertCurrent(): Promise<void>;
    apply(files: CodeImplementationFile[]): void;
    restore(): void;
    verify(run: () => ReturnType<typeof runLocalVerificationGate>): Promise<ReturnType<typeof runLocalVerificationGate>>;
    agent(prompt: string, options: WorkflowAgentOptions): Promise<unknown>;
}): Promise<CodingPreparation | { engine: "code-feature"; packet: Json; reviewHistory: Json[]; blocked: true }> {
    if (input.maxRounds < 1 || input.maxRounds > MAX_FIX_ITERATIONS + 1) {
        throw new FactoryError("coding_limit_unsupported", `CodeFeature supports at most ${MAX_FIX_ITERATIONS + 1} review rounds. Review updated delivery settings before starting; the approved limit was not silently changed.`, 409);
    }
    const requirements = input.requirements.filter((requirement) => input.item.requirementIds.includes(requirement.id));
    if (requirements.length !== input.item.requirementIds.length) {
        throw new FactoryError("invalid_scope", "The PBI refers to missing approved requirements.", 409);
    }
    const task = {
        title: input.item.title, revision: input.revision, workItemId: input.workItemId,
        approval: { approvedRevision: input.revision, approvedBy: "Factory confirmed dispatch", reference: input.approvalReference },
        requirements, blockingUnknowns: [],
        designSummary: JSON.stringify({
            scope: "Implement only this approved PBI; the feature design is supporting context, not additional scope.",
            item: input.item, design: input.design, dependencyContext: input.dependencyContext,
        }),
        targets: [{
            id: input.item.id, repository: input.item.repository, title: input.item.title,
            description: input.item.description, workItemId: input.workItemId,
            acceptanceCriteria: input.item.acceptanceCriteria,
            verificationCommands: input.item.tests.filter((test) => parseAllowedGradleVerificationCommand(test) !== null),
            ...input.repository,
        }],
    };
    const options = {
        workspaceRoot: input.workspace, createPullRequests: false, observePullRequests: false,
        applyToWorkspace: true, allowLocalOnly: true, maxFixIterations: input.maxRounds - 1,
        slimSchemas: false, pauseOnBlocker: false, verifyPrerequisites: false,
        iterativeIntake: false, requireBriefApproval: false, challengeBrief: false,
    };
    const checkedFiles = (files: unknown) => {
        if (!Array.isArray(files)) throw new FactoryError("invalid_implementation", "CodeFeature returned no file set.", 502);
        return parseCodeImplementation({
            summary: "CodeFeature proposed source", files: files.map((entry) => {
                const file = object(entry, "CodeFeature file");
                return { path: file.path, action: file.action === "delete" ? "delete" : "write", content: file.content };
            }), testsAddedOrUpdated: [], blockingUnknowns: [],
        }).files;
    };
    let previousFiles: CodeImplementationFile[] = [];
    // Replay the engine's journaled agent results, but reapply structured files after a restored/cancelled attempt.
    const packet = object(await prepareCodeFeature({
            ...input.ctx, args: { task, options },
            step: (key: string, producer: () => Promise<Json>, stepOptions?: { volatile?: boolean }) =>
                input.ctx.step(`${input.executionId}:coding:${input.item.id}:${key}`, async () => {
                    await input.assertCurrent();
                    return producer();
                }, stepOptions),
            agent: async (prompt: string, agentOptions: WorkflowAgentOptions) => {
                await input.assertCurrent();
                return input.agent(prompt, agentOptions);
            },
        }, {
            verifyLocal: (args: Parameters<typeof runLocalVerificationGate>[0]) =>
                input.verify(() => runLocalVerificationGate(args)),
            implementationHost: {
                guides: { evidence: workerGuidance.research, builder: workerGuidance.code, reviewer: workerGuidance.codeReview },
                prepareTarget: async () => input.assertCurrent(),
                applyFiles: (_target: unknown, files: unknown) => {
                    const parsed = checkedFiles(files);
                    if (previousFiles.some((file) => !parsed.some((next) => next.path === file.path))) {
                        throw new FactoryError("invalid_implementation", "Each revision must include every previously changed file.", 409);
                    }
                    input.apply(parsed);
                    previousFiles = parsed;
                },
                restoreFiles: () => input.restore(),
            },
        }), "CodeFeature preparation");
    await input.assertCurrent();
    const mapReview = (value: unknown): CodeReviewResult => {
        if (!validateReview(value, task)) throw new FactoryError("invalid_code_review", "The shared engine returned an incomplete review.", 502);
        const review = object(value, "CodeFeature review");
        const findings = (review.findings as unknown[]).map((value) => {
            const finding = object(value, "CodeFeature finding");
            return {
                id: text(finding.id, "Finding ID"), severity: finding.severity as "blocker" | "major" | "minor",
                category: "correctness" as const, file: text(finding.file, "Finding file"),
                location: text(finding.location, "Finding location"), evidence: text(finding.evidence, "Finding evidence"),
                requiredChange: text(finding.requiredChange, "Required change"),
            };
        });
        const criteria = (review.acceptanceCriteria as unknown[]).map((value) => {
            const criterion = object(value, "CodeFeature criterion");
            return { criterion: text(criterion.criterion, "Criterion"),
                status: criterion.passed === true ? "addressed" as const : "missing" as const,
                evidence: text(criterion.evidence, "Criterion evidence") };
        });
        const pass = (review.criteria as { pass: boolean }[]).every((criterion) => criterion.pass) &&
            criteria.every((criterion) => criterion.status === "addressed") &&
            !findings.length && !(review.blockingUnknowns as unknown[]).length;
        return { pass, criteria, findings,
            summary: pass ? "CodeFeature source review passed." : "CodeFeature found unresolved rubric checks, criteria or findings." };
    };
    const rounds = Array.isArray(packet.reviews) ? packet.reviews.map((entry) => object(entry, "CodeFeature round")) : [];
    const reviewHistory: Json[] = rounds.map((entry) => jsonValue({
        round: entry.round, mode: "local", engine: "code-feature",
        ...(typeof entry.builderSummary === "string" && entry.builderSummary ? { builderSummary: entry.builderSummary } : {}),
        ...(Array.isArray(entry.files) ? { files: entry.files.filter((file): file is string => typeof file === "string" && Boolean(file)) } : {}),
        ...(Array.isArray(entry.testsAddedOrUpdated) ? { testsAddedOrUpdated: entry.testsAddedOrUpdated.filter((item): item is string => typeof item === "string" && Boolean(item)) } : {}),
        ...(entry.review ? { review: mapReview(entry.review), codeFeatureReview: entry.review }
            : { validationError: Array.isArray(entry.validationErrors) ? entry.validationErrors.join("\n") : "No usable review." }),
    }));
    if (packet.recommendation !== "publication-ready") return { engine: "code-feature", packet: jsonValue(packet), reviewHistory, blocked: true };
    const implementation = object(packet.implementation, "Reviewed implementation");
    if (!Array.isArray(implementation.targets) || implementation.targets.length !== 1) {
        throw new FactoryError("invalid_implementation", "Expected the exact single approved PBI target.", 502);
    }
    const target = object(implementation.targets[0], "Reviewed target");
    if (target.targetId !== input.item.id || !Array.isArray(packet.reviews)) {
        throw new FactoryError("invalid_implementation", "The reviewed target or history does not match the PBI.", 502);
    }
    const last = rounds.at(-1);
    const review = object(last?.review, "Final CodeFeature review");
    if (!validateReview(review, task) ||
        !(review.criteria as { pass: boolean }[]).every((criterion) => criterion.pass) ||
        !(review.acceptanceCriteria as { passed: boolean }[]).every((criterion) => criterion.passed) ||
        (review.findings as Json[]).length || (review.blockingUnknowns as Json[]).length) {
        throw new FactoryError("review_evidence_required", "CodeFeature did not return a complete passing independent review.", 409);
    }
    const bindings = packet.sourceBindings;
    if (!Array.isArray(bindings) || bindings.length !== 1) throw new FactoryError("review_evidence_required", "Missing reviewed source binding.", 409);
    const source = object(object(bindings[0], "Source binding").publicationSource, "Reviewed Git tree");
    const sourceBinding: PublicationSource = {
        head: text(source.head, "Reviewed head"), indexTree: text(source.indexTree, "Reviewed index tree"),
        base: text(source.base, "Reviewed base"), tree: text(source.tree, "Reviewed tree"),
    };
    const files = checkedFiles(target.files);
    const summary = text(target.summary, "Implementation summary");
    const verification = jsonValue({
        ...object(target.verification, "CodeFeature verification"),
        unrun: input.item.tests.filter((test) => parseAllowedGradleVerificationCommand(test) === null),
    });
    const finalReview = mapReview(review);
    const roundsCompleted = Number(last!.round);
    if (!Number.isSafeInteger(roundsCompleted) || roundsCompleted < 1 || roundsCompleted > input.maxRounds) {
        throw new FactoryError("review_evidence_required", "CodeFeature returned an invalid review-round identity.", 409);
    }
    reviewHistory[reviewHistory.length - 1] = jsonValue({
        round: roundsCompleted, mode: "local", builderSummary: summary, files: files.map((file) => file.path),
        testsAddedOrUpdated: target.testsAddedOrUpdated, verification, sourceBinding, review: finalReview,
        codeFeatureReview: last!.review, engine: "code-feature",
    });
    return { engine: "code-feature", summary, files, roundsCompleted, sourceBinding, verification, reviewHistory, packet: jsonValue(packet) };
}
