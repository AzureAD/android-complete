#!/usr/bin/env node
/**
 * journal-utils.js — friction journal store for the skill-evolver system.
 *
 * Single source of truth for the append-only friction journal (JSONL) and the
 * "active skill" attribution marker. Used by:
 *   - the friction-capture.js hook (require()d as a module), and
 *   - the skill-evolver skill / agent (invoked as a CLI).
 *
 * Mirrors the state-utils.js pattern. The live store lives outside the repo so
 * it never pollutes git status. Override with SKILL_EVOLUTION_HOME.
 *
 * Store layout (default ~/.skill-evolution/):
 *   journal.jsonl      — one friction event per line (see references/friction-schema.md)
 *   active-skill.json  — { "skill": "<name>", "ts": <ms> }
 *
 * CLI usage:
 *   node journal-utils.js record '<json>'          → append a friction event
 *   node journal-utils.js set-active <skill>        → mark the active skill for attribution
 *   node journal-utils.js clear-active              → clear the active-skill marker
 *   node journal-utils.js active                    → print the active skill (or "unknown")
 *   node journal-utils.js list [--skill X] [--type Y] [--since ISO] [--limit N]
 *   node journal-utils.js stats [--md]              → aggregated digest (JSON by default)
 *   node journal-utils.js path                      → print store paths
 *   node journal-utils.js clear --yes               → wipe the journal (keeps a .bak)
 */

'use strict';

var fs = require('fs');
var os = require('os');
var path = require('path');

var STORE_DIR = process.env.SKILL_EVOLUTION_HOME ||
    path.join(os.homedir(), '.skill-evolution');
var JOURNAL_FILE = path.join(STORE_DIR, 'journal.jsonl');
var ACTIVE_FILE = path.join(STORE_DIR, 'active-skill.json');

var MAX_FIELD = 1200; // truncate long text fields to keep the journal lean

function ensureStore() {
    if (!fs.existsSync(STORE_DIR)) {
        fs.mkdirSync(STORE_DIR, { recursive: true });
    }
}

function truncate(val) {
    if (typeof val !== 'string') return val;
    if (val.length <= MAX_FIELD) return val;
    return val.slice(0, MAX_FIELD) + ' …[truncated]';
}

function genId() {
    return 'fr-' + Date.now().toString(36) + '-' +
        Math.random().toString(36).slice(2, 7);
}

var VALID_EVENT_TYPES = [
    'tool_error', 'retry', 'user_correction', 'dead_end', 'missing_context',
    'ambiguity', 'trigger_miss', 'skill_step_mismatch', 'note'
];
var VALID_SEVERITY = ['low', 'medium', 'high'];

/**
 * Append a friction event. Fills in id/ts/iso/source/skill defaults and
 * truncates verbose fields. Returns the stored event.
 */
function recordEvent(evt) {
    // Global off switch — when disabled, capture is a silent no-op.
    // Read paths (stats/list) still work so past data stays reviewable.
    if (process.env.SKILL_EVOLUTION_DISABLE) {
        return null;
    }
    ensureStore();
    evt = evt || {};

    var now = Date.now();
    var stored = {
        id: evt.id || genId(),
        ts: evt.ts || now,
        iso: evt.iso || new Date(now).toISOString(),
        skill: evt.skill || getActive() || 'unknown',
        tool: evt.tool || null,
        eventType: VALID_EVENT_TYPES.indexOf(evt.eventType) !== -1 ? evt.eventType : 'note',
        severity: VALID_SEVERITY.indexOf(evt.severity) !== -1 ? evt.severity : 'medium',
        expected: truncate(evt.expected || ''),
        actual: truncate(evt.actual || ''),
        detail: truncate(evt.detail || ''),
        turnsCost: typeof evt.turnsCost === 'number' ? evt.turnsCost : 0,
        fixHint: truncate(evt.fixHint || ''),
        source: evt.source || 'agent',
        sessionId: evt.sessionId || null
    };

    fs.appendFileSync(JOURNAL_FILE, JSON.stringify(stored) + '\n', 'utf-8');
    return stored;
}

function setActive(skill) {
    ensureStore();
    fs.writeFileSync(ACTIVE_FILE, JSON.stringify({ skill: skill, ts: Date.now() }), 'utf-8');
}

function clearActive() {
    try {
        if (fs.existsSync(ACTIVE_FILE)) fs.unlinkSync(ACTIVE_FILE);
    } catch (e) { /* ignore */ }
}

function getActive() {
    try {
        if (!fs.existsSync(ACTIVE_FILE)) return null;
        var obj = JSON.parse(fs.readFileSync(ACTIVE_FILE, 'utf-8'));
        return obj && obj.skill ? obj.skill : null;
    } catch (e) {
        return null;
    }
}

function readEvents() {
    if (!fs.existsSync(JOURNAL_FILE)) return [];
    var lines = fs.readFileSync(JOURNAL_FILE, 'utf-8').split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line) continue;
        try { out.push(JSON.parse(line)); } catch (e) { /* skip corrupt line */ }
    }
    return out;
}

/**
 * Aggregate the journal into a digest the agent can reason over:
 * totals, per-skill / per-type / per-severity counts, and ranked recurring
 * issues (grouped by skill + eventType + a normalized actual/detail signature).
 */
function computeStats() {
    var events = readEvents();
    var bySkill = {}, byType = {}, bySeverity = {}, groups = {};
    var severityWeight = { low: 1, medium: 3, high: 8 };

    for (var i = 0; i < events.length; i++) {
        var e = events[i];
        bySkill[e.skill] = (bySkill[e.skill] || 0) + 1;
        byType[e.eventType] = (byType[e.eventType] || 0) + 1;
        bySeverity[e.severity] = (bySeverity[e.severity] || 0) + 1;

        var sig = (e.actual || e.detail || '').toLowerCase()
            .replace(/[0-9]+/g, '#')          // normalize ids/numbers
            .replace(/[^a-z#]+/g, ' ')
            .trim().split(' ').slice(0, 8).join(' ');
        var key = e.skill + '::' + e.eventType + '::' + sig;
        if (!groups[key]) {
            groups[key] = { skill: e.skill, eventType: e.eventType, signature: sig, count: 0, score: 0, sample: e, lastIso: e.iso };
        }
        groups[key].count += 1;
        groups[key].score += (severityWeight[e.severity] || 3);
        if (e.iso > groups[key].lastIso) groups[key].lastIso = e.iso;
    }

    var recurring = Object.keys(groups).map(function (k) { return groups[k]; })
        .sort(function (a, b) { return b.score - a.score; });

    return {
        total: events.length,
        bySkill: bySkill,
        byEventType: byType,
        bySeverity: bySeverity,
        recurring: recurring.slice(0, 25),
        recent: events.slice(-10)
    };
}

function statsToMarkdown(s) {
    var lines = [];
    lines.push('# Friction Digest');
    lines.push('');
    lines.push('Total events: **' + s.total + '**');
    lines.push('');
    lines.push('## Top recurring issues (ranked by frequency × severity)');
    lines.push('');
    lines.push('| Rank | Skill | Type | Count | Score | Signature | Last seen |');
    lines.push('|------|-------|------|-------|-------|-----------|-----------|');
    s.recurring.forEach(function (g, i) {
        lines.push('| ' + (i + 1) + ' | ' + g.skill + ' | ' + g.eventType + ' | ' +
            g.count + ' | ' + g.score + ' | ' + g.signature + ' | ' + g.lastIso + ' |');
    });
    lines.push('');
    lines.push('## Counts by skill');
    Object.keys(s.bySkill).sort(function (a, b) { return s.bySkill[b] - s.bySkill[a]; })
        .forEach(function (k) { lines.push('- ' + k + ': ' + s.bySkill[k]); });
    return lines.join('\n');
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
function parseFlags(args) {
    var flags = {};
    for (var i = 0; i < args.length; i++) {
        if (args[i].indexOf('--') === 0) {
            var key = args[i].slice(2);
            var val = (i + 1 < args.length && args[i + 1].indexOf('--') !== 0) ? args[++i] : true;
            flags[key] = val;
        }
    }
    return flags;
}

function runCli() {
    var argv = process.argv.slice(2);
    var cmd = argv[0];
    var rest = argv.slice(1);

    try {
        if (cmd === 'record') {
            var json = rest[0];
            var evt = json ? JSON.parse(json) : {};
            evt.source = evt.source || 'cli';
            var rec = recordEvent(evt);
            console.log(rec ? JSON.stringify(rec) : 'capture disabled (SKILL_EVOLUTION_DISABLE set) — not recorded');
        } else if (cmd === 'set-active') {
            setActive(rest[0] || 'unknown');
            console.log('active skill set to: ' + (rest[0] || 'unknown'));
        } else if (cmd === 'clear-active') {
            clearActive();
            console.log('active skill cleared');
        } else if (cmd === 'active') {
            console.log(getActive() || 'unknown');
        } else if (cmd === 'list') {
            var f = parseFlags(rest);
            var events = readEvents();
            if (f.skill) events = events.filter(function (e) { return e.skill === f.skill; });
            if (f.type) events = events.filter(function (e) { return e.eventType === f.type; });
            if (f.since) events = events.filter(function (e) { return e.iso >= f.since; });
            if (f.limit) events = events.slice(-parseInt(f.limit, 10));
            console.log(JSON.stringify(events, null, 2));
        } else if (cmd === 'stats') {
            var s = computeStats();
            var fl = parseFlags(rest);
            console.log(fl.md ? statsToMarkdown(s) : JSON.stringify(s, null, 2));
        } else if (cmd === 'path') {
            console.log(JSON.stringify({ storeDir: STORE_DIR, journal: JOURNAL_FILE, activeMarker: ACTIVE_FILE }, null, 2));
        } else if (cmd === 'clear') {
            var cf = parseFlags(rest);
            if (!cf.yes) { console.error('Refusing to clear without --yes'); process.exit(1); }
            if (fs.existsSync(JOURNAL_FILE)) fs.renameSync(JOURNAL_FILE, JOURNAL_FILE + '.bak');
            console.log('journal cleared (backup at ' + JOURNAL_FILE + '.bak)');
        } else {
            console.error('Unknown command: ' + cmd);
            console.error('Commands: record, set-active, clear-active, active, list, stats, path, clear');
            process.exit(1);
        }
    } catch (e) {
        console.error('journal-utils error: ' + e.message);
        process.exit(1);
    }
}

module.exports = {
    recordEvent: recordEvent,
    setActive: setActive,
    clearActive: clearActive,
    getActive: getActive,
    readEvents: readEvents,
    computeStats: computeStats,
    statsToMarkdown: statsToMarkdown,
    STORE_DIR: STORE_DIR,
    JOURNAL_FILE: JOURNAL_FILE,
    ACTIVE_FILE: ACTIVE_FILE,
    VALID_EVENT_TYPES: VALID_EVENT_TYPES
};

if (require.main === module) {
    runCli();
}
