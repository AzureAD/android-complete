#!/usr/bin/env node
/**
 * SubagentStop hook — advances orchestrator state when a subagent completes.
 *
 * Maps subagent names to pipeline steps, writes the next step to
 * orchestrator-state.json. The VS Code extension watches this file
 * and renders a clickable "next step" notification button.
 */

const fs = require('fs');
const path = require('path');

// Read stdin (hook input)
let hookInput = {};
try {
    hookInput = JSON.parse(fs.readFileSync(0, 'utf-8'));
} catch { /* no stdin */ }

// Don't interfere if this is a re-entry
if (hookInput.stop_hook_active) {
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
}

const agentType = hookInput.agent_type || '';

// Only handle subagents that are part of our orchestrator pipeline
var ourAgents = ['codebase-researcher', 'design-writer', 'feature-planner', 'pbi-creator', 'agent-dispatcher'];
if (ourAgents.indexOf(agentType) === -1) {
    // Not one of our subagents — let it pass without modifying state
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
}

// Map subagent names to the next pipeline step
const agentToNextStep = {
    'codebase-researcher': null,  // research is intermediate, no step change
    'design-writer': 'design_review',
    'feature-planner': 'plan_review',
    'pbi-creator': 'backlog_review',
    'agent-dispatcher': 'monitoring',
};

const nextStep = agentToNextStep[agentType];

if (!nextStep) {
    // Not a tracked subagent, let it pass
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
}

const os = require('os');

const stateFile = path.join(os.homedir(), '.android-auth-orchestrator', 'state.json');

try {
    if (fs.existsSync(stateFile)) {
        const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));

        // Find the most recently updated in-progress feature
        const activeFeature = state.features
            ?.filter(f => f.step !== 'done' && f.step !== 'idle')
            ?.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))?.[0];

        if (activeFeature) {
            activeFeature.step = nextStep;
            activeFeature.updatedAt = Date.now();

            // Write a "pendingAction" field that the extension will consume
            // to show a clickable notification button
            activeFeature.pendingAction = {
                completedAgent: agentType,
                nextStep: nextStep,
                timestamp: Date.now(),
            };

            state.lastUpdated = Date.now();
            fs.writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf-8');
        }
    }
} catch (e) {
    console.error('SubagentStop hook error:', e.message);
}

// Always allow the subagent to stop normally
console.log(JSON.stringify({ continue: true }));
