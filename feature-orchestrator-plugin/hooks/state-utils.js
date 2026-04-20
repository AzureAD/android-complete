#!/usr/bin/env node
/**
 * Feature Orchestrator — State Management CLI
 *
 * Manages feature pipeline state for the orchestrator dashboard.
 * State is stored at ~/.feature-orchestrator/state.json (fixed location).
 * This script is installed to ~/.feature-orchestrator/state-utils.js during setup.
 *
 * Usage:
 *   node ~/.feature-orchestrator/state-utils.js add-feature '{"name": "...", "step": "designing"}'
 *   node ~/.feature-orchestrator/state-utils.js set-step "<name>" <step>
 *   node ~/.feature-orchestrator/state-utils.js set-design "<name>" '{"docPath":"...","status":"approved"}'
 *   node ~/.feature-orchestrator/state-utils.js add-pbi "<name>" '{"adoId":123,"title":"...","module":"...","status":"Committed"}'
 *   node ~/.feature-orchestrator/state-utils.js add-agent-pr "<name>" '{"repo":"...","prNumber":1,"prUrl":"...","status":"open"}'
 *   node ~/.feature-orchestrator/state-utils.js list-features
 *   node ~/.feature-orchestrator/state-utils.js get-feature "<name>"
 *   node ~/.feature-orchestrator/state-utils.js get
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

// Fixed state directory — always ~/.feature-orchestrator/
const STATE_DIR = path.join(os.homedir(), '.feature-orchestrator');
const STATE_FILE = path.join(STATE_DIR, 'state.json');

function ensureDir() {
    if (!fs.existsSync(STATE_DIR)) fs.mkdirSync(STATE_DIR, { recursive: true });
}

function readState() {
    if (!fs.existsSync(STATE_FILE)) return { version: 1, features: [], lastUpdated: 0 };
    try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8')); }
    catch { return { version: 1, features: [], lastUpdated: 0 }; }
}

function writeState(state) {
    ensureDir();
    state.lastUpdated = Date.now();
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf-8');
}

function findFeature(state, identifier) {
    if (!identifier) return null;
    const byId = state.features.find(f => f.id === identifier);
    if (byId) return byId;
    const lower = identifier.toLowerCase();
    return state.features.find(f => f.name && f.name.toLowerCase() === lower)
        || state.features.find(f => f.name && f.name.toLowerCase().includes(lower))
        || null;
}

function checkAutoCompletion(feature) {
    if (!feature.artifacts) return;
    const pbis = feature.artifacts.pbis || [];
    const prs = feature.artifacts.agentPrs || [];
    if (pbis.length === 0) return;
    const allPbisResolved = pbis.every(p =>
        ['Resolved', 'Done', 'Closed', 'Removed'].includes(p.status));
    const allPrsClosed = prs.length > 0 && prs.every(p =>
        ['merged', 'closed'].includes(p.status));
    if (allPbisResolved && allPrsClosed && feature.step !== 'completed') {
        feature.step = 'completed';
        feature.completedAt = Date.now();
        if (!feature.phaseTimestamps) feature.phaseTimestamps = {};
        feature.phaseTimestamps.completed = Date.now();
    }
}

const [,, command, ...args] = process.argv;

switch (command) {
    case 'get': {
        const state = readState();
        console.log(JSON.stringify(state, null, 2));
        break;
    }
    case 'list-features': {
        const state = readState();
        console.log(JSON.stringify(state.features.map(f => ({
            name: f.name, step: f.step, id: f.id,
            updatedAt: new Date(f.updatedAt).toISOString()
        })), null, 2));
        break;
    }
    case 'get-feature': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        console.log(JSON.stringify(feature || null, null, 2));
        break;
    }
    case 'add-feature': {
        const state = readState();
        const feature = JSON.parse(args[0]);
        if (!feature.id) {
            feature.id = 'feature-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
        }
        const idx = state.features.findIndex(f =>
            f.name && feature.name && f.name.toLowerCase() === feature.name.toLowerCase());
        if (idx >= 0) {
            state.features[idx] = { ...state.features[idx], ...feature, updatedAt: Date.now() };
        } else {
            state.features.push({
                ...feature,
                startedAt: Date.now(),
                updatedAt: Date.now(),
                artifacts: { designSpec: null, pbis: [], agentPrs: [] },
                phaseTimestamps: { [feature.step || 'designing']: Date.now() }
            });
        }
        writeState(state);
        console.log(JSON.stringify({ ok: true, id: feature.id }));
        break;
    }
    case 'set-step': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            feature.step = args[1];
            feature.updatedAt = Date.now();
            if (!feature.phaseTimestamps) feature.phaseTimestamps = {};
            feature.phaseTimestamps[args[1]] = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found: ' + args[0] }));
        }
        break;
    }
    case 'set-design': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const design = JSON.parse(args[1]);
            if (!feature.artifacts) feature.artifacts = { designSpec: null, pbis: [], agentPrs: [] };
            feature.artifacts.designSpec = { ...feature.artifacts.designSpec, ...design };
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
            if (!feature.artifacts) feature.artifacts = { designSpec: null, pbis: [], agentPrs: [] };
            if (!feature.artifacts.pbis) feature.artifacts.pbis = [];
            const existingIdx = feature.artifacts.pbis.findIndex(p => p.adoId === pbi.adoId);
            if (existingIdx >= 0) {
                feature.artifacts.pbis[existingIdx] = { ...feature.artifacts.pbis[existingIdx], ...pbi };
            } else {
                feature.artifacts.pbis.push(pbi);
            }
            feature.updatedAt = Date.now();
            checkAutoCompletion(feature);
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
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
            if (!feature.artifacts) feature.artifacts = { designSpec: null, pbis: [], agentPrs: [] };
            if (!feature.artifacts.agentPrs) feature.artifacts.agentPrs = [];
            const existingIdx = feature.artifacts.agentPrs.findIndex(p =>
                p.prNumber === pr.prNumber && p.repo === pr.repo);
            if (existingIdx >= 0) {
                feature.artifacts.agentPrs[existingIdx] = { ...feature.artifacts.agentPrs[existingIdx], ...pr };
            } else {
                feature.artifacts.agentPrs.push(pr);
            }
            feature.updatedAt = Date.now();
            checkAutoCompletion(feature);
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    case 'set-agent-info': {
        const state = readState();
        const feature = findFeature(state, args[0]);
        if (feature) {
            const info = JSON.parse(args[1]);
            feature.agentInfo = { ...feature.agentInfo, ...info };
            feature.updatedAt = Date.now();
            writeState(state);
            console.log(JSON.stringify({ ok: true }));
        } else {
            console.log(JSON.stringify({ ok: false, error: 'Feature not found' }));
        }
        break;
    }
    default:
        console.error('Feature Orchestrator State CLI');
        console.error('Commands: get, list-features, get-feature, add-feature, set-step,');
        console.error('          set-design, add-pbi, add-agent-pr, set-agent-info');
        process.exit(1);
}
