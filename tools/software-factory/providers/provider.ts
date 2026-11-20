// Copyright (c) Microsoft Corporation. All rights reserved.

import type { CodeImplementationFile, Configuration, Json, LocalPublicationBinding, ObserverEffectPlan, ObserverMutation, PlannedItem, PublicationSource, PullRequest, RepositoryTarget, WorkItem } from "../core/lifecycle-contracts.ts";

export interface LocalImplementationInput {
    branch: string;
    summary: string;
    files: CodeImplementationFile[];
    roundsCompleted: number;
    maxRounds: number;
    reviewHistory: Json[];
    sourceBinding: PublicationSource;
    verification: Json;
}

export interface RepairPublicationInput {
    localPath: string;
    expectedHead: string;
    source: PublicationSource;
    files: CodeImplementationFile[];
    summary: string;
    mergeParent?: string;
}
export type ObserverEffectRequest =
    | { kind: "retry"; stageOnly: boolean }
    | { kind: "refresh" }
    | { kind: "status"; block: string; ci: Json };

export interface Provider {
    findWorkItem(config: Configuration, marker: string): Promise<WorkItem | null>;
    createWorkItem(config: Configuration, item: PlannedItem, marker: string): Promise<WorkItem>;
    linkWorkItem(config: Configuration, item: WorkItem, dependencies: WorkItem[]): Promise<Json>;
    inspectWorkItem(config: Configuration, item: WorkItem): Promise<WorkItem>;
    dispatch(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string): Promise<Json>;
    verifyLocalReadiness?(repo: RepositoryTarget, expectedBase: string): Promise<void>;
    fetchRepairRefs?(repo: RepositoryTarget, sourceRoot: string, branches: string[]): Promise<void>;
    prepareLocalPublication?(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput): Promise<LocalPublicationBinding>;
    publishLocal?(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput, binding: LocalPublicationBinding): Promise<Json>;
    findLocalPublication?(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, binding: LocalPublicationBinding): Promise<Json | null>;
    prepareRepair?(repo: RepositoryTarget, input: RepairPublicationInput): Promise<string>;
    publishRepair?(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string,
        original: LocalPublicationBinding, input: RepairPublicationInput, headSha: string, beforeSend: () => Promise<void>): Promise<Json>;
    replyRepairThread?(repo: RepositoryTarget, number: number, thread: Json, body: string, marker: string,
        beforeSend: () => Promise<void>, retry: boolean): Promise<Json>;
    prepareObserverEffect?(repo: RepositoryTarget, number: number, snapshot: Json, request: ObserverEffectRequest): Promise<ObserverEffectPlan>;
    sendObserverMutation?(repo: RepositoryTarget, mutation: ObserverMutation, publisher: LocalPublicationBinding["target"],
        beforeSend: () => Promise<void>): Promise<Json>;
    findObserverMutation?(repo: RepositoryTarget, mutation: ObserverMutation): Promise<Json | null>;
    findDispatch(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string): Promise<Json | null>;
    inspectPr(repo: RepositoryTarget, number: number, itemId: string): Promise<PullRequest>;
    findPr(config: Configuration, item: PlannedItem, workItem: WorkItem): Promise<PullRequest | null>;
    diff(repo: RepositoryTarget, number: number): Promise<string>;
    preflight(config: Configuration): Promise<Json>;
    findFeedback(repo: RepositoryTarget, number: number, marker: string): Promise<Json | null>;
    postFeedback(repo: RepositoryTarget, number: number, body: string): Promise<Json>;
    markDispatched(config: Configuration, item: WorkItem, marker: string): Promise<Json>;
    review(repo: RepositoryTarget, number: number): Promise<Json>;
}
