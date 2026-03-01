import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { runCommand, switchGhAccount } from './tools';

/**
 * Artifact types that can be tracked per feature.
 */
export interface DesignArtifact {
    docPath: string;        // workspace-relative path to the design doc
    prUrl?: string;         // ADO PR URL for the design review
    status: 'draft' | 'in-review' | 'approved';
}

export interface PbiArtifact {
    adoId: number;          // AB# work item ID
    title: string;
    targetRepo: string;     // e.g. "AzureAD/microsoft-authentication-library-common-for-android"
    module: string;         // e.g. "common", "msal", "broker"
    adoUrl: string;         // full ADO URL
    status: 'new' | 'committed' | 'active' | 'resolved' | 'closed';
    priority?: number;
    dependsOn?: number[];   // AB# IDs this PBI depends on
    agentPr?: AgentPrArtifact; // linked PR from coding agent
}

export interface AgentPrArtifact {
    repo: string;
    prNumber: number;
    prUrl: string;
    status: 'open' | 'merged' | 'closed' | 'draft';
    title?: string;
}

export interface FeatureArtifacts {
    design?: DesignArtifact;
    pbis: PbiArtifact[];
    agentPrs: AgentPrArtifact[];
}

const ADO_ORG = 'IdentityDivision';
const ADO_PROJECT = 'Engineering';

/**
 * Opens a detail panel for a specific feature, showing all tracked artifacts.
 */
export class FeatureDetailPanel {
    public static readonly viewType = 'orchestrator.featureDetail';
    private static panels: Map<string, vscode.WebviewPanel> = new Map();

    static show(context: vscode.ExtensionContext, featureId: string): void {
        // Reuse existing panel for this feature if open
        const existing = FeatureDetailPanel.panels.get(featureId);
        if (existing) {
            existing.reveal(vscode.ViewColumn.One);
            return;
        }

        const state = FeatureDetailPanel.readState();
        const feature = state.features?.find((f: any) => f.id === featureId);
        if (!feature) {
            vscode.window.showWarningMessage(`Feature "${featureId}" not found in state.`);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            FeatureDetailPanel.viewType,
            `Feature: ${feature.name}`,
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        let autoRefreshInterval: NodeJS.Timeout | undefined;

        FeatureDetailPanel.panels.set(featureId, panel);
        panel.onDidDispose(() => {
            FeatureDetailPanel.panels.delete(featureId);
            if (autoRefreshInterval) { clearInterval(autoRefreshInterval); }
        });

        panel.webview.html = FeatureDetailPanel.getHtml(feature);

        panel.webview.onDidReceiveMessage(async (message) => {
            switch (message.command) {
                case 'openUrl':
                    vscode.env.openExternal(vscode.Uri.parse(message.url));
                    break;
                case 'openFile': {
                    const folders = vscode.workspace.workspaceFolders;
                    if (folders) {
                        const uri = vscode.Uri.file(path.join(folders[0].uri.fsPath, message.path));
                        vscode.commands.executeCommand('vscode.open', uri);
                    }
                    break;
                }
                case 'continueInChat': {
                    // Build a context-rich prompt summarizing the feature's current state
                    const freshState2 = FeatureDetailPanel.readState();
                    const feat = freshState2.features?.find((f: any) => f.id === featureId);
                    let contextPrompt = `Continue working on feature: "${feat?.name || 'Unknown'}".\n`;
                    contextPrompt += `Current step: ${feat?.step || 'unknown'}.\n`;
                    // Include PBI context if available
                    const pbiList = feat?.artifacts?.pbis || feat?.pbis || [];
                    if (pbiList.length > 0) {
                        contextPrompt += `PBIs: ${pbiList.map((p: any) => `${p.id || 'AB#' + p.adoId} (${p.title})`).join(', ')}.\n`;
                    }
                    // Include design doc path if available
                    const designPath = feat?.artifacts?.design?.docPath || feat?.designDocPath;
                    if (designPath) {
                        contextPrompt += `Design doc: ${designPath}\n`;
                    }
                    await vscode.commands.executeCommand('workbench.action.chat.newChat');
                    vscode.commands.executeCommand('workbench.action.chat.open', {
                        query: `@feature-orchestrator ${contextPrompt}`,
                    });
                    break;
                }
                case 'refresh':
                    // Fetch live statuses from GitHub, update state, and re-render
                    panel.webview.postMessage({ command: 'refreshing', status: true });
                    try {
                        await FeatureDetailPanel.refreshLiveStatuses(featureId);
                    } catch (e) {
                        console.error('[FeatureDetail] Live refresh error:', e);
                    }
                    {
                        const freshState = FeatureDetailPanel.readState();
                        const freshFeature = freshState.features?.find((f: any) => f.id === featureId);
                        if (freshFeature) {
                            panel.webview.html = FeatureDetailPanel.getHtml(freshFeature);
                        }
                    }
                    break;
            }
        });

        // Periodic auto-refresh every 5 minutes (fetches live PR + PBI statuses)
        autoRefreshInterval = setInterval(async () => {
            try {
                await FeatureDetailPanel.refreshLiveStatuses(featureId);
                const updated = FeatureDetailPanel.readState();
                const updatedFeature = updated.features?.find((f: any) => f.id === featureId);
                if (updatedFeature) {
                    panel.webview.html = FeatureDetailPanel.getHtml(updatedFeature);
                }
            } catch { /* silent */ }
        }, 300000); // 5 minutes

        // Watch for state file changes
        const stateDir = path.join(os.homedir(), '.android-auth-orchestrator');
        if (!fs.existsSync(stateDir)) {
            fs.mkdirSync(stateDir, { recursive: true });
        }
        const pattern = new vscode.RelativePattern(vscode.Uri.file(stateDir), 'state.json');
        const watcher = vscode.workspace.createFileSystemWatcher(pattern);
        watcher.onDidChange(() => {
            const updated = FeatureDetailPanel.readState();
            const updatedFeature = updated.features?.find((f: any) => f.id === featureId);
            if (updatedFeature) {
                panel.webview.html = FeatureDetailPanel.getHtml(updatedFeature);
            }
        });
        panel.onDidDispose(() => watcher.dispose());
    }

    private static readState(): any {
        const filePath = path.join(os.homedir(), '.android-auth-orchestrator', 'state.json');
        if (!fs.existsSync(filePath)) { return { features: [] }; }
        try { return JSON.parse(fs.readFileSync(filePath, 'utf-8')); }
        catch { return { features: [] }; }
    }

    private static writeState(state: any): void {
        const dir = path.join(os.homedir(), '.android-auth-orchestrator');
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        state.lastUpdated = Date.now();
        fs.writeFileSync(path.join(dir, 'state.json'), JSON.stringify(state, null, 2), 'utf-8');
    }

    /**
     * Fetch live PR statuses from GitHub and PBI statuses from ADO,
     * then write updated data back to state.json.
     */
    private static async refreshLiveStatuses(featureId: string): Promise<void> {
        const state = FeatureDetailPanel.readState();
        const feature = state.features?.find((f: any) => f.id === featureId);
        if (!feature) { return; }

        let changed = false;

        // Repo slug mapping: short names → full GitHub slugs
        const repoSlugs: Record<string, string> = {
            'common': 'AzureAD/microsoft-authentication-library-common-for-android',
            'msal': 'AzureAD/microsoft-authentication-library-for-android',
            'adal': 'AzureAD/azure-activedirectory-library-for-android',
            'broker': 'identity-authnz-teams/ad-accounts-for-android',
        };

        // --- Refresh Agent PRs from GitHub ---
        const agentPrs = feature.artifacts?.agentPrs || [];
        if (agentPrs.length > 0) {
            // Group PRs by org to minimize gh account switches
            const prsByOrg: Record<string, any[]> = {};
            for (const pr of agentPrs) {
                const repoSlug = repoSlugs[pr.repo] || pr.repo;
                const org = repoSlug.split('/')[0] || 'AzureAD';
                if (!prsByOrg[org]) { prsByOrg[org] = []; }
                prsByOrg[org].push({ pr, repoSlug });
            }

            for (const [org, prs] of Object.entries(prsByOrg)) {
                try {
                    // Switch account once per org
                    await switchGhAccount(`${org}/dummy`);
                } catch {
                    console.error(`[FeatureDetail] Failed to switch gh account for ${org}`);
                    continue;
                }

                for (const { pr, repoSlug } of prs) {
                    try {
                        const prNumber = pr.prNumber || pr.number;
                        if (!prNumber) { continue; }

                                        const json = await runCommand(
                            `gh pr view ${prNumber} --repo "${repoSlug}" --json state,title,url,comments,reviews`,
                            undefined, 15000 // 15s timeout
                        );
                        const prData = JSON.parse(json);

                        const stateMap: Record<string, string> = {
                            'OPEN': 'open', 'MERGED': 'merged', 'CLOSED': 'closed',
                        };
                        const newStatus = stateMap[prData.state] || prData.state?.toLowerCase() || pr.status;

                        if (newStatus !== pr.status || prData.title !== pr.title) {
                            pr.status = newStatus;
                            if (prData.title) { pr.title = prData.title; }
                            if (prData.url) { pr.prUrl = prData.url; }
                            changed = true;
                        }

                        // Count review comments (from reviews + comments)
                        const reviews = prData.reviews || [];
                        const comments = prData.comments || [];
                        const totalComments = reviews.length + comments.length;
                        // Count resolved: reviews/comments with state RESOLVED or DISMISSED
                        const resolvedComments = reviews.filter((r: any) => r.state === 'APPROVED' || r.state === 'DISMISSED').length;
                        if (pr.totalComments !== totalComments || pr.resolvedComments !== resolvedComments) {
                            pr.totalComments = totalComments;
                            pr.resolvedComments = resolvedComments;
                            changed = true;
                        }
                    } catch (e) {
                        console.error(`[FeatureDetail] Failed to refresh PR #${pr.prNumber}:`, e);
                    }
                }
            }
        }

        // --- Refresh PBI statuses from ADO via az CLI ---
        const pbis = feature.artifacts?.pbis || [];
        if (pbis.length > 0) {
            try {
                // Check if az CLI is available and authenticated (quick test)
                await runCommand('az account show --only-show-errors -o none', undefined, 5000);

                for (const pbi of pbis) {
                    try {
                        const adoId = pbi.adoId;
                        if (!adoId) { continue; }

                        const json = await runCommand(
                            `az boards work-item show --id ${adoId} --org "https://dev.azure.com/IdentityDivision" --only-show-errors -o json`,
                            undefined, 15000 // 15s timeout
                        );
                        const wiData = JSON.parse(json);
                        const fields = wiData.fields || {};

                        const newState = fields['System.State'];
                        if (newState && newState !== pbi.status) {
                            pbi.status = newState;
                            changed = true;
                        }
                        // Also update title if it changed
                        const newTitle = fields['System.Title'];
                        if (newTitle && newTitle !== pbi.title) {
                            pbi.title = newTitle;
                            changed = true;
                        }
                    } catch (e) {
                        console.error(`[FeatureDetail] Failed to refresh PBI AB#${pbi.adoId}:`, e);
                    }
                }

                // Also sync to legacy pbis array
                if (changed && feature.pbis) {
                    for (const legacyPbi of feature.pbis) {
                        const artPbi = pbis.find((p: any) => p.adoId === legacyPbi.adoId);
                        if (artPbi) {
                            legacyPbi.status = artPbi.status;
                            legacyPbi.title = artPbi.title;
                        }
                    }
                }
            } catch {
                // az CLI not available or not authenticated — skip PBI refresh silently
                console.log('[FeatureDetail] az CLI not available, skipping PBI status refresh');
            }
        }

        if (changed) {
            feature.updatedAt = Date.now();
            FeatureDetailPanel.writeState(state);
        }
    }

    private static getHtml(feature: any): string {
        const artifacts: FeatureArtifacts = feature.artifacts || { pbis: [], agentPrs: [] };
        const design = artifacts.design;
        const rawPbis: any[] = artifacts.pbis || feature.pbis || [];
        const agentPrs = artifacts.agentPrs || feature.agentSessions || [];

        // Normalize PBI fields — state may use different field names depending on how it was written
        const pbis = rawPbis.map((p: any) => {
            // adoId can be: p.adoId (number), p.id ("AB#12345"), or missing
            let adoId: string | number = p.adoId || p.id || '?';
            if (typeof adoId === 'string') {
                adoId = adoId.replace(/^AB#/, ''); // strip "AB#" prefix for URL building
            }
            // dependsOn can be: array of numbers, array of "AB#NNN" strings, or missing
            let dependsOn: string[] = [];
            if (Array.isArray(p.dependsOn)) {
                dependsOn = p.dependsOn.map((d: any) => String(d).replace(/^AB#/, ''));
            }
            return {
                adoId,
                displayId: p.id || (p.adoId ? `AB#${p.adoId}` : '?'), // what to show in UI
                title: p.title || '',
                module: p.module || p.repo || p.targetRepo || '',
                adoUrl: p.adoUrl || `https://dev.azure.com/${ADO_ORG}/${ADO_PROJECT}/_workitems/edit/${adoId}`,
                status: p.status || 'new',
                dependsOn,
                priority: p.priority,
            };
        });

        const stepConfig: Record<string, { icon: string; label: string; color: string }> = {
            'idle':           { icon: '⏳', label: 'Ready',                 color: '#8b949e' },
            'designing':      { icon: '📝', label: 'Writing Design',       color: '#58a6ff' },
            'design_review':  { icon: '👀', label: 'Design Review',        color: '#d29922' },
            'planning':       { icon: '📋', label: 'Planning PBIs',        color: '#58a6ff' },
            'plan_review':    { icon: '👀', label: 'Plan Review',          color: '#d29922' },
            'backlogging':    { icon: '📝', label: 'Adding to Backlog',    color: '#58a6ff' },
            'backlog_review': { icon: '👀', label: 'Backlog Review',       color: '#d29922' },
            'dispatching':    { icon: '🚀', label: 'Dispatching',          color: '#58a6ff' },
            'monitoring':     { icon: '📡', label: 'Monitoring Agents',    color: '#3fb950' },
            'done':           { icon: '✅', label: 'Complete',              color: '#3fb950' },
        };

        // Normalize step names: map aliases/past-tense/legacy names to canonical keys
        const stepAliases: Record<string, string> = {
            'designed': 'design_review', 'design_complete': 'design_review',
            'planned': 'plan_review', 'plan_complete': 'plan_review',
            'backlogged': 'backlog_review', 'created': 'backlog_review', 'creating': 'backlogging', 'create_review': 'backlog_review',
            'dispatched': 'monitoring', 'dispatch_complete': 'monitoring',
            'complete': 'done', 'completed': 'done',
        };
        const normalizedStep = stepAliases[feature.step] || feature.step;

        const cfg = stepConfig[normalizedStep] || stepConfig['idle'];

        const pipelineStages = [
            { key: 'designing',      label: 'Design' },
            { key: 'planning',       label: 'Plan' },
            { key: 'backlogging',    label: 'Backlog' },
            { key: 'dispatching',    label: 'Dispatch' },
            { key: 'monitoring',     label: 'Monitor' },
        ];
        const stageOrder = ['idle', 'designing', 'design_review', 'planning', 'plan_review', 'backlogging', 'backlog_review', 'dispatching', 'monitoring', 'done'];
        const currentIdx = stageOrder.indexOf(normalizedStep);

        const pipelineHtml = pipelineStages.map((stage, i) => {
            // Each stage maps to 2 entries in stageOrder (active + review), roughly at i*2+1
            const stageIdx = i * 2 + 1;
            const isActive = currentIdx >= stageIdx && currentIdx < stageIdx + 2;
            const isDone = currentIdx >= stageIdx + 2;
            const cls = isDone ? 'stage done' : isActive ? 'stage active' : 'stage';
            return `<div class="${cls}">${isDone ? '✅' : isActive ? '🔵' : '○'} ${stage.label}</div>`;
        }).join('<div class="stage-arrow">→</div>');

        // Design section
        const designHtml = design
            ? `<div class="artifact-card">
                <div class="artifact-header">📄 Design Spec</div>
                <div class="artifact-body">
                  ${design.docPath ? `<div class="artifact-row"><span class="label">Document:</span> <a href="#" onclick="openFile('${escapeAttr(design.docPath)}')">${escapeHtml(design.docPath)}</a></div>` : ''}
                  ${design.prUrl ? `<div class="artifact-row"><span class="label">PR:</span> <a href="#" onclick="openUrl('${escapeAttr(design.prUrl)}')">View in ADO</a></div>` : ''}
                  <div class="artifact-row"><span class="label">Status:</span> <span class="badge badge-${design.status}">${design.status}</span></div>
                </div>
              </div>`
            : (feature.designDocPath
                ? `<div class="artifact-card">
                    <div class="artifact-header">📄 Design Spec</div>
                    <div class="artifact-body">
                      <div class="artifact-row"><span class="label">Document:</span> <a href="#" onclick="openFile('${escapeAttr(feature.designDocPath)}')">${escapeHtml(feature.designDocPath)}</a></div>
                      ${feature.designPrUrl ? `<div class="artifact-row"><span class="label">PR:</span> <a href="#" onclick="openUrl('${escapeAttr(feature.designPrUrl)}')">View in ADO</a></div>` : ''}
                    </div>
                  </div>`
                : '<div class="artifact-card muted-card"><div class="artifact-header">📄 Design Spec</div><div class="artifact-body"><p class="muted">Not yet created</p></div></div>');

        // PBIs section
        const hasDeps = pbis.some((p: any) => p.dependsOn && p.dependsOn.length > 0);

        // Compute topological order for implementation sequence
        const pbiOrder = new Map<string, number>();
        if (pbis.length > 0) {
            // Build adjacency: each PBI's dependencies
            const resolved = new Set<string>();
            const remaining = pbis.map((p: any) => ({ id: String(p.adoId), deps: (p.dependsOn || []).map(String) }));
            let order = 1;
            let maxIter = pbis.length + 1; // safety limit
            while (remaining.length > 0 && maxIter-- > 0) {
                const ready = remaining.filter(r => r.deps.every((d: string) => resolved.has(d)));
                if (ready.length === 0) {
                    // Cycle or unresolved deps — assign remaining in original order
                    for (const r of remaining) { pbiOrder.set(r.id, order++); }
                    break;
                }
                for (const r of ready) {
                    pbiOrder.set(r.id, order++);
                    resolved.add(r.id);
                    remaining.splice(remaining.indexOf(r), 1);
                }
            }
        }

        const pbisHtml = pbis.length > 0
            ? `<div class="artifact-card">
                <div class="artifact-header">📋 Product Backlog Items <span class="count">${pbis.length}</span></div>
                <div class="artifact-body">
                  <table class="artifact-table">
                    <thead><tr><th>Order</th><th>AB#</th><th>Title</th><th>Repo</th>${hasDeps ? '<th>Depends On</th>' : ''}<th>Status</th></tr></thead>
                    <tbody>
                      ${pbis.map((p: any) => {
                        const statusClass = (p.status || 'new').toLowerCase().replace(/\s/g, '-');
                        const depsCell = hasDeps
                            ? `<td>${(p.dependsOn || []).map((d: string) => `<a href="#" onclick="openUrl('https://dev.azure.com/${ADO_ORG}/${ADO_PROJECT}/_workitems/edit/${d}')">AB#${d}</a>`).join(', ') || '—'}</td>`
                            : '';
                        const orderNum = pbiOrder.get(String(p.adoId)) || '—';
                        return `<tr>
                          <td class="order-cell">${orderNum}</td>
                          <td><a href="#" onclick="openUrl('${escapeAttr(p.adoUrl)}')">${escapeHtml(String(p.displayId))}</a></td>
                          <td>${escapeHtml(p.title || '')}</td>
                          <td><code>${escapeHtml(p.module || '')}</code></td>
                          ${depsCell}
                          <td><span class="badge badge-${statusClass}">${escapeHtml(p.status || 'new')}</span></td>
                        </tr>`;
                      }).join('')}
                    </tbody>
                  </table>
                </div>
              </div>`
            : '<div class="artifact-card muted-card"><div class="artifact-header">📋 Product Backlog Items</div><div class="artifact-body"><p class="muted">No PBIs yet</p></div></div>';

        // Agent PRs section
        const prsHtml = agentPrs.length > 0
            ? `<div class="artifact-card">
                <div class="artifact-header">🤖 Agent Pull Requests <span class="count">${agentPrs.length}</span></div>
                <div class="artifact-body">
                  <table class="artifact-table">
                    <thead><tr><th>PR</th><th>Repo</th><th>Title</th><th>Comments</th><th>Status</th></tr></thead>
                    <tbody>
                      ${agentPrs.map((pr: any) => {
                        const prUrl = pr.prUrl || pr.url || '#';
                        const statusColor: Record<string, string> = { open: '#3fb950', merged: '#8957e5', closed: '#da3633', draft: '#8b949e' };
                        const status = (pr.status || pr.state || 'open').toLowerCase();
                        const totalComments = pr.totalComments ?? '—';
                        const resolvedComments = pr.resolvedComments ?? 0;
                        const commentsDisplay = totalComments === '—' ? '—'
                            : `<span class="comments-count">${resolvedComments}/${totalComments}</span>`;
                        return `<tr>
                          <td><a href="#" onclick="openUrl('${escapeAttr(prUrl)}')">#${pr.prNumber || pr.number || '?'}</a></td>
                          <td><code>${escapeHtml(pr.repo || '')}</code></td>
                          <td>${escapeHtml(pr.title || '')}</td>
                          <td>${commentsDisplay}</td>
                          <td><span class="badge" style="background:${statusColor[status] || '#8b949e'}">${status}</span></td>
                        </tr>`;
                      }).join('')}
                    </tbody>
                  </table>
                </div>
              </div>`
            : '<div class="artifact-card muted-card"><div class="artifact-header">🤖 Agent Pull Requests</div><div class="artifact-body"><p class="muted">No agent PRs yet</p></div></div>';

        const timeAgo = (ts: number) => {
            if (!ts) return 'unknown';
            const s = Math.floor((Date.now() - ts) / 1000);
            if (s < 60) return 'just now';
            if (s < 3600) return `${Math.floor(s / 60)}m ago`;
            if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
            return `${Math.floor(s / 86400)}d ago`;
        };

        return `<!DOCTYPE html>
<html><head><style>
* { box-sizing: border-box; }
body {
    font-family: var(--vscode-font-family, system-ui);
    font-size: 13px;
    color: var(--vscode-foreground);
    background: var(--vscode-editor-background);
    padding: 24px 32px;
    margin: 0;
    line-height: 1.5;
}

/* Header */
.header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
}
.header-icon { font-size: 28px; }
.header-info { flex: 1; }
.header-info h1 { margin: 0; font-size: 20px; font-weight: 600; }
.header-info .subtitle { color: var(--vscode-descriptionForeground); font-size: 12px; margin-top: 2px; }
.header-actions { display: flex; gap: 8px; }
.btn {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none; border-radius: 4px;
    padding: 6px 14px; font-size: 12px;
    cursor: pointer; font-weight: 600;
}
.btn:hover { background: var(--vscode-button-hoverBackground); }
.btn-secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
}
.btn-secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }

/* Pipeline */
.pipeline {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 16px;
    background: var(--vscode-editorWidget-background);
    border: 1px solid var(--vscode-widget-border);
    border-radius: 8px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}
.stage {
    font-size: 12px;
    color: var(--vscode-descriptionForeground);
    padding: 4px 10px;
    border-radius: 12px;
    white-space: nowrap;
}
.stage.active {
    color: var(--vscode-button-foreground);
    background: var(--vscode-progressBar-background);
    font-weight: 600;
}
.stage.done { color: #3fb950; font-weight: 500; }
.stage-arrow { color: var(--vscode-descriptionForeground); font-size: 12px; }

/* Status bar */
.status-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
    font-size: 12px;
    color: var(--vscode-descriptionForeground);
}
.status-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 12px;
    border: 1px solid ${cfg.color}40;
    background: ${cfg.color}15;
    color: ${cfg.color};
    font-weight: 600;
}

/* Artifact cards */
.artifact-card {
    border: 1px solid var(--vscode-widget-border);
    border-radius: 8px;
    margin-bottom: 12px;
    overflow: hidden;
}
.muted-card { opacity: 0.6; }
.artifact-header {
    background: var(--vscode-editorWidget-background);
    padding: 10px 14px;
    font-weight: 600;
    font-size: 13px;
    border-bottom: 1px solid var(--vscode-widget-border);
    display: flex;
    align-items: center;
    gap: 8px;
}
.artifact-body { padding: 12px 14px; }
.artifact-row { margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
.label { color: var(--vscode-descriptionForeground); font-size: 11px; min-width: 70px; text-transform: uppercase; letter-spacing: 0.5px; }
.count {
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
}

/* Table */
.artifact-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.artifact-table th {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--vscode-widget-border);
    color: var(--vscode-descriptionForeground);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.artifact-table td { padding: 6px 8px; border-bottom: 1px solid var(--vscode-widget-border, transparent); }
.artifact-table tr:last-child td { border-bottom: none; }
.artifact-table code {
    background: var(--vscode-textCodeBlock-background);
    padding: 1px 5px; border-radius: 3px; font-size: 11px;
}

/* Badge */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    text-transform: capitalize;
}
.badge-draft { background: #8b949e30; color: #8b949e; }
.badge-in-review { background: #d2992230; color: #d29922; }
.badge-approved { background: #3fb95030; color: #3fb950; }
.badge-new { background: #58a6ff30; color: #58a6ff; }
.badge-committed { background: #d2992230; color: #d29922; }
.badge-in-progress { background: #f7883030; color: #f78830; }
.badge-active { background: #3fb95030; color: #3fb950; }
.badge-resolved { background: #8957e530; color: #8957e5; }
.badge-closed { background: #8b949e30; color: #8b949e; }

a { color: var(--vscode-textLink-foreground); text-decoration: none; cursor: pointer; }
a:hover { text-decoration: underline; }
.muted { color: var(--vscode-descriptionForeground); font-style: italic; }

/* Comments count */
.comments-count { font-size: 11px; font-weight: 600; color: var(--vscode-descriptionForeground); }

/* Order column */
.order-cell { font-weight: 700; color: var(--vscode-descriptionForeground); text-align: center; min-width: 24px; }
.prompt-block {
    background: var(--vscode-textCodeBlock-background);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    margin-bottom: 16px;
    color: var(--vscode-descriptionForeground);
    white-space: pre-wrap;
    word-break: break-word;
}

.section-title {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--vscode-descriptionForeground);
    margin: 20px 0 8px;
}
</style></head>
<body>
  <div class="header">
    <div class="header-icon">${cfg.icon}</div>
    <div class="header-info">
      <h1>${escapeHtml(feature.name)}</h1>
      <div class="subtitle">Started ${timeAgo(feature.startedAt)} · Updated ${timeAgo(feature.updatedAt)}</div>
    </div>
    <div class="header-actions">
      <button class="btn btn-secondary" id="refreshBtn" onclick="refresh()">↻ Refresh</button>
      <button class="btn" onclick="continueInChat()">💬 Continue in New Chat</button>
    </div>
  </div>

  ${feature.prompt ? `<div class="prompt-block">${escapeHtml(feature.prompt)}</div>` : ''}

  <div class="pipeline">${pipelineHtml}</div>

  <div class="status-bar">
    <div class="status-indicator">${cfg.icon} ${cfg.label}</div>
  </div>

  <div class="section-title">Artifacts</div>
  ${designHtml}
  ${pbisHtml}
  ${prsHtml}

  <script>
    const vscode = acquireVsCodeApi();
    function openUrl(url) { vscode.postMessage({ command: 'openUrl', url }); }
    function openFile(p) { vscode.postMessage({ command: 'openFile', path: p }); }
    function continueInChat() { vscode.postMessage({ command: 'continueInChat' }); }
    function refresh() {
      const btn = document.getElementById('refreshBtn');
      if (btn) { btn.textContent = '↻ Refreshing...'; btn.disabled = true; }
      vscode.postMessage({ command: 'refresh' });
    }
    // Listen for messages from the extension
    window.addEventListener('message', (event) => {
      if (event.data.command === 'refreshing' && !event.data.status) {
        const btn = document.getElementById('refreshBtn');
        if (btn) { btn.textContent = '↻ Refresh'; btn.disabled = false; }
      }
    });
  </script>
</body></html>`;
    }
}

function escapeHtml(s: string): string {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escapeAttr(s: string): string {
    return s.replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/\\/g, '\\\\');
}
