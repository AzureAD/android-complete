#!/usr/bin/env node
/**
 * SubagentStart hook — injects orchestrator context into subagent sessions.
 *
 * SCOPE: This hook runs only when the orchestrator invokes a subagent,
 * NOT for regular Agent Mode sessions. It injects active feature context
 * so subagents are aware of the pipeline state.
 */

var fs = require('fs');
var path = require('path');

// Read stdin (hook input)
var hookInput = {};
try {
    hookInput = JSON.parse(fs.readFileSync(0, 'utf-8'));
} catch (e) {
    // no stdin
}

var os = require('os');

var stateFile = path.join(os.homedir(), '.android-auth-orchestrator', 'state.json');

function readState() {
    if (!fs.existsSync(stateFile)) {
        return { version: 1, features: [], lastUpdated: Date.now() };
    }
    try {
        return JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
    } catch (e) {
        return { version: 1, features: [], lastUpdated: Date.now() };
    }
}

var additionalContext = '';

try {
    var state = readState();

    // Check if there's an active feature being tracked by the orchestrator.
    // If yes, inject its context. If no, just add basic workspace info.
    // We do NOT auto-create feature entries here — that would pollute state
    // for every normal Agent Mode session. Feature entries are created by
    // the orchestrator agent itself (via the state-utils.js CLI).
    var activeFeature = null;
    if (state.features && state.features.length > 0) {
        activeFeature = state.features
            .filter(function(f) { return f.step !== 'done' && f.step !== 'idle'; })
            .sort(function(a, b) { return (b.updatedAt || 0) - (a.updatedAt || 0); })[0] || null;
    }

    if (activeFeature) {
        // Orchestrator session — inject full feature context
        var parts = [
            'Active feature: "' + activeFeature.name + '"',
            'Current step: ' + activeFeature.step,
        ];

        if (activeFeature.designDocPath) {
            parts.push('Design doc: ' + activeFeature.designDocPath);
        }

        if (activeFeature.pbis && activeFeature.pbis.length > 0) {
            var pbiSummary = activeFeature.pbis
                .map(function(p) { return 'AB#' + p.adoId + ' (' + p.targetRepo + ') [' + p.status + ']'; })
                .join(', ');
            parts.push('PBIs: ' + pbiSummary);
        }

        additionalContext = parts.join('. ') + '.';
    }

    // Add basic workspace info (cwd is the workspace root when hooks run)
    var root = process.cwd();
    var skillsDir = path.join(root, '.github', 'skills');
    var skills = fs.existsSync(skillsDir)
        ? fs.readdirSync(skillsDir).join(', ')
        : 'none';
    var hasDesignDocs = fs.existsSync(path.join(root, 'design-docs'));

    additionalContext += ' Android Auth workspace. Skills: ' + skills + '.';
    if (hasDesignDocs) {
        additionalContext += ' Design docs available at design-docs/.';
    }

} catch (e) {
    additionalContext = 'Android Auth workspace (state read error: ' + e.message + ')';
}

// Output
console.log(JSON.stringify({
    hookSpecificOutput: {
        hookEventName: 'SubagentStart',
        additionalContext: additionalContext.trim(),
    }
}));
