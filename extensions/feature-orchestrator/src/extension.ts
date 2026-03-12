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
import { DashboardViewProvider } from './dashboard';
import { DesignReviewController } from './designReview';
import { FeatureDetailPanel } from './featureDetail';

export function activate(context: vscode.ExtensionContext) {
    // Register the sidebar dashboard (reads from orchestrator-state.json)
    const dashboardProvider = new DashboardViewProvider(context);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            DashboardViewProvider.viewType,
            dashboardProvider
        )
    );

    // Register the design review commenting system
    const designReview = new DesignReviewController(context);
    designReview.register();

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('orchestrator.refreshDashboard', () => {
            dashboardProvider.refresh();
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('orchestrator.newFeature', async () => {
            const panel = vscode.window.createWebviewPanel(
                'orchestrator.newFeature', 'New Feature',
                vscode.ViewColumn.Active, { enableScripts: true }
            );
            panel.webview.html = getNewFeatureHtml();
            panel.webview.onDidReceiveMessage(async (message) => {
                if (message.command === 'submit') {
                    panel.dispose();
                    // Open chat with the design prompt file
                    await vscode.commands.executeCommand('workbench.action.chat.newChat');
                    vscode.commands.executeCommand('workbench.action.chat.open', {
                        query: `/feature-design ${message.prompt}`,
                    });
                }
            });
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('orchestrator.openFeatureDetail', (featureId: string) => {
            FeatureDetailPanel.show(context, featureId);
        })
    );

    console.log('Feature Orchestrator extension activated');
}

function getNewFeatureHtml(): string {
    return `<!DOCTYPE html>
<html><head><style>
body { font-family: var(--vscode-font-family, system-ui); padding: 40px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 60vh; color: var(--vscode-foreground, #ccc); background: var(--vscode-editor-background, #1e1e1e); }
h1 { font-size: 24px; margin-bottom: 8px; }
p { color: var(--vscode-descriptionForeground, #888); margin-bottom: 24px; font-size: 14px; max-width: 600px; text-align: center; }
textarea { width: 100%; max-width: 600px; height: 120px; padding: 12px; font-size: 14px; font-family: inherit; border: 2px solid var(--vscode-focusBorder, #007acc); border-radius: 8px; background: var(--vscode-input-background, #3c3c3c); color: var(--vscode-input-foreground, #ccc); resize: vertical; }
textarea:focus { outline: none; box-shadow: 0 0 0 2px rgba(0,122,204,0.3); }
textarea::placeholder { color: var(--vscode-input-placeholderForeground, #888); }
button { margin-top: 16px; padding: 12px 32px; font-size: 14px; font-weight: 600; border: none; border-radius: 6px; cursor: pointer; background: var(--vscode-button-background, #007acc); color: var(--vscode-button-foreground, #fff); }
button:hover { background: var(--vscode-button-hoverBackground, #005a9e); }
.examples { color: var(--vscode-descriptionForeground, #888); font-size: 12px; margin-top: 16px; max-width: 600px; }
.examples code { background: var(--vscode-textCodeBlock-background, #2d2d2d); padding: 2px 6px; border-radius: 3px; }
</style></head><body>
  <h1>🚀 New Feature</h1>
  <p>Describe the feature. The <strong>design-author</strong> agent will research the codebase and create a detailed design spec.</p>
  <textarea id="prompt" placeholder="e.g., Add retry logic with exponential backoff to IPC calls" autofocus></textarea>
  <button onclick="submit()">Start Design</button>
  <div class="examples">
    <strong>Examples:</strong><br>
    <code>Add retry logic to IPC calls</code> · <code>Implement certificate-based auth</code> · <code>Add PRT latency telemetry</code>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    function submit() { const p = document.getElementById('prompt').value.trim(); if (p) vscode.postMessage({ command: 'submit', prompt: p }); }
    document.getElementById('prompt').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit(); });
  </script>
</body></html>`;
}

export function deactivate() {}
