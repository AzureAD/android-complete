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
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { getDeveloperLocalConfigPath } from './config';

/**
 * Run a terminal command and return the output.
 */
export function runCommand(command: string, cwd?: string, timeoutMs?: number): Promise<string> {
    return new Promise((resolve, reject) => {
        const options: cp.ExecOptions = {
            cwd: cwd ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
            maxBuffer: 1024 * 1024, // 1MB
            timeout: timeoutMs ?? 60000,
            encoding: 'utf-8',
        };

        cp.exec(command, options, (error, stdout, stderr) => {
            if (error) {
                reject(new Error(`Command failed: ${command}\n${stderr?.toString() || error.message}`));
                return;
            }
            resolve(stdout?.toString().trim() ?? '');
        });
    });
}

function loadGhAccountMap(): Record<string, string> {
    const configPath = getDeveloperLocalConfigPath();
    if (!configPath || !fs.existsSync(configPath)) {
        return {};
    }

    try {
        const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
        const accounts = config.github_accounts;
        return accounts && typeof accounts === 'object' ? accounts : {};
    } catch {
        return {};
    }
}

async function resolveGhAccountForRepo(repo: string): Promise<string> {
    const org = repo.split('/')[0] || '';
    const accountMap = loadGhAccountMap();

    if (accountMap[repo]) {
        return accountMap[repo];
    }
    if (accountMap[org]) {
        return accountMap[org];
    }

    try {
        await runCommand('gh --version');
    } catch {
        const install = await vscode.window.showWarningMessage(
            'GitHub CLI (gh) is not installed. It\'s required for dispatching PBIs to Copilot coding agent.',
            'Install now',
            'I\'ll install manually'
        );
        if (install === 'Install now') {
            const terminal = vscode.window.createTerminal('Install gh CLI');
            terminal.show();
            if (process.platform === 'win32') {
                terminal.sendText('winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements');
            } else if (process.platform === 'darwin') {
                terminal.sendText('brew install gh');
            } else {
                terminal.sendText('echo "Please install gh CLI: https://cli.github.com"');
            }
            vscode.window.showInformationMessage(
                'Installing gh CLI... After installation, run `gh auth login` in a terminal, then retry.'
            );
        }
        throw new Error('gh CLI not installed. Install it and run `gh auth login`, then retry.');
    }

    try {
        const currentLogin = (await runCommand('gh api user --jq .login', undefined, 10000)).trim();
        if (currentLogin) {
            return currentLogin;
        }
    } catch {
        // Fall through to prompt
    }

    const repoLabel = repo || 'target repository';
    const username = await vscode.window.showInputBox({
        prompt: `Enter your GitHub username for ${repoLabel}`,
        placeHolder: 'e.g., myusername',
    });

    if (!username) {
        throw new Error('GitHub username is required for dispatching. Please configure it.');
    }

    const save = await vscode.window.showQuickPick(['Yes', 'No'], {
        placeHolder: 'Save this mapping to .github/developer-local.json for next time?',
    });

    if (save === 'Yes') {
        const configPath = getDeveloperLocalConfigPath();
        if (configPath) {
            const existing = loadGhAccountMap();
            existing[repo] = username;
            const configDir = path.dirname(configPath);
            if (!fs.existsSync(configDir)) {
                fs.mkdirSync(configDir, { recursive: true });
            }
            fs.writeFileSync(configPath, JSON.stringify({ github_accounts: existing }, null, 2) + '\n', 'utf-8');
        }
    }

    return username;
}

/**
 * Switch the gh CLI to the correct account for a given repo.
 */
export async function switchGhAccount(repo: string): Promise<void> {
    const account = await resolveGhAccountForRepo(repo);
    if (account) {
        await runCommand(`gh auth switch --user ${account}`);
    }
}

/**
 * Check the status of Copilot agent PRs for a repo.
 */
export async function getAgentPRs(repo: string): Promise<string> {
    await switchGhAccount(repo);

    return runCommand(
        `gh pr list --repo "${repo}" --author "copilot-swe-agent[bot]" --state all --limit 10 --json number,title,state,createdAt,url`
    );
}
