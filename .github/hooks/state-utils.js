#!/usr/bin/env node
/**
 * Shared state file utilities for the Feature Orchestrator.
 *
 * The state file (state.json) lives at ~/.android-auth-orchestrator/ and is
 * read/written by both hooks (via this CLI script) and the VS Code extension.
 *
 * Usage from hooks:
 *   node .github/hooks/state-utils.js get                  → prints full state JSON
 *   node .github/hooks/state-utils.js get-feature <id>     → prints one feature
 *   node .github/hooks/state-utils.js set-step <id> <step> → updates a feature's step
 *   node .github/hooks/state-utils.js add-feature <json>   → adds/updates a feature
 *   node .github/hooks/state-utils.js set-agent-info <id> <json> → sets agent session info
 *   node .github/hooks/state-utils.js set-design <id> <json>    → sets design artifact
 *   node .github/hooks/state-utils.js add-pbi <id> <json>       → adds a PBI artifact
 *   node .github/hooks/state-utils.js add-agent-pr <id> <json>  → adds an agent PR artifact
 *
 * State file schema:
 * {
 *   "version": 2,
 *   "features": [
 *     {
 *       "id": "feature-<timestamp>-<random>",
 *       "name": "Short feature name",
 *       "prompt": "Original user prompt",
 *       "step": "idle|designing|design_review|planning|plan_review|backlogging|backlog_review|dispatching|monitoring|done",
 *       "artifacts": {
 *         "design": { "docPath": "design-docs/.../spec.md", "prUrl": "https://...", "status": "draft|in-review|approved" },
 *         "pbis": [
 *           { "adoId": 12345, "title": "...", "targetRepo": "AzureAD/...", "module": "common", "adoUrl": "https://...", "status": "new|committed|active|resolved|closed", "priority": 1 }
 *         ],
 *         "agentPrs": [
 *           { "repo": "common", "prNumber": 2916, "prUrl": "https://...", "status": "open|merged|closed|draft", "title": "..." }
 *         ]
 *       },
 *       "designDocPath": "design-docs/.../spec.md",
 *       "designPrUrl": "https://dev.azure.com/...",
 *       "pbis": [
 *         { "adoId": 12345, "title": "...", "targetRepo": "AzureAD/...", "dependsOn": [], "status": "pending" }
 *       ],
 *       "agentSessions": [
 *         { "repo": "AzureAD/...", "prNumber": 2916, "prUrl": "https://...", "sessionUrl": "https://...", "status": "in_progress" }
 *       ],
 *       "startedAt": 1740000000000,
 *       "updatedAt": 1740000000000
 *     }
 *   ],
 *   "lastUpdated": 1740000000000
 * }
 */

const fs = require('fs');
const path = require('path');

const os = require('os');

// State file lives in user's home directory (not workspace root)
const STATE_DIR = path.join(os.homedir(), '.android-auth-orchestrator');
const STATE_FILE = path.join(STATE_DIR, 'state.json');

function ensureStateDir() {
    if (!fs.existsSync(STATE_DIR)) {
        fs.mkdirSync(STATE_DIR, { recursive: true });
    }
}

function readState() {
    if (!fs.existsSync(STATE_FILE)) {
        return { version: 1, features: [], lastUpdated: Date.now() };
    }
    try {
        return JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
    } catch {
        return { version: 1, features: [], lastUpdated: Date.now() };
    }
}

function writeState(state) {
    ensureStateDir();
    state.lastUpdated = Date.now();
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf-8');
}

/**
 * Find a feature by ID or name (case-insensitive).
 * The agent may pass either the auto-generated ID (feature-17723...) or the
 * human-readable name ("IPC Retry with Exponential Backoff"). Support both.
 * If multiple features match by name, return the most recently updated one.
 */
function findFeature(state, identifier) {
    // Try exact ID match first
    const byId = state.features.find(f => f.id === identifier);
    if (byId) return byId;

    // Try exact name match (case-insensitive)
    const lower = identifier.toLowerCase();
    const byName = state.features
        .filter(f => f.name && f.name.toLowerCase() === lower)
        .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    if (byName.length > 0) return byName[0];

    // Try partial name match as a last resort
    const partial = state.features
        .filter(f => f.name && f.name.toLowerCase().includes(lower))
        .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    return partial[0] || null;
}

// CLI
const [, , command, ...args] = process.argv;

switch (command) {
    case 'get': {
        console.log(JSON.stringify(readState(), null, 2));
        break;
    }
    case 'get-feature': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        console.log(JSON.stringify(feature || null, null, 2));
        break;
    }
    case 'set-step': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            feature.step = args[1];
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true, id: args[0], step: args[1] }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    case 'add-feature': {
        const state = readState();
        const feature = JSON.parse(args[0]);
        // Auto-generate ID if not provided
        if (!feature.id) {
            feature.id = 'feature-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
        }
        // Ensure required fields have defaults
        if (!feature.pbis) feature.pbis = [];
        if (!feature.agentSessions) feature.agentSessions = [];
        if (!feature.prompt) feature.prompt = '';
        // Also match by name when deduplicating
        const idx = state.features.findIndex(f => f.id === feature.id || (f.name && feature.name && f.name.toLowerCase() === feature.name.toLowerCase()));
        if (idx >= 0) {
            state.features[idx] = { ...state.features[idx], ...feature, updatedAt: Date.now() };
        } else {
            state.features.push({ ...feature, startedAt: Date.now(), updatedAt: Date.now() });
        }
        writeState(state);
        console.log(JSON.stringify({ ok: true, id: feature.id }));
        break;
    }
    case 'set-agent-info': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const info = JSON.parse(args[1]);
            if (!feature.agentSessions) feature.agentSessions = [];
            feature.agentSessions.push(info);
            // Also add to artifacts.agentPrs
            if (!feature.artifacts) feature.artifacts = { pbis: [], agentPrs: [] };
            if (!feature.artifacts.agentPrs) feature.artifacts.agentPrs = [];
            feature.artifacts.agentPrs.push({
                repo: info.repo,
                prNumber: info.prNumber || info.number,
                prUrl: info.prUrl || info.url,
                status: info.status || 'open',
                title: info.title || '',
            });
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
        }
        break;
    }
    case 'set-design': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const design = JSON.parse(args[1]);
            if (!feature.artifacts) feature.artifacts = { pbis: [], agentPrs: [] };
            feature.artifacts.design = design;
            // Also set legacy fields for backward compat
            if (design.docPath) feature.designDocPath = design.docPath;
            if (design.prUrl) feature.designPrUrl = design.prUrl;
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    case 'add-pbi': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const pbi = JSON.parse(args[1]);
            if (!feature.artifacts) feature.artifacts = { pbis: [], agentPrs: [] };
            if (!feature.artifacts.pbis) feature.artifacts.pbis = [];
            // Avoid duplicates by adoId
            const existingIdx = feature.artifacts.pbis.findIndex(p => p.adoId === pbi.adoId);
            if (existingIdx >= 0) {
                feature.artifacts.pbis[existingIdx] = { ...feature.artifacts.pbis[existingIdx], ...pbi };
            } else {
                feature.artifacts.pbis.push(pbi);
            }
            // Also maintain legacy pbis array
            if (!feature.pbis) feature.pbis = [];
            const legacyIdx = feature.pbis.findIndex(p => p.adoId === pbi.adoId);
            if (legacyIdx >= 0) {
                feature.pbis[legacyIdx] = { ...feature.pbis[legacyIdx], ...pbi };
            } else {
                feature.pbis.push({ adoId: pbi.adoId, title: pbi.title, targetRepo: pbi.targetRepo, status: pbi.status || 'pending' });
            }
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true, pbiCount: feature.artifacts.pbis.length }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    case 'add-agent-pr': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const pr = JSON.parse(args[1]);
            if (!feature.artifacts) feature.artifacts = { pbis: [], agentPrs: [] };
            if (!feature.artifacts.agentPrs) feature.artifacts.agentPrs = [];
            // Avoid duplicates by prNumber+repo
            const existingIdx = feature.artifacts.agentPrs.findIndex(p => p.prNumber === pr.prNumber && p.repo === pr.repo);
            if (existingIdx >= 0) {
                feature.artifacts.agentPrs[existingIdx] = { ...feature.artifacts.agentPrs[existingIdx], ...pr };
            } else {
                feature.artifacts.agentPrs.push(pr);
            }
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true, prCount: feature.artifacts.agentPrs.length }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    default:
        console.error('Usage: state-utils.js <get|get-feature|set-step|add-feature|set-agent-info|set-design|add-pbi|add-agent-pr> [args]');
        process.exit(1);
}
