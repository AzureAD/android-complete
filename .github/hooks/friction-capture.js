#!/usr/bin/env node
/**
 * friction-capture.js — automatic friction capture for the skill-evolver system.
 *
 * Registered as a PostToolUse and Stop hook (see orchestrator.json). It reads the
 * hook payload from stdin and:
 *   - PostToolUse: if the tool reported a failure/error, appends a high-signal
 *     `tool_error` friction event to the journal (attributed to the active skill).
 *   - Stop / SubagentStop: clears the active-skill marker so attribution does not
 *     leak across tasks.
 *
 * Design rules (match the existing hooks in this folder):
 *   - Never block the tool flow. Always print {continue:true} and exit 0.
 *   - Wrap everything in try/catch; capture is best-effort.
 *   - Only record on detected failure to keep the journal high-signal.
 */

'use strict';

var fs = require('fs');
var path = require('path');

function emitAndExit() {
    console.log(JSON.stringify({ continue: true }));
    process.exit(0);
}

// Read stdin (hook input)
var hookInput = {};
try {
    hookInput = JSON.parse(fs.readFileSync(0, 'utf-8'));
} catch (e) {
    // no stdin / not JSON — nothing to capture
    emitAndExit();
}

// Global off switch — set SKILL_EVOLUTION_DISABLE=1 to silence all capture.
if (process.env.SKILL_EVOLUTION_DISABLE) {
    emitAndExit();
}

// Avoid re-entry loops
if (hookInput.stop_hook_active) {
    emitAndExit();
}

var journal;
try {
    journal = require('./journal-utils.js');
} catch (e) {
    // store unavailable — never block the tool flow
    emitAndExit();
}

var eventName = hookInput.hook_event_name || hookInput.hookEventName || '';

try {
    // End-of-task events: clear attribution so the next task starts clean.
    if (eventName === 'Stop' || eventName === 'SubagentStop') {
        journal.clearActive();
        emitAndExit();
    }

    // From here we treat the payload as a (Post)ToolUse event.
    var toolName = hookInput.tool_name || hookInput.toolName || 'unknown-tool';
    var resp = hookInput.tool_response || hookInput.toolResponse || hookInput.result || {};

    var failure = detectFailure(resp);
    if (failure.failed) {
        journal.recordEvent({
            tool: toolName,
            eventType: 'tool_error',
            severity: failure.severity,
            expected: 'Tool call to complete successfully',
            actual: failure.summary,
            detail: failure.detail,
            source: 'hook',
            sessionId: hookInput.session_id || hookInput.sessionId || null
        });
    }
} catch (e) {
    // swallow — capture must never break the session
}

emitAndExit();

/**
 * Heuristically decide whether a tool response represents a failure, and how bad.
 * Conservative on purpose: false positives create journal noise.
 */
function detectFailure(resp) {
    var result = { failed: false, severity: 'medium', summary: '', detail: '' };
    if (resp === null || resp === undefined) return result;

    // Explicit structured failure signals
    if (resp.success === false || resp.is_error === true || resp.isError === true || resp.error) {
        result.failed = true;
    }

    // Non-zero exit codes (powershell / shell-style tools)
    var exitCode = resp.exit_code !== undefined ? resp.exit_code
        : (resp.exitCode !== undefined ? resp.exitCode : undefined);
    if (typeof exitCode === 'number' && exitCode !== 0) {
        result.failed = true;
        result.severity = 'high';
    }

    // String/text payloads that smell like errors
    var text = '';
    if (typeof resp === 'string') text = resp;
    else text = [resp.error, resp.stderr, resp.message, resp.output, resp.content]
        .filter(function (x) { return typeof x === 'string'; }).join('\n');

    if (!result.failed && text) {
        if (/\b(error|exception|failed|fatal|cannot find|not found|denied|traceback)\b/i.test(text)) {
            result.failed = true;
        }
    }

    if (result.failed) {
        var src = (typeof resp.error === 'string' && resp.error) ||
            (typeof resp.stderr === 'string' && resp.stderr) ||
            (typeof resp.message === 'string' && resp.message) || text || 'Tool reported a failure';
        result.detail = String(src);
        result.summary = result.detail.split('\n')[0].slice(0, 200);
        if (/\b(fatal|denied|traceback|exception)\b/i.test(result.detail)) result.severity = 'high';
    }
    return result;
}
