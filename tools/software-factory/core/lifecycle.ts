// Copyright (c) Microsoft Corporation. All rights reserved.

import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { FactoryError, object, text, validateActor, type Actor } from "./contracts.ts";
import {
    deliverySettings, jsonValue, parseCodeReview, parseConfiguration, parsePlan, parseMonitor, sharedConfiguration, type Artifact, type Configuration, type DeliverySettings, type Execution, type ExecutionLimits,
    type GatePreview, type Json, type LocalPublicationBinding, type MonitorReport, type Operation, type Plan,
    type PlannedItem, type PullRequest, type SessionBinding, type Stage, type WorkflowAdmission, type WorkflowKind, type WorkItem,
} from "./lifecycle-contracts.ts";
import { hash, LifecycleStore, now } from "./lifecycle-store.ts";
import type { Registry } from "./registry.ts";
import { ExternalProvider } from "../providers/external.ts";
import type { LocalImplementationInput, Provider } from "../providers/provider.ts";
import { runtimeFingerprint } from "./evidence.ts";
import { parseProgress } from "./progress.ts";
import { currentPublicationBinding, repairHistory } from "./repairs.ts";
import { reconcileObserverEffect } from "./observer-effects.ts";

export class Lifecycle {
    readonly store: LifecycleStore;
    readonly provider: Provider;
    checkpoint?: (featureId: string) => Promise<void>;
    constructor(registry: Registry, provider: Provider = new ExternalProvider()) {
        this.store = new LifecycleStore(registry);
        this.provider = provider;
    }

    configuration(): Configuration {
        const config = this.store.configuration();
        if (!config) throw new FactoryError("configuration_required", "Configure repository/account mappings and ADO board settings first.", 409);
        return config.value;
    }

    reportProgress(featureId: string, expectedRevision: string, requestId: string, input: unknown, actor: Actor) {
        const progress = parseProgress(input);
        const trustedActor = validateActor(actor);
        return this.store.change(featureId, (state) => {
            const feature = this.store.registry.get(featureId);
            if (feature.current.id !== expectedRevision) throw new FactoryError("stale_revision", "The feature changed. Read the current scope before reporting progress.", 409);
            if (state.cancelled || state.completed?.revision === feature.current.id) throw new FactoryError("feature_terminal", "This feature is terminal; progress cannot restart it.", 409);
            const existing = state.progress.find((entry) => entry.requestId === requestId);
            if (existing) {
                const original = { stage: existing.stage, status: existing.status, summary: existing.summary,
                    expectedOutput: existing.expectedOutput, nextStep: existing.nextStep, ...(existing.question ? { question: existing.question } : {}) };
                if (existing.requirementsRevision !== expectedRevision || hash(original) !== hash(progress)) throw new FactoryError("request_conflict", "This progress request ID was already used with a different update.", 409);
                return existing;
            }
            const entry = { ...progress, id: randomUUID(), requestId: text(requestId, "Progress request ID"),
                requirementsRevision: expectedRevision, actor: trustedActor, createdAt: now(),
                auditSequence: feature.audit.at(-1)!.sequence, decisionId: state.decisions.at(-1)?.id || null };
            state.progress.push(entry);
            return entry;
        }, { localOnly: true });
    }

    configure(value: unknown, actor: Actor) {
        const previous = this.store.configuration();
        const config = this.store.configure(parseConfiguration(value), validateActor(actor));
        if (previous && hash(previous.value) === hash(config.value)) return config;
        for (const feature of this.store.registry.list()) {
            this.store.change(feature.id, (state) => {
                for (const run of state.executions) if (["starting", "running", "paused"].includes(run.status)) run.status = "superseded";
                if (state.schedule) { state.schedule = null; this.store.event(state, "schedule_invalidated_by_configuration", config.id); }
            });
        }
        return config;
    }

    plan(featureId: string): { artifact: Artifact; value: Plan } {
        const artifact = this.store.latest(featureId, "plan");
        if (!artifact) throw new FactoryError("plan_required", "Generate and review a plan first.", 409);
        return {
            artifact,
            value: parsePlan(artifact.content, this.configuration(), this.store.registry.get(featureId).current.requirements.map((item) => item.id)),
        };
    }

    private workItems(featureId: string, planId: string): WorkItem[] {
        return this.store.state(featureId).operations.filter((operation) =>
            operation.planId === planId && operation.kind === "work_item" && operation.status === "completed")
            .map((operation) => {
                const receipt = object(operation.receipt, "Work item receipt");
                if (!Number.isSafeInteger(receipt.adoId)) throw new FactoryError("storage_corrupt", "Work item receipt is invalid.", 500);
                return {
                    key: operation.itemId, adoId: Number(receipt.adoId), url: text(receipt.url, "Work item URL"),
                    title: text(receipt.title, "Work item title"), repository: text(receipt.repository, "Repository"),
                    status: text(receipt.status, "Work item status"),
                };
            });
    }

    async linkedPr(featureId: string, item: PlannedItem, workItem: WorkItem): Promise<PullRequest | null> {
        const config = this.configuration();
        const plan = this.plan(featureId);
        const binding = this.store.state(featureId).prBindings.filter((entry) => entry.planId === plan.artifact.id && entry.itemId === item.id).at(-1);
        return binding
            ? this.provider.inspectPr(config.repositories[item.repository]!, binding.number, item.id)
            : this.provider.findPr(config, item, workItem);
    }

    async attachPr(featureId: string, itemId: string, number: number, actor: Actor) {
        const { artifact, value } = this.plan(featureId);
        const item = value.items.find((entry) => entry.id === itemId);
        const workItem = this.workItems(featureId, artifact.id).find((entry) => entry.key === itemId);
        if (!item || !workItem || !Number.isSafeInteger(number) || number <= 0) throw new FactoryError("invalid_pr_binding", "Choose a known planned work item and positive PR number.");
        const repo = this.configuration().repositories[item.repository]!;
        const pr = await this.provider.inspectPr(repo, number, itemId);
        if (!pr.workItemIds.includes(workItem.adoId) || pr.baseBranch !== repo.baseBranch) throw new FactoryError("wrong_pr_link", "The PR does not link to this exact work item/base branch.", 409);
        this.store.change(featureId, (state) => {
            if (this.store.latest(featureId, "plan")?.id !== artifact.id) throw new FactoryError("stale_scope", "The plan changed before PR association.", 409);
            state.prBindings.push({ planId: artifact.id, itemId, number, actor });
            this.store.event(state, "canonical_pr_associated", `${itemId}:${number}`);
        });
        return pr;
    }

    waive(featureId: string, monitorId: string, itemId: string, criterion: string, reason: string, actor: Actor) {
        const artifact = this.store.latest(featureId, "monitor");
        const report = this.monitor(featureId);
        if (artifact?.id !== monitorId || !report?.validation.some((entry) => entry.itemId === itemId && entry.criterion === criterion && entry.status !== "addressed")) {
            throw new FactoryError("invalid_waiver", "A waiver must name a current, unresolved validation criterion.", 409);
        }
        return this.store.change(featureId, (state) => {
            const waiver = { id: randomUUID(), monitorId, itemId, criterion, reason: text(reason, "Waiver reason"), actor };
            state.waivers.push(waiver);
            this.store.event(state, "validation_gap_accepted", waiver.id);
            return waiver;
        });
    }

    feedbackScope(featureId: string, itemId: string, requestId: string, body: string) {
        const { artifact, value } = this.plan(featureId);
        const spec = value.items.find((entry) => entry.id === itemId);
        const report = this.monitor(featureId);
        const pr = report?.prs.find((entry) => entry.key === itemId);
        const config = this.store.configuration();
        const state = this.store.state(featureId);
        if (state.cancelled) throw new FactoryError("feature_cancelled", "Cancelled features cannot authorize new PR feedback.", 409);
        if (!spec || !pr || !config) throw new FactoryError("monitor_required", "Monitor this work item's canonical PR before composing feedback.", 409);
        const binding = state.prBindings.filter((entry) => entry.planId === artifact.id && entry.itemId === itemId).at(-1);
        if (binding && binding.number !== pr.number) throw new FactoryError("monitor_required", "The canonical PR changed; monitor it before composing feedback.", 409);
        const key = hash({ featureId, planId: artifact.id, itemId, requestId });
        const marker = `software-factory-feedback-${key}`;
        const postedBody = `${text(body, "Feedback body")}\n\nSoftware Factory operation: ${marker}`;
        const existing = state.operations.find((entry) => entry.key === key);
        if (existing?.manualScope && object(existing.manualScope, "Original feedback scope").body !== postedBody) throw new FactoryError("request_conflict", "This feedback request ID was already used with a different body.", 409);
        const scope = {
            key, marker, featureId, itemId, planId: artifact.id, configurationId: config.id,
            repository: sharedConfiguration(config.value).repositories[spec.repository]!, pr,
            body: postedBody,
            effect: "Post exactly this PR feedback. Mentions may activate a coding agent and consume separate usage. This does not approve or merge.",
        };
        if (existing?.manualScope && hash(existing.manualScope) !== hash(scope)) {
            throw new FactoryError("request_conflict", "This feedback request ID belongs to another PR/head or configuration. Reconcile its original operation or review a new request ID.", 409);
        }
        return scope;
    }

    private async findFeedbackReceipt(operation: Operation): Promise<Json | null> {
        const scope = object(operation.manualScope, "Original feedback scope");
        const target = object(scope.repository, "Original feedback repository");
        const config = this.store.configuration(operation.configurationId);
        const repo = Object.values(config?.value.repositories || {}).find((entry) =>
            (["provider", "hostname", "slug", "baseBranch"] as const).every((key) => entry[key] === target[key]));
        const pr = object(scope.pr, "Original feedback PR");
        if (!repo || !Number.isSafeInteger(pr.number) || Number(pr.number) <= 0 ||
            scope.marker !== `software-factory-feedback-${operation.key}`) {
            throw new FactoryError("storage_corrupt", "The original feedback target cannot be verified; inspect its preserved operation scope.", 500);
        }
        return this.provider.findFeedback(repo, Number(pr.number), scope.marker);
    }

    async postFeedback(featureId: string, itemId: string, requestId: string, body: string, actor: Actor, expectedScopeHash?: string): Promise<Json> {
        const scope = this.feedbackScope(featureId, itemId, requestId, body);
        const scopeHash = hash(scope);
        const localRoutingHash = hash(this.configuration());
        const assertScope = () => {
            if ((expectedScopeHash && expectedScopeHash !== scopeHash) ||
                hash(this.configuration()) !== localRoutingHash ||
                hash(this.feedbackScope(featureId, itemId, requestId, body)) !== scopeHash) {
                throw new FactoryError("stale_scope", "The feedback authority changed; review its exact current scope before posting.", 409);
            }
        };
        assertScope();
        const existing = this.store.state(featureId).operations.find((entry) => entry.key === scope.key);
        if (existing?.status === "completed") return existing.receipt!;
        if (existing) {
            const found = await this.findFeedbackReceipt(existing);
            if (!found) throw new FactoryError("reconciliation_required", "No unique posted feedback receipt was found; do not post again automatically.", 409);
            this.store.change(featureId, (state) => { const entry = state.operations.find((value) => value.key === scope.key)!; entry.receipt = found; entry.status = "completed"; entry.error = null; });
            return found;
        }
        const claimId = randomUUID();
        this.store.change(featureId, (state) => {
            assertScope();
            if (state.operations.some((entry) => entry.key === scope.key)) throw new FactoryError("operation_exists", "Another admission owns this feedback.", 409);
            state.operations.push({
                key: scope.key, kind: "feedback", itemId, planId: scope.planId, configurationId: scope.configurationId,
                claimId, approvalId: actor.reference, manualConsent: { scopeHash, actor },
                manualScope: jsonValue(scope),
                status: "inflight", receipt: null, error: null, updatedAt: now(),
            });
        });
        let sent = false;
        try {
            await this.checkpoint?.(featureId);
            const repository = Object.values(this.configuration().repositories).find((repo) =>
                repo.hostname === scope.repository.hostname && repo.slug === scope.repository.slug && repo.baseBranch === scope.repository.baseBranch);
            if (!repository) throw new FactoryError("configuration_changed", "The approved feedback repository is no longer configured.", 409);
            const prior = await this.provider.findFeedback(repository, scope.pr.number, scope.marker);
            let receipt = prior;
            if (!receipt) {
                const current = await this.provider.inspectPr(repository, scope.pr.number, itemId);
                assertScope();
                if (current.head !== scope.pr.head || current.status !== "open" || current.baseBranch !== scope.pr.baseBranch ||
                    !scope.pr.workItemIds.every((id) => current.workItemIds.includes(id))) {
                    throw new FactoryError("stale_pr", "The PR head/state/link changed. Monitor and review feedback again.", 409);
                }
                await this.checkpoint?.(featureId);
                assertScope();
                sent = true;
                receipt = await this.provider.postFeedback(repository, scope.pr.number, scope.body);
            }
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === scope.key)!;
                if (entry.status === "completed") return;
                if (entry.claimId !== claimId) throw new FactoryError("operation_owner_changed", "Another admission owns this feedback.", 409);
                entry.receipt = receipt; entry.status = "completed"; entry.error = null; entry.updatedAt = now();
                this.store.event(state, "feedback_completed", scope.key);
            });
            await this.checkpoint?.(featureId);
            return receipt;
        } catch (error) {
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === scope.key)!;
                if (entry.status === "completed" || entry.claimId !== claimId) return;
                entry.status = !sent || (error instanceof FactoryError && error.code === "provider_rejected") ? "failed" : "reconciliation_required";
                entry.error = entry.status === "failed" ? "No feedback was posted. Review a new request ID after correcting the cause."
                    : "Feedback outcome is uncertain; reconcile the original operation without posting again.";
                entry.updatedAt = now();
            });
            await this.checkpoint?.(featureId);
            throw error;
        }
    }

    async review(featureId: string, itemId: string): Promise<Json> {
        const { artifact, value } = this.plan(featureId);
        const spec = value.items.find((entry) => entry.id === itemId);
        const wi = this.workItems(featureId, artifact.id).find((entry) => entry.key === itemId);
        if (!spec || !wi) throw new FactoryError("work_item_required", "Choose an existing planned work item.", 409);
        const pr = await this.linkedPr(featureId, spec, wi);
        if (!pr || !pr.workItemIds.includes(wi.adoId)) throw new FactoryError("pr_pending", "No unique verified PR is available yet.", 409);
        return { pr: jsonValue(pr), feedback: await this.provider.review(this.configuration().repositories[spec.repository]!, pr.number) };
    }

    monitor(featureId: string): MonitorReport | null {
        const artifact = this.store.latest(featureId, "monitor");
        const plan = this.store.latest(featureId, "plan");
        if (!artifact || artifact.parentId !== plan?.id) return null;
        return parseMonitor(artifact.content);
    }

    gate(featureId: string, stage: Stage): GatePreview {
        const feature = this.store.registry.get(featureId);
        const state = this.store.state(featureId);
        const config = this.store.configuration();
        const blockers: string[] = [];
        let targetId = feature.current.id;
        let scope: Json;
        let context: Json | null = null;
        if (stage === "design") {
            const artifact = this.store.latest(featureId, "design");
            if (!artifact) throw new FactoryError("design_required", "Run the guarded design workflow first.", 409);
            targetId = artifact.id;
            const packet = object(artifact.content, "Design packet");
            if (packet.recommendation !== "GO to Planning") blockers.push("The independent design gate has not recommended GO to Planning.");
            scope = { requirementsRevision: feature.current.id, designId: artifact.id, packet: artifact.content };
        } else if (stage === "plan") {
            const plan = this.plan(featureId);
            targetId = plan.artifact.id;
            const parent = this.gate(featureId, "design");
            if (!parent.approved) blockers.push("The current design must remain approved.");
            const parentDecision = state.decisions.filter((entry) => entry.stage === "design" && entry.scopeHash === parent.scopeHash).at(-1);
            if (plan.value.blockingUnknowns.length) blockers.push(...plan.value.blockingUnknowns);
            scope = { requirementsRevision: feature.current.id, planId: targetId, designApprovalId: parentDecision?.id || null, plan: jsonValue(plan.value) };
        } else {
            const plan = this.plan(featureId);
            targetId = plan.artifact.id;
            if (!config) throw new FactoryError("configuration_required", "Configure external targets before approving operations.", 409);
            if (plan.value.blockingUnknowns.length) blockers.push(...plan.value.blockingUnknowns);
            const items = this.workItems(featureId, targetId);
            if (stage === "backlog") {
                const parent = this.gate(featureId, "plan");
                if (!parent.approved) blockers.push("Approve the exact plan before creating remote work.");
                const parentDecision = state.decisions.filter((entry) => entry.stage === "plan" && entry.scopeHash === parent.scopeHash).at(-1);
                for (const field of ["organization", "project", "workItemType"] as const) {
                    if (!config.value.boards[field]) blockers.push(`Configure the ADO boards ${field} before creating remote work.`);
                }
                for (const operation of state.operations.filter((entry) => entry.planId === targetId && entry.kind === "work_item")) {
                    const original = this.store.configuration(operation.configurationId);
                    if (original && (original.value.boards.organization !== config.value.boards.organization || original.value.boards.project !== config.value.boards.project)) {
                        blockers.push("Existing work items belong to different board targets. Create an explicitly reviewed successor plan instead of duplicating them.");
                    }
                }
                scope = { requirementsRevision: feature.current.id, planId: targetId, planApprovalId: parentDecision?.id || null, configurationId: config.id,
                    configuration: jsonValue(sharedConfiguration(config.value)), items: jsonValue(plan.value.items),
                    effect: "Create or reconcile these ADO work items and dependency/parent links. Existing items are preserved, not moved or recreated; new settings apply to new objects." };
                context = { existingWorkItems: jsonValue(items) };
            } else if (stage === "dispatch") {
                const parent = this.gate(featureId, "backlog");
                if (!parent.approved) blockers.push("The current backlog authorization must remain approved.");
                const parentDecision = state.decisions.filter((entry) => entry.stage === "backlog" && entry.scopeHash === parent.scopeHash).at(-1);
                const monitor = this.monitor(featureId);
                const dispatched = state.operations.filter((operation) => operation.planId === targetId && operation.kind === "dispatch" && operation.status === "completed");
                const eligible = plan.value.items.filter((item) => {
                    const workItem = items.find((value) => value.key === item.id);
                    return workItem && !dispatched.some((operation) => operation.itemId === item.id) &&
                        item.dependsOn.every((id) => monitor?.prs.some((pr) => pr.key === id && pr.status === "merged"));
                });
                if (items.length !== plan.value.items.length) blockers.push("Complete backlog creation and links before dispatch.");
                const links = state.operations.filter((operation) => operation.planId === targetId && operation.kind === "links" && operation.status === "completed");
                if (links.length !== plan.value.items.length) blockers.push("Work item links are not fully reconciled.");
                if (!eligible.length) blockers.push("No undispatched work item has satisfied all implementation dependencies.");
                const delivery = deliverySettings(config.value);
                scope = { requirementsRevision: feature.current.id, planId: targetId, backlogApprovalId: parentDecision?.id || null, configurationId: config.id,
                    configuration: jsonValue(sharedConfiguration(config.value)),
                    delivery: jsonValue(delivery),
                    ...(delivery.mode === "local" ? { observer: { engine: "pr-observer", maxRepairs: 2, ciRecovery: true, statusPublishing: true } } : {}),
                    eligible: jsonValue(eligible.map((item) => ({ ...item, workItem: items.find((value) => value.key === item.id),
                        dependencyContext: item.dependsOn.map((id) => {
                            const pr = monitor!.prs.find((value) => value.key === id)!;
                            return { itemId: id, url: pr.url, head: pr.head, title: pr.title };
                        }),
                    }))),
                    effect: delivery.mode === "local"
                        ? `Implement these dependency-ready work items locally with an independent reviewer loop (up to ${delivery.maxReviewRounds} rounds), push a branch, open a PR, and mark the work items Active. Then use the full pr-observer flow: up to 2 cumulative tactical repairs, independent review, incremental pushes, addressed-thread replies/resolution, bounded infrastructure CI retries, stale required-build requeues, and current-head PR status updates. Routine recovery needs no additional approval. Do not merge.`
                        : `Dispatch these remote coding tasks and mark the work items Active with agent-dispatched tags. A separately confirmed implement workflow can review their PRs (up to ${delivery.maxReviewRounds} rounds) and post feedback. Remote coding costs are separate from workflow subagents; do not merge.` };
            } else {
                const artifact = this.store.latest(featureId, "monitor");
                const monitor = this.monitor(featureId);
                if (!this.gate(featureId, "plan").approved) blockers.push("The current design and plan must remain approved.");
                targetId = artifact?.id || targetId;
                if (!monitor?.delivered) blockers.push("Every approved implementation must have a merged PR and completed work item, with passing checks.");
                if (monitor?.errors.length) blockers.push(...monitor.errors);
                if (monitor?.reviews?.some((review) => !review.pass || review.status !== "approved" || review.findings.length > 0 ||
                    !monitor.prs.some((pr) => pr.key === review.itemId && pr.head === review.head))) {
                    blockers.push("Independent code review has unresolved findings or stale evidence. Acceptance-criteria waivers cannot approve that review.");
                }
                const waivers = state.waivers.filter((entry) => entry.monitorId === targetId);
                if (monitor?.validation.some((entry) => entry.status !== "addressed" && !waivers.some((waiver) => waiver.itemId === entry.itemId && waiver.criterion === entry.criterion))) blockers.push("Acceptance criteria still need verification, revision, or an explicit reasoned human waiver.");
                if (monitor && monitor.validation.length !== plan.value.items.reduce((count, item) => count + item.acceptanceCriteria.length, 0)) blockers.push("The report does not cover every acceptance criterion.");
                scope = { requirementsRevision: feature.current.id, monitorId: targetId, report: monitor ? jsonValue(monitor) : null, acceptedValidationGaps: jsonValue(waivers) };
            }
        }
        const scopeHash = hash(scope);
        const decision = state.decisions.filter((item) => item.stage === stage && item.targetId === targetId && item.scopeHash === scopeHash).at(-1);
        return { stage, targetId, scopeHash, scope, ...(context ? { context } : {}), ready: blockers.length === 0, blockers, approved: decision?.approved === true };
    }

    decide(featureId: string, stage: Stage, expectedScope: string, approved: boolean, actor: Actor) {
        const preview = this.gate(featureId, stage);
        if (preview.scopeHash !== expectedScope) throw new FactoryError("stale_scope", "The approval scope changed. Review the current preview.", 409);
        if (approved && !preview.ready) throw new FactoryError("gate_blocked", preview.blockers.join("\n"), 409);
        return this.store.change(featureId, (state) => {
            const current = this.gate(featureId, stage);
            if (current.scopeHash !== expectedScope) throw new FactoryError("stale_scope", "Approval scope changed.", 409);
            const latest = state.decisions.filter((item) => item.stage === stage && item.targetId === preview.targetId && item.scopeHash === expectedScope).at(-1);
            if (latest?.approved === approved) return latest;
            const existing = state.decisions.find((item) => item.actor.reference === actor.reference && item.stage === stage && item.scopeHash === expectedScope);
            if (existing) return existing;
            const decision = {
                id: randomUUID(), stage, targetId: preview.targetId, scopeHash: expectedScope,
                scope: preview.scope, approved, actor: validateActor(actor), createdAt: now(),
            };
            state.decisions.push(decision);
            if (!approved) state.completed = null;
            this.store.event(state, approved ? `${stage}_approved` : `${stage}_rejected`, decision.id);
            if (stage === "complete" && approved) {
                state.completed = { revision: this.store.registry.get(featureId).current.id, monitorId: preview.targetId, decisionId: decision.id };
            }
            return decision;
        });
    }

    feedback(featureId: string, stage: "design" | "plan", expectedArtifactId: string, feedback: string) {
        const artifact = this.store.latest(featureId, stage);
        if (!artifact || artifact.id !== expectedArtifactId) throw new FactoryError("stale_artifact", "Review the current artifact before requesting a revision.", 409);
        this.store.change(featureId, (state) => {
            state.feedback.push({ id: randomUUID(), stage, revision: artifact.requirementsRevision, text: text(feedback, "Revision feedback"), createdAt: now() });
            state.completed = null;
            for (const execution of state.executions) {
                if (["starting", "running", "paused"].includes(execution.status)) execution.status = "superseded";
            }
            this.store.event(state, `${stage}_revision_requested`, expectedArtifactId);
        });
        return this.store.view(featureId);
    }

    begin(featureId: string, kind: WorkflowKind, actor: Actor, sourcePins: Record<string, string> = {}, limits: ExecutionLimits = {},
        admission?: WorkflowAdmission, preferredSession?: SessionBinding,
        initialPhaseOrDelivery: string | Partial<DeliverySettings> = "Starting native workflow",
        deliveryOverride?: Partial<DeliverySettings>): Execution {
        const initialPhase = typeof initialPhaseOrDelivery === "string" ? initialPhaseOrDelivery : "Starting native workflow";
        const effectiveDelivery = typeof initialPhaseOrDelivery === "string" ? deliveryOverride : initialPhaseOrDelivery;
        const feature = this.store.registry.get(featureId);
        if (kind !== "intake" && feature.status !== "ready_for_design") throw new FactoryError("requirements_approval_required", "Review and approve the success criteria before starting design or delivery.", 409);
        if (kind === "lifecycle") throw new FactoryError("workflow_retired", "The multi-stage lifecycle workflow is retired. Start plan, then explicitly create the approved backlog and dispatch approved work.", 409);
        if (kind === "plan" && !this.gate(featureId, "design").approved) throw new FactoryError("design_approval_required", "Explicit design approval is required before planning.", 409);
        if (kind === "monitor") this.plan(featureId);
        const resolvedDelivery = kind === "implement" || kind === "monitor"
            ? deliverySettings(this.store.configuration()?.value, effectiveDelivery)
            : undefined;
        if (kind === "implement") this.requireDispatchDecision(featureId, resolvedDelivery);
        return this.store.change(featureId, (state) => {
            if (this.store.registry.get(featureId).current.id !== feature.current.id ||
                (admission && (admission.requirementsRevision !== feature.current.id ||
                    admission.configurationId !== (this.store.configuration()?.id || null)))) {
                throw new FactoryError("stale_scope", "The feature brief or configuration changed before workflow admission. Review its current execution scope.", 409);
            }
            if (state.cancelled) throw new FactoryError("feature_cancelled", "This feature was cancelled; create a successor rather than silently restarting it.", 409);
            if (state.completed?.revision === feature.current.id) throw new FactoryError("feature_completed", "This scope is already delivered. Create a successor or explicitly revise requirements.", 409);
            const active = state.executions.find((item) =>
                (item.kind === kind || (kind !== "monitor" && item.kind !== "monitor")) &&
                ["starting", "running", "paused"].includes(item.status));
            if (active) throw new FactoryError("execution_exists", `Use the existing ${active.status} execution ${active.id}; do not duplicate it.`, 409);
            if (state.executions.some((item) => item.kind === kind && item.status === "error" && item.admissionClaim && !item.runtimeId)) {
                throw new FactoryError("native_admission_uncertain", "A previous submission has no confirmed native run ID. Inspect its owner and explicitly supersede/reconcile that admission before starting more paid work.", 409);
            }
            if (kind === "implement" && resolvedDelivery?.mode === "local") {
                const prov = this.store.registry.provenance(featureId);
                const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
                if (prov?.codingWorkspace && normalize(prov.codingWorkspace) !== normalize(this.store.registry.project.workspace) && existsSync(prov.codingWorkspace)) {
                    const hasUnpushedAttempt = state.artifacts.some((art) => {
                        if (art.kind !== "implementation_attempt" || art.requirementsRevision !== feature.current.id) return false;
                        const content = art.content && typeof art.content === "object" && !Array.isArray(art.content) ? art.content as Record<string, unknown> : null;
                        if (!content || content.receipt) return false;
                        const itemId = typeof content.itemId === "string" ? content.itemId : "";
                        if (!itemId) return false;
                        const alreadyPublished = state.prBindings.some((b) => b.itemId === itemId) ||
                            state.operations.some((op) => op.itemId === itemId && op.kind === "dispatch" && op.status === "completed");
                        return !alreadyPublished;
                    });
                    if (hasUnpushedAttempt) {
                        throw new FactoryError(
                            "cross_worktree_checkout_conflict",
                            `Unpushed local coding changes for this feature were bound in sibling worktree "${prov.codingWorktree || prov.codingWorkspace}" (${prov.codingWorkspace}). Push or reconcile from that worktree before starting local coding here.`,
                            409,
                        );
                    }
                }
            }
            const existingDesign = kind === "design" ? this.store.latest(featureId, "design") : null;
            if (existingDesign && object(existingDesign.content, "Design packet").recommendation === "GO to Planning") {
                throw new FactoryError("revision_request_required", "A current design already exists. Request a reviewed revision rather than silently replacing it.", 409);
            }
            const timestamp = now();
            const targetOwner = (preferredSession?.kind === "chat" ? preferredSession : null) || state.sessionBinding || preferredSession;
            if (admission?.sessionOwner && (admission.sessionOwner.sessionId !== (targetOwner?.sessionId || null) ||
                admission.sessionOwner.kind !== (targetOwner?.kind || "owned"))) {
                throw new FactoryError("session_owner_changed", "The feature's owning session changed while execution was being confirmed. Review its current owner.", 409);
            }
            const binding = targetOwner || { sessionId: randomUUID(), kind: "owned" as const, established: false };
            const execution: Execution = {
                id: randomUUID(), kind, requirementsRevision: feature.current.id, configurationId: this.store.configuration()?.id || null,
                sessionId: binding.sessionId, sessionKind: binding.kind, reuseSession: binding.established,
                runtimeId: null, parentSessionId: actor.sessionId || null,
                status: "starting", phase: initialPhase,
                generation: { design: this.store.generation(state, "design", feature.current.id), plan: this.store.generation(state, "plan", feature.current.id) },
                sourcePins, runtimeFingerprint: runtimeFingerprint(), requestedBy: validateActor(actor),
                ...(resolvedDelivery ? { delivery: resolvedDelivery } : {}),
                limits: { maxAiCredits: null, ...limits }, checkpoints: {}, outcome: null, error: null, createdAt: timestamp, updatedAt: timestamp,
            };
            state.sessionBinding = binding;
            state.executions.push(execution);
            if (kind === "implement" && resolvedDelivery?.mode === "local") {
                this.store.registry.recordCodingProvenance(featureId);
            }
            this.store.event(state, "workflow_requested", execution.id, { actor: execution.requestedBy, message: `${kind} workflow requested.` });
            return execution;
        });
    }

    assertFresh(featureId: string, executionId: string): Execution {
        const feature = this.store.registry.get(featureId);
        const state = this.store.state(featureId);
        const execution = this.store.execution(featureId, executionId);
        if (state.cancelled || execution.status === "cancelled" || execution.status === "superseded" ||
            execution.requirementsRevision !== feature.current.id ||
            execution.configurationId !== (this.store.configuration()?.id || null) ||
            execution.runtimeFingerprint !== runtimeFingerprint() ||
            execution.generation.design !== this.store.generation(state, "design", feature.current.id) ||
            execution.generation.plan !== this.store.generation(state, "plan", feature.current.id)) {
            throw new FactoryError("execution_stale", "Requirements, configuration, or revision requests changed. Reconcile and start a successor execution.", 409);
        }
        if (execution.kind === "lifecycle") throw new FactoryError("workflow_retired", "Do not resume a legacy multi-stage run. Review its recorded effects and start a separate stage.", 409);
        if (execution.kind === "plan" && !this.gate(featureId, "design").approved) {
            throw new FactoryError("upstream_approval_revoked", "The design approval was revoked; no further lifecycle work is authorized.", 409);
        }
        if (execution.kind === "implement") {
            if (!execution.delivery) throw new FactoryError("invalid_scope", "The execution has no frozen delivery settings.", 409);
            this.requireDispatchDecision(featureId, execution.delivery);
        }
        return execution;
    }

    private assertActiveExecution(featureId: string, executionId: string): Execution {
        const execution = this.assertFresh(featureId, executionId);
        if (execution.status !== "starting" && execution.status !== "running") {
            throw new FactoryError("execution_not_active", "The execution is not active; resume it before sending provider mutations.", 409);
        }
        return execution;
    }

    requireDispatchDecision(featureId: string, effectiveDelivery?: DeliverySettings) {
        if (!this.gate(featureId, "design").approved || !this.gate(featureId, "plan").approved) {
            throw new FactoryError("upstream_approval_revoked", "Upstream design or plan approval was revoked.", 409);
        }
        const backlogDecision = this.requireGate(featureId, "backlog").decision;
        const { artifact } = this.plan(featureId);
        const decisions = this.store.state(featureId).decisions.filter((entry) => entry.stage === "dispatch" && entry.targetId === artifact.id);
        const latest = decisions.at(-1);
        if (!latest || !latest.approved) {
            throw new FactoryError("approval_required", "Explicit approval of the current dispatch scope is required.", 409);
        }
        const scope = object(latest.scope, "Dispatch scope");
        if (scope.backlogApprovalId !== backlogDecision.id) {
            throw new FactoryError("approval_lineage_changed", "Dispatch authorization belongs to a different backlog approval.", 409);
        }
        if (scope.requirementsRevision !== this.store.registry.get(featureId).current.id ||
            scope.planId !== artifact.id || scope.configurationId !== this.store.configuration()?.id) {
            throw new FactoryError("approval_lineage_changed", "Dispatch authorization belongs to a different requirements revision, plan, or configuration.", 409);
        }
        const approvedDelivery = object(scope.delivery, "Approved delivery settings");
        if ((approvedDelivery.mode !== "local" && approvedDelivery.mode !== "remote") ||
            !Number.isSafeInteger(approvedDelivery.maxReviewRounds) ||
            Number(approvedDelivery.maxReviewRounds) < 1 || Number(approvedDelivery.maxReviewRounds) > 10) {
            throw new FactoryError("invalid_scope", "The approved dispatch is missing valid delivery settings.", 409);
        }
        if (effectiveDelivery && (effectiveDelivery.mode !== approvedDelivery.mode ||
            effectiveDelivery.maxReviewRounds !== approvedDelivery.maxReviewRounds)) {
            throw new FactoryError("dispatch_scope_changed", "Coding mode or review-round limit differs from the approved dispatch scope. Review and approve the changed configuration first.", 409);
        }
        return { decision: latest, scope };
    }

    stage0(featureId: string) {
        const feature = this.store.registry.get(featureId);
        const approval = feature.approvals.find((item) => item.revisionId === feature.current.id);
        if (!approval) throw new FactoryError("requirements_approval_required", "Requirements approval is missing.", 409);
        const directory = path.join(path.dirname(this.store.registry.databasePath), "approval-evidence");
        mkdirSync(directory, { recursive: true, mode: 0o700 });
        const reference = path.join(directory, `${approval.id}.json`);
        const content = JSON.stringify({ featureId, requirements: feature.current, approval }, null, 2);
        if (existsSync(reference)) {
            if (readFileSync(reference, "utf8") !== content) throw new FactoryError("approval_evidence_modified", "An immutable approval evidence file was modified.", 409);
        } else writeFileSync(reference, content, { flag: "wx", mode: 0o600 });
        return {
            title: feature.current.title, revision: feature.current.id, requirements: feature.current.requirements,
            blockingUnknowns: feature.current.blockingUnknowns,
            ...(feature.current.designTasks?.length ? { designTasks: feature.current.designTasks } : {}),
            approval: { approvedRevision: feature.current.id, approvedBy: approval.actor.channel, reference },
        };
    }

    requireGate(featureId: string, stage: Stage) {
        const gate = this.gate(featureId, stage);
        if (!gate.ready || !gate.approved) throw new FactoryError("approval_required", `Explicit approval of the current ${stage} scope is required.`, 409);
        const decision = this.store.state(featureId).decisions.filter((entry) =>
            entry.stage === stage && entry.targetId === gate.targetId && entry.scopeHash === gate.scopeHash && entry.approved).at(-1);
        if (!decision) throw new FactoryError("approval_required", "Approval receipt is missing.", 409);
        return { gate, decision };
    }

    private operationKey(featureId: string, kind: Operation["kind"], item: PlannedItem, planId: string, config: Configuration): string {
        const target = kind === "dispatch" ? sharedConfiguration(config).repositories[item.repository] : {
            organization: config.boards.organization, project: config.boards.project,
        };
        return hash({ featureId, planId, target, kind, itemId: item.id });
    }

    private async operation(featureId: string, kind: Operation["kind"], item: PlannedItem, stage: "backlog" | "dispatch",
        reconcile: (marker: string) => Promise<Json | null>, send: (marker: string) => Promise<Json>, idempotent = false,
        dispatchDecision?: { id: string; scopeHash: string }, dispatchMode?: "local" | "remote",
        publicationBinding?: LocalPublicationBinding, executionId?: string): Promise<Json> {
        const { artifact } = this.plan(featureId);
        const config = this.store.configuration();
        if (!config) throw new FactoryError("configuration_required", "Configure providers first.", 409);
        if (dispatchMode === "local" && !publicationBinding) {
            throw new FactoryError("publication_binding_required", "A reviewed commit and branch must be recorded before local publication.", 409);
        }
        const key = this.operationKey(featureId, kind, item, artifact.id, config.value);
        const marker = `software-factory-${key}`;
        const existing = this.store.state(featureId).operations.find((entry) => entry.key === key);
        if (existing && kind === "dispatch" && dispatchMode) {
            const previousMode = existing.dispatchMode ??
                (typeof existing.receipt === "object" && existing.receipt !== null && !Array.isArray(existing.receipt) && existing.receipt.mode === "local" ? "local" : undefined);
            if (!previousMode || previousMode !== dispatchMode) {
                throw new FactoryError("dispatch_mode_conflict", "This work item already has a dispatch with a different or unknown delivery mode; reconcile it before continuing.", 409);
            }
            if (dispatchMode === "local" && existing.status !== "failed" &&
                (!existing.publicationBinding || hash(existing.publicationBinding) !== hash(publicationBinding))) {
                throw new FactoryError("publication_binding_changed", "The original reviewed commit and branch must be reconciled without replacing them.", 409);
            }
        }
        if (existing?.status === "completed") return existing.receipt!;
        const decision = dispatchDecision || this.requireGate(featureId, stage).decision;
        if (dispatchDecision) {
            const grant = this.store.state(featureId).decisions.filter((entry) => entry.id === dispatchDecision.id && entry.approved && entry.stage === stage && entry.targetId === artifact.id).at(-1);
            if (!grant || grant.scopeHash !== dispatchDecision.scopeHash) throw new FactoryError("approval_required", "The frozen dispatch approval is unavailable.", 409);
        }
        if (existing) {
            const receipt = await reconcile(marker);
            if (receipt !== null) {
                this.store.change(featureId, (state) => {
                    const entry = state.operations.find((value) => value.key === key)!;
                    if (entry.claimId !== existing.claimId || entry.status !== existing.status) {
                        throw new FactoryError("operation_owner_changed", "The operation changed while its receipt was being reconciled.", 409);
                    }
                    entry.status = "completed"; entry.receipt = receipt; entry.error = null; entry.updatedAt = now();
                    this.store.event(state, `${kind}_reconciled`, key);
                });
                await this.checkpoint?.(featureId);
                return receipt;
            }
            if (!idempotent && existing.status !== "failed") throw new FactoryError("reconciliation_required", `Operation ${key} has an uncertain outcome. Do not redispatch; reconcile it explicitly.`, 409);
        }
        const claimId = randomUUID();
        let completedReceipt: Json | undefined;
        this.store.change(featureId, (state) => {
            const entry = state.operations.find((value) => value.key === key);
            if (entry && entry.status === "completed") {
                if (entry.receipt === null) throw new FactoryError("storage_corrupt", "The completed operation has no receipt.", 500);
                completedReceipt = entry.receipt;
                return;
            }
            if (entry && (entry.status === "inflight" || (!idempotent && entry.status !== "failed"))) throw new FactoryError("reconciliation_required", "Another admission owns this operation.", 409);
            if (entry) {
                entry.claimId = claimId; entry.status = "inflight"; entry.configurationId = config.id;
                entry.approvalId = decision.id; entry.error = null; entry.updatedAt = now();
                if (publicationBinding) entry.publicationBinding = publicationBinding;
            }
            if (!entry) state.operations.push({
                key, kind, itemId: item.id, planId: artifact.id, configurationId: config.id,
                ...(dispatchMode ? { dispatchMode } : {}),
                ...(publicationBinding ? { publicationBinding } : {}),
                claimId, status: "inflight", approvalId: decision.id, receipt: null, error: null, updatedAt: now(),
            });
        });
        if (completedReceipt !== undefined) return completedReceipt;
        let sent = false;
        try {
            await this.checkpoint?.(featureId);
            const prior = await reconcile(marker);
            let receipt = prior;
            if (receipt === null) {
                await this.checkpoint?.(featureId);
                if (executionId) this.assertActiveExecution(featureId, executionId);
                if (this.store.registry.get(featureId).current.id !== artifact.requirementsRevision ||
                    this.store.latest(featureId, "plan")?.id !== artifact.id || this.store.configuration()?.id !== config.id ||
                    hash(this.configuration()) !== hash(config.value) ||
                    this.store.state(featureId).cancelled) throw new FactoryError("stale_scope", "The operation scope changed before the provider send.", 409);
                const decisions = this.store.state(featureId).decisions.filter((entry) => entry.stage === stage && entry.targetId === artifact.id);
                if (decisions.at(-1)?.approved === false || !this.gate(featureId, "plan").approved) {
                    throw new FactoryError("approval_revoked", "An upstream or operation approval was revoked before send.", 409);
                }
                if (stage === "backlog" && this.gate(featureId, "backlog").scopeHash !== decision.scopeHash) {
                    throw new FactoryError("approval_lineage_changed", "Backlog approval lineage changed; review its current authority.", 409);
                }
                if (stage === "dispatch") {
                    const grant = this.store.state(featureId).decisions.find((entry) => entry.id === decision.id)!;
                    const parent = this.requireGate(featureId, "backlog").decision;
                    if (object(grant.scope, "Dispatch grant").backlogApprovalId !== parent.id) throw new FactoryError("approval_lineage_changed", "Dispatch authorization belongs to a different backlog approval.", 409);
                    if (kind === "dispatch") {
                        const approved = this.requireDispatchDecision(featureId);
                        if (approved.decision.id !== decision.id) {
                            throw new FactoryError("approval_lineage_changed", "The dispatch approval changed before the provider send.", 409);
                        }
                        if (object(approved.scope.delivery, "Approved delivery settings").mode !== dispatchMode) {
                            throw new FactoryError("dispatch_scope_changed", "The requested delivery mode differs from the approved dispatch scope.", 409);
                        }
                    }
                }
                sent = true; receipt = await send(marker);
            }
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (entry.status === "completed") return;
                if (entry.claimId !== claimId) throw new FactoryError("operation_owner_changed", "The provider operation is owned by another admission.", 409);
                entry.status = "completed"; entry.receipt = jsonValue(receipt); entry.error = null; entry.updatedAt = now();
                this.store.event(state, `${kind}_completed`, key);
            });
            await this.checkpoint?.(featureId);
            return receipt;
        } catch (error) {
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (entry.status === "completed" || entry.claimId !== claimId) return;
                entry.status = !sent || (dispatchMode !== "local" && error instanceof FactoryError &&
                    ["provider_rejected", "cli_unavailable"].includes(error.code)) ? "failed" : "reconciliation_required";
                entry.error = error instanceof FactoryError ? error.message : "The provider outcome is uncertain; reconcile before retrying."; entry.updatedAt = now();
            });
            await this.checkpoint?.(featureId);
            throw error;
        }
    }

    async backlog(featureId: string, executionId?: string): Promise<Json> {
        if (executionId) this.assertFresh(featureId, executionId);
        const { decision } = this.requireGate(featureId, "backlog");
        const { artifact, value: plan } = this.plan(featureId);
        const config = this.configuration();
        for (const item of plan.items) {
            if (executionId) this.assertFresh(featureId, executionId);
            await this.operation(featureId, "work_item", item, "backlog",
                async (marker) => {
                    const existing = await this.provider.findWorkItem(config, marker);
                    return existing ? jsonValue({ ...existing, key: item.id, repository: item.repository }) : null;
                },
                async (marker) => jsonValue(await this.provider.createWorkItem(config, item, marker)), false, decision, undefined, undefined, executionId);
        }
        const workItems = this.workItems(featureId, artifact.id);
        const sharedParentId = this.store.view(featureId).shared?.target.workItemId;
        const linkConfig = sharedParentId ? { ...config, boards: { ...config.boards, parentId: sharedParentId } } : config;
        for (const item of plan.items) {
            if (executionId) this.assertFresh(featureId, executionId);
            const workItem = workItems.find((entry) => entry.key === item.id)!;
            const dependencies = item.dependsOn.map((id) => {
                const dependency = workItems.find((entry) => entry.key === id);
                if (!dependency) throw new FactoryError("missing_dependency", `Missing work item for ${id}.`, 409);
                return dependency;
            });
            await this.operation(featureId, "links", item, "backlog", async () => null,
                () => this.provider.linkWorkItem(linkConfig, workItem, dependencies), true, decision, undefined, undefined, executionId);
        }
        return jsonValue({ planId: artifact.id, items: workItems });
    }

    async verifyItemDependencies(featureId: string, item: PlannedItem, approvedDependencyContext: unknown): Promise<Record<string, unknown>[]> {
        const { artifact, value: plan } = this.plan(featureId);
        const workItems = this.workItems(featureId, artifact.id);
        if (!Array.isArray(approvedDependencyContext)) throw new FactoryError("invalid_scope", "Dependency context was not included in the dispatch approval.");
        const verified: Record<string, unknown>[] = [];
        for (const dependency of item.dependsOn) {
            const spec = plan.items.find((entry) => entry.id === dependency)!;
            const wi = workItems.find((entry) => entry.key === dependency)!;
            const pr = await this.linkedPr(featureId, spec, wi);
            if (!pr || pr.status !== "merged") throw new FactoryError("dependency_not_delivered", `Dependency ${dependency} has not actually merged.`, 409);
            const approved = approvedDependencyContext.map((entry) => object(entry, "Dependency context")).find((entry) => entry.itemId === dependency);
            if (!approved || approved.head !== pr.head || approved.url !== pr.url) throw new FactoryError("dependency_changed", "Dependency evidence changed after dispatch approval; review a fresh scope.", 409);
            verified.push(approved);
        }
        return verified;
    }

    async dispatchItem(featureId: string, item: PlannedItem, approvedDependencyContext: unknown,
        decision: { id: string; scopeHash: string }, executionId?: string): Promise<Json> {
        if (executionId) this.assertFresh(featureId, executionId);
        const { artifact } = this.plan(featureId);
        const config = this.configuration();
        const workItem = this.workItems(featureId, artifact.id).find((entry) => entry.key === item.id);
        if (!workItem) throw new FactoryError("work_item_required", `Missing work item for ${item.id}.`, 409);
        this.assertApprovedDispatchItem(featureId, item, workItem, approvedDependencyContext, decision, "remote");
        await this.verifyItemDependencies(featureId, item, approvedDependencyContext);
        const enriched = { ...item, description: `${item.description}\n\nApproved merged dependency context:\n${JSON.stringify(approvedDependencyContext, null, 2)}\nBuild on these merged changes; do not duplicate them. No private source diff is transferred automatically.` };
        const receipt = await this.operation(featureId, "dispatch", item, "dispatch",
            (marker) => this.provider.findDispatch(config, item, workItem, marker),
            (marker) => this.provider.dispatch(config, enriched, workItem, marker), false, decision, "remote", undefined, executionId);
        await this.operation(featureId, "dispatch_status", item, "dispatch", async () => null,
            (marker) => this.provider.markDispatched(config, workItem, marker), true, decision, undefined, undefined, executionId);
        return receipt;
    }

    async publishLocalItem(featureId: string, item: PlannedItem, approvedDependencyContext: unknown,
        implementation: LocalImplementationInput, decision: { id: string; scopeHash: string }, executionId?: string): Promise<Json> {
        if (executionId) this.assertFresh(featureId, executionId);
        const { artifact } = this.plan(featureId);
        const config = this.configuration();
        const workItem = this.workItems(featureId, artifact.id).find((entry) => entry.key === item.id);
        if (!workItem) throw new FactoryError("work_item_required", `Missing work item for ${item.id}.`, 409);
        this.assertApprovedDispatchItem(featureId, item, workItem, approvedDependencyContext, decision, "local", implementation.maxRounds);
        this.assertReviewedLocalAttempt(featureId, item, implementation, executionId);
        if (!this.provider.prepareLocalPublication || !this.provider.publishLocal || !this.provider.findLocalPublication) {
            throw new FactoryError("local_publication_unavailable", "This provider cannot safely publish and reconcile local implementations; remote dispatch was not attempted.", 409);
        }
        await this.verifyItemDependencies(featureId, item, approvedDependencyContext);
        const enriched = { ...item, description: `${item.description}\n\nApproved merged dependency context:\n${JSON.stringify(approvedDependencyContext, null, 2)}\nBuild on these merged changes; do not duplicate them. No private source diff is transferred automatically.` };
        const key = this.operationKey(featureId, "dispatch", item, artifact.id, config);
        const existing = this.store.state(featureId).operations.find((entry) => entry.key === key);
        if (existing && existing.status !== "failed") {
            throw new FactoryError("reconciliation_required", "A prior local publication must be reconciled without preparing or sending another commit.", 409);
        }
        this.assertReviewedLocalAttempt(featureId, item, implementation, executionId);
        const marker = `software-factory-${key}`;
        const binding = await this.provider.prepareLocalPublication(config, enriched, workItem, marker, implementation);
        if (binding.branch !== implementation.branch || hash(binding.source) !== hash(implementation.sourceBinding)) {
            throw new FactoryError("publication_binding_changed", "The prepared commit does not match the reviewed branch and source tree.", 409);
        }
        const receipt = await this.operation(featureId, "dispatch", item, "dispatch",
            (operationMarker) => this.provider.findLocalPublication!(config, item, workItem, operationMarker, binding),
            (operationMarker) => this.provider.publishLocal!(config, enriched, workItem, operationMarker, implementation, binding), false, decision, "local", binding, executionId);
        await this.operation(featureId, "dispatch_status", item, "dispatch", async () => null,
            (marker) => this.provider.markDispatched(config, workItem, marker), true, decision, undefined, undefined, executionId);
        return receipt;
    }

    private assertReviewedLocalAttempt(featureId: string, item: PlannedItem, implementation: LocalImplementationInput, executionId?: string): void {
        if (!executionId) throw new FactoryError("review_evidence_required", "Local publication requires an implementation run with a saved independent review.", 409);
        const execution = this.assertActiveExecution(featureId, executionId);
        if (execution.kind !== "implement" || execution.delivery?.mode !== "local") {
            throw new FactoryError("review_evidence_required", "The publication does not belong to a local implementation run.", 409);
        }
        const plan = this.plan(featureId);
        const attempt = this.store.state(featureId).artifacts.filter((artifact) =>
            artifact.kind === "implementation_attempt" && artifact.parentId === plan.artifact.id &&
            artifact.requirementsRevision === execution.requirementsRevision && artifact.configurationId === execution.configurationId &&
            artifact.executionId === executionId).findLast((artifact) => object(artifact.content, "Implementation attempt").itemId === item.id);
        if (!attempt) throw new FactoryError("review_evidence_required", "The implementation run has no saved independent review.", 409);
        const saved = object(attempt.content, "Reviewed implementation attempt");
        if (saved.mode !== "local" || saved.recommendation !== "REVIEWED" ||
            saved.branch !== implementation.branch || saved.maxRounds !== implementation.maxRounds ||
            saved.roundsCompleted !== implementation.roundsCompleted ||
            !Number.isSafeInteger(implementation.roundsCompleted) || implementation.roundsCompleted < 1 ||
            implementation.roundsCompleted > implementation.maxRounds || !Array.isArray(saved.reviewHistory) ||
            !saved.reviewHistory.length || saved.sourceBinding === undefined || saved.verification === undefined ||
            hash(saved.sourceBinding) !== hash(implementation.sourceBinding) ||
            hash(saved.verification) !== hash(implementation.verification) ||
            hash(saved.reviewHistory) !== hash(implementation.reviewHistory)) {
            throw new FactoryError("review_evidence_required", "The publication does not match the saved reviewed attempt.", 409);
        }
        const last = object(saved.reviewHistory.at(-1), "Final review round");
        if (last.round !== implementation.roundsCompleted || last.mode !== "local" || last.builderSummary !== implementation.summary ||
            !Array.isArray(last.files) || hash(last.files) !== hash(implementation.files.map((file) => file.path)) ||
            last.sourceBinding === undefined || hash(last.sourceBinding) !== hash(implementation.sourceBinding) ||
            last.verification === undefined || hash(last.verification) !== hash(implementation.verification)) {
            throw new FactoryError("review_evidence_required", "The final review does not cover the exact publication tree and files.", 409);
        }
        const review = parseCodeReview(last.review, item.acceptanceCriteria);
        if (!review.pass || review.findings.length || review.criteria.some((criterion) => criterion.status !== "addressed")) {
            throw new FactoryError("review_evidence_required", "The saved independent review did not pass all acceptance criteria.", 409);
        }
    }

    private assertApprovedDispatchItem(featureId: string, item: PlannedItem, workItem: WorkItem, dependencyContext: unknown,
        decision: { id: string; scopeHash: string }, mode: "local" | "remote", maxRounds?: number): void {
        const approved = this.requireDispatchDecision(featureId);
        const delivery = object(approved.scope.delivery, "Approved delivery settings");
        if (delivery.mode !== mode || (maxRounds !== undefined && delivery.maxReviewRounds !== maxRounds)) {
            throw new FactoryError("dispatch_scope_changed", "The requested delivery settings differ from the approved dispatch scope.", 409);
        }
        if (approved.decision.id !== decision.id || approved.decision.scopeHash !== decision.scopeHash) {
            throw new FactoryError("approval_lineage_changed", "The requested dispatch does not use the current approval.", 409);
        }
        const eligible = approved.scope.eligible;
        const entry = Array.isArray(eligible)
            ? eligible.map((value) => object(value, "Approved dispatch item")).find((value) => value.id === item.id)
            : undefined;
        if (!entry || !Array.isArray(entry.dependencyContext) || !Array.isArray(dependencyContext)) {
            throw new FactoryError("dispatch_scope_changed", "The work item is not in the approved dispatch wave.", 409);
        }
        const { workItem: approvedWorkItem, dependencyContext: approvedDependencies, ...approvedItem } = entry;
        if (!approvedWorkItem || hash(approvedItem) !== hash(item) || hash(approvedWorkItem) !== hash(workItem) ||
            hash(approvedDependencies) !== hash(dependencyContext)) {
            throw new FactoryError("dispatch_scope_changed", "The work item or dependency evidence differs from the approved dispatch scope.", 409);
        }
    }

    async postReviewRoundFeedback(featureId: string, item: PlannedItem, pr: PullRequest, round: number,
        body: string, actor: Actor, executionId: string): Promise<Json> {
        const execution = this.assertActiveExecution(featureId, executionId);
        if (execution.kind !== "implement" || execution.delivery?.mode !== "remote" ||
            !Number.isSafeInteger(round) || round < 1 || round >= execution.delivery.maxReviewRounds) {
            throw new FactoryError("invalid_scope", "Automatic review feedback requires an active remote implementation round.", 409);
        }
        const { artifact } = this.plan(featureId);
        const config = this.store.configuration();
        if (!config) throw new FactoryError("configuration_required", "Configure providers first.", 409);
        const { decision, scope: dispatchScope } = this.requireDispatchDecision(featureId, execution.delivery);
        const approvedItem = Array.isArray(dispatchScope.eligible)
            ? dispatchScope.eligible.map((entry) => object(entry, "Approved dispatch item")).find((entry) => entry.id === item.id)
            : undefined;
        const workItem = this.workItems(featureId, artifact.id).find((entry) => entry.key === item.id);
        if (!approvedItem || !workItem) throw new FactoryError("dispatch_scope_changed", "The reviewed PR is not in the approved dispatch wave.", 409);
        const dependencyContext = approvedItem.dependencyContext;
        const repo = config.value.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Feedback repository is missing.", 409);
        const routingHash = hash(this.configuration());
        const assertAuthority = () => {
            const currentExecution = this.assertActiveExecution(featureId, executionId);
            if (currentExecution.kind !== "implement" || currentExecution.delivery?.mode !== "remote" ||
                hash(this.configuration()) !== routingHash) {
                throw new FactoryError("stale_scope", "The automatic feedback authority changed before posting.", 409);
            }
            this.assertApprovedDispatchItem(featureId, item, workItem, dependencyContext, decision, "remote", currentExecution.delivery.maxReviewRounds);
            const binding = this.store.state(featureId).prBindings.filter((entry) => entry.planId === artifact.id && entry.itemId === item.id).at(-1);
            if (binding && binding.number !== pr.number) throw new FactoryError("stale_pr", "The canonical PR changed before feedback was posted.", 409);
        };
        assertAuthority();
        if (pr.key !== item.id || pr.repository.toLowerCase() !== repo.slug.toLowerCase() || pr.status !== "open" ||
            pr.baseBranch !== repo.baseBranch || !pr.workItemIds.includes(workItem.adoId)) {
            throw new FactoryError("stale_pr", "The reviewed PR does not match the approved work item and repository.", 409);
        }
        const requestId = `auto-review-${artifact.id}-${item.id}-round-${round}-${pr.head}`;
        const key = hash({ featureId, planId: artifact.id, itemId: item.id, requestId });
        const marker = `software-factory-feedback-${key}`;
        const postedBody = `${text(body, "Feedback body")}\n\nSoftware Factory operation: ${marker}`;
        const scope = {
            key, marker, featureId, itemId: item.id, planId: artifact.id, configurationId: config.id,
            repository: sharedConfiguration(config.value).repositories[item.repository]!, pr,
            body: postedBody,
            effect: `Post independent reviewer findings (round ${round}) to PR #${pr.number}.`,
        };
        const scopeHash = hash(scope);
        const existing = this.store.state(featureId).operations.find((entry) => entry.key === key);
        if (existing && (!existing.manualScope || hash(existing.manualScope) !== scopeHash)) {
            throw new FactoryError("request_conflict", "This review round already has a different feedback target or body.", 409);
        }
        if (existing?.status === "completed") return existing.receipt!;
        if (existing && existing.status !== "failed") {
            const found = await this.findFeedbackReceipt(existing);
            if (!found) throw new FactoryError("reconciliation_required", "No unique original feedback receipt was found; do not post again automatically.", 409);
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (entry.claimId !== existing.claimId || entry.status !== existing.status) {
                    throw new FactoryError("operation_owner_changed", "The feedback changed while its receipt was being reconciled.", 409);
                }
                entry.receipt = found; entry.status = "completed"; entry.error = null; entry.updatedAt = now();
                this.store.event(state, "feedback_reconciled", key);
            });
            await this.checkpoint?.(featureId);
            return found;
        }
        const claimId = randomUUID();
        this.store.change(featureId, (state) => {
            assertAuthority();
            const entry = state.operations.find((value) => value.key === key);
            if (entry && entry.status !== "failed") throw new FactoryError("operation_exists", "Another admission owns this feedback.", 409);
            if (entry) { entry.claimId = claimId; entry.status = "inflight"; entry.error = null; entry.updatedAt = now(); }
            else state.operations.push({
                key, kind: "feedback", itemId: item.id, planId: artifact.id, configurationId: config.id,
                claimId, approvalId: decision.id, manualConsent: { scopeHash, actor: validateActor(actor) },
                manualScope: jsonValue(scope), status: "inflight", receipt: null, error: null, updatedAt: now(),
            });
        });
        let sent = false;
        try {
            await this.checkpoint?.(featureId);
            let receipt = await this.provider.findFeedback(repo, pr.number, marker);
            if (!receipt) {
                const current = await this.linkedPr(featureId, item, workItem);
                assertAuthority();
                if (!current || current.number !== pr.number || current.url !== pr.url ||
                    current.repository.toLowerCase() !== repo.slug.toLowerCase() || current.head !== pr.head ||
                    current.status !== "open" || current.baseBranch !== pr.baseBranch || !current.workItemIds.includes(workItem.adoId)) {
                    throw new FactoryError("stale_pr", "The canonical PR, head, state, or work-item link changed before review feedback was posted.", 409);
                }
                await this.checkpoint?.(featureId);
                assertAuthority();
                sent = true;
                receipt = await this.provider.postFeedback(repo, pr.number, postedBody);
            }
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (entry.status === "completed") return;
                if (entry.claimId !== claimId) throw new FactoryError("operation_owner_changed", "Another admission owns this feedback.", 409);
                entry.receipt = jsonValue(receipt); entry.status = "completed"; entry.error = null; entry.updatedAt = now();
                this.store.event(state, "feedback_completed", key);
            });
            await this.checkpoint?.(featureId);
            return receipt;
        } catch (error) {
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (entry.status === "completed" || entry.claimId !== claimId) return;
                entry.status = !sent || (error instanceof FactoryError && error.code === "provider_rejected") ? "failed" : "reconciliation_required";
                entry.error = entry.status === "failed" ? "No feedback was posted." : "Feedback outcome is uncertain; reconcile before reposting.";
                entry.updatedAt = now();
            });
            await this.checkpoint?.(featureId);
            throw error;
        }
    }

    async dispatch(featureId: string, executionId?: string): Promise<Json> {
        if (executionId) this.assertFresh(featureId, executionId);
        const { gate, decision } = this.requireGate(featureId, "dispatch");
        if (object(object(gate.scope, "Dispatch scope").delivery, "Approved delivery settings").mode !== "remote") {
            throw new FactoryError("dispatch_scope_changed", "A local delivery approval cannot authorize remote dispatch.", 409);
        }
        const { value: plan } = this.plan(featureId);
        const eligible = object(gate.scope, "Dispatch scope").eligible;
        if (!Array.isArray(eligible)) throw new FactoryError("invalid_scope", "Dispatch scope is missing its exact work set.");
        const receipts: Json[] = [];
        for (const raw of eligible) {
            if (executionId) this.assertFresh(featureId, executionId);
            const id = text(object(raw, "Dispatch item").id, "Dispatch work item ID");
            const item = plan.items.find((entry) => entry.id === id)!;
            const approvedDependencyContext = object(raw, "Dispatch item").dependencyContext;
            receipts.push(await this.dispatchItem(featureId, item, approvedDependencyContext, decision, executionId));
        }
        return receipts;
    }

    async verifyRecordedLocalPublication(featureId: string, item: PlannedItem, workItem: WorkItem, operation: Operation): Promise<Json> {
        if (operation.kind !== "dispatch" || operation.status !== "completed" ||
            operation.dispatchMode !== "local" || !operation.publicationBinding || !this.provider.findLocalPublication) {
            throw new FactoryError("reconciliation_required", "The completed dispatch has no verifiable local publication binding.", 409);
        }
        if (operation.itemId !== item.id || workItem.key !== item.id || operation.planId !== this.plan(featureId).artifact.id ||
            operation.configurationId !== this.store.configuration()?.id) {
            throw new FactoryError("stale_scope", "The publication no longer belongs to the current approved work item and configuration.", 409);
        }
        for (const pending of repairHistory(this.store.state(featureId), operation).filter((entry) => entry.repair!.phase === "prepared")) {
            const repair = pending.repair!;
            if (!repair.headSha) throw new FactoryError("reconciliation_required", "The uncertain repair has no prepared commit.", 409);
            const found = await this.provider.findLocalPublication(this.configuration(), item, workItem,
                `software-factory-${operation.key}`, { ...operation.publicationBinding, headSha: repair.headSha });
            if (!found) throw new FactoryError("reconciliation_required", "The repair push could not be confirmed; no repair was rebuilt or resent.", 409);
            this.store.change(featureId, (state) => {
                const current = state.operations.find((entry) => entry.key === pending.key)!;
                if (current.claimId !== pending.claimId || !current.repair || current.repair.headSha !== repair.headSha) {
                    throw new FactoryError("operation_owner_changed", "The pending repair changed during reconciliation.", 409);
                }
                current.repair.phase = "pushed"; current.receipt = found; current.error = null;
                current.updatedAt = now();
            });
            await this.checkpoint?.(featureId);
        }
        const binding = currentPublicationBinding(this.store.state(featureId), operation);
        const found = await this.provider.findLocalPublication(this.configuration(), item, workItem,
            `software-factory-${operation.key}`, binding);
        if (!found) throw new FactoryError("reconciliation_required", "The original publication was not found; no replacement was sent.", 409);
        const recorded = object(operation.receipt, "Recorded publication");
        const current = object(found, "Current publication");
        if (current.mode !== "local" || current.marker !== `software-factory-${operation.key}` ||
            current.branch !== binding.branch || current.headSha !== binding.headSha ||
            !Number.isSafeInteger(current.prNumber) || Number(current.prNumber) <= 0 ||
            typeof current.provider !== "string" || typeof current.url !== "string" ||
            !(["provider", "mode", "marker", "branch", "prNumber", "url"] as const)
                .every((key) => current[key] === recorded[key])) {
            throw new FactoryError("publication_identity_changed", "The current PR differs from the original publication receipt.", 409);
        }
        return found;
    }

    async repairDispatchStatus(featureId: string, executionId?: string): Promise<void> {
        if (executionId) this.assertFresh(featureId, executionId);
        const { artifact, value } = this.plan(featureId);
        const state = this.store.state(featureId);
        const config = this.configuration();
        for (const operation of state.operations.filter((entry) => entry.planId === artifact.id && entry.kind === "dispatch" && entry.status === "completed")) {
            if (state.operations.some((entry) => entry.planId === artifact.id && entry.itemId === operation.itemId && entry.kind === "dispatch_status" && entry.status === "completed")) continue;
            const item = value.items.find((entry) => entry.id === operation.itemId)!;
            const wi = this.workItems(featureId, artifact.id).find((entry) => entry.key === item.id)!;
            const decision = state.decisions.find((entry) => entry.id === operation.approvalId && entry.approved);
            if (!decision || operation.configurationId !== this.store.configuration()?.id) throw new FactoryError("stale_scope", "Review historical dispatch status against its original approved configuration.", 409);
            if (operation.dispatchMode === "local") await this.verifyRecordedLocalPublication(featureId, item, wi, operation);
            await this.operation(featureId, "dispatch_status", item, "dispatch", async () => null,
                (marker) => this.provider.markDispatched(config, wi, marker), true, decision, undefined, undefined, executionId);
        }
    }

    async observe(featureId: string): Promise<MonitorReport> {
        const { artifact, value: plan } = this.plan(featureId);
        const config = this.configuration();
        const report: MonitorReport = { observedAt: now(), items: [], prs: [], validation: [], errors: [], delivered: false };
        for (const workItem of this.workItems(featureId, artifact.id)) {
            const item = plan.items.find((entry) => entry.id === workItem.key)!;
            try {
                report.items.push(await this.provider.inspectWorkItem(config, workItem));
                const pr = await this.linkedPr(featureId, item, workItem);
                if (pr) {
                    if (!pr.workItemIds.includes(workItem.adoId) || pr.baseBranch !== config.repositories[item.repository]?.baseBranch) {
                        throw new FactoryError("wrong_pr_link", "The observed PR does not match the work item or configured base.", 409);
                    }
                    report.prs.push(pr);
                }
            } catch (error) {
                report.errors.push(`${item.id}: ${error instanceof FactoryError ? error.message : "Provider observation failed."}`);
            }
        }
        report.delivered = report.items.length === plan.items.length && report.prs.length === plan.items.length && !report.errors.length &&
            report.items.every((item) => ["done", "resolved", "closed"].includes(item.status.toLowerCase())) &&
            report.prs.every((pr) => pr.status === "merged" && pr.checks === "passing");
        return report;
    }

    async reconcile(featureId: string, key: string): Promise<Json> {
        const operation = this.store.state(featureId).operations.find((entry) => entry.key === key);
        if (!operation || operation.status === "completed") throw new FactoryError("operation_not_pending", "This operation does not require reconciliation.", 409);
        if (operation.kind === "observer") return reconcileObserverEffect(this, featureId, operation);
        if (operation.kind === "feedback") {
            const receipt = await this.findFeedbackReceipt(operation);
            if (!receipt) throw new FactoryError("reconciliation_required", "No unique original feedback receipt was found. Nothing was posted.", 409);
            this.store.change(featureId, (state) => {
                const entry = state.operations.find((value) => value.key === key)!;
                if (!entry || entry.claimId !== operation.claimId || entry.status !== operation.status) {
                    throw new FactoryError("operation_owner_changed", "The feedback changed while its receipt was being reconciled.", 409);
                }
                entry.status = "completed"; entry.receipt = receipt; entry.error = null; entry.updatedAt = now();
                this.store.event(state, "feedback_reconciled", key);
            });
            await this.checkpoint?.(featureId);
            return receipt;
        }
        const { artifact, value } = this.plan(featureId);
        if (operation.planId !== artifact.id || operation.configurationId !== this.store.configuration()?.id) throw new FactoryError("stale_scope", "Reconcile historical targets manually; current configuration no longer matches.", 409);
        const item = value.items.find((entry) => entry.id === operation.itemId)!;
        const config = this.configuration();
        const wi = this.workItems(featureId, artifact.id).find((entry) => entry.key === item.id);
        let receipt: Json | null;
        if (operation.kind === "work_item") {
            const found = await this.provider.findWorkItem(config, `software-factory-${key}`);
            receipt = found ? jsonValue({ ...found, key: item.id, repository: item.repository }) : null;
        } else if (operation.kind === "repair" && wi) {
            const publication = this.store.state(featureId).operations.find((entry) => entry.key === operation.repair?.publicationKey);
            if (!publication) throw new FactoryError("reconciliation_required", "The original publication is missing.", 409);
            await this.verifyRecordedLocalPublication(featureId, item, wi, publication);
            throw new FactoryError("repair_continuation_required", "The repair push is reconciled. Resume its owning implementation workflow to reconcile any remaining thread replies.", 409);
        } else if (operation.kind === "dispatch" && wi) {
            if (operation.dispatchMode === "local") {
                if (!this.provider.findLocalPublication || !operation.publicationBinding) throw new FactoryError("reconciliation_required", "The original reviewed commit and branch cannot be verified; inspect the publication manually.", 409);
                receipt = await this.provider.findLocalPublication(config, item, wi, `software-factory-${key}`, operation.publicationBinding);
            } else if (operation.dispatchMode === "remote") {
                receipt = await this.provider.findDispatch(config, item, wi, `software-factory-${key}`);
            } else {
                throw new FactoryError("reconciliation_required", "The historical dispatch mode is unknown; inspect the original target manually before any retry.", 409);
            }
        }
        else if (wi && operation.kind === "dispatch_status") receipt = await this.provider.markDispatched(config, wi, `software-factory-${key}`);
        else if (wi && operation.kind === "links") {
            const sharedParentId = this.store.view(featureId).shared?.target.workItemId;
            const linkConfig = sharedParentId ? { ...config, boards: { ...config.boards, parentId: sharedParentId } } : config;
            receipt = await this.provider.linkWorkItem(linkConfig, wi,
                item.dependsOn.map((id) => this.workItems(featureId, artifact.id).find((entry) => entry.key === id)!));
        }
        else throw new FactoryError("reconciliation_required", "This operation needs an explicit target-specific recovery.", 409);
        if (!receipt) throw new FactoryError("reconciliation_required", "No unique remote receipt was found. No new mutation was sent.", 409);
        this.store.change(featureId, (state) => {
            const entry = state.operations.find((value) => value.key === key)!;
            if (!entry || entry.claimId !== operation.claimId || entry.status !== operation.status) {
                throw new FactoryError("operation_owner_changed", "The operation changed while its receipt was being reconciled.", 409);
            }
            entry.status = "completed"; entry.receipt = receipt; entry.error = null; entry.updatedAt = now();
            this.store.event(state, "operation_reconciled", key);
        });
        await this.checkpoint?.(featureId);
        return receipt;
    }
}
