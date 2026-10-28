// Copyright (c) Microsoft Corporation. All rights reserved.

import type { Configuration, Json, PlannedItem, PullRequest, RepositoryTarget, WorkItem } from "../core/lifecycle-contracts.ts";

export interface Provider {
    findWorkItem(config: Configuration, marker: string): Promise<WorkItem | null>;
    createWorkItem(config: Configuration, item: PlannedItem, marker: string): Promise<WorkItem>;
    linkWorkItem(config: Configuration, item: WorkItem, dependencies: WorkItem[]): Promise<Json>;
    inspectWorkItem(config: Configuration, item: WorkItem): Promise<WorkItem>;
    dispatch(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string): Promise<Json>;
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
