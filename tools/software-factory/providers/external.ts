// Copyright (c) Microsoft Corporation. All rights reserved.

import { mkdirSync, mkdtempSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FactoryError, object, text } from "../core/contracts.ts";
import { jsonValue, type Configuration, type Json, type LocalPublicationBinding, type ObserverEffectPlan, type ObserverMutation, type PlannedItem, type PublicationSource, type PullRequest, type RepositoryTarget, type WorkItem } from "../core/lifecycle-contracts.ts";
import type { LocalImplementationInput, ObserverEffectRequest, Provider, RepairPublicationInput } from "./provider.ts";
import { ensureSubrepoWorktree } from "../core/runtime-health.ts";
import { systemRunner, type CommandRunner } from "./cli.ts";
import { verifyPublicationSource } from "../../../.github/extensions/CodeFeature/workflow.mjs";
import { renderObserverDescription, retryFailedAdoBuildStages, staleAdoBuildPolicies } from "../../../.github/extensions/CodeFeature/observer.mjs";
import { hash } from "../core/lifecycle-store.ts";

const defaultWorkspaceRoot = () => fileURLToPath(new URL("../../../", import.meta.url));

const escapeHtml = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const segment = (value: string) => encodeURIComponent(value);
const wiql = (value: string) => value.replaceAll("'", "''");
const gitObjectId = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;

function dispatchedWorkItemState(fields: Record<string, unknown>, config: Configuration): string {
    const current = typeof fields["System.State"] === "string" ? fields["System.State"].trim() : "";
    if (["Committed", "Active", "In Progress", "Resolved", "Closed", "Done", "Removed"].includes(current)) {
        return current;
    }
    const workItemType = (typeof fields["System.WorkItemType"] === "string" && fields["System.WorkItemType"].trim()
        ? fields["System.WorkItemType"]
        : config.boards.workItemType).trim().toLowerCase();
    if (workItemType === "product backlog item" || workItemType === "bug") {
        return "Committed";
    }
    if (workItemType === "task") {
        return "In Progress";
    }
    return "Active";
}

function assertPublicationBinding(binding: LocalPublicationBinding): void {
    const target = object(binding.target, "Publication target");
    if ((target.provider !== "github" && target.provider !== "ado") ||
        typeof target.hostname !== "string" || !target.hostname ||
        typeof target.slug !== "string" || !target.slug ||
        typeof target.baseBranch !== "string" || !target.baseBranch ||
        typeof target.publisherId !== "string" || !target.publisherId ||
        typeof target.publisherAccount !== "string" ||
        (target.provider === "github" && !target.publisherAccount)) {
        throw new FactoryError("invalid_publication_binding", "The saved publication target or publisher is invalid.", 409);
    }
    const branch = binding.branch;
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._/-]*$/.test(branch) || branch.includes("..") || branch.includes("//") ||
        branch.endsWith("/") || branch.endsWith(".") || branch.split("/").some((part) => part.startsWith(".") || part.endsWith(".lock"))) {
        throw new FactoryError("invalid_publication_binding", "The saved publication branch is invalid.", 409);
    }
    const source = binding.source;
    if (!source || ![binding.headSha, source.head, source.indexTree, source.base, source.tree].every((value) => gitObjectId.test(value)) ||
        [source.head, source.indexTree, source.base, source.tree].some((value) => value.length !== binding.headSha.length) || source.head !== source.base) {
        throw new FactoryError("invalid_publication_binding", "The saved publication commit or source tree is invalid.", 409);
    }
}

function assertPublicationTarget(repo: RepositoryTarget, binding: LocalPublicationBinding, writing = false): void {
    const target = binding.target;
    if (target.provider !== repo.provider || target.hostname.toLowerCase() !== repo.hostname.toLowerCase() ||
        target.slug.toLowerCase() !== repo.slug.toLowerCase() || target.baseBranch !== repo.baseBranch ||
        (writing && repo.provider === "github" && target.publisherAccount.toLowerCase() !== repo.account.toLowerCase())) {
        throw new FactoryError("publication_identity_changed", "The publication target or publisher differs from the original binding.", 409);
    }
}

function sameOrigin(raw: string, repo: RepositoryTarget): boolean {
    let hostname: string;
    let segments: string[];
    try {
        if (raw.startsWith("https://")) {
            const url = new URL(raw);
            if (url.protocol !== "https:" || url.port || url.password || url.search || url.hash) return false;
            hostname = url.hostname.toLowerCase();
            segments = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
        } else {
            const match = /^(?:ssh:\/\/)?git@([^/:]+)(?::|\/)(.+)$/.exec(raw);
            if (!match) return false;
            hostname = match[1]!.toLowerCase();
            segments = match[2]!.split("/").filter(Boolean).map(decodeURIComponent);
        }
    } catch { return false; }
    if (segments.length) segments[segments.length - 1] = segments.at(-1)!.replace(/\.git$/i, "");
    if (repo.provider === "github") return hostname === repo.hostname && segments.join("/").toLowerCase() === repo.slug.toLowerCase();
    const expected = repo.slug.split("/");
    if (hostname === "dev.azure.com" && segments.length === 4 && segments[2] === "_git") segments.splice(2, 1);
    else if (hostname === "ssh.dev.azure.com" && segments.length === 4 && segments[0] === "v3") segments.shift();
    else if (hostname === "vs-ssh.visualstudio.com" && segments.length === 4 && segments[0] === "v3") segments.shift();
    else if (hostname.endsWith(".visualstudio.com") && hostname.split(".").length === 3) {
        const org = hostname.split(".")[0]!;
        if (segments.length === 4 && segments[0]!.toLowerCase() === "defaultcollection" && segments[2] === "_git") {
            segments = [org, segments[1]!, segments[3]!];
        } else if (segments.length === 3 && segments[1] === "_git") {
            segments = [org, segments[0]!, segments[2]!];
        } else return false;
    } else return false;
    return segments.join("/").toLowerCase() === expected.join("/").toLowerCase();
}

function hasOperationMarker(body: unknown, marker: string): boolean {
    return typeof body === "string" && body.split(/\r?\n/).some((line) => line.trim() === `Software Factory operation: ${marker}`);
}

function samePublisherId(provider: RepositoryTarget["provider"], actual: string, expected: string): boolean {
    return provider === "ado" ? actual.toLowerCase() === expected.toLowerCase() : actual === expected;
}

const GITHUB_AUTH_ENV_KEYS = new Set([
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GH_HOST",
]);

export function stripGitHubAuthEnv(baseEnv: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
    const env: NodeJS.ProcessEnv = {};
    for (const [key, value] of Object.entries(baseEnv)) {
        if (value !== undefined && !GITHUB_AUTH_ENV_KEYS.has(key.toUpperCase())) {
            env[key] = value;
        }
    }
    return env;
}

export function githubCliEnv(hostname: string, token: string, baseEnv: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
    const isGhe = hostname.toLowerCase() !== "github.com";
    const cleaned = stripGitHubAuthEnv(baseEnv);
    return {
        ...cleaned,
        GH_HOST: hostname,
        GH_TOKEN: token,
        GITHUB_TOKEN: token,
        ...(isGhe ? { GH_ENTERPRISE_TOKEN: token, GITHUB_ENTERPRISE_TOKEN: token } : {}),
    };
}

export class ExternalProvider implements Provider {
    private readonly runner: CommandRunner;
    private readonly fetcher: typeof fetch;
    private readonly githubTokens = new Map<string, { value: string; expiresAt: number }>();
    private adoTokenCache?: { value: string; expiresAt: number };
    constructor(runner = systemRunner, fetcher = fetch) { this.runner = runner; this.fetcher = fetcher; }

    private async githubToken(repo: RepositoryTarget): Promise<string> {
        const key = `${repo.hostname}\0${repo.account}`;
        const cached = this.githubTokens.get(key);
        if (cached && Date.now() < cached.expiresAt) return cached.value;
        const value = await this.runner.run(
            "gh",
            ["auth", "token", "--hostname", repo.hostname, "--user", repo.account],
            { env: stripGitHubAuthEnv() },
        );
        this.githubTokens.set(key, { value, expiresAt: Date.now() + 4 * 60_000 });
        return value;
    }

    private async adoToken(): Promise<string> {
        if (this.adoTokenCache && Date.now() < this.adoTokenCache.expiresAt) return this.adoTokenCache.value;
        const value = await this.runner.run("az", ["account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798", "--query", "accessToken", "-o", "tsv"]);
        this.adoTokenCache = { value, expiresAt: Date.now() + 4 * 60_000 };
        return value;
    }

    private async adoIdentity(organization: string): Promise<string> {
        const token = await this.adoToken();
        const response = await this.fetcher(`https://dev.azure.com/${segment(organization)}/_apis/connectionData?api-version=7.1-preview.1`, {
            redirect: "error", signal: AbortSignal.timeout(60_000), headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) {
            this.adoTokenCache = undefined;
            throw new FactoryError("ado_identity_unavailable", "Cannot verify the signed-in ADO identity; no publication was sent.", 403);
        }
        const connection = object(await response.json(), "ADO connection");
        const authenticated = text(object(connection.authenticatedUser, "Authenticated ADO user").id, "Authenticated ADO user ID");
        const authorized = text(object(connection.authorizedUser, "Authorized ADO user").id, "Authorized ADO user ID");
        if (authenticated.toLowerCase() !== authorized.toLowerCase()) {
            throw new FactoryError("ado_impersonation_unsupported", "The authenticated and authorized ADO identities differ; local publication cannot safely bind its creator.", 409);
        }
        return authorized;
    }

    private async github(repo: RepositoryTarget, endpoint: string, method = "GET", data?: unknown): Promise<unknown> {
        const token = await this.githubToken(repo);
        let result: string;
        try {
            result = await this.runner.run("gh", [
                "api", "--hostname", repo.hostname, "--method", method, endpoint,
                ...(data === undefined ? [] : ["--input", "-"]),
            ], {
                env: githubCliEnv(repo.hostname, token),
                input: data === undefined ? undefined : JSON.stringify(data),
            });
        } catch (error) {
            this.githubTokens.delete(`${repo.hostname}\0${repo.account}`);
            throw error;
        }
        if (!result) return null;
        try { return JSON.parse(result); }
        catch { throw new FactoryError("invalid_provider_response", "GitHub returned invalid JSON.", 502); }
    }

    private async ado(organization: string, project: string, suffix: string, method = "GET", body?: unknown, patch = false, apiVersion = "7.1"): Promise<unknown> {
        const tokenText = await this.adoToken();
        const url = `https://dev.azure.com/${segment(organization)}/${segment(project)}/_apis/${suffix}${suffix.includes("?") ? "&" : "?"}api-version=${apiVersion}`;
        const response = await this.fetcher(url, {
            method, redirect: "error",
            signal: AbortSignal.timeout(60_000),
            headers: { Authorization: `Bearer ${tokenText}`, "Content-Type": patch ? "application/json-patch+json" : "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (!response.ok) {
            this.adoTokenCache = undefined;
            throw new FactoryError(response.status < 500 ? "provider_rejected" : "ado_request_failed", `ADO returned HTTP ${response.status}. Resolve authentication, permission, or concurrency failures before retrying.`, 502);
        }
        if (response.status === 204) return null;
        return response.json();
    }

    private workItem(raw: unknown, key: string, repository: string, config: Configuration): WorkItem {
        const value = object(raw, "ADO work item");
        const fields = object(value.fields, "Work item fields");
        if (!Number.isSafeInteger(value.id) || Number(value.id) <= 0) throw new FactoryError("invalid_provider_response", "ADO returned no valid work item ID.", 502);
        return {
            key, adoId: Number(value.id), title: text(fields["System.Title"], "Work item title"), repository,
            status: text(fields["System.State"], "Work item status"),
            url: `https://dev.azure.com/${segment(config.boards.organization)}/${segment(config.boards.project)}/_workitems/edit/${value.id}`,
        };
    }

    async findWorkItem(config: Configuration, marker: string): Promise<WorkItem | null> {
        const result = object(await this.ado(config.boards.organization, config.boards.project, "wit/wiql", "POST", {
            query: `SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '${wiql(config.boards.project)}' AND [System.Tags] CONTAINS '${wiql(marker)}'`,
        }), "ADO query");
        if (!Array.isArray(result.workItems)) throw new FactoryError("invalid_provider_response", "ADO omitted work item query results.", 502);
        if (result.workItems.length > 1) throw new FactoryError("reconciliation_required", "Multiple work items match this operation. Reconcile them explicitly.", 409);
        if (!result.workItems.length) return null;
        const id = object(result.workItems[0], "Work item reference").id;
        return this.workItem(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${Number(id)}`), "", "", config);
    }

    async createWorkItem(config: Configuration, item: PlannedItem, marker: string): Promise<WorkItem> {
        const fields: Record<string, string> = {
            "System.Title": item.title,
            "System.Description": `<pre>${escapeHtml(item.description)}\n\n${escapeHtml(JSON.stringify({ requirementIds: item.requirementIds, acceptanceCriteria: item.acceptanceCriteria, files: item.files, tests: item.tests }, null, 2))}</pre>`,
            "Microsoft.VSTS.Common.AcceptanceCriteria": `<ul>${item.acceptanceCriteria.map((criterion) => `<li>${escapeHtml(criterion)}</li>`).join("")}</ul>`,
            "System.Tags": `ai-generated; copilot-agent-ready; ${marker}`,
        };
        if (config.boards.areaPath.trim()) fields["System.AreaPath"] = config.boards.areaPath.trim();
        if (config.boards.iterationPath.trim()) fields["System.IterationPath"] = config.boards.iterationPath.trim();
        if (config.boards.assignee) fields["System.AssignedTo"] = config.boards.assignee;
        const value = await this.ado(config.boards.organization, config.boards.project,
            `wit/workitems/$${segment(config.boards.workItemType)}`, "POST",
            Object.entries(fields).map(([field, value]) => ({ op: "add", path: `/fields/${field}`, value })), true);
        return this.workItem(value, item.id, item.repository, config);
    }

    async linkWorkItem(config: Configuration, item: WorkItem, dependencies: WorkItem[]): Promise<Json> {
        const current = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}?$expand=relations`), "ADO work item");
        const fields = object(current.fields, "ADO fields");
        if (!Number.isSafeInteger(current.rev)) throw new FactoryError("invalid_provider_response", "ADO omitted the work item revision.", 502);
        const existing = Array.isArray(current.relations) ? current.relations.map((value) => object(value, "Relation")) : [];
        const relations = dependencies.map((dependency) => ({
            rel: "System.LinkTypes.Dependency-Reverse",
            url: `https://dev.azure.com/${segment(config.boards.organization)}/_apis/wit/workItems/${dependency.adoId}`,
        }));
        if (config.boards.parentId) relations.push({
            rel: "System.LinkTypes.Hierarchy-Reverse",
            url: `https://dev.azure.com/${segment(config.boards.organization)}/_apis/wit/workItems/${config.boards.parentId}`,
        });
        const updates: unknown[] = [{ op: "test", path: "/rev", value: current.rev }];
        for (const relation of relations) {
            if (!existing.some((entry) => entry.rel === relation.rel && entry.url === relation.url)) updates.push({ op: "add", path: "/relations/-", value: relation });
        }
        if (fields["System.State"] === "New") updates.push({ op: "add", path: "/fields/System.State", value: "Committed" });
        if (updates.length > 1) await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}`, "PATCH", updates, true);
        return { adoId: item.adoId, linked: true };
    }

    async inspectWorkItem(config: Configuration, item: WorkItem): Promise<WorkItem> {
        return this.workItem(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}`), item.key, item.repository, config);
    }

    async dispatch(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string): Promise<Json> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Dispatch repository is missing.");
        if (repo.provider === "github") {
            await this.github(repo, `repos/${repo.slug}/branches/${segment(repo.baseBranch)}`);
            const token = await this.githubToken(repo);
            const output = await this.runner.run("gh", ["agent-task", "create", "-F", "-", "--repo", `${repo.hostname}/${repo.slug}`, "--base", repo.baseBranch], {
                env: githubCliEnv(repo.hostname, token),
                input: `${item.description}\n\nAcceptance criteria:\n${item.acceptanceCriteria.map((value) => `- ${value}`).join("\n")}\n\nExpected tests:\n${item.tests.join("\n")}\n\nFixes AB#${workItem.adoId}\nSoftware Factory operation: ${marker}\nFollow .github/copilot-instructions.md strictly. Preserve the operation marker in the PR body. Do not merge the PR.`,
            });
            const urls = output.match(/https:\/\/[^\s"'<>]+/g) || [];
            return { submitted: true, provider: "github", marker, sessionUrls: urls.filter((url) => new URL(url).hostname === repo.hostname) };
        }

        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
        const metadata = object(await this.ado(organization, project, `git/repositories/${segment(repository)}`), "ADO repository");
        if (!metadata.id) throw new FactoryError("invalid_provider_response", "ADO repository could not be verified.", 502);
        const refs = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/refs?filter=${encodeURIComponent(`heads/${repo.baseBranch}`)}`), "ADO branches");
        if (!Array.isArray(refs.value) || !refs.value.some((entry) => object(entry, "Branch").name === `refs/heads/${repo.baseBranch}`)) throw new FactoryError("branch_not_found", "The ADO dispatch base branch does not exist.", 409);
        const current = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${workItem.adoId}`), "ADO work item");
        if (!Number.isSafeInteger(current.rev)) throw new FactoryError("invalid_provider_response", "ADO omitted the revision required for safe assignment.", 502);
        const fields = object(current.fields, "ADO fields");
        const tags = typeof fields["System.Tags"] === "string" ? fields["System.Tags"].split(";").map((tag) => tag.trim()).filter(Boolean) : [];
        const repoTag = `copilot:repo=${repo.slug}@${repo.baseBranch}`;
        if (tags.some((tag) => tag.startsWith("copilot:repo=") && tag !== repoTag)) throw new FactoryError("conflicting_repository", "This work item is already bound to another Copilot repository.", 409);
        for (const tag of [repoTag, marker, "agent-dispatched"]) if (!tags.includes(tag)) tags.push(tag);
        const state = dispatchedWorkItemState(fields, config);
        await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${workItem.adoId}`, "PATCH", [
            { op: "test", path: "/rev", value: current.rev },
            { op: "add", path: "/fields/System.Tags", value: tags.join("; ") },
            { op: "add", path: "/fields/System.AssignedTo", value: "GitHub Copilot" },
            { op: "add", path: "/fields/System.State", value: state },
        ], true);
        return { submitted: true, provider: "ado", marker, adoId: workItem.adoId };
    }

    private async localCheckout(repo: RepositoryTarget): Promise<string> {
        const workspace = process.env.SOFTWARE_FACTORY_WORKSPACE || defaultWorkspaceRoot();
        const root = await ensureSubrepoWorktree(workspace, repo.localPath, repo.baseBranch, this.runner);
        const remote = await this.runner.run("git", ["remote", "get-url", "origin"], { cwd: root });
        if (!sameOrigin(remote, repo)) throw new FactoryError("wrong_repository", "The checkout origin does not match the approved repository.", 409);
        return root;
    }

    async verifyLocalReadiness(repo: RepositoryTarget, expectedBase: string): Promise<void> {
        await this.verifyRemoteBase(repo, {
            head: expectedBase,
            indexTree: expectedBase,
            base: expectedBase,
            tree: expectedBase,
        });
    }

    async fetchRepairRefs(repo: RepositoryTarget, sourceRoot: string, branches: string[]): Promise<void> {
        const remoteUrl = await this.runner.run("git", ["remote", "get-url", "origin"], { cwd: sourceRoot });
        if (!sameOrigin(remoteUrl, repo)) {
            throw new FactoryError("wrong_repository", "The checkout origin does not match the approved repository.", 409);
        }
        const directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-fetch-"));
        try {
            const env = stripGitHubAuthEnv();
            for (const name of Object.keys(env)) if (/^GIT_/i.test(name)) delete env[name];
            const token = repo.provider === "github" ? await this.githubToken(repo) : await this.adoToken();
            const authorization = repo.provider === "github"
                ? `AUTHORIZATION: basic ${Buffer.from(`x-access-token:${token}`).toString("base64")}`
                : `AUTHORIZATION: bearer ${token}`;
            Object.assign(env, {
                GIT_CONFIG_NOSYSTEM: "1",
                GIT_CONFIG_GLOBAL: path.join(directory, "no-global-config"),
                GIT_TERMINAL_PROMPT: "0",
                GCM_INTERACTIVE: "Never",
                GIT_CONFIG_COUNT: "2",
                GIT_CONFIG_KEY_0: `http.https://${repo.hostname}/.extraheader`,
                GIT_CONFIG_VALUE_0: authorization,
                GIT_CONFIG_KEY_1: "http.followRedirects",
                GIT_CONFIG_VALUE_1: "false",
            });
            const canonicalRemote = repo.provider === "github"
                ? `https://${repo.hostname}/${repo.slug}.git`
                : `https://dev.azure.com/${repo.slug.split("/").map(segment).slice(0, 2).join("/")}/_git/${segment(repo.slug.split("/")[2]!)}`;
            const refspecs = [...new Set(branches.filter(Boolean))].map((branch) =>
                branch === repo.baseBranch
                    ? `+refs/heads/${branch}:refs/remotes/origin/${branch}`
                    : `refs/heads/${branch}`,
            );
            await this.runner.run("git", ["fetch", "--no-tags", canonicalRemote, ...refspecs], { cwd: sourceRoot, env });
        } finally {
            rmSync(directory, { recursive: true, force: true });
        }
    }

    private async verifyRemoteBase(repo: RepositoryTarget, source: PublicationSource): Promise<string> {
        if (repo.provider === "github") {
            const identity = object(await this.github(repo, "user"), "GitHub identity");
            const metadata = object(await this.github(repo, `repos/${repo.slug}`), "GitHub repository");
            const branch = object(await this.github(repo, `repos/${repo.slug}/branches/${segment(repo.baseBranch)}`), "GitHub base branch");
            if (typeof identity.login !== "string" || identity.login.toLowerCase() !== repo.account.toLowerCase() ||
                !Number.isSafeInteger(identity.id) || Number(identity.id) <= 0 ||
                typeof metadata.full_name !== "string" || metadata.full_name.toLowerCase() !== repo.slug.toLowerCase()) {
                throw new FactoryError("wrong_repository", "The authenticated account or repository does not match the approved target.", 409);
            }
            if (object(branch.commit, "GitHub base commit").sha !== source.base) {
                throw new FactoryError("local_base_changed", "The live base branch changed after review; a new review is required.", 409);
            }
            return String(identity.id);
        }
        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository || repo.hostname !== "dev.azure.com") throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
        const metadata = object(await this.ado(organization, project, `git/repositories/${segment(repository)}`), "ADO repository");
        const targetProject = object(metadata.project, "ADO repository project");
        if (!metadata.id || typeof metadata.name !== "string" || metadata.name.toLowerCase() !== repository.toLowerCase() ||
            typeof targetProject.name !== "string" || targetProject.name.toLowerCase() !== project.toLowerCase()) {
            throw new FactoryError("wrong_repository", "ADO resolved a different repository or project.", 409);
        }
        const refs = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/refs?filter=${encodeURIComponent(`heads/${repo.baseBranch}`)}`), "ADO branches");
        if (!Array.isArray(refs.value)) throw new FactoryError("invalid_provider_response", "ADO omitted base branch results.", 502);
        const matches = refs.value.map((entry) => object(entry, "ADO branch")).filter((entry) => entry.name === `refs/heads/${repo.baseBranch}`);
        if (matches.length !== 1 || matches[0]!.objectId !== source.base) {
            throw new FactoryError("local_base_changed", "The live base branch changed after review; a new review is required.", 409);
        }
        return this.adoIdentity(organization);
    }

    private async verifyLocalSource(root: string, repo: RepositoryTarget, item: PlannedItem, implementation: LocalImplementationInput): Promise<void> {
        if (!implementation.files.length) throw new FactoryError("invalid_implementation", "Local implementation has no reviewed file changes.", 409);
        const source = implementation.sourceBinding;
        const options = { cwd: root };
        const [head, indexTree, base, headTree] = await Promise.all([
            this.runner.run("git", ["rev-parse", "HEAD"], options),
            this.runner.run("git", ["write-tree"], options),
            this.runner.run("git", ["rev-parse", `refs/remotes/origin/${repo.baseBranch}^{commit}`], options),
            this.runner.run("git", ["rev-parse", "HEAD^{tree}"], options),
        ]);
        if (head !== source.head || base !== source.base || source.head !== source.base ||
            indexTree !== source.indexTree || headTree !== source.indexTree) {
            throw new FactoryError("source_drift", "The checkout, index, or approved base changed after review.", 409);
        }
        verifyPublicationSource(root, { targetId: item.id, files: implementation.files }, source);
    }

    async prepareLocalPublication(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput): Promise<LocalPublicationBinding> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Dispatch repository is missing.");
        const root = await this.localCheckout(repo);
        const branch = text(implementation.branch, "Implementation branch");
        await this.runner.run("git", ["check-ref-format", "--branch", branch], { cwd: root });
        await this.verifyLocalSource(root, repo, item, implementation);
        const publisherId = await this.verifyRemoteBase(repo, implementation.sourceBinding);
        const initialBinding: LocalPublicationBinding = {
            branch, headSha: implementation.sourceBinding.head, source: implementation.sourceBinding,
            target: { provider: repo.provider, hostname: repo.hostname, slug: repo.slug, baseBranch: repo.baseBranch,
                publisherId, publisherAccount: repo.account },
        };
        assertPublicationBinding(initialBinding);
        const message = [
            item.title, "", implementation.summary, "", `Fixes AB#${workItem.adoId}`,
            `Software Factory operation: ${marker}`, "",
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
        ].join("\n");
        const headSha = await this.runner.run("git", ["commit-tree", implementation.sourceBinding.tree, "-p", implementation.sourceBinding.head], { cwd: root, input: message });
        const binding = { ...initialBinding, headSha };
        assertPublicationBinding(binding);
        const [tree, parents] = await Promise.all([
            this.runner.run("git", ["rev-parse", `${headSha}^{tree}`], { cwd: root }),
            this.runner.run("git", ["rev-list", "--parents", "-n", "1", headSha], { cwd: root }),
        ]);
        if (tree !== binding.source.tree || parents !== `${headSha} ${binding.source.head}`) {
            throw new FactoryError("publication_binding_changed", "The prepared commit does not contain the reviewed tree and base.", 409);
        }
        await this.verifyLocalSource(root, repo, item, implementation);
        return binding;
    }

    private async pushNewBranch(root: string, repo: RepositoryTarget, binding: LocalPublicationBinding,
        expectedHead = "", beforeSend?: () => Promise<void>): Promise<void> {
        const directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-push-"));
        try {
            const template = path.join(directory, "empty-template");
            const bare = path.join(directory, "push.git");
            mkdirSync(template);
            const commonDir = await this.runner.run("git", ["rev-parse", "--git-common-dir"], { cwd: root });
            const objects = realpathSync(path.resolve(root, commonDir, "objects"));
            const env = stripGitHubAuthEnv();
            for (const name of Object.keys(env)) if (/^GIT_/i.test(name)) delete env[name];
            Object.assign(env, {
                GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: path.join(directory, "no-global-config"),
                GIT_TERMINAL_PROMPT: "0", GCM_INTERACTIVE: "Never",
                GIT_ALTERNATE_OBJECT_DIRECTORIES: objects,
            });
            await this.runner.run("git", ["init", "--bare", "--quiet",
                `--object-format=${binding.headSha.length === 64 ? "sha256" : "sha1"}`, `--template=${template}`, bare], { cwd: directory, env });
            const token = repo.provider === "github" ? await this.githubToken(repo) : await this.adoToken();
            const authorization = repo.provider === "github"
                ? `AUTHORIZATION: basic ${Buffer.from(`x-access-token:${token}`).toString("base64")}`
                : `AUTHORIZATION: bearer ${token}`;
            const remote = repo.provider === "github" ? `https://${repo.hostname}/${repo.slug}.git`
                : `https://dev.azure.com/${repo.slug.split("/").map(segment).slice(0, 2).join("/")}/_git/${segment(repo.slug.split("/")[2]!)}`;
            Object.assign(env, {
                GIT_CONFIG_COUNT: "2",
                GIT_CONFIG_KEY_0: `http.https://${repo.hostname}/.extraheader`,
                GIT_CONFIG_VALUE_0: authorization,
                GIT_CONFIG_KEY_1: "http.followRedirects", GIT_CONFIG_VALUE_1: "false",
            });
            const ref = `refs/heads/${binding.branch}`;
            await beforeSend?.();
            await this.runner.run("git", [`--git-dir=${bare}`, "push", "--porcelain", `--force-with-lease=${ref}:${expectedHead}`, remote, `${binding.headSha}:${ref}`], { cwd: directory, env });
        } finally {
            rmSync(directory, { recursive: true, force: true });
        }
    }

    async prepareRepair(repo: RepositoryTarget, input: RepairPublicationInput): Promise<string> {
        const root = await this.localCheckout({ ...repo, localPath: input.localPath });
        if (input.expectedHead !== input.source.head || !gitObjectId.test(input.expectedHead) ||
            (input.mergeParent && !gitObjectId.test(input.mergeParent))) {
            throw new FactoryError("repair_source_changed", "Repair parents do not match the inspected PR head.", 409);
        }
        verifyPublicationSource(root, { files: input.files, targetId: "observer-repair" }, input.source);
        const parents = ["-p", input.expectedHead, ...(input.mergeParent ? ["-p", input.mergeParent] : [])];
        const head = await this.runner.run("git", ["commit-tree", input.source.tree, ...parents], {
            cwd: root, input: `${input.summary}\n\nCo-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>\n`,
        });
        if (!gitObjectId.test(head)) throw new FactoryError("invalid_repair_commit", "Git did not produce a valid repair commit.", 409);
        return head;
    }

    async publishRepair(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string,
        original: LocalPublicationBinding, input: RepairPublicationInput, headSha: string, beforeSend: () => Promise<void>): Promise<Json> {
        const repo = config.repositories[item.repository]!;
        assertPublicationBinding(original);
        assertPublicationTarget(repo, original, true);
        const root = await this.localCheckout({ ...repo, localPath: input.localPath });
        const parents = [input.expectedHead, ...(input.mergeParent ? [input.mergeParent] : [])];
        if (!gitObjectId.test(headSha) ||
            await this.runner.run("git", ["rev-list", "--parents", "-n", "1", headSha], { cwd: root }) !== [headSha, ...parents].join(" ") ||
            await this.runner.run("git", ["rev-parse", `${headSha}^{tree}`], { cwd: root }) !== input.source.tree) {
            throw new FactoryError("repair_source_changed", "Only an incremental commit of the independently reviewed repair tree may be pushed.", 409);
        }
        const assertTarget = async () => {
            const publisher = repo.provider === "github"
                ? String(object(await this.github(repo, "user"), "GitHub publisher").id)
                : await this.adoIdentity(repo.slug.split("/")[0]!);
            if (!samePublisherId(repo.provider, publisher, original.target.publisherId)) {
                throw new FactoryError("publication_identity_changed", "The repair publisher differs from the original approved publisher.", 409);
            }
            if (!await this.findLocalPublication(config, item, workItem, marker, { ...original, headSha: input.expectedHead })) {
                throw new FactoryError("publication_identity_changed", "The exact original PR cannot be verified before repair.", 409);
            }
            verifyPublicationSource(root, { files: input.files, targetId: item.id }, input.source);
        };
        await assertTarget();
        await this.pushNewBranch(root, repo, { ...original, headSha }, input.expectedHead, async () => {
            await assertTarget();
            await beforeSend();
        });
        const receipt = await this.findLocalPublication(config, item, workItem, marker, { ...original, headSha });
        if (!receipt) throw new FactoryError("reconciliation_required", "The repair push could not be verified. Do not resend it.", 409);
        return receipt;
    }

    async replyRepairThread(repo: RepositoryTarget, number: number, value: Json, body: string, marker: string,
        beforeSend: () => Promise<void>, retry: boolean): Promise<Json> {
        const thread = object(value, "Addressed review thread");
        const postedBody = `${body}\n\nSoftware Factory operation: ${marker}`;
        if (repo.provider === "github") {
            const id = text(thread.threadId, "Review thread ID");
            const rootId = Number(thread.id);
            const load = async () => {
                const result = object(await this.github(repo, "graphql", "POST", {
                    query: "query($id: ID!) { node(id:$id) { ... on PullRequestReviewThread { id isResolved pullRequest { number repository { nameWithOwner } } comments(first:100) { pageInfo { hasNextPage } nodes { databaseId body } } } } }",
                    variables: { id },
                }), "Review thread lookup");
                if (result.errors) throw new FactoryError("review_thread_unavailable", "GitHub rejected the review-thread lookup.", 502);
                const live = object(object(result.data, "Thread data").node, "Live review thread");
                const pr = object(live.pullRequest, "Thread PR");
                const comments = object(live.comments, "Thread comments");
                if (pr.number !== number || String(object(pr.repository, "Thread repository").nameWithOwner).toLowerCase() !== repo.slug.toLowerCase() ||
                    object(comments.pageInfo, "Thread pagination").hasNextPage || !Array.isArray(comments.nodes) ||
                    object(comments.nodes[0], "Root comment").databaseId !== rootId) {
                    throw new FactoryError("review_thread_changed", "The review thread is incomplete or belongs to a different PR.", 409);
                }
                const all = comments.nodes.map((entry) => object(entry, "Thread comment"));
                if (thread.commentsComplete !== true || !Array.isArray(thread.comments) ||
                    JSON.stringify(all.filter((entry) => !hasOperationMarker(entry.body, marker)).map((entry) => [entry.databaseId, entry.body])) !==
                    JSON.stringify(thread.comments.map((entry) => object(entry, "Reviewed comment")).map((entry) => [entry.databaseId, entry.body]))) {
                    throw new FactoryError("review_thread_changed", "The addressed review thread changed after source review; it was not resolved.", 409);
                }
                return { live, matches: all.filter((entry) => hasOperationMarker(entry.body, marker)) };
            };
            const { live, matches } = await load();
            if (matches.length > 1 || (matches[0] && matches[0].body !== postedBody)) {
                throw new FactoryError("reconciliation_required", "The repair reply marker is ambiguous or changed.", 409);
            }
            if (!matches.length) {
                if (retry) throw new FactoryError("reconciliation_required", "An uncertain repair reply was not found; it was not reposted.", 409);
                if (live.isResolved) return { threadId: id, alreadyResolved: true };
                await beforeSend();
                await this.github(repo, `repos/${repo.slug}/pulls/${number}/comments/${rootId}/replies`, "POST", { body: postedBody });
            }
            const refreshed = await load();
            if (!refreshed.live.isResolved) {
                if (refreshed.matches.length !== 1 || refreshed.matches[0]!.body !== postedBody) throw new FactoryError("reconciliation_required", "The repair reply could not be confirmed.", 409);
                await beforeSend();
                const resolved = object(await this.github(repo, "graphql", "POST", {
                    query: "mutation($id: ID!) { resolveReviewThread(input:{threadId:$id}) { thread { isResolved } } }",
                    variables: { id },
                }), "Thread resolution");
                if (resolved.errors || object(object(object(resolved.data, "Resolution data").resolveReviewThread, "Resolution").thread, "Resolved thread").isResolved !== true) {
                    throw new FactoryError("reconciliation_required", "GitHub did not confirm thread resolution.", 409);
                }
            }
            return { threadId: id, replied: true, resolved: true };
        }
        const [organization, project, repository] = repo.slug.split("/");
        const id = Number(thread.id);
        if (!Number.isSafeInteger(id) || id <= 0) throw new FactoryError("review_thread_changed", "Invalid ADO review thread.", 409);
        const endpoint = `git/repositories/${segment(repository!)}/pullrequests/${number}/threads/${id}`;
        const load = async () => {
            const live = object(await this.ado(organization!, project!, endpoint), "ADO review thread");
            if (!Array.isArray(live.comments) || live.id !== id) throw new FactoryError("review_thread_changed", "ADO omitted the addressed thread.", 409);
            const all = live.comments.map((entry) => object(entry, "ADO comment"));
            if (!Array.isArray(thread.comments) ||
                JSON.stringify(all.filter((entry) => !hasOperationMarker(entry.content, marker)).map((entry) => [entry.id, entry.content])) !==
                JSON.stringify(thread.comments.map((entry) => object(entry, "Reviewed ADO comment")).map((entry) => [entry.id, entry.content]))) {
                throw new FactoryError("review_thread_changed", "The addressed ADO thread changed after source review; it was not resolved.", 409);
            }
            return { live, matches: all.filter((entry) => hasOperationMarker(entry.content, marker)) };
        };
        const { matches } = await load();
        if (matches.length > 1 || (matches[0] && matches[0].content !== postedBody)) {
            throw new FactoryError("reconciliation_required", "The repair reply marker is ambiguous or changed.", 409);
        }
        if (!matches.length) {
            if (retry) throw new FactoryError("reconciliation_required", "An uncertain repair reply was not found; it was not reposted.", 409);
            await beforeSend();
            await this.ado(organization!, project!, `${endpoint}/comments`, "POST", { parentCommentId: 0, content: postedBody, commentType: 1 });
        }
        const refreshed = await load();
        if (refreshed.matches.length !== 1 || refreshed.matches[0]!.content !== postedBody) throw new FactoryError("reconciliation_required", "The repair reply could not be confirmed.", 409);
        if (refreshed.live.status !== "fixed" && refreshed.live.status !== 2) {
            await beforeSend();
            const resolved = object(await this.ado(organization!, project!, endpoint, "PATCH", { status: 2 }), "ADO thread resolution");
            if (resolved.status !== "fixed" && resolved.status !== 2) throw new FactoryError("reconciliation_required", "ADO did not confirm thread resolution.", 409);
        }
        return { threadId: String(id), replied: true, resolved: true };
    }

    private async observerPr(repo: RepositoryTarget, number: number, head: string): Promise<Record<string, unknown>> {
        if (!Number.isSafeInteger(number) || number <= 0 || !gitObjectId.test(head)) throw new FactoryError("invalid_observer_target", "Observer needs an exact PR and full source head.", 409);
        if (repo.provider === "github") {
            const pr = object(await this.github(repo, `repos/${repo.slug}/pulls/${number}`), "Observer PR");
            const source = object(pr.head, "Observer head");
            const target = object(pr.base, "Observer base");
            if (pr.number !== number || pr.state !== "open" || pr.merged_at || source.sha !== head ||
                target.ref !== repo.baseBranch ||
                String(object(source.repo, "Head repository").full_name).toLowerCase() !== repo.slug.toLowerCase() ||
                String(object(target.repo, "Base repository").full_name).toLowerCase() !== repo.slug.toLowerCase()) {
                throw new FactoryError("publication_identity_changed", "The observer target/head changed; no effect was sent.", 409);
            }
            return pr;
        }
        const [org, project, name] = repo.slug.split("/");
        if (!org || !project || !name || repo.hostname !== "dev.azure.com") throw new FactoryError("invalid_observer_target", "The ADO observer target is invalid.", 409);
        const pr = object(await this.ado(org, project, `git/repositories/${segment(name)}/pullrequests/${number}`), "Observer ADO PR");
        const repository = object(pr.repository, "Observer ADO repository");
        if (pr.pullRequestId !== number || pr.status !== "active" ||
            object(pr.lastMergeSourceCommit, "Observer source").commitId !== head ||
            pr.targetRefName !== `refs/heads/${repo.baseBranch}` ||
            String(repository.name).toLowerCase() !== name.toLowerCase() ||
            String(object(repository.project, "Observer project").name).toLowerCase() !== project.toLowerCase()) {
            throw new FactoryError("publication_identity_changed", "The ADO observer target/head changed; no effect was sent.", 409);
        }
        return pr;
    }

    private async observerPolicies(repo: RepositoryTarget, pr: Record<string, unknown>): Promise<Record<string, unknown>[]> {
        const [org, project] = repo.slug.split("/");
        const projectId = text(object(object(pr.repository, "PR repository").project, "PR project").id, "PR project ID");
        const artifact = `vstfs:///CodeReview/CodeReviewId/${projectId}/${pr.pullRequestId}`;
        const result = object(await this.ado(org!, project!, `policy/evaluations?artifactId=${segment(artifact)}`, "GET", undefined, false, "7.1-preview.1"), "Policy evaluations");
        if (!Array.isArray(result.value) || result.continuationToken) throw new FactoryError("observer_evidence_incomplete", "ADO omitted complete policy evaluations.", 409);
        return result.value.map((value) => object(value, "Policy evaluation"));
    }

    private policyEvidence(policy: Record<string, unknown>): Json {
        return jsonValue({ evaluationId: policy.evaluationId, status: policy.status,
            context: policy.context, startedDate: policy.startedDate ?? null, completedDate: policy.completedDate ?? null });
    }

    private async observerTimeline(repo: RepositoryTarget, pr: Record<string, unknown>, id: number, head: string): Promise<Record<string, unknown>[]> {
        if (!Number.isSafeInteger(id) || id <= 0) throw new FactoryError("invalid_observer_target", "Invalid build ID.", 409);
        const [org, project] = repo.slug.split("/");
        const build = object(await this.ado(org!, project!, `build/builds/${id}`), "Observer build");
        let parameters: Record<string, unknown> = {};
        if (build.parameters) {
            try { parameters = object(JSON.parse(text(build.parameters, "Build parameters")), "Build parameters"); }
            catch { throw new FactoryError("observer_evidence_incomplete", "Malformed build parameters cannot authorize a CI retry.", 409); }
        }
        const source = parameters["system.pullRequest.sourceCommitId"] ||
            (pr.lastMergeCommit && build.sourceVersion === object(pr.lastMergeCommit, "PR merge").commitId ? head : null);
        if (build.id !== id || object(build.repository, "Build repository").id !== object(pr.repository, "PR repository").id ||
            build.sourceBranch !== `refs/pull/${pr.pullRequestId}/merge` || source !== head ||
            /PR-Reviewer|AI PR Review/i.test(String(object(build.definition, "Build definition").name))) {
            throw new FactoryError("observer_evidence_changed", "The build is not current-head validation for this exact PR.", 409);
        }
        const timeline = object(await this.ado(org!, project!, `build/builds/${id}/timeline`), "Build timeline");
        if (!Array.isArray(timeline.records)) throw new FactoryError("observer_evidence_incomplete", "ADO omitted stage diagnostics.", 409);
        return timeline.records.map((value) => object(value, "Timeline record"));
    }

    private stageEvidence(stage: Record<string, unknown>): Json {
        return jsonValue({ id: stage.id, identifier: stage.identifier, attempt: stage.attempt,
            state: stage.state, result: stage.result });
    }

    async prepareObserverEffect(repo: RepositoryTarget, number: number, value: Json, request: ObserverEffectRequest): Promise<ObserverEffectPlan> {
        const snapshot = object(value, "Observer snapshot");
        const head = text(snapshot.headRefOid, "Observed head");
        const pr = await this.observerPr(repo, number, head);
        if (Number(snapshot.number) !== number || snapshot.baseRefName !== repo.baseBranch ||
            snapshot.headRefName !== (repo.provider === "github" ? object(pr.head, "PR source").ref : String(pr.sourceRefName).replace(/^refs\/heads\//, ""))) {
            throw new FactoryError("observer_evidence_changed", "The observer snapshot differs from the current PR identity.", 409);
        }
        const mutations: ObserverMutation[] = [];
        if (request.kind === "status") {
            const before = snapshot.description ?? snapshot.body;
            const liveBody = repo.provider === "github" ? pr.body : pr.description;
            if (typeof before !== "string" || before !== liveBody) throw new FactoryError("observer_evidence_changed", "The PR description changed during observation; it was not overwritten.", 409);
            const ci = object(request.ci, "CI evidence");
            if (typeof ci.status !== "string" || !["passed", "failed", "pending", "unknown", "not-observed"].includes(ci.status) ||
                !Array.isArray(ci.evidence) || (ci.status === "passed" && ci.headSha !== head)) {
                throw new FactoryError("observer_evidence_incomplete", "Current-head CI status and evidence are required.", 409);
            }
            const body = renderObserverDescription({ provider: repo.provider }, snapshot, request.block, {
                status: ci.status, headSha: ci.headSha, evidence: ci.evidence,
            });
            if (body !== before) mutations.push({ kind: "status", prNumber: number, headSha: head, before, body });
            return { mutations, result: body };
        }
        if (repo.provider !== "ado") return { mutations, result: request.kind === "refresh" ? [] : false };
        const policies = () => this.observerPolicies(repo, pr);
        const requeue = async (policy: Record<string, unknown>) => {
            const id = text(policy.evaluationId, "Policy evaluation ID");
            if (!/^[a-f0-9-]{36}$/i.test(id)) throw new FactoryError("invalid_observer_target", "Invalid policy evaluation ID.", 409);
            mutations.push({ kind: "policy", prNumber: number, headSha: head, evaluationId: id, before: this.policyEvidence(policy) });
            return true;
        };
        if (request.kind === "refresh") {
            const selected = staleAdoBuildPolicies(snapshot);
            if (!selected.length) return { mutations, result: [] };
            const live = await policies();
            for (const candidate of selected) {
                const policy = live.find((policy) => policy.evaluationId === candidate.evaluationId);
                if (!policy || object(policy.configuration, "Build policy").isBlocking !== true ||
                    object(policy.context, "Policy context").buildIsNotCurrent !== true ||
                    Number(object(policy.context, "Policy context").buildId) !== candidate.buildId ||
                    policy.status !== candidate.status) throw new FactoryError("observer_evidence_changed", "Stale build policy changed before requeue.", 409);
                await requeue(policy);
            }
            return { mutations, result: selected.map((policy) => String(policy.evaluationId)) };
        }
        const result = await retryFailedAdoBuildStages({ provider: "ado" }, snapshot, {
            stageOnly: request.stageOnly, transport: {
                timeline: (id) => this.observerTimeline(repo, pr, id, head),
                policies,
                retryStage: async (id, stage) => {
                    if (!Number.isSafeInteger(stage.attempt) || Number(stage.attempt) < 1 || typeof stage.id !== "string") {
                        throw new FactoryError("observer_evidence_incomplete", "Stage identity/attempt is required for durable retries.", 409);
                    }
                    mutations.push({ kind: "stage", prNumber: number, headSha: head, buildId: id,
                        stageId: text(stage.identifier, "Stage identifier"), attempt: Number(stage.attempt), before: this.stageEvidence(stage) });
                    return true;
                },
                requeuePolicy: requeue,
            },
        });
        return { mutations, result };
    }

    private async observerMutationEvidence(repo: RepositoryTarget, mutation: ObserverMutation): Promise<Json> {
        const pr = await this.observerPr(repo, mutation.prNumber, mutation.headSha);
        if (mutation.kind === "status") {
            const body = repo.provider === "github" ? pr.body : pr.description;
            if (typeof body !== "string") throw new FactoryError("observer_evidence_incomplete", "The PR description is unavailable.", 409);
            return body;
        }
        if (repo.provider !== "ado") throw new FactoryError("invalid_observer_target", "CI recovery targets only supported ADO validation.", 409);
        if (mutation.kind === "stage") {
            const stages = await this.observerTimeline(repo, pr, mutation.buildId, mutation.headSha);
            const matches = stages.filter((stage) => stage.type === "Stage" && stage.identifier === mutation.stageId);
            if (matches.length !== 1) throw new FactoryError("observer_evidence_changed", "The original retry stage is missing or ambiguous.", 409);
            return this.stageEvidence(matches[0]!);
        }
        const matches = (await this.observerPolicies(repo, pr)).filter((policy) => policy.evaluationId === mutation.evaluationId);
        if (matches.length !== 1) throw new FactoryError("observer_evidence_changed", "The original PR policy is missing or ambiguous.", 409);
        return this.policyEvidence(matches[0]!);
    }

    async findObserverMutation(repo: RepositoryTarget, mutation: ObserverMutation): Promise<Json | null> {
        const evidence = await this.observerMutationEvidence(repo, mutation);
        if (mutation.kind === "status") {
            if (evidence === mutation.body) return { accepted: true, reconciled: true, headSha: mutation.headSha, kind: mutation.kind };
            if (evidence !== mutation.before) throw new FactoryError("reconciliation_required", "The PR description was edited after an uncertain status update; no overwrite was attempted.", 409);
            return null;
        }
        const current = object(evidence, "Observer effect evidence");
        const before = object(mutation.before, "Original observer evidence");
        const advanced = mutation.kind === "stage"
            ? current.id === before.id && Number.isSafeInteger(current.attempt) && Number(current.attempt) > mutation.attempt
            : current.evaluationId === before.evaluationId &&
                ((current.startedDate !== null && current.startedDate !== before.startedDate) ||
                    Number(object(current.context, "Current policy context").buildId) > Number(object(before.context, "Original policy context").buildId));
        return advanced ? { accepted: true, reconciled: true, headSha: mutation.headSha, kind: mutation.kind, evidence } : null;
    }

    async sendObserverMutation(repo: RepositoryTarget, mutation: ObserverMutation, publisher: LocalPublicationBinding["target"],
        beforeSend: () => Promise<void>): Promise<Json> {
        if (publisher.provider !== repo.provider || publisher.hostname !== repo.hostname || publisher.slug !== repo.slug ||
            publisher.baseBranch !== repo.baseBranch || (repo.provider === "github" && publisher.publisherAccount.toLowerCase() !== repo.account.toLowerCase())) {
            throw new FactoryError("publication_identity_changed", "The observer publisher scope changed.", 409);
        }
        await beforeSend();
        const identity = repo.provider === "github" ? String(object(await this.github(repo, "user"), "Observer publisher").id)
            : await this.adoIdentity(repo.slug.split("/")[0]!);
        if (!samePublisherId(repo.provider, identity, publisher.publisherId)) throw new FactoryError("publication_identity_changed", "The observer publisher changed.", 409);
        if (hash(await this.observerMutationEvidence(repo, mutation)) !== hash(mutation.before)) {
            throw new FactoryError("observer_evidence_changed", "The exact observer target changed before send; nothing was overwritten or retried.", 409);
        }
        const [org, project, repository] = repo.slug.split("/");
        if (mutation.kind === "status") {
            if (repo.provider === "github") await this.github(repo, `repos/${repo.slug}/pulls/${mutation.prNumber}`, "PATCH", { body: mutation.body });
            else await this.ado(org!, project!, `git/repositories/${segment(repository!)}/pullrequests/${mutation.prNumber}`, "PATCH", { description: mutation.body });
            if (await this.observerMutationEvidence(repo, mutation) !== mutation.body) {
                throw new FactoryError("reconciliation_required", "The observer status PATCH returned, but its exact description could not be verified.", 409);
            }
        } else if (mutation.kind === "stage") {
            await this.ado(org!, project!, `build/builds/${mutation.buildId}/stages/${segment(mutation.stageId)}`, "PATCH", { state: 1, forceRetryAllJobs: false }, false, "7.0-preview.1");
        } else {
            await this.ado(org!, project!, `policy/evaluations/${segment(mutation.evaluationId)}`, "PATCH", {}, false, "7.1-preview.1");
        }
        return { accepted: true, kind: mutation.kind, headSha: mutation.headSha };
    }

    async publishLocal(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput, binding: LocalPublicationBinding): Promise<Json> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Dispatch repository is missing.");
        assertPublicationBinding(binding);
        assertPublicationTarget(repo, binding, true);
        if (binding.branch !== implementation.branch || JSON.stringify(binding.source) !== JSON.stringify(implementation.sourceBinding)) {
            throw new FactoryError("publication_binding_changed", "The prepared publication differs from the reviewed implementation.", 409);
        }
        const root = await this.localCheckout(repo);
        await this.verifyLocalSource(root, repo, item, implementation);
        const [tree, parents] = await Promise.all([
            this.runner.run("git", ["rev-parse", `${binding.headSha}^{tree}`], { cwd: root }),
            this.runner.run("git", ["rev-list", "--parents", "-n", "1", binding.headSha], { cwd: root }),
        ]);
        if (tree !== binding.source.tree || parents !== `${binding.headSha} ${binding.source.head}`) {
            throw new FactoryError("publication_binding_changed", "The prepared commit no longer matches the reviewed tree.", 409);
        }
        if (!samePublisherId(repo.provider, await this.verifyRemoteBase(repo, binding.source), binding.target.publisherId)) {
            throw new FactoryError("publication_identity_changed", "The authenticated publisher changed after the commit was prepared.", 409);
        }
        await this.verifyLocalSource(root, repo, item, implementation);
        await this.pushNewBranch(root, repo, binding);
        if (!samePublisherId(repo.provider, await this.verifyRemoteBase(repo, binding.source), binding.target.publisherId)) {
            throw new FactoryError("publication_identity_changed", "The authenticated publisher changed after branch creation; reconcile it manually.", 409);
        }
        const verification = object(implementation.verification, "Verification evidence");
        const evidence = ["passed", "failed", "blocked", "deferred", "unrun"].map((kind) => {
            const entries = Array.isArray(verification[kind]) ? verification[kind] as unknown[] : [];
            const commands = entries.map((entry) => typeof entry === "string" ? entry :
                entry && typeof entry === "object" && "command" in entry ? String(entry.command) : "").filter(Boolean);
            return `- ${kind}: ${commands.length ? commands.join(", ") : "none"}`;
        });
        const body = [
            implementation.summary, "", "### Acceptance Criteria",
            ...item.acceptanceCriteria.map((criterion) => `- ${criterion}`), "", "### Verification", ...evidence, "",
            "### Independent Code Review",
            `- Passed independent AI code review in ${implementation.roundsCompleted} of ${implementation.maxRounds} round(s) (mode: local).`,
            "", `Fixes AB#${workItem.adoId}`, `Software Factory operation: ${marker}`,
        ].join("\n");
        const title = new RegExp(`\\bAB#${workItem.adoId}(?!\\d)`).test(item.title) ? item.title : `${item.title} (Fixes AB#${workItem.adoId})`;
        let createdNumber: number;
        if (repo.provider === "github") {
            const created = object(await this.github(repo, `repos/${repo.slug}/pulls`, "POST", {
                title, head: binding.branch, base: repo.baseBranch, body,
            }), "Created GitHub PR");
            createdNumber = Number(created.number);
            if (Number.isSafeInteger(createdNumber) && createdNumber > 0) {
                await this.applyDefaultChangelogLabel(repo, createdNumber);
            }
        } else {
            const [organization, project, repository] = repo.slug.split("/");
            if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
            const created = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests`, "POST", {
                sourceRefName: `refs/heads/${binding.branch}`, targetRefName: `refs/heads/${repo.baseBranch}`,
                title, description: body, workItemRefs: [{ id: String(workItem.adoId) }],
            }), "Created ADO PR");
            createdNumber = Number(created.pullRequestId);
        }
        const receipt = await this.findLocalPublication(config, item, workItem, marker, binding);
        if (!receipt || !Number.isSafeInteger(createdNumber) || createdNumber <= 0 ||
            object(receipt, "Verified publication").prNumber !== createdNumber) {
            throw new FactoryError("reconciliation_required", "The created PR could not be verified against its reviewed commit. Do not retry publication.", 409);
        }
        return receipt;
    }

    private async applyDefaultChangelogLabel(repo: RepositoryTarget, prNumber: number): Promise<void> {
        try {
            const labels = await this.github(repo, `repos/${repo.slug}/labels?per_page=100`);
            if (!Array.isArray(labels)) return;
            const available = new Set(
                labels.map((entry) => (entry && typeof entry === "object" && "name" in entry && typeof entry.name === "string" ? entry.name : "")),
            );
            const preferred = available.has("changelog:minor")
                ? "changelog:minor"
                : available.has("changelog:patch")
                    ? "changelog:patch"
                    : "";
            if (!preferred) return;
            await this.github(repo, `repos/${repo.slug}/issues/${prNumber}/labels`, "POST", { labels: [preferred] });
        } catch {
            // Best-effort CI hygiene; never fail or trigger reconciliation after PR creation.
        }
    }

    async findLocalPublication(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, binding: LocalPublicationBinding): Promise<Json | null> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Dispatch repository is missing.");
        assertPublicationBinding(binding);
        assertPublicationTarget(repo, binding);
        if (!/^software-factory-[0-9a-f]{64}$/.test(marker)) throw new FactoryError("invalid_publication_binding", "The operation marker is invalid.", 409);
        let pr: Record<string, unknown>;
        let number: number;
        let url: string;
        if (repo.provider === "github") {
            const owner = repo.slug.split("/")[0]!;
            const candidates = await this.github(repo, `repos/${repo.slug}/pulls?state=all&head=${segment(`${owner}:${binding.branch}`)}&base=${segment(repo.baseBranch)}&per_page=100`);
            if (!Array.isArray(candidates) || candidates.length >= 100) throw new FactoryError("reconciliation_required", "The PR lookup was incomplete or ambiguous.", 409);
            if (!candidates.length) return null;
            if (candidates.length !== 1) throw new FactoryError("reconciliation_required", "Multiple PRs use the saved publication branch.", 409);
            number = Number(object(candidates[0], "GitHub PR candidate").number);
            if (!Number.isSafeInteger(number) || number <= 0) throw new FactoryError("invalid_provider_response", "GitHub returned an invalid PR number.", 502);
            pr = object(await this.github(repo, `repos/${repo.slug}/pulls/${number}`), "GitHub PR");
            const head = object(pr.head, "PR head");
            const base = object(pr.base, "PR base");
            const headRepo = object(head.repo, "PR head repository");
            const baseRepo = object(base.repo, "PR base repository");
            const author = object(pr.user, "PR author");
            if (pr.number !== number || pr.state !== "open" || pr.merged_at ||
                head.ref !== binding.branch || head.sha !== binding.headSha || base.ref !== repo.baseBranch ||
                String(author.id) !== binding.target.publisherId || typeof author.login !== "string" ||
                author.login.toLowerCase() !== binding.target.publisherAccount.toLowerCase() ||
                typeof headRepo.full_name !== "string" || headRepo.full_name.toLowerCase() !== repo.slug.toLowerCase() ||
                typeof baseRepo.full_name !== "string" || baseRepo.full_name.toLowerCase() !== repo.slug.toLowerCase() ||
                !hasOperationMarker(pr.body, marker) || !new RegExp(`\\bAB#${workItem.adoId}(?!\\d)`).test(String(pr.body))) {
                throw new FactoryError("publication_identity_changed", "The PR does not match the saved marker, work item, branch, base, and reviewed commit.", 409);
            }
            const parsed = new URL(text(pr.html_url, "PR URL"));
            if (parsed.protocol !== "https:" || parsed.hostname !== repo.hostname || parsed.port || parsed.username || parsed.password || parsed.search || parsed.hash ||
                parsed.pathname.replace(/\/$/, "").toLowerCase() !== `/${repo.slug}/pull/${number}`.toLowerCase()) {
                throw new FactoryError("publication_identity_changed", "The PR URL does not match the configured repository.", 409);
            }
            url = parsed.href;
        } else {
            const [organization, project, repository] = repo.slug.split("/");
            if (!organization || !project || !repository || repo.hostname !== "dev.azure.com") throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
            const metadata = object(await this.ado(organization, project, `git/repositories/${segment(repository)}`), "ADO repository");
            const targetProject = object(metadata.project, "ADO project");
            if (!metadata.id || typeof metadata.name !== "string" || metadata.name.toLowerCase() !== repository.toLowerCase() ||
                typeof targetProject.name !== "string" || targetProject.name.toLowerCase() !== project.toLowerCase()) {
                throw new FactoryError("wrong_repository", "ADO resolved a different repository or project.", 409);
            }
            const query = `searchCriteria.status=all&searchCriteria.sourceRefName=${segment(`refs/heads/${binding.branch}`)}&searchCriteria.targetRefName=${segment(`refs/heads/${repo.baseBranch}`)}&$top=100`;
            const candidates = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests?${query}`), "ADO PR lookup");
            if (!Array.isArray(candidates.value) || candidates.value.length >= 100) throw new FactoryError("reconciliation_required", "The ADO PR lookup was incomplete or ambiguous.", 409);
            if (!candidates.value.length) return null;
            if (candidates.value.length !== 1) throw new FactoryError("reconciliation_required", "Multiple PRs use the saved publication branch.", 409);
            number = Number(object(candidates.value[0], "ADO PR candidate").pullRequestId);
            if (!Number.isSafeInteger(number) || number <= 0) throw new FactoryError("invalid_provider_response", "ADO returned an invalid PR number.", 502);
            pr = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}`), "ADO PR");
            const prRepo = object(pr.repository, "ADO PR repository");
            const sourceCommit = object(pr.lastMergeSourceCommit, "ADO source commit");
            const author = object(pr.createdBy, "ADO PR author");
            const links = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/workitems`), "ADO PR work items");
            if (pr.pullRequestId !== number || pr.status !== "active" || prRepo.id !== metadata.id ||
                typeof author.id !== "string" || !samePublisherId(repo.provider, author.id, binding.target.publisherId) ||
                pr.sourceRefName !== `refs/heads/${binding.branch}` || pr.targetRefName !== `refs/heads/${repo.baseBranch}` ||
                sourceCommit.commitId !== binding.headSha || !hasOperationMarker(pr.description, marker) ||
                !Array.isArray(links.value) || !links.value.some((entry) => Number(object(entry, "Work item link").id) === workItem.adoId)) {
                throw new FactoryError("publication_identity_changed", "The ADO PR does not match the saved marker, work item, branch, base, and reviewed commit.", 409);
            }
            url = `https://dev.azure.com/${segment(organization)}/${segment(project)}/_git/${segment(repository)}/pullrequest/${number}`;
        }
        return { submitted: true, mode: "local", provider: repo.provider, marker, branch: binding.branch, headSha: binding.headSha, prNumber: number, url };
    }

    async markDispatched(config: Configuration, item: WorkItem, marker: string): Promise<Json> {
        const current = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}`), "ADO work item");
        if (!Number.isSafeInteger(current.rev)) throw new FactoryError("invalid_provider_response", "ADO omitted the revision required for status repair.", 502);
        const fields = object(current.fields, "Fields");
        const tags = typeof fields["System.Tags"] === "string" ? fields["System.Tags"].split(";").map((tag) => tag.trim()).filter(Boolean) : [];
        for (const tag of ["agent-dispatched", marker]) if (!tags.includes(tag)) tags.push(tag);
        const state = dispatchedWorkItemState(fields, config);
        if (fields["System.State"] !== state || fields["System.Tags"] !== tags.join("; ")) {
            await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}`, "PATCH", [
                { op: "test", path: "/rev", value: current.rev },
                { op: "add", path: "/fields/System.Tags", value: tags.join("; ") },
                { op: "add", path: "/fields/System.State", value: state },
            ], true);
        }
        return { adoId: item.adoId, state, tagged: true };
    }

    async findDispatch(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string): Promise<Json | null> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Repository mapping missing.");
        if (repo.provider === "ado") {
            const value = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${workItem.adoId}`), "ADO work item");
            const fields = object(value.fields, "ADO fields");
            return typeof fields["System.Tags"] === "string" && fields["System.Tags"].split(";").map((value) => value.trim()).includes(marker)
                ? { submitted: true, provider: "ado", marker, adoId: workItem.adoId } : null;
        }
        const result = object(await this.github(repo, `search/issues?q=${encodeURIComponent(`repo:${repo.slug} is:pr "${marker}" in:body`)}`), "GitHub search");
        if (!Array.isArray(result.items)) throw new FactoryError("invalid_provider_response", "GitHub omitted search results.", 502);
        const matches = result.items.map((raw) => object(raw, "PR search result")).filter((entry) => typeof entry.body === "string" && entry.body.includes(marker));
        if (matches.length > 1) throw new FactoryError("reconciliation_required", "Multiple PRs match the dispatch operation.", 409);
        return matches[0] ? { submitted: true, provider: "github", marker, prNumber: Number(matches[0].number) } : null;
    }

    async inspectPr(repo: RepositoryTarget, number: number, itemId: string): Promise<PullRequest> {
        if (!Number.isSafeInteger(number) || number <= 0) throw new FactoryError("invalid_pr", "A positive PR number is required.");
        if (repo.provider === "github") {
            const pr = object(await this.github(repo, `repos/${repo.slug}/pulls/${number}`), "GitHub PR");
            const head = object(pr.head, "PR head");
            const base = object(pr.base, "PR base");
            if (base.ref !== repo.baseBranch) throw new FactoryError("wrong_base_branch", "The linked PR targets a different base branch.", 409);
            const checks = object(await this.github(repo, `repos/${repo.slug}/commits/${text(head.sha, "Head SHA")}/status`), "Commit checks");
            const runs = object(await this.github(repo, `repos/${repo.slug}/commits/${String(head.sha)}/check-runs?per_page=100`), "Check runs");
            if (!Array.isArray(runs.check_runs)) throw new FactoryError("invalid_provider_response", "GitHub omitted check-run results.", 502);
            const checkRuns = runs.check_runs.map((entry) => object(entry, "Check run"));
            const hasLegacy = typeof checks.total_count === "number" && checks.total_count > 0;
            const failed = (hasLegacy && ["failure", "error"].includes(String(checks.state))) ||
                checkRuns.some((entry) => ["failure", "timed_out", "cancelled", "action_required"].includes(String(entry.conclusion)));
            const passing = (hasLegacy || checkRuns.length > 0) && (!hasLegacy || checks.state === "success") &&
                checkRuns.every((entry) => entry.status === "completed" && ["success", "skipped", "neutral"].includes(String(entry.conclusion))) &&
                (hasLegacy || checkRuns.some((entry) => entry.conclusion === "success"));
            const url = new URL(text(pr.html_url, "PR URL"));
            if (url.hostname !== repo.hostname || url.pathname.replace(/\/$/, "").toLowerCase() !== `/${repo.slug}/pull/${number}`.toLowerCase()) throw new FactoryError("invalid_provider_response", "PR belongs to a different configured target.", 502);
            return {
                key: itemId, repository: repo.slug, number, url: url.href,
                status: pr.merged_at ? "merged" : pr.state === "closed" ? "closed" : "open",
                head: String(head.sha), title: text(pr.title, "PR title"),
                baseBranch: repo.baseBranch,
                workItemIds: typeof pr.body === "string" ? [...pr.body.matchAll(/\bAB#(\d+)\b/g)].map((match) => Number(match[1])) : [],
                checks: Number(runs.total_count) > checkRuns.length ? "unknown" : failed ? "failing" : passing ? "passing" : checkRuns.length || hasLegacy ? "pending" : "unknown",
            };
        }
        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
        const pr = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}`), "ADO PR");
        if (pr.pullRequestId !== number) throw new FactoryError("invalid_provider_response", "ADO returned a different PR.", 502);
        const commit = object(pr.lastMergeSourceCommit, "ADO source commit");
        if (pr.targetRefName !== `refs/heads/${repo.baseBranch}`) throw new FactoryError("wrong_base_branch", "The linked ADO PR targets a different branch.", 409);
        const links = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/workitems`), "PR work items");
        const policies = object(await this.ado(organization, project, `policy/evaluations?artifactId=${encodeURIComponent(`vstfs:///CodeReview/CodeReviewId/${String(object(pr.repository, "Repository").project ? object(object(pr.repository, "Repository").project, "Project").id : project)}/${number}`)}`), "ADO policies");
        const evaluations = Array.isArray(policies.value) ? policies.value.map((entry) => object(entry, "Policy")) : [];
        return {
            key: itemId, repository: repo.slug, number,
            url: `https://dev.azure.com/${segment(organization)}/${segment(project)}/_git/${segment(repository)}/pullrequest/${number}`,
            status: pr.status === "completed" ? "merged" : pr.status === "abandoned" ? "closed" : "open",
            head: text(commit.commitId, "Source commit"), title: text(pr.title, "PR title"),
            baseBranch: repo.baseBranch,
            workItemIds: Array.isArray(links.value) ? links.value.map((entry) => Number(object(entry, "Work item reference").id)).filter(Number.isSafeInteger) : [],
            checks: !evaluations.length ? "unknown" : evaluations.some((entry) => entry.status === "rejected") ? "failing"
                : evaluations.every((entry) => entry.status === "approved" || entry.status === "notApplicable") ? "passing" : "pending",
        };
    }

    async findPr(config: Configuration, item: PlannedItem, workItem: WorkItem): Promise<PullRequest | null> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Repository mapping missing.");
        if (repo.provider === "github") {
            const result = object(await this.github(repo, `search/issues?q=${encodeURIComponent(`repo:${repo.slug} is:pr "AB#${workItem.adoId}" in:body`)}`), "PR search");
            if (!Array.isArray(result.items)) throw new FactoryError("invalid_provider_response", "PR search results missing.", 502);
            const boundary = new RegExp(`\\bAB#${workItem.adoId}(?!\\d)`);
            const matches = result.items.map((entry) => object(entry, "PR")).filter((entry) => typeof entry.body === "string" && boundary.test(entry.body));
            if (matches.length > 1) throw new FactoryError("ambiguous_pr", "Multiple PRs link to this work item; reconcile its canonical PR explicitly.", 409);
            return matches[0] ? this.inspectPr(repo, Number(matches[0].number), item.id) : null;
        }
        const wi = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${workItem.adoId}?$expand=relations`), "ADO work item");
        const relations = Array.isArray(wi.relations) ? wi.relations.map((entry) => object(entry, "Relation")) : [];
        const ids = relations.filter((entry) => typeof entry.url === "string" && entry.url.startsWith("vstfs:///Git/PullRequestId/"))
            .map((entry) => decodeURIComponent(String(entry.url).split("/").at(-1) || "").split("/").at(-1));
        if (ids.length > 1) throw new FactoryError("ambiguous_pr", "Multiple PR artifact links require explicit reconciliation.", 409);
        return ids[0] ? this.inspectPr(repo, Number(ids[0]), item.id) : null;
    }

    async diff(repo: RepositoryTarget, number: number): Promise<string> {
        if (repo.provider === "github") {
            const token = await this.githubToken(repo);
            return this.runner.run("gh", ["pr", "diff", String(number), "--repo", `${repo.hostname}/${repo.slug}`], {
                env: githubCliEnv(repo.hostname, token),
            });
        }
        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping missing.");
        const iterations = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/iterations`), "PR iterations");
        if (!Array.isArray(iterations.value) || !iterations.value.length) throw new FactoryError("provider_unavailable", "ADO has no inspectable PR iteration.", 502);
        const iteration = object(iterations.value.at(-1), "PR iteration");
        return JSON.stringify(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/iterations/${Number(iteration.id)}/changes`));
    }

    private async verifyRepositoryTarget(key: string, repo: RepositoryTarget): Promise<Json> {
        if (repo.provider === "github") {
            const identity = object(await this.github(repo, "user"), "GitHub identity");
            if (typeof identity.login !== "string" || identity.login.toLowerCase() !== repo.account.toLowerCase()) {
                throw new FactoryError("wrong_repository", `GitHub authenticated as ${typeof identity.login === "string" ? identity.login : "unknown"}, not configured account ${repo.account}.`, 409);
            }
            const metadata = object(await this.github(repo, `repos/${repo.slug}`), "GitHub repository");
            if (typeof metadata.full_name !== "string" || metadata.full_name.toLowerCase() !== repo.slug.toLowerCase()) throw new FactoryError("wrong_repository", "GitHub resolved a different repository.", 409);
            await this.github(repo, `repos/${repo.slug}/branches/${segment(repo.baseBranch)}`);
            return { key, provider: repo.provider, hostname: repo.hostname, account: identity.login, verified: metadata.full_name.toLowerCase() === repo.slug.toLowerCase() };
        }
        const [org, project, name] = repo.slug.split("/");
        if (!org || !project || !name) throw new FactoryError("invalid_configuration", "ADO target missing.");
        const refs = object(await this.ado(org, project, `git/repositories/${segment(name)}/refs?filter=${encodeURIComponent(`heads/${repo.baseBranch}`)}`), "ADO branches");
        if (!Array.isArray(refs.value) || !refs.value.some((entry) => object(entry, "Branch").name === `refs/heads/${repo.baseBranch}`)) throw new FactoryError("branch_not_found", "The configured ADO base branch does not exist.", 409);
        return { key, provider: repo.provider, hostname: repo.hostname, verified: true };
    }

    async preflight(config: Configuration): Promise<Json> {
        const repositories: Json[] = [];
        for (const [key, repo] of Object.entries(config.repositories)) {
            repositories.push(await this.verifyRepositoryTarget(key, repo));
        }
        const referenceRepositories: Json[] = [];
        for (const [key, repo] of Object.entries(config.referenceRepositories || {})) {
            referenceRepositories.push(await this.verifyRepositoryTarget(key, repo));
        }
        const rawBoards = config.boards.organization && config.boards.project && config.boards.workItemType
            ? object(await this.ado(config.boards.organization, config.boards.project, `wit/workitemtypes/${segment(config.boards.workItemType)}`), "ADO work item type")
            : null;
        const boards = rawBoards
            ? {
                verified: true,
                organization: config.boards.organization,
                project: config.boards.project,
                workItemType: config.boards.workItemType,
                ...(typeof rawBoards.name === "string" ? { name: rawBoards.name } : {}),
                ...(typeof rawBoards.referenceName === "string" ? { referenceName: rawBoards.referenceName } : {}),
                ...(typeof rawBoards.isDisabled === "boolean" ? { isDisabled: rawBoards.isDisabled } : {}),
            }
            : { status: "not_configured", reason: "Repository-only design is available; configure board targets before backlog creation." };
        return jsonValue({
            repositories,
            ...(referenceRepositories.length ? { referenceRepositories } : {}),
            boards,
        });
    }

    async findFeedback(repo: RepositoryTarget, number: number, marker: string): Promise<Json | null> {
        let comments: Record<string, unknown>[];
        if (repo.provider === "github") {
            const token = await this.githubToken(repo);
            const pages: unknown = JSON.parse(await this.runner.run("gh", [
                "api", "--hostname", repo.hostname, "--paginate", "--slurp", `repos/${repo.slug}/issues/${number}/comments?per_page=100`,
            ], { env: githubCliEnv(repo.hostname, token) }));
            if (!Array.isArray(pages) || !pages.every(Array.isArray)) throw new FactoryError("invalid_provider_response", "Comment pagination was incomplete.", 502);
            comments = pages.flat().map((entry) => object(entry, "PR comment"));
        } else {
            const [org, project, name] = repo.slug.split("/");
            if (!org || !project || !name) throw new FactoryError("invalid_configuration", "ADO target missing.");
            const result = object(await this.ado(org, project, `git/repositories/${segment(name)}/pullrequests/${number}/threads`), "ADO threads");
            if (!Array.isArray(result.value)) throw new FactoryError("invalid_provider_response", "ADO omitted review threads.", 502);
            comments = result.value.flatMap((entry) => {
                const thread = object(entry, "Thread");
                return Array.isArray(thread.comments) ? thread.comments.map((comment) => object(comment, "Thread comment")) : [];
            });
        }
        const found = comments.filter((comment) => String(comment.body ?? comment.content ?? "").includes(marker));
        if (found.length > 1) throw new FactoryError("reconciliation_required", "Multiple comments match this operation; reconcile manually.", 409);
        return found[0] ? { commentId: String(found[0].id), reconciled: true } : null;
    }

    async postFeedback(repo: RepositoryTarget, number: number, body: string): Promise<Json> {
        if (repo.provider === "github") {
            const comment = object(await this.github(repo, `repos/${repo.slug}/issues/${number}/comments`, "POST", { body }), "Posted comment");
            return { commentId: String(comment.id), url: text(comment.html_url, "Comment URL") };
        }
        const [org, project, name] = repo.slug.split("/");
        if (!org || !project || !name) throw new FactoryError("invalid_configuration", "ADO target missing.");
        const result = object(await this.ado(org, project, `git/repositories/${segment(name)}/pullrequests/${number}/threads`, "POST", {
            comments: [{ parentCommentId: 0, content: body, commentType: 1 }], status: 1,
        }), "Posted thread");
        return { threadId: String(result.id), posted: true };
    }

    async review(repo: RepositoryTarget, number: number): Promise<Json> {
        if (repo.provider === "github") {
            const token = await this.githubToken(repo);
            const result: Record<string, Json> = {};
            for (const [kind, endpoint] of Object.entries({
                inlineComments: `repos/${repo.slug}/pulls/${number}/comments`,
                reviews: `repos/${repo.slug}/pulls/${number}/reviews`,
                conversation: `repos/${repo.slug}/issues/${number}/comments`,
            })) {
                const pages: unknown = JSON.parse(await this.runner.run("gh", ["api", "--hostname", repo.hostname, "--paginate", "--slurp", `${endpoint}?per_page=100`],
                    { env: githubCliEnv(repo.hostname, token) }));
                if (!Array.isArray(pages) || !pages.every(Array.isArray)) throw new FactoryError("invalid_provider_response", "Review pagination was incomplete.", 502);
                result[kind] = jsonValue(pages.flat());
            }
            return result;
        }
        const [org, project, name] = repo.slug.split("/");
        if (!org || !project || !name) throw new FactoryError("invalid_configuration", "ADO target missing.");
        return jsonValue({
            threads: await this.ado(org, project, `git/repositories/${segment(name)}/pullrequests/${number}/threads`),
            reviewers: object(await this.ado(org, project, `git/repositories/${segment(name)}/pullrequests/${number}`), "PR").reviewers,
        });
    }
}
