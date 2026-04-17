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
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { runCommand, switchGhAccount } from './tools';
import { ensureStateDirectoryExists, getGithubRepositories, getStateFilePath } from './config';

interface OpenPr {
    repo: string;
    number: number;
    title: string;
    url: string;
    isDraft: boolean;
    author: string;
    createdAt: string;
}

interface FeatureState {
    id: string;
    name: string;
    prompt: string;
    step: string;
    designDocPath?: string;
    designPrUrl?: string;
    pbis: Array<{ adoId: number; title: string; targetRepo: string; status: string }>;
    agentSessions: Array<{ repo: string; prNumber: number; prUrl: string; status: string }>;
    startedAt: number;
    updatedAt: number;
    pendingAction?: {
        completedAgent: string;
        nextStep: string;
        timestamp: number;
    };
}

interface OrchestratorState {
    version: number;
    features: FeatureState[];
    lastUpdated: number;
}

export class DashboardViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'orchestrator.dashboard';

    private view?: vscode.WebviewView;
    private refreshInterval?: NodeJS.Timeout;
    private fileWatcher?: vscode.FileSystemWatcher;
    private cachedOpenPrs: OpenPr[] = [];
    private prsFetching = false;
    private prsLastFetched = 0;
    private prsEverFetched = false;

    constructor(private readonly context: vscode.ExtensionContext) {}

    resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ): void {
        this.view = webviewView;
        webviewView.webview.options = { enableScripts: true };

        webviewView.webview.onDidReceiveMessage(async (message) => {
            switch (message.command) {
                case 'refresh':
                    await this.refresh();
                    this.fetchOpenPrsInBackground();
                    break;
                case 'openUrl':
                    vscode.env.openExternal(vscode.Uri.parse(message.url));
                    break;
                case 'openAgent': {
                    // Open a new chat with a prompt file
                    await vscode.commands.executeCommand('workbench.action.chat.newChat');
                    const query = `/${message.promptFile || 'feature-continue'} ${message.context || ''}`.trim();
                    vscode.commands.executeCommand('workbench.action.chat.open', { query });
                    break;
                }
                case 'openFeatureDetail':
                    vscode.commands.executeCommand('orchestrator.openFeatureDetail', message.featureId);
                    break;
                case 'removeFeature':
                    this.removeFeatureFromState(message.featureId);
                    await this.refresh();
                    break;
            }
        });

        this.refresh();

        // Fetch open PRs in background on initial load
        this.fetchOpenPrsInBackground();

        // Auto-refresh every 30 seconds (state only, not PRs)
        this.refreshInterval = setInterval(() => this.refresh(), 30000);

        // Watch state file for changes (hooks write to it)
        const stateFilePath = this.getStateFilePath();
        if (stateFilePath) {
            const stateDir = path.dirname(stateFilePath);
            // Ensure the directory exists so the watcher can be set up
            ensureStateDirectoryExists();
            const pattern = new vscode.RelativePattern(
                vscode.Uri.file(stateDir),
                'state.json'
            );
            this.fileWatcher = vscode.workspace.createFileSystemWatcher(pattern);
            this.fileWatcher.onDidChange(() => {
                this.refresh();
                this.checkForPendingAction();
            });
            this.fileWatcher.onDidCreate(() => {
                this.refresh();
                this.checkForPendingAction();
            });
        }

        webviewView.onDidDispose(() => {
            if (this.refreshInterval) { clearInterval(this.refreshInterval); }
            this.fileWatcher?.dispose();
        });
    }

    async refresh(): Promise<void> {
        if (!this.view) { return; }

        const state = this.readStateFile();

        // Auto-completion detection: check all non-done features
        const completedStates = new Set(['done', 'resolved', 'closed', 'removed']);
        let stateChanged = false;
        for (const feature of state.features) {
            if (feature.step === 'done') { continue; }
            const allPbis = (feature as any).artifacts?.pbis || [];
            const allPrs = (feature as any).artifacts?.agentPrs || feature.agentSessions || [];

            let shouldComplete = false;
            if (allPbis.length > 0) {
                shouldComplete = allPbis.every((p: any) => completedStates.has((p.status || '').toLowerCase()));
            } else if (allPrs.length > 0) {
                shouldComplete = allPrs.every((pr: any) => (pr.status || '').toLowerCase() === 'merged');
            }

            if (shouldComplete) {
                feature.step = 'done';
                feature.updatedAt = Date.now();
                stateChanged = true;
                vscode.window.showInformationMessage(
                    `🎉 Feature "${feature.name}" is complete! All work items are resolved.`,
                    'View Feature'
                ).then(selection => {
                    if (selection === 'View Feature') {
                        vscode.commands.executeCommand('orchestrator.openFeatureDetail', feature.id);
                    }
                });
            }
        }
        if (stateChanged) {
            const filePath = this.getStateFilePath();
            if (filePath) {
                state.lastUpdated = Date.now();
                fs.writeFileSync(filePath, JSON.stringify(state, null, 2), 'utf-8');
            }
        }

        this.view.webview.html = this.getHtml(state);
    }

    /**
     * Check if a subagent just completed and show a clickable notification
     * with a button to proceed to the next pipeline step.
     *
     * The SubagentStop hook writes a `pendingAction` field to the active feature
     * in orchestrator-state.json. We consume it here and clear it after showing
     * the notification to avoid duplicate prompts.
     */
    private checkForPendingAction(): void {
        const state = this.readStateFile();
        const feature = state.features?.find(f => f.pendingAction);
        if (!feature?.pendingAction) { return; }

        const action = feature.pendingAction;
        const staleMs = Date.now() - (action.timestamp || 0);
        if (staleMs > 60000) {
            // Ignore stale actions older than 60 seconds
            return;
        }

        // Map next step to a notification message + button label + chat prompt
        const stepActions: Record<string, { message: string; button: string; promptFile: string }> = {
            'design_review': {
                message: `✅ Design spec written for "${feature.name}". Ready to plan PBIs.`,
                button: '📋 Plan PBIs',
                promptFile: 'feature-plan',
            },
            'plan_review': {
                message: `✅ PBI plan created for "${feature.name}". Review and create in ADO.`,
                button: '✅ Create in ADO',
                promptFile: 'feature-backlog',
            },
            'backlog_review': {
                message: `✅ PBIs backlogged in ADO for "${feature.name}". Ready to dispatch.`,
                button: '🚀 Dispatch to Agent',
                promptFile: 'feature-dispatch',
            },
            'monitoring': {
                message: `✅ PBIs dispatched for "${feature.name}". Agents are working.`,
                button: '📡 Check Status',
                promptFile: 'feature-status',
            },
        };

        const cfg = stepActions[action.nextStep];
        if (!cfg) { return; }

        // Show VS Code notification with clickable button
        vscode.window.showInformationMessage(cfg.message, cfg.button).then(async selection => {
            if (selection === cfg.button) {
                await vscode.commands.executeCommand('workbench.action.chat.newChat');
                vscode.commands.executeCommand('workbench.action.chat.open', {
                    query: `/${cfg.promptFile} Feature: "${feature.name}"`,
                });
            }
        });

        // Clear the pendingAction so we don't show duplicate notifications
        delete feature.pendingAction;
        const filePath = this.getStateFilePath();
        if (filePath) {
            state.lastUpdated = Date.now();
            fs.writeFileSync(filePath, JSON.stringify(state, null, 2), 'utf-8');
        }
    }

    private getStateFilePath(): string | null {
        return getStateFilePath();
    }

    private readStateFile(): OrchestratorState {
        const filePath = this.getStateFilePath();
        if (!filePath || !fs.existsSync(filePath)) {
            return { version: 1, features: [], lastUpdated: 0 };
        }
        try {
            return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        } catch {
            return { version: 1, features: [], lastUpdated: 0 };
        }
    }

    private removeFeatureFromState(featureId: string): void {
        const filePath = this.getStateFilePath();
        if (!filePath) { return; }
        const state = this.readStateFile();
        state.features = state.features.filter(f => f.id !== featureId);
        state.lastUpdated = Date.now();
        fs.writeFileSync(filePath, JSON.stringify(state, null, 2), 'utf-8');
    }

    private async fetchAllAgentPrs(): Promise<void> {
        // No-op — Agent PRs are tracked per-feature in artifacts
    }

    /**
     * Fetch open PRs authored by the user or by copilot-swe-agent across all repos.
     * Runs in background and updates the cached list, then re-renders.
     */
    private async fetchOpenPrsInBackground(): Promise<void> {
        // Don't double-fetch
        if (this.prsFetching) { return; }
        this.prsFetching = true;

        // Re-render immediately to show loading state
        await this.refresh();

        try {
            const repos = getGithubRepositories().map((repo) => ({
                slug: repo.slug,
                label: repo.key,
            }));

            if (repos.length === 0) {
                this.cachedOpenPrs = [];
                this.prsLastFetched = Date.now();
                this.prsEverFetched = true;
                await this.refresh();
                return;
            }

            const allPrs: OpenPr[] = [];

            // Group by org to minimize account switches
            const orgRepos: Record<string, Array<{ slug: string; label: string }>> = {};
            for (const repo of repos) {
                const org = repo.slug.split('/')[0];
                if (!orgRepos[org]) { orgRepos[org] = []; }
                orgRepos[org].push(repo);
            }

            for (const [, repoList] of Object.entries(orgRepos)) {
                // Discover the GitHub username for this org
                let ghUsername = '';
                try {
                    await switchGhAccount(repoList[0].slug);
                    const statusOutput = await runCommand('gh api user --jq .login', undefined, 10000).catch(() => '');
                    ghUsername = statusOutput.trim();
                } catch {
                    continue;
                }

                for (const repo of repoList) {
                    try {
                        // Fetch user's own PRs
                        const userPrsJson = await runCommand(
                            `gh pr list --repo "${repo.slug}" --author "@me" --state open --limit 10 --json number,title,url,isDraft,author,createdAt`,
                            undefined, 15000
                        ).catch(() => '[]');

                        // Fetch agent PRs assigned to this user (agent PRs are assigned to the triggering user)
                        const agentPrsJson = ghUsername
                            ? await runCommand(
                                `gh pr list --repo "${repo.slug}" --author "copilot-swe-agent[bot]" --assignee "${ghUsername}" --state open --limit 10 --json number,title,url,isDraft,author,createdAt`,
                                undefined, 15000
                            ).catch(() => '[]')
                            : '[]';

                        for (const json of [userPrsJson, agentPrsJson]) {
                            try {
                                const prs = JSON.parse(json);
                                for (const pr of prs) {
                                    // Dedupe by number+repo
                                    if (!allPrs.some(p => p.number === pr.number && p.repo === repo.label)) {
                                        allPrs.push({
                                            repo: repo.label,
                                            number: pr.number,
                                            title: pr.title || '',
                                            url: pr.url || '',
                                            isDraft: pr.isDraft || false,
                                            author: pr.author?.login || '',
                                            createdAt: pr.createdAt || '',
                                        });
                                    }
                                }
                            } catch { /* skip parse errors */ }
                        }
                    } catch { /* skip repo errors */ }
                }
            }

            // Sort by most recent first
            allPrs.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());

            this.cachedOpenPrs = allPrs;
            this.prsLastFetched = Date.now();
            this.prsEverFetched = true;

            // Re-render with fresh PR data
            await this.refresh();
        } catch (e) {
            console.error('[Dashboard] Failed to fetch open PRs:', e);
        } finally {
            this.prsFetching = false;
        }
    }

    private getHtml(state: OrchestratorState): string {
        const stepConfig: Record<string, { icon: string; label: string; nextPromptFile?: string; nextLabel?: string; nextContext?: string }> = {
            'idle': { icon: '⏳', label: 'Ready', nextPromptFile: 'feature-design', nextLabel: '▶ Start Design', nextContext: '' },
            'designing': { icon: '📝', label: 'Writing Design...' },
            'design_review': { icon: '👀', label: 'Awaiting Design Approval', nextPromptFile: 'feature-plan', nextLabel: '📋 Approve → Plan PBIs', nextContext: '' },
            'planning': { icon: '📋', label: 'Planning PBIs...' },
            'plan_review': { icon: '👀', label: 'Awaiting Plan Approval', nextPromptFile: 'feature-backlog', nextLabel: '✅ Approve → Backlog in ADO', nextContext: '' },
            'backlogging': { icon: '📝', label: 'Adding to Backlog...' },
            'backlog_review': { icon: '👀', label: 'PBIs Backlogged — Review', nextPromptFile: 'feature-dispatch', nextLabel: '🚀 Dispatch to Agent', nextContext: '' },
            'dispatching': { icon: '🚀', label: 'Dispatching...' },
            'monitoring': { icon: '📡', label: 'Agents Working', nextPromptFile: 'feature-status', nextLabel: '👁 Check Status', nextContext: '' },
            'done': { icon: '✅', label: 'Complete' },
        };

        // Normalize step names
        const stepAliases: Record<string, string> = {
            'designed': 'design_review', 'design_complete': 'design_review',
            'planned': 'plan_review', 'plan_complete': 'plan_review',
            'backlogged': 'backlog_review', 'created': 'backlog_review', 'creating': 'backlogging', 'create_review': 'backlog_review',
            'dispatched': 'monitoring', 'dispatch_complete': 'monitoring',
            'complete': 'done', 'completed': 'done',
        };

        // Split features into active vs completed
        const activeFeatures = state.features.filter(f => {
            const ns = stepAliases[f.step] || f.step;
            return ns !== 'done';
        });
        const completedFeatures = state.features.filter(f => {
            const ns = stepAliases[f.step] || f.step;
            return ns === 'done';
        });

        // Compute metrics
        const totalFeatures = state.features.length;
        const totalPbis = state.features.reduce((sum, f) => {
            return sum + ((f as any).artifacts?.pbis?.length || f.pbis?.length || 0);
        }, 0);
        const totalPrs = state.features.reduce((sum, f) => {
            return sum + ((f as any).artifacts?.agentPrs?.length || f.agentSessions?.length || 0);
        }, 0);
        const mergedPrs = state.features.reduce((sum, f) => {
            const prs = (f as any).artifacts?.agentPrs || f.agentSessions || [];
            return sum + prs.filter((pr: any) => (pr.status || pr.state || '').toLowerCase() === 'merged').length;
        }, 0);

        const metricsHtml = totalFeatures > 0
            ? `<div class="metrics">
                <div class="metric"><span class="metric-value">${activeFeatures.length}</span><span class="metric-label">Active</span></div>
                <div class="metric"><span class="metric-value">${completedFeatures.length}</span><span class="metric-label">Done</span></div>
                <div class="metric"><span class="metric-value">${totalPbis}</span><span class="metric-label">PBIs</span></div>
                <div class="metric"><span class="metric-value">${mergedPrs}/${totalPrs}</span><span class="metric-label">PRs Merged</span></div>
              </div>`
            : '';

        // Render a feature card
        const renderCard = (f: FeatureState, compact: boolean = false) => {
            const normalizedStep = stepAliases[f.step] || f.step;
            const cfg = stepConfig[normalizedStep] || stepConfig['idle'];
            const progressSteps = ['designing', 'design_review', 'planning', 'plan_review', 'backlogging', 'backlog_review', 'dispatching', 'monitoring', 'done'];
            const currentIdx = progressSteps.indexOf(normalizedStep);

            // Determine if dispatch is still in progress (some PBIs not yet dispatched)
            const artifacts = (f as any).artifacts;
            const allPbis = artifacts?.pbis || f.pbis || [];
            const allPrs = artifacts?.agentPrs || f.agentSessions || [];
            const resolvedStatuses = new Set(['resolved', 'done', 'closed', 'removed']);
            const unresolvedPbis = allPbis.filter((p: any) => !resolvedStatuses.has((p.status || '').toLowerCase()));
            const allDispatched = unresolvedPbis.length === 0 || allPrs.length >= unresolvedPbis.length;

            // Mini pipeline with agent icons
            const stageInfo = [
                { icon: '📐', label: 'Design', startIdx: 0, endIdx: 2 },
                { icon: '🗂️', label: 'Plan', startIdx: 2, endIdx: 4 },
                { icon: '📋', label: 'Backlog', startIdx: 4, endIdx: 6 },
                { icon: '🚀', label: 'Dispatch', startIdx: 6, endIdx: 7 },
                { icon: '👁️', label: 'Monitor', startIdx: 7, endIdx: 8 },
            ];

            const miniPipeline = stageInfo.map(s => {
                let isDone = normalizedStep === 'done' || currentIdx >= s.endIdx;
                let isActive = !isDone && currentIdx >= s.startIdx && currentIdx < s.endIdx;

                // When monitoring: if not all PBIs dispatched, Dispatch is still active
                if (s.label === 'Dispatch' && normalizedStep === 'monitoring' && !allDispatched) {
                    isDone = false;
                    isActive = true;
                }
                if (isDone) {
                    return `<div class="mini-stage done" title="${s.label}"><span class="mini-icon">✅</span></div>`;
                } else if (isActive) {
                    return `<div class="mini-stage active" title="${s.label}"><span class="mini-icon">${s.icon}</span></div>`;
                } else {
                    return `<div class="mini-stage upcoming" title="${s.label}"><span class="mini-icon">${s.icon}</span></div>`;
                }
            }).join('<span class="mini-arrow">›</span>');

            // Artifact summary
            const pbiCount = allPbis.length;
            const prCount = (artifacts?.agentPrs || f.agentSessions || []).length;
            const hasDesign = !!artifacts?.design || !!f.designDocPath;
            const artifactPills: string[] = [];
            if (hasDesign) { artifactPills.push('📄 Design'); }
            if (pbiCount > 0) { artifactPills.push(`📋 ${pbiCount} PBI${pbiCount > 1 ? 's' : ''}`); }
            if (prCount > 0) { artifactPills.push(`🤖 ${prCount} PR${prCount > 1 ? 's' : ''}`); }
            const artifactSummary = artifactPills.length > 0
                ? `<div class="artifact-summary">${artifactPills.join(' · ')}</div>`
                : '';

            if (compact) {
                // Completed feature card — minimal
                return `
                <div class="feature-card compact" onclick="openDetail('${f.id}')" title="Click to view details">
                  <div class="feature-header">
                    <span class="step-icon">✅</span>
                    <strong class="feature-name">${this.escapeHtml(f.name)}</strong>
                    <button class="x-btn" onclick="event.stopPropagation(); removeFeature('${f.id}')" title="Remove">✕</button>
                  </div>
                  ${artifactSummary}
                  <div class="feature-time">${this.timeAgo(f.updatedAt)}</div>
                </div>`;
            }

            // Active feature card — full
            let actionContext = `Feature: "${f.name}"`;
            if (normalizedStep === 'monitoring') {
                const fPrs = (f as any).artifacts?.agentPrs || f.agentSessions || [];
                if (fPrs.length > 0) {
                    const prList = fPrs.map((pr: any) => `${pr.repo || ''} #${pr.prNumber || pr.number || '?'}`).join(', ');
                    actionContext += `. Tracked PRs: ${prList}`;
                }
            }

            const actionBtn = cfg.nextPromptFile
                ? `<button class="action-btn" onclick="event.stopPropagation(); openPrompt('${cfg.nextPromptFile}', '${this.escapeAttr(actionContext)}')">${cfg.nextLabel}</button>`
                : `<div class="step-status">${cfg.icon} ${cfg.label}</div>`;

            return `
            <div class="feature-card" onclick="openDetail('${f.id}')" title="Click to view details">
              <div class="feature-header">
                <span class="step-icon">${cfg.icon}</span>
                <strong class="feature-name" title="${this.escapeHtml(f.prompt || f.name)}">${this.escapeHtml(f.name)}</strong>
                <button class="x-btn" onclick="event.stopPropagation(); removeFeature('${f.id}')" title="Remove">✕</button>
              </div>
              <div class="mini-pipeline">${miniPipeline}</div>
              ${actionBtn}
              ${artifactSummary}
              <div class="feature-time">${this.timeAgo(f.updatedAt)}</div>
            </div>`;
        };

        const activeFeaturesHtml = activeFeatures.length === 0
            ? `<div class="empty-state">
                 <div class="empty-icon">🚀</div>
                 <p><strong>No active features</strong></p>
                 <p class="muted">Click <strong>+</strong> above or type <code>/feature-design</code> in chat</p>
               </div>`
            : activeFeatures.map(f => renderCard(f)).join('');

        const completedFeaturesHtml = completedFeatures.length > 0
            ? completedFeatures.map(f => renderCard(f, true)).join('')
            : '<p class="muted center">No completed features yet</p>';

        // Build "My Open PRs" section
        let openPrsHtml: string;
        if (this.prsFetching || !this.prsEverFetched) {
            openPrsHtml = '<div class="loading-spinner"><div class="spinner"></div> Loading PRs...</div>';
        } else if (this.cachedOpenPrs.length === 0) {
            openPrsHtml = '<p class="muted center">No open PRs</p>';
        } else {
            openPrsHtml = this.cachedOpenPrs.map(pr => {
                const age = this.timeAgo(new Date(pr.createdAt).getTime());
                const draftBadge = pr.isDraft ? '<span class="badge-draft-pr">Draft</span> ' : '';
                const isAgent = pr.author === 'app/copilot-swe-agent' || pr.author === 'copilot-swe-agent[bot]' || pr.author === 'copilot-swe-agent';
                const agentBadge = isAgent ? '<span class="badge-ai">AI</span> ' : '';
                return `<div class="pr-row">
                  <span class="pr-dot open"></span>
                  <a href="#" onclick="openUrl('${this.escapeAttr(pr.url)}')">${pr.repo} #${pr.number}</a>
                  ${agentBadge}${draftBadge}<span class="pr-title">${this.escapeHtml(pr.title).substring(0, 40)}</span>
                  <span class="pr-age">${age}</span>
                </div>`;
            }).join('');
        }

        return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body { font-family: var(--vscode-font-family); font-size: 12px; color: var(--vscode-foreground); padding: 0 8px; margin: 0; }
h3 { margin: 14px 0 6px; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--vscode-descriptionForeground); }

/* Metrics banner */
.metrics { display: flex; justify-content: space-around; padding: 10px 4px; margin-bottom: 4px; background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border); border-radius: 8px; }
.metric { text-align: center; }
.metric-value { display: block; font-size: 18px; font-weight: 700; color: var(--vscode-foreground); }
.metric-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--vscode-descriptionForeground); }

/* Feature cards */
.feature-card { background: var(--vscode-editor-background); border: 1px solid var(--vscode-widget-border); border-radius: 8px; padding: 12px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.2s; }
.feature-card:hover { border-color: var(--vscode-focusBorder); }
.feature-card.compact { padding: 8px 12px; }
.feature-card.compact .feature-time { margin-top: 4px; }
.feature-header { display: flex; align-items: center; gap: 6px; }
.feature-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.step-icon { font-size: 14px; }
.x-btn { background: none; border: none; color: var(--vscode-descriptionForeground); cursor: pointer; font-size: 11px; padding: 2px 4px; opacity: 0.5; }
.x-btn:hover { opacity: 1; color: var(--vscode-errorForeground); }
.progress-bar { display: flex; gap: 4px; margin: 8px 0; align-items: center; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--vscode-widget-border); flex-shrink: 0; transition: all 0.3s; }
.dot.active { background: var(--vscode-progressBar-background); box-shadow: 0 0 6px var(--vscode-progressBar-background); }
.dot.done { background: #238636; }

/* Mini pipeline stages */
.mini-pipeline { display: flex; align-items: center; gap: 2px; margin: 8px 0; }
.mini-stage { display: flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; transition: all 0.3s; }
.mini-stage.done { background: #23863620; }
.mini-stage.active { background: var(--vscode-editorWidget-background); border: 1.5px solid var(--vscode-focusBorder); animation: borderBreath 3s ease-in-out infinite; }
.mini-stage.upcoming { opacity: 0.35; }
.mini-icon { font-size: 12px; line-height: 1; }
.mini-arrow { color: var(--vscode-descriptionForeground); font-size: 10px; opacity: 0.3; margin: 0 1px; }
@keyframes borderBreath { 0%, 100% { border-color: var(--vscode-focusBorder); } 50% { border-color: transparent; } }
.action-btn { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; border-radius: 4px; padding: 6px 12px; font-size: 11px; cursor: pointer; font-weight: 600; width: 100%; margin: 4px 0; }
.action-btn:hover { background: var(--vscode-button-hoverBackground); }
.step-status { font-size: 11px; color: var(--vscode-descriptionForeground); font-style: italic; text-align: center; padding: 4px 0; }
.feature-time { font-size: 10px; color: var(--vscode-descriptionForeground); margin-top: 6px; text-align: right; }
.artifact-summary { font-size: 10px; color: var(--vscode-descriptionForeground); margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
a { color: var(--vscode-textLink-foreground); text-decoration: none; cursor: pointer; } a:hover { text-decoration: underline; }
.empty-state { text-align: center; padding: 24px 8px; }
.empty-icon { font-size: 36px; margin-bottom: 8px; }
.muted { color: var(--vscode-descriptionForeground); } .center { text-align: center; }
code { background: var(--vscode-textCodeBlock-background); padding: 1px 4px; border-radius: 3px; font-size: 11px; }
hr { border: none; border-top: 1px solid var(--vscode-widget-border); margin: 12px 0; }
.footer { text-align: center; font-size: 10px; color: var(--vscode-descriptionForeground); padding: 4px 0; }

/* Loading spinner */
.loading-spinner { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 12px; font-size: 11px; color: var(--vscode-descriptionForeground); }
.spinner { width: 14px; height: 14px; border: 2px solid var(--vscode-widget-border); border-top-color: var(--vscode-progressBar-background); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* PR rows */
.pr-row { display: flex; align-items: center; gap: 5px; padding: 4px 0; font-size: 11px; border-bottom: 1px solid var(--vscode-widget-border, transparent); }
.pr-row:last-child { border-bottom: none; }
.pr-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.pr-dot.open { background: #3fb950; }
.pr-dot.draft { background: #8b949e; }
.pr-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--vscode-descriptionForeground); }
.pr-age { font-size: 10px; color: var(--vscode-descriptionForeground); white-space: nowrap; }
.badge-draft-pr { font-size: 9px; padding: 1px 4px; border-radius: 3px; background: #8b949e30; color: #8b949e; }
.badge-ai { font-size: 9px; padding: 1px 5px; border-radius: 3px; background: #8957e530; color: #8957e5; font-weight: 600; letter-spacing: 0.3px; }
</style></head>
<body>
  ${metricsHtml}
  <h3>Active Features</h3>
  ${activeFeaturesHtml}
  <hr>
  <h3>Completed</h3>
  ${completedFeaturesHtml}
  <hr>
  <h3>My Open PRs</h3>
  ${openPrsHtml}
  <hr>
  <div class="footer">Auto-refreshes · ${new Date().toLocaleTimeString()}</div>
  <script>
    const vscode = acquireVsCodeApi();
    function openUrl(url) { vscode.postMessage({ command: 'openUrl', url }); }
    function removeFeature(id) { vscode.postMessage({ command: 'removeFeature', featureId: id }); }
    function openPrompt(promptFile, context) { vscode.postMessage({ command: 'openAgent', promptFile, context }); }
    function openDetail(id) { vscode.postMessage({ command: 'openFeatureDetail', featureId: id }); }
  </script>
</body></html>`;
    }

    private escapeHtml(s: string): string {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    private escapeAttr(s: string): string {
        return s.replace(/'/g, "\\'").replace(/"/g, '&quot;');
    }

    private timeAgo(ts: number): string {
        if (!ts) { return ''; }
        const s = Math.floor((Date.now() - ts) / 1000);
        if (s < 60) { return 'just now'; }
        if (s < 3600) { return `${Math.floor(s / 60)}m ago`; }
        if (s < 86400) { return `${Math.floor(s / 3600)}h ago`; }
        return `${Math.floor(s / 86400)}d ago`;
    }
}
