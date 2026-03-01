import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { getAgentPRs, switchGhAccount } from './tools';

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

interface AgentPr {
    repo: string;
    number: number;
    title: string;
    state: string;
    url: string;
    createdAt: string;
}

export class DashboardViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'orchestrator.dashboard';

    private view?: vscode.WebviewView;
    private refreshInterval?: NodeJS.Timeout;
    private fileWatcher?: vscode.FileSystemWatcher;

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
                    break;
                case 'openUrl':
                    vscode.env.openExternal(vscode.Uri.parse(message.url));
                    break;
                case 'openAgent': {
                    // Open a new chat with the custom agent (not the old @orchestrator participant)
                    await vscode.commands.executeCommand('workbench.action.chat.newChat');
                    const query = `@feature-orchestrator ${message.prompt || ''}`.trim();
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

        // Auto-refresh every 30 seconds
        this.refreshInterval = setInterval(() => this.refresh(), 30000);

        // Watch state file for changes (hooks write to it)
        const stateFilePath = this.getStateFilePath();
        if (stateFilePath) {
            const stateDir = path.dirname(stateFilePath);
            // Ensure the directory exists so the watcher can be set up
            if (!fs.existsSync(stateDir)) {
                fs.mkdirSync(stateDir, { recursive: true });
            }
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
        let agentPrs: AgentPr[] = [];
        try {
            agentPrs = await this.fetchAllAgentPrs();
        } catch { /* ignore */ }

        this.view.webview.html = this.getHtml(state, agentPrs);
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
        const stepActions: Record<string, { message: string; button: string; prompt: string }> = {
            'design_review': {
                message: `✅ Design spec written for "${feature.name}". Ready to plan PBIs.`,
                button: '📋 Plan PBIs',
                prompt: 'The design has been approved. Break it down into PBIs.',
            },
            'plan_review': {
                message: `✅ PBI plan created for "${feature.name}". Review and create in ADO.`,
                button: '✅ Create in ADO',
                prompt: 'Plan approved. Create the PBIs in ADO.',
            },
            'backlog_review': {
                message: `✅ PBIs backlogged in ADO for "${feature.name}". Ready to dispatch.`,
                button: '🚀 Dispatch to Agent',
                prompt: 'PBIs approved. Dispatch to Copilot coding agent.',
            },
            'monitoring': {
                message: `✅ PBIs dispatched for "${feature.name}". Agents are working.`,
                button: '📡 Check Status',
                prompt: 'Check agent status.',
            },
        };

        const cfg = stepActions[action.nextStep];
        if (!cfg) { return; }

        // Show VS Code notification with clickable button
        vscode.window.showInformationMessage(cfg.message, cfg.button).then(selection => {
            if (selection === cfg.button) {
                // Open chat with the next-step prompt pre-filled
                vscode.commands.executeCommand('workbench.action.chat.open', {
                    query: `@feature-orchestrator ${cfg.prompt}`,
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
        return path.join(os.homedir(), '.android-auth-orchestrator', 'state.json');
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

    private async fetchAllAgentPrs(): Promise<AgentPr[]> {
        const repos = [
            { slug: 'AzureAD/microsoft-authentication-library-common-for-android', label: 'common' },
            { slug: 'AzureAD/microsoft-authentication-library-for-android', label: 'msal' },
            { slug: 'AzureAD/azure-activedirectory-library-for-android', label: 'adal' },
        ];
        const allPrs: AgentPr[] = [];
        for (const repo of repos) {
            try {
                await switchGhAccount(repo.slug);
                const json = await getAgentPRs(repo.slug);
                const prs = JSON.parse(json);
                for (const pr of prs) {
                    allPrs.push({
                        repo: repo.label, number: pr.number, title: pr.title,
                        state: pr.state, url: pr.url, createdAt: pr.createdAt,
                    });
                }
            } catch { /* skip */ }
        }
        return allPrs;
    }

    private getHtml(state: OrchestratorState, agentPrs: AgentPr[]): string {
        const stepConfig: Record<string, { icon: string; label: string; nextAgent?: string; nextLabel?: string; nextPrompt?: string }> = {
            'idle': { icon: '⏳', label: 'Ready', nextAgent: 'design-author', nextLabel: '▶ Start Design', nextPrompt: '' },
            'designing': { icon: '📝', label: 'Writing Design...' },
            'design_review': { icon: '👀', label: 'Awaiting Design Approval', nextAgent: 'feature-planner', nextLabel: '📋 Approve → Plan PBIs', nextPrompt: 'The design spec has been approved. Break it down into PBIs.' },
            'planning': { icon: '📋', label: 'Planning PBIs...' },
            'plan_review': { icon: '👀', label: 'Awaiting Plan Approval', nextAgent: 'pbi-creator', nextLabel: '✅ Approve → Backlog in ADO', nextPrompt: 'Plan approved. Create the PBIs in Azure DevOps.' },
            'backlogging': { icon: '📝', label: 'Adding to Backlog...' },
            'backlog_review': { icon: '👀', label: 'PBIs Backlogged — Review', nextAgent: 'agent-dispatcher', nextLabel: '🚀 Dispatch to Agent', nextPrompt: 'PBIs approved. Dispatch to Copilot coding agent.' },
            'dispatching': { icon: '🚀', label: 'Dispatching...' },
            'monitoring': { icon: '📡', label: 'Agents Working', nextAgent: 'agent-monitor', nextLabel: '👁 Check Status', nextPrompt: 'Check the status of all agent PRs.' },
            'done': { icon: '✅', label: 'Complete' },
        };

        // Normalize step names: map aliases/past-tense/legacy names to canonical keys
        const stepAliases: Record<string, string> = {
            'designed': 'design_review', 'design_complete': 'design_review',
            'planned': 'plan_review', 'plan_complete': 'plan_review',
            'backlogged': 'backlog_review', 'created': 'backlog_review', 'creating': 'backlogging', 'create_review': 'backlog_review',
            'dispatched': 'monitoring', 'dispatch_complete': 'monitoring',
            'complete': 'done', 'completed': 'done',
        };

        const stateColors: Record<string, string> = {
            'OPEN': '#238636', 'MERGED': '#8957e5', 'CLOSED': '#da3633',
        };

        const featuresHtml = state.features.length === 0
            ? `<div class="empty-state">
                 <div class="empty-icon">🚀</div>
                 <p><strong>No features tracked</strong></p>
                 <p class="muted">Click <strong>+</strong> above or type <code>@feature-orchestrator</code> in chat</p>
               </div>`
            : state.features.map(f => {
                const normalizedStep = stepAliases[f.step] || f.step;
                const cfg = stepConfig[normalizedStep] || stepConfig['idle'];
                const progressSteps = ['designing', 'design_review', 'planning', 'plan_review', 'backlogging', 'backlog_review', 'dispatching', 'monitoring', 'done'];
                const currentIdx = progressSteps.indexOf(normalizedStep);

                const progressDots = progressSteps.map((_s, i) =>
                    `<div class="dot ${i < currentIdx ? 'done' : i === currentIdx ? 'active' : ''}"></div>`
                ).join('');

                const actionBtn = cfg.nextAgent
                    ? `<button class="action-btn" onclick="event.stopPropagation(); openAgent('${cfg.nextAgent}', '${this.escapeAttr(cfg.nextPrompt || '')}')">${cfg.nextLabel}</button>`
                    : `<div class="step-status">${cfg.icon} ${cfg.label}</div>`;

                // Artifact summary counts
                const artifacts = (f as any).artifacts;
                const pbiCount = artifacts?.pbis?.length || f.pbis?.length || 0;
                const prCount = artifacts?.agentPrs?.length || f.agentSessions?.length || 0;
                const hasDesign = !!artifacts?.design || !!f.designDocPath;
                const artifactPills: string[] = [];
                if (hasDesign) { artifactPills.push('📄 Design'); }
                if (pbiCount > 0) { artifactPills.push(`📋 ${pbiCount} PBI${pbiCount > 1 ? 's' : ''}`); }
                if (prCount > 0) { artifactPills.push(`🤖 ${prCount} PR${prCount > 1 ? 's' : ''}`); }
                const artifactSummary = artifactPills.length > 0
                    ? `<div class="artifact-summary">${artifactPills.join(' · ')}</div>`
                    : '';

                return `
                <div class="feature-card" onclick="openDetail('${f.id}')" title="Click to view details">
                  <div class="feature-header">
                    <span class="step-icon">${cfg.icon}</span>
                    <strong class="feature-name" title="${this.escapeHtml(f.prompt || f.name)}">${this.escapeHtml(f.name)}</strong>
                    <button class="x-btn" onclick="event.stopPropagation(); removeFeature('${f.id}')" title="Remove">✕</button>
                  </div>
                  <div class="progress-bar">${progressDots}</div>
                  ${actionBtn}
                  ${artifactSummary}
                  <div class="feature-time">${this.timeAgo(f.updatedAt)}</div>
                </div>`;
            }).join('');

        const prsHtml = agentPrs.length === 0
            ? '<p class="muted center">No agent PRs found</p>'
            : agentPrs.slice(0, 10).map(pr => `
                <div class="pr-row">
                  <span class="pr-dot" style="background:${stateColors[pr.state] || '#8b949e'}"></span>
                  <a href="#" onclick="openUrl('${pr.url}')">${pr.repo} #${pr.number}</a>
                  <span class="pr-title">${this.escapeHtml(pr.title).substring(0, 30)}</span>
                </div>`).join('');

        return `<!DOCTYPE html>
<html><head><style>
body { font-family: var(--vscode-font-family); font-size: 12px; color: var(--vscode-foreground); padding: 0 8px; margin: 0; }
h3 { margin: 14px 0 6px; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--vscode-descriptionForeground); }
.feature-card { background: var(--vscode-editor-background); border: 1px solid var(--vscode-widget-border); border-radius: 8px; padding: 12px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.2s; }
.feature-card:hover { border-color: var(--vscode-focusBorder); }
.feature-header { display: flex; align-items: center; gap: 6px; }
.feature-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.step-icon { font-size: 14px; }
.x-btn { background: none; border: none; color: var(--vscode-descriptionForeground); cursor: pointer; font-size: 11px; padding: 2px 4px; opacity: 0.5; }
.x-btn:hover { opacity: 1; color: var(--vscode-errorForeground); }
.progress-bar { display: flex; gap: 4px; margin: 8px 0; align-items: center; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--vscode-widget-border); flex-shrink: 0; transition: all 0.3s; }
.dot.active { background: var(--vscode-progressBar-background); box-shadow: 0 0 6px var(--vscode-progressBar-background); }
.dot.done { background: #238636; }
.action-btn { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; border-radius: 4px; padding: 6px 12px; font-size: 11px; cursor: pointer; font-weight: 600; width: 100%; margin: 4px 0; }
.action-btn:hover { background: var(--vscode-button-hoverBackground); }
.step-status { font-size: 11px; color: var(--vscode-descriptionForeground); font-style: italic; text-align: center; padding: 4px 0; }
.pbi-list { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.pbi-item { font-size: 10px; display: flex; align-items: center; gap: 3px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); padding: 2px 6px; border-radius: 10px; }
.pbi-dot { width: 5px; height: 5px; border-radius: 50%; }
.pbi-dot.pending { background: #8b949e; } .pbi-dot.dispatched { background: #238636; }
.pbi-dot.blocked { background: #da3633; } .pbi-dot.merged { background: #8957e5; }
.feature-time { font-size: 10px; color: var(--vscode-descriptionForeground); margin-top: 6px; text-align: right; }
.artifact-summary { font-size: 10px; color: var(--vscode-descriptionForeground); margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
.pr-row { display: flex; align-items: center; gap: 6px; padding: 3px 0; font-size: 11px; }
.pr-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.pr-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--vscode-descriptionForeground); }
a { color: var(--vscode-textLink-foreground); text-decoration: none; cursor: pointer; } a:hover { text-decoration: underline; }
.empty-state { text-align: center; padding: 24px 8px; }
.empty-icon { font-size: 36px; margin-bottom: 8px; }
.muted { color: var(--vscode-descriptionForeground); } .center { text-align: center; }
code { background: var(--vscode-textCodeBlock-background); padding: 1px 4px; border-radius: 3px; font-size: 11px; }
hr { border: none; border-top: 1px solid var(--vscode-widget-border); margin: 12px 0; }
.footer { text-align: center; font-size: 10px; color: var(--vscode-descriptionForeground); padding: 4px 0; }
</style></head>
<body>
  <h3>Features</h3>
  ${featuresHtml}
  <hr>
  <h3>Agent PRs</h3>
  ${prsHtml}
  <hr>
  <div class="footer">Auto-refreshes · ${new Date().toLocaleTimeString()}</div>
  <script>
    const vscode = acquireVsCodeApi();
    function openUrl(url) { vscode.postMessage({ command: 'openUrl', url }); }
    function removeFeature(id) { vscode.postMessage({ command: 'removeFeature', featureId: id }); }
    function openAgent(agent, prompt) { vscode.postMessage({ command: 'openAgent', agent, prompt }); }
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
