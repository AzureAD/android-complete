// Copyright (c) Microsoft Corporation. All rights reserved.

import { exactKeys, FactoryError, object, parseSuccessCriteria, text, type Actor, type Feature, type Requirement } from "./contracts.ts";
import type { ActivityEntry } from "./progress.ts";

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Stage = "design" | "plan" | "backlog" | "dispatch" | "complete";
export type WorkflowKind = "intake" | "design" | "lifecycle" | "monitor" | "check";
export interface RepositoryTarget {
    provider: "github" | "ado";
    hostname: string;
    slug: string;
    baseBranch: string;
    account: string;
    localPath: string;
}
export interface Configuration {
    repositories: Record<string, RepositoryTarget>;
    boards: {
        organization: string; project: string; workItemType: string; areaPath: string;
        iterationPath: string; assignee: string; parentId: number | null;
    };
}
export interface ConfigRevision { id: string; value: Configuration; hash: string; createdAt: string }
export interface PlannedItem {
    id: string; title: string; repository: string; description: string;
    requirementIds: string[]; acceptanceCriteria: string[]; dependsOn: string[];
    files: string[]; tests: string[];
}
export interface Plan { summary: string; items: PlannedItem[]; blockingUnknowns: string[]; reviewHistory?: Json[] }
export interface IntakeProposal { summary: string; requirements: Requirement[]; blockingUnknowns: string[]; designTasks?: string[] }
export interface Artifact {
    id: string; kind: "intake" | "design" | "plan" | "monitor" | "legacy" | "planning_attempt";
    requirementsRevision: string; parentId: string | null; generation: number;
    configurationId: string | null;
    analysisHash: string;
    executionId: string | null;
    hash: string; content: Json; createdAt: string;
}
export interface Decision {
    id: string; stage: Stage; targetId: string; scopeHash: string;
    scope: Json; approved: boolean; actor: Actor; createdAt: string;
}
export interface Feedback { id: string; stage: "design" | "plan"; revision: string; text: string; createdAt: string }
export interface WorkItem {
    key: string; adoId: number; url: string; title: string; repository: string; status: string;
}
export interface PullRequest {
    key: string; repository: string; number: number; url: string; status: "open" | "merged" | "closed";
    head: string; checks: "passing" | "pending" | "failing" | "unknown"; title: string;
    baseBranch: string; workItemIds: number[];
}
export interface Operation {
    key: string; kind: "work_item" | "links" | "dispatch" | "dispatch_status" | "feedback"; itemId: string;
    planId: string; configurationId: string;
    claimId: string;
    manualConsent?: { scopeHash: string; actor: Actor };
    manualScope?: Json;
    resolution?: { kind: "confirmed_not_sent"; reason: string; actor: Actor; createdAt: string };
    status: "inflight" | "completed" | "reconciliation_required" | "failed";
    approvalId: string; receipt: Json | null; error: string | null; updatedAt: string;
}
export type ExecutionLimits = Record<string, number | null>;
export interface SessionBinding { sessionId: string; kind: "chat" | "owned"; established: boolean }
export interface WorkflowAdmission {
    requirementsRevision: string; configurationId: string | null;
    sessionOwner?: { sessionId: string | null; kind: "chat" | "owned" };
}

export interface Execution {
    id: string; kind: WorkflowKind; requirementsRevision: string; configurationId: string | null;
    sessionId: string; runtimeId: string | null; parentSessionId: string | null;
    sessionKind?: "chat" | "owned";
    reuseSession?: boolean;
    admissionClaim?: string;
    status: "starting" | "running" | "paused" | "completed" | "cancelled" | "error" | "superseded";
    phase: string; generation: { design: number; plan: number }; sourcePins: Record<string, string>;
    runtimeFingerprint: string;
    requestedBy?: Actor;
    errorCode?: string | null;
    registration?: WorkflowRegistration;
    launchContext?: { hostExecutable: string; extensionNode: string; nodeVersion: string; nativeRuntime: string };
    limits: ExecutionLimits;
    usage?: {
        observedAt: number; activeMs: number; subagents: number; nanoAiu: number;
        declaredLimits: Record<string, number>; approvedLimits: Record<string, number> | null;
        agents: { label: string; status: string; requestedModel: string | null; resolvedModel: string | null; activeMs: number }[];
    };
    observationError?: string | null;
    checkpoints: Record<string, number>; outcome: Json | null; error: string | null;
    createdAt: string; updatedAt: string;
}
export interface WorkflowRegistration {
    sessionId: string; workflowNames: string[]; runtimeFingerprint: string;
    nodeExecutable?: string; nodeVersion?: string;
    readerAgent?: string;
}
export interface ProgressInput {
    stage: "intake" | "research" | "design" | "plan" | "delivery" | "monitor";
    status: "working" | "waiting_for_user" | "blocked" | "idle";
    summary: string; expectedOutput: string; nextStep: string;
    question?: { text: string; whyNeeded: string; recommendation: string };
}
export interface ChatProgress extends ProgressInput {
    id: string; requestId: string; requirementsRevision: string; actor: Actor; createdAt: string;
    auditSequence: number; decisionId: string | null;
}
export interface FactoryState {
    version: 2;
    artifacts: Artifact[]; decisions: Decision[]; feedback: Feedback[];
    operations: Operation[]; executions: Execution[];
    events: { id: string; kind: string; reference: string; createdAt: string; message?: string; actor?: Actor }[];
    progress: ChatProgress[];
    sessionBinding?: SessionBinding | null;
    completed: { revision: string; monitorId: string; decisionId: string } | null;
    cancelled: boolean;
    prBindings: { planId: string; itemId: string; number: number; actor: Actor }[];
    waivers: { id: string; monitorId: string; itemId: string; criterion: string; reason: string; actor: Actor }[];
    schedule: { minutes: number; nextAt: string; actor: Actor; scopeHash: string } | null;
    controls: { id: string; executionId: string; action: "pause" | "resume" | "cancel"; limits: ExecutionLimits;
        status: "pending" | "running" | "completed" | "error"; error: string | null; actor: Actor }[];
}
export interface FactoryFeature extends Feature {
    factory: FactoryState; lifecycleStatus: string;
    sessionOwnerOnline?: boolean | null;
    activity: ActivityEntry[];
    currentProgress: ChatProgress | null;
    currentArtifacts: { intake: string | null; design: string | null; plan: string | null; monitor: string | null };
}
export interface GatePreview {
    stage: Stage; targetId: string; scopeHash: string; scope: Json;
    context?: Json;
    ready: boolean; blockers: string[]; approved: boolean;
}
export interface MonitorReport {
    observedAt: string; items: WorkItem[]; prs: PullRequest[];
    validation: { itemId: string; criterion: string; status: "addressed" | "partial" | "missing" | "unknown"; evidence: string }[];
    errors: string[]; delivered: boolean;
}

export function strings(value: unknown, name: string, nonempty = false): string[] {
    if (!Array.isArray(value) || (nonempty && !value.length)) throw new FactoryError("invalid_input", `${name} must be ${nonempty ? "a nonempty" : "an"} array.`);
    return value.map((item) => text(item, name));
}

export function jsonValue(value: unknown): Json {
    if (value === null || typeof value === "boolean" || typeof value === "string") return value;
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (Array.isArray(value)) return value.map(jsonValue);
    if (typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, jsonValue(item)]));
    throw new FactoryError("invalid_json_value", "Artifacts must be finite JSON values.");
}

export function parseIntakeProposal(value: unknown, previous: Requirement[] = []): IntakeProposal {
    const proposal = object(value, "Intake proposal");
    exactKeys(proposal, ["summary", "requirements", "blockingUnknowns", "designTasks"], "Intake proposal");
    const requirements = parseSuccessCriteria(proposal.requirements, previous);
    if (!requirements.length || new Set(requirements.map((entry) => entry.text)).size !== requirements.length) {
        throw new FactoryError("invalid_intake_proposal", "Factory must propose at least one distinct, reviewable success criterion.", 502);
    }
    const designTasks = proposal.designTasks === undefined ? [] : strings(proposal.designTasks, "Design tasks");
    return { summary: text(proposal.summary, "Proposal explanation"), requirements, blockingUnknowns: strings(proposal.blockingUnknowns, "Open questions"),
        ...(designTasks.length ? { designTasks } : {}) };
}

export function parseConfiguration(value: unknown): Configuration {
    const config = object(value, "Configuration");
    exactKeys(config, ["repositories", "boards"], "Configuration");
    const repositories: Record<string, RepositoryTarget> = {};
    for (const [key, raw] of Object.entries(object(config.repositories, "Repositories"))) {
        if (!/^[a-zA-Z0-9_-]+$/.test(key) || ["__proto__", "constructor", "prototype"].includes(key)) throw new FactoryError("invalid_configuration", "Repository keys must use safe, non-reserved aliases.");
        const repo = object(raw, "Repository");
        exactKeys(repo, ["provider", "hostname", "slug", "baseBranch", "account", "localPath"], "Repository");
        if (repo.provider !== "github" && repo.provider !== "ado") throw new FactoryError("invalid_configuration", "Repository provider must be github or ado.");
        const hostname = text(repo.hostname, "Hostname").toLowerCase();
        if (!/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(hostname) || !hostname.includes(".")) {
            throw new FactoryError("invalid_configuration", "Use an explicit DNS hostname without a scheme, port, or credentials.");
        }
        const slug = text(repo.slug, "Repository slug");
        const segments = slug.split("/");
        if (segments.length !== (repo.provider === "github" ? 2 : 3) || segments.some((segment) => !/^[a-zA-Z0-9_. -]+$/.test(segment) || segment === "..")) {
            throw new FactoryError("invalid_configuration", "Use owner/repo for GitHub or organization/project/repo for ADO.");
        }
        if (repo.provider === "ado" && hostname !== "dev.azure.com") throw new FactoryError("invalid_configuration", "The ADO adapter currently requires dev.azure.com.");
        if (repo.provider === "ado" && repo.account !== "") throw new FactoryError("invalid_configuration", "ADO uses the explicitly documented current az identity; leave its GitHub account field empty.");
        const baseBranch = text(repo.baseBranch, "Base branch");
        if (/[\s~^:?*[\]\\]/.test(baseBranch) || baseBranch.includes("..") || baseBranch.startsWith("-")) {
            throw new FactoryError("invalid_configuration", "Invalid base branch.");
        }
        repositories[key] = {
            provider: repo.provider, hostname, slug, baseBranch,
            account: repo.provider === "github" ? text(repo.account, "GitHub account") : "",
            localPath: text(repo.localPath, "Repository local path"),
        };
    }
    if (!Object.keys(repositories).length) throw new FactoryError("invalid_configuration", "Configure at least one target repository.");
    const boards = object(config.boards, "Boards");
    exactKeys(boards, ["organization", "project", "workItemType", "areaPath", "iterationPath", "assignee", "parentId"], "Boards");
    const metadata = (value: unknown, name: string) => {
        if (typeof value !== "string") throw new FactoryError("invalid_configuration", `${name} must be a string; leave it empty until configured.`);
        return value.trim();
    };
    const organization = metadata(boards.organization, "Boards organization");
    if (organization && !/^[a-zA-Z0-9_-]+$/.test(organization)) throw new FactoryError("invalid_configuration", "Boards organization must be a name, not a URL.");
    if (boards.parentId !== null && (!Number.isSafeInteger(boards.parentId) || Number(boards.parentId) <= 0)) throw new FactoryError("invalid_configuration", "Parent ID must be a positive integer or null.");
    return {
        repositories,
        boards: {
            organization, project: metadata(boards.project, "Boards project"), workItemType: metadata(boards.workItemType, "Work item type"),
            areaPath: metadata(boards.areaPath, "Area path"), iterationPath: metadata(boards.iterationPath, "Iteration path"),
            assignee: typeof boards.assignee === "string" ? boards.assignee.trim() : text(boards.assignee, "Assignee"),
            parentId: boards.parentId === null ? null : Number(boards.parentId),
        },
    };
}

export function analysisConfiguration(config: Configuration | null): Json {
    return { repositories: Object.fromEntries(Object.entries(config?.repositories || {}).map(([key, repo]) => [key, {
        provider: repo.provider, hostname: repo.hostname, slug: repo.slug, baseBranch: repo.baseBranch, localPath: repo.localPath,
    }])) };
}

export function parsePlan(value: unknown, config: Configuration, requirementIds: string[]): Plan {
    const plan = object(value, "Plan");
    if (!Array.isArray(plan.items) || !plan.items.length) throw new FactoryError("invalid_plan", "A plan must contain work items.");
    const items: PlannedItem[] = plan.items.map((raw: unknown) => {
        const item = object(raw, "Planned work item");
        const repository = text(item.repository, "Repository key");
        if (!Object.hasOwn(config.repositories, repository)) throw new FactoryError("invalid_plan", `Unknown repository: ${repository}.`);
        const description = text(item.description, "Self-contained description");
        if (/design-docs[\\/]|file:\/\/|(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]/i.test(description)) throw new FactoryError("invalid_plan", "Cloud coding specifications cannot depend on local design files or drive paths.");
        const covered = strings(item.requirementIds, "Requirement IDs", true);
        if (covered.some((id) => !requirementIds.includes(id))) throw new FactoryError("invalid_plan", "A work item references an unknown requirement.");
        const criteria = strings(item.acceptanceCriteria, "Acceptance criteria", true);
        const files = strings(item.files, "Expected files");
        if (files.some((file) => /^(?:[\\/]|[A-Za-z]:[\\/])|(?:^|[\\/])\.\.(?:[\\/]|$)/.test(file))) throw new FactoryError("invalid_plan", "Expected files must stay within the target repository.");
        if (new Set(criteria).size !== criteria.length) throw new FactoryError("invalid_plan", "Acceptance criteria must be unique within a work item.");
        return {
            id: text(item.id, "Work item key"), title: text(item.title, "Work item title"), repository, description,
            requirementIds: covered, acceptanceCriteria: criteria,
            dependsOn: strings(item.dependsOn, "Dependencies"), files,
            tests: strings(item.tests, "Expected tests", true),
        };
    });
    const ids = new Set(items.map((item) => item.id));
    if (ids.size !== items.length || items.some((item) => item.dependsOn.some((id) => !ids.has(id) || id === item.id))) {
        throw new FactoryError("invalid_plan", "Work item keys must be unique and dependencies must name other known items.");
    }

    if (requirementIds.some((id) => !items.some((item) => item.requirementIds.includes(id)))) throw new FactoryError("invalid_plan", "Every approved requirement must be covered.");
    const remaining = new Set(ids);
    while (remaining.size) {
        const ready = items.filter((item) => remaining.has(item.id) && item.dependsOn.every((id) => !remaining.has(id)));
        if (!ready.length) throw new FactoryError("invalid_plan", "The work item dependency graph contains a cycle.");
        ready.forEach((item) => remaining.delete(item.id));
    }
    return {
        summary: text(plan.summary, "Plan summary"), items, blockingUnknowns: strings(plan.blockingUnknowns, "Blocking unknowns"),
        ...(Array.isArray(plan.reviewHistory) ? { reviewHistory: plan.reviewHistory.map(jsonValue) } : {}),
    };
}

export function parseMonitor(value: unknown): MonitorReport {
    const report = object(value, "Monitor report");
    if (!Array.isArray(report.items) || !Array.isArray(report.prs) || !Array.isArray(report.validation) || typeof report.delivered !== "boolean") {
        throw new FactoryError("invalid_monitor", "Monitor report has an invalid shape.");
    }
    const items = report.items.map((raw: unknown): WorkItem => {
        const item = object(raw, "Observed work item");
        if (!Number.isSafeInteger(item.adoId) || Number(item.adoId) <= 0) throw new FactoryError("invalid_monitor", "Observed work item ID is invalid.");
        return { key: text(item.key, "Item key"), adoId: Number(item.adoId), url: text(item.url, "Work item URL"),
            title: text(item.title, "Title"), repository: text(item.repository, "Repository"), status: text(item.status, "Status") };
    });
    const prs = report.prs.map((raw: unknown): PullRequest => {
        const pr = object(raw, "Observed PR");
        if (!Number.isSafeInteger(pr.number) || Number(pr.number) <= 0 || !["open", "merged", "closed"].includes(String(pr.status)) ||
            !["passing", "pending", "failing", "unknown"].includes(String(pr.checks)) ||
            !Array.isArray(pr.workItemIds) || !pr.workItemIds.every((id) => Number.isSafeInteger(id) && Number(id) > 0)) throw new FactoryError("invalid_monitor", "Observed PR state is invalid.");
        return {
            key: text(pr.key, "PR item key"), repository: text(pr.repository, "PR repository"), number: Number(pr.number),
            url: text(pr.url, "PR URL"), status: pr.status as PullRequest["status"], head: text(pr.head, "PR head"),
            checks: pr.checks as PullRequest["checks"], title: text(pr.title, "PR title"), baseBranch: text(pr.baseBranch, "PR base"),
            workItemIds: pr.workItemIds.map(Number),
        };
    });
    const validation = report.validation.map((raw: unknown): MonitorReport["validation"][number] => {
        const criterion = object(raw, "Criterion result");
        if (!["addressed", "partial", "missing", "unknown"].includes(String(criterion.status))) throw new FactoryError("invalid_monitor", "Criterion result is invalid.");
        return { itemId: text(criterion.itemId, "Criterion item"), criterion: text(criterion.criterion, "Criterion"),
            status: criterion.status as MonitorReport["validation"][number]["status"], evidence: text(criterion.evidence, "Criterion evidence") };
    });
    const observedAt = text(report.observedAt, "Observation timestamp");
    if (!Number.isFinite(Date.parse(observedAt))) throw new FactoryError("invalid_monitor", "Observation timestamp is invalid.");
    return { observedAt, items, prs, validation, errors: strings(report.errors, "Observation errors"), delivered: report.delivered };
}
