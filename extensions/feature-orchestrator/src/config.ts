//  Copyright (c) Microsoft Corporation.
//  All rights reserved.
//
//  This code is licensed under the MIT License.
//
//  Permission is hereby granted, free of charge, to any person obtaining a copy
//  of this software and associated documentation files(the "Software"), to deal
//  in the Software without restriction, including without limitation the rights
//  to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
//  copies of the Software, and to permit persons to whom the Software is
//  furnished to do so, subject to the following conditions :
//
//  The above copyright notice and this permission notice shall be included in
//  all copies or substantial portions of the Software.
//
//  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
//  THE SOFTWARE.

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';

interface RepositoryConfig {
    slug?: string;
    host?: string;
}

interface ModuleConfig {
    repo?: string;
}

interface OrchestratorConfig {
    project?: { name?: string };
    repositories?: Record<string, RepositoryConfig>;
    modules?: Record<string, ModuleConfig>;
    ado?: { org?: string; project?: string };
    design?: { docsPath?: string };
    github?: { configFile?: string };
}

let cachedConfig: OrchestratorConfig | null = null;

export function getWorkspaceRoot(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

export function getOrchestratorConfig(): OrchestratorConfig {
    if (cachedConfig) {
        return cachedConfig;
    }

    const workspaceRoot = getWorkspaceRoot();
    if (!workspaceRoot) {
        cachedConfig = {};
        return cachedConfig;
    }

    const configPath = path.join(workspaceRoot, '.github', 'orchestrator-config.json');
    if (!fs.existsSync(configPath)) {
        cachedConfig = {};
        return cachedConfig;
    }

    try {
        cachedConfig = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    } catch {
        cachedConfig = {};
    }

    return cachedConfig || {};
}

export function getAdoConfig(): { org: string; project: string } {
    const config = getOrchestratorConfig();
    return {
        org: config.ado?.org || '',
        project: config.ado?.project || '',
    };
}

export function getAdoOrgUrl(): string {
    const ado = getAdoConfig();
    return ado.org ? `https://dev.azure.com/${ado.org}` : '';
}

export function getAdoWorkItemUrl(id: string | number): string {
    const ado = getAdoConfig();
    if (!ado.org || !ado.project) {
        return '';
    }
    return `https://dev.azure.com/${ado.org}/${ado.project}/_workitems/edit/${id}`;
}

export function getDesignDocsPath(): string {
    const docsPath = getOrchestratorConfig().design?.docsPath;
    return docsPath || 'design-docs/';
}

export function getStateFilePath(): string {
    const legacyPath = path.join(os.homedir(), '.android-auth-orchestrator', 'state.json');

    const workspaceRoot = getWorkspaceRoot();
    const config = getOrchestratorConfig();
    const projectName = config.project?.name || (workspaceRoot ? path.basename(workspaceRoot) : 'workspace');
    const projectSlug = projectName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'workspace';

    const genericPath = path.join(os.homedir(), '.feature-orchestrator', projectSlug, 'state.json');

    if (fs.existsSync(legacyPath) && !fs.existsSync(genericPath)) {
        return legacyPath;
    }

    return genericPath;
}

export function ensureStateDirectoryExists(): void {
    const stateDir = path.dirname(getStateFilePath());
    if (!fs.existsSync(stateDir)) {
        fs.mkdirSync(stateDir, { recursive: true });
    }
}

export function getRepositoryMap(): Record<string, string> {
    const repos = getOrchestratorConfig().repositories || {};
    const result: Record<string, string> = {};

    for (const [key, repo] of Object.entries(repos)) {
        if (repo.slug) {
            result[key] = repo.slug;
        }
    }

    return result;
}

export function getGithubRepositories(): Array<{ key: string; slug: string }> {
    const repos = getOrchestratorConfig().repositories || {};
    const result: Array<{ key: string; slug: string }> = [];

    for (const [key, repo] of Object.entries(repos)) {
        if ((repo.host || '').toLowerCase() === 'github' && repo.slug) {
            result.push({ key, slug: repo.slug });
        }
    }

    return result;
}

export function getRepoSlugForModule(moduleOrRepo: string): string | undefined {
    if (!moduleOrRepo) {
        return undefined;
    }

    if (moduleOrRepo.includes('/')) {
        return moduleOrRepo;
    }

    const config = getOrchestratorConfig();
    const repos = config.repositories || {};

    if (repos[moduleOrRepo]?.slug) {
        return repos[moduleOrRepo].slug;
    }

    const moduleConfig = config.modules?.[moduleOrRepo];
    if (moduleConfig?.repo && repos[moduleConfig.repo]?.slug) {
        return repos[moduleConfig.repo].slug;
    }

    return undefined;
}

export function getModuleRepoChoices(): Array<{ label: string; description: string; value: string }> {
    const config = getOrchestratorConfig();
    const repos = config.repositories || {};
    const modules = config.modules || {};

    const seen = new Set<string>();
    const choices: Array<{ label: string; description: string; value: string }> = [];

    for (const [moduleName, moduleConfig] of Object.entries(modules)) {
        const repoKey = moduleConfig.repo || moduleName;
        const repoSlug = repos[repoKey]?.slug;
        if (!repoSlug || seen.has(moduleName)) {
            continue;
        }

        seen.add(moduleName);
        choices.push({
            label: moduleName,
            description: repoSlug,
            value: repoSlug,
        });
    }

    if (choices.length > 0) {
        return choices;
    }

    for (const [repoKey, repoConfig] of Object.entries(repos)) {
        if (repoConfig.slug) {
            choices.push({
                label: repoKey,
                description: repoConfig.slug,
                value: repoConfig.slug,
            });
        }
    }

    return choices;
}

export function getDeveloperLocalConfigPath(): string | undefined {
    const workspaceRoot = getWorkspaceRoot();
    if (!workspaceRoot) {
        return undefined;
    }

    const configured = getOrchestratorConfig().github?.configFile;
    if (configured) {
        return path.join(workspaceRoot, configured);
    }

    return path.join(workspaceRoot, '.github', 'developer-local.json');
}
