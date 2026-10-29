// Copyright (c) Microsoft Corporation. All rights reserved.

import { existsSync, mkdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FactoryError, object, text } from "../core/contracts.ts";
import { jsonValue, type Configuration, type Json, type PlannedItem, type PullRequest, type RepositoryTarget, type WorkItem } from "../core/lifecycle-contracts.ts";
import type { LocalImplementationInput, Provider } from "./provider.ts";
import { systemRunner, type CommandRunner } from "./cli.ts";

const defaultWorkspaceRoot = () => fileURLToPath(new URL("../../../", import.meta.url));

const escapeHtml = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const segment = (value: string) => encodeURIComponent(value);
const wiql = (value: string) => value.replaceAll("'", "''");

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
        const value = await this.runner.run("gh", ["auth", "token", "--hostname", repo.hostname, "--user", repo.account]);
        this.githubTokens.set(key, { value, expiresAt: Date.now() + 4 * 60_000 });
        return value;
    }

    private async adoToken(): Promise<string> {
        if (this.adoTokenCache && Date.now() < this.adoTokenCache.expiresAt) return this.adoTokenCache.value;
        const value = await this.runner.run("az", ["account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798", "--query", "accessToken", "-o", "tsv"]);
        this.adoTokenCache = { value, expiresAt: Date.now() + 4 * 60_000 };
        return value;
    }

    private async github(repo: RepositoryTarget, endpoint: string, method = "GET", data?: unknown): Promise<unknown> {
        const token = await this.githubToken(repo);
        let result: string;
        try {
            result = await this.runner.run("gh", [
                "api", "--hostname", repo.hostname, "--method", method, endpoint,
                ...(data === undefined ? [] : ["--input", "-"]),
            ], {
                env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token },
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

    private async ado(organization: string, project: string, suffix: string, method = "GET", body?: unknown, patch = false): Promise<unknown> {
        const tokenText = await this.adoToken();
        const url = `https://dev.azure.com/${segment(organization)}/${segment(project)}/_apis/${suffix}${suffix.includes("?") ? "&" : "?"}api-version=7.1`;
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
            "System.AreaPath": config.boards.areaPath, "System.IterationPath": config.boards.iterationPath,
            "System.Tags": `ai-generated; copilot-agent-ready; ${marker}`,
        };
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
                env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token },
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
        await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${workItem.adoId}`, "PATCH", [
            { op: "test", path: "/rev", value: current.rev },
            { op: "add", path: "/fields/System.Tags", value: tags.join("; ") },
            { op: "add", path: "/fields/System.AssignedTo", value: "GitHub Copilot" },
            { op: "add", path: "/fields/System.State", value: "Active" },
        ], true);
        return { submitted: true, provider: "ado", marker, adoId: workItem.adoId };
    }

    async publishLocal(config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput): Promise<Json> {
        const repo = config.repositories[item.repository];
        if (!repo) throw new FactoryError("invalid_configuration", "Dispatch repository is missing.");
        const workspace = process.env.SOFTWARE_FACTORY_WORKSPACE || defaultWorkspaceRoot();
        const repoRoot = path.resolve(workspace, repo.localPath);
        if (!existsSync(repoRoot)) throw new FactoryError("local_repository_missing", `Configured localPath for ${item.repository} does not exist: ${repoRoot}.`, 409);
        const canonicalRoot = realpathSync(repoRoot);
        const normalize = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
        const normalizedRoot = normalize(canonicalRoot);
        if (!implementation.files.length) throw new FactoryError("invalid_implementation", "Local implementation has no file changes to publish.");
        for (const file of implementation.files) {
            const target = path.resolve(canonicalRoot, file.path);
            if (normalize(target) === normalizedRoot || !normalize(target).startsWith(normalizedRoot + path.sep)) {
                throw new FactoryError("invalid_implementation", `File path escapes repository root: ${file.path}.`, 403);
            }
            if (file.action === "delete") {
                if (existsSync(target)) rmSync(target, { force: true });
            } else {
                mkdirSync(path.dirname(target), { recursive: true });
                const resolvedParent = normalize(realpathSync(path.dirname(target)));
                if (resolvedParent !== normalizedRoot && !resolvedParent.startsWith(normalizedRoot + path.sep)) {
                    throw new FactoryError("invalid_implementation", `Symlink parent escapes repository root: ${file.path}.`, 403);
                }
                writeFileSync(target, file.content, "utf8");
            }
        }
        const branch = text(implementation.branch, "Implementation branch");
        if (/[\s~^:?*[\]\\]/.test(branch) || branch.includes("..") || branch.startsWith("-")) {
            throw new FactoryError("invalid_implementation", "Invalid implementation branch name.");
        }
        await this.runner.run("git", ["checkout", "-B", branch], { cwd: canonicalRoot });
        await this.runner.run("git", ["add", "--", ...implementation.files.map((file) => file.path)], { cwd: canonicalRoot });
        const commitMessage = [
            item.title,
            "",
            implementation.summary,
            "",
            `Fixes AB#${workItem.adoId}`,
            `Software Factory operation: ${marker}`,
            "",
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
        ].join("\n");
        await this.runner.run("git", ["commit", "-m", commitMessage], { cwd: canonicalRoot });
        const prBody = [
            implementation.summary,
            "",
            "### Acceptance Criteria",
            ...item.acceptanceCriteria.map((criterion) => `- [x] ${criterion}`),
            "",
            "### Expected Tests",
            ...item.tests.map((testName) => `- ${testName}`),
            "",
            "### Independent Code Review",
            `- Passed independent AI code review in ${implementation.roundsCompleted} of ${implementation.maxRounds} round(s) (mode: local).`,
            "",
            `Fixes AB#${workItem.adoId}`,
            `Software Factory operation: ${marker}`,
        ].join("\n");

        if (repo.provider === "github") {
            await this.github(repo, `repos/${repo.slug}/branches/${segment(repo.baseBranch)}`);
            const token = await this.runner.run("gh", ["auth", "token", "--hostname", repo.hostname, "--user", repo.account]);
            const basic = Buffer.from(`x-access-token:${token}`).toString("base64");
            await this.runner.run("git", [
                "-c", `http.https://${repo.hostname}/.extraheader=AUTHORIZATION: basic ${basic}`,
                "push", "-u", "origin", branch, "--force-with-lease",
            ], {
                cwd: canonicalRoot,
                env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token },
            });
            const created = object(await this.github(repo, `repos/${repo.slug}/pulls`, "POST", {
                title: item.title,
                head: branch,
                base: repo.baseBranch,
                body: prBody,
            }), "Created GitHub PR");
            return {
                submitted: true, mode: "local", provider: "github", marker, branch,
                prNumber: Number(created.number),
                url: text(created.html_url, "PR URL"),
                roundsCompleted: implementation.roundsCompleted,
                maxRounds: implementation.maxRounds,
            };
        }

        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping is incomplete.");
        const tokenText = await this.runner.run("az", ["account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798", "--query", "accessToken", "-o", "tsv"]);
        await this.runner.run("git", [
            "-c", `http.https://dev.azure.com/.extraheader=AUTHORIZATION: bearer ${tokenText}`,
            "push", "-u", "origin", branch, "--force-with-lease",
        ], { cwd: canonicalRoot });
        const created = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests`, "POST", {
            sourceRefName: `refs/heads/${branch}`,
            targetRefName: `refs/heads/${repo.baseBranch}`,
            title: item.title,
            description: prBody,
            workItemRefs: [{ id: String(workItem.adoId) }],
        }), "Created ADO PR");
        return {
            submitted: true, mode: "local", provider: "ado", marker, branch,
            prNumber: Number(created.pullRequestId),
            adoId: workItem.adoId,
            roundsCompleted: implementation.roundsCompleted,
            maxRounds: implementation.maxRounds,
        };
    }

    async markDispatched(config: Configuration, item: WorkItem, marker: string): Promise<Json> {
        const current = object(await this.ado(config.boards.organization, config.boards.project, `wit/workitems/${item.adoId}`), "ADO work item");
        if (!Number.isSafeInteger(current.rev)) throw new FactoryError("invalid_provider_response", "ADO omitted the revision required for status repair.", 502);
        const fields = object(current.fields, "Fields");
        const tags = typeof fields["System.Tags"] === "string" ? fields["System.Tags"].split(";").map((tag) => tag.trim()).filter(Boolean) : [];
        for (const tag of ["agent-dispatched", marker]) if (!tags.includes(tag)) tags.push(tag);
        const state = ["Resolved", "Closed", "Done"].includes(String(fields["System.State"])) ? String(fields["System.State"]) : "Active";
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
                checkRuns.every((entry) => entry.status === "completed" && entry.conclusion === "success");
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
                env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token },
            });
        }
        const [organization, project, repository] = repo.slug.split("/");
        if (!organization || !project || !repository) throw new FactoryError("invalid_configuration", "ADO repository mapping missing.");
        const iterations = object(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/iterations`), "PR iterations");
        if (!Array.isArray(iterations.value) || !iterations.value.length) throw new FactoryError("provider_unavailable", "ADO has no inspectable PR iteration.", 502);
        const iteration = object(iterations.value.at(-1), "PR iteration");
        return JSON.stringify(await this.ado(organization, project, `git/repositories/${segment(repository)}/pullrequests/${number}/iterations/${Number(iteration.id)}/changes`));
    }

    async preflight(config: Configuration): Promise<Json> {
        const repositories: Json[] = [];
        for (const [key, repo] of Object.entries(config.repositories)) {
            if (repo.provider === "github") {
                const metadata = object(await this.github(repo, `repos/${repo.slug}`), "GitHub repository");
                if (typeof metadata.full_name !== "string" || metadata.full_name.toLowerCase() !== repo.slug.toLowerCase()) throw new FactoryError("wrong_repository", "GitHub resolved a different repository.", 409);
                await this.github(repo, `repos/${repo.slug}/branches/${segment(repo.baseBranch)}`);
                repositories.push({ key, provider: repo.provider, hostname: repo.hostname, verified: metadata.full_name === repo.slug });
            } else {
                const [org, project, name] = repo.slug.split("/");
                if (!org || !project || !name) throw new FactoryError("invalid_configuration", "ADO target missing.");
                const refs = object(await this.ado(org, project, `git/repositories/${segment(name)}/refs?filter=${encodeURIComponent(`heads/${repo.baseBranch}`)}`), "ADO branches");
                if (!Array.isArray(refs.value) || !refs.value.some((entry) => object(entry, "Branch").name === `refs/heads/${repo.baseBranch}`)) throw new FactoryError("branch_not_found", "The configured ADO base branch does not exist.", 409);
                repositories.push({ key, provider: repo.provider, hostname: repo.hostname, verified: true });
            }
        }
        const boards = config.boards.organization && config.boards.project && config.boards.workItemType
            ? await this.ado(config.boards.organization, config.boards.project, `wit/workitemtypes/${segment(config.boards.workItemType)}`)
            : { status: "not_configured", reason: "Repository-only design is available; configure board targets before backlog creation." };
        return jsonValue({ repositories, boards });
    }

    async findFeedback(repo: RepositoryTarget, number: number, marker: string): Promise<Json | null> {
        let comments: Record<string, unknown>[];
        if (repo.provider === "github") {
            const token = await this.githubToken(repo);
            const pages: unknown = JSON.parse(await this.runner.run("gh", [
                "api", "--hostname", repo.hostname, "--paginate", "--slurp", `repos/${repo.slug}/issues/${number}/comments?per_page=100`,
            ], { env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token } }));
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
                    { env: { ...process.env, GH_HOST: repo.hostname, GH_TOKEN: token, GH_ENTERPRISE_TOKEN: token } }));
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
