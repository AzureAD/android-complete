import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Run a terminal command and return the output.
 */
export function runCommand(command: string, cwd?: string, timeoutMs?: number): Promise<string> {
    return new Promise((resolve, reject) => {
        const options: cp.ExecOptions = {
            cwd: cwd ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
            maxBuffer: 1024 * 1024, // 1MB
            timeout: timeoutMs ?? 60000,
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

/**
 * Resolve GitHub account mapping for the current developer.
 * Discovery sequence:
 * 1. .github/developer-local.json
 * 2. gh auth status (parse logged-in accounts)
 * 3. Prompt the developer (via VS Code input box)
 */
async function resolveGhAccounts(): Promise<Record<string, string>> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

    // Step 1: Check .github/developer-local.json
    if (workspaceRoot) {
        const configPath = path.join(workspaceRoot, '.github', 'developer-local.json');
        if (fs.existsSync(configPath)) {
            try {
                const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
                const accounts = config.github_accounts;
                if (accounts?.AzureAD && accounts?.['identity-authnz-teams']) {
                    return accounts;
                }
            } catch {
                // Fall through to discovery
            }
        }
    }

    // Step 2: Discover from gh auth status
    try {
        // First verify gh is installed
        try {
            await runCommand('gh --version');
        } catch {
            // gh not installed — offer to install
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

        const status = await runCommand('gh auth status 2>&1');
        const accounts: Record<string, string> = {};
        for (const line of status.split('\n')) {
            const match = line.match(/account\s+(\S+)/);
            if (match) {
                const username = match[1];
                if (username.includes('_')) {
                    accounts['identity-authnz-teams'] = username;
                } else {
                    accounts['AzureAD'] = username;
                }
            }
        }
        if (accounts['AzureAD'] && accounts['identity-authnz-teams']) {
            return accounts;
        }
    } catch {
        // Fall through to prompt
    }

    // Step 3: Prompt the developer
    const publicUser = await vscode.window.showInputBox({
        prompt: 'Enter your public GitHub username (for AzureAD/* repos like common, msal)',
        placeHolder: 'e.g., myusername',
    });
    const emuUser = await vscode.window.showInputBox({
        prompt: 'Enter your GitHub EMU username (for identity-authnz-teams/* repos like broker)',
        placeHolder: 'e.g., myusername_microsoft',
    });

    if (!publicUser || !emuUser) {
        throw new Error('GitHub usernames are required for dispatching. Please configure them.');
    }

    const accounts: Record<string, string> = {
        'AzureAD': publicUser,
        'identity-authnz-teams': emuUser,
    };

    // Offer to save
    if (workspaceRoot) {
        const save = await vscode.window.showQuickPick(['Yes', 'No'], {
            placeHolder: 'Save to .github/developer-local.json for next time?',
        });
        if (save === 'Yes') {
            const configPath = path.join(workspaceRoot, '.github', 'developer-local.json');
            const config = { github_accounts: accounts };
            fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n', 'utf-8');
        }
    }

    return accounts;
}

/**
 * Switch the gh CLI to the correct account for a given repo.
 */
export async function switchGhAccount(repo: string): Promise<void> {
    const org = repo.split('/')[0];
    const accountMap = await resolveGhAccounts();

    const account = accountMap[org];
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
