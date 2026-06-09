#!/usr/bin/env node
// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
//
// reduce-items.js
// ----------------------------------------------------------------------------
// Reduces merged S360 items into logical report rows. Implements the dedup
// rules from SKILL.md Step 2 — most importantly, the CRITICAL exception that
// items with distinct per-finding ADO work items must NEVER be merged into a
// single umbrella row.
//
// Background — the bug this script exists to prevent:
//   13 Nightwatch Security Code Bugs (all sharing the same KpiId) were merged
//   into one umbrella row, hiding 13 distinct pre-created ADO Bugs. The fix is
//   to key dedup on the per-item ADO work-item ID extracted from the S360 URL
//   field — if it's non-null, the item gets its own row regardless of how many
//   peers share the same KpiId/title.
//
// Usage:
//   node reduce-items.js --input merged.json [--kpi-metadata kpi.json] \
//                        --output reduced.json
//
// Inputs:
//   --input         JSON array of merged items (from merge-items.js).
//   --kpi-metadata  Optional JSON object: { "<kpiId>": "Display Name", ... }.
//                   Used to populate `ProgramName`. Falls back to item title.
//   --output        Path to write reduced rows. If omitted, prints to stdout.
//
// Diagnostics (warnings, coverage anomalies) go to stderr.

'use strict';

const fs = require('fs');
const path = require('path');

// ── Known per-finding KPIs ────────────────────────────────────────────────────
// These KPIs publish one ADO Bug per finding. If any item under one of these
// KPIs is missing a per-item ADO URL, we DO NOT umbrella-merge it — we give it
// its own row keyed by KpiActionItemId so a temporary URL outage cannot
// silently collapse rows like the original Nightwatch bug.
const PER_FINDING_KPIS = new Set([
  'a0f0ce42-3063-5d3b-3b47-1ff3143abdc9'  // [SFI-PS3.1] Security Code Bugs (Nightwatch)
  // Add more KPIs here as they are discovered. Examples likely to belong:
  //   - Accessibility per-issue bugs
  //   - BinSkim per-rule findings
  //   - SDL per-tool findings
]);

// ── CLI args ──────────────────────────────────────────────────────────────────
function getArg(name) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : null;
}

const inputPath = getArg('input');
const kpiMetaPath = getArg('kpi-metadata');
const outputPath = getArg('output');

if (!inputPath) {
  console.error('Usage: node reduce-items.js --input <merged.json> [--kpi-metadata <kpi.json>] [--output <reduced.json>]');
  process.exit(1);
}

const items = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const kpiMeta = kpiMetaPath ? JSON.parse(fs.readFileSync(kpiMetaPath, 'utf8')) : {};
console.error(`Loaded ${items.length} items from ${path.basename(inputPath)}`);

// ── Helpers ───────────────────────────────────────────────────────────────────

// Strict ISO date parse. Accepts:
//   • YYYY-MM-DD
//   • YYYY-MM-DDTHH:MM:SS(.fff)?(Z|±HH:MM)?
// Returns the original string if valid, else null. Rejects arbitrary
// Date.parse-able strings (e.g. "Today at 5pm") to avoid accidental ETAs.
const ISO_DATE_RX = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/;
function parseIsoDate(v) {
  if (typeof v !== 'string') return null;
  if (!ISO_DATE_RX.test(v)) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : v;
}

// Walk an object collecting all ISO-date values from any key containing "eta"
// (case-insensitive). Return the most recent (latest) one. SKILL.md Step 2
// "ETA Field Resolution" — the S360 API doesn't reliably populate CurrentETA.
function resolveETA(obj) {
  const found = []; // [{ path, value }]
  function walk(o, depth, prefix) {
    if (!o || typeof o !== 'object' || depth > 4) return;
    for (const [k, v] of Object.entries(o)) {
      if (v == null) continue;
      const p = prefix ? `${prefix}.${k}` : k;
      if (typeof v === 'string' && /eta/i.test(k)) {
        const iso = parseIsoDate(v);
        if (iso) found.push({ path: p, value: iso });
      } else if (typeof v === 'object') {
        walk(v, depth + 1, p);
      }
    }
  }
  walk(obj, 0, '');
  if (!found.length) return null;
  // Deterministic: sort by value desc, then path asc.
  found.sort((a, b) => b.value.localeCompare(a.value) || a.path.localeCompare(b.path));
  return found[0].value;
}

// Extract an ADO work item ID from a URL. Matches the two common forms:
//   https://dev.azure.com/{org}/{project}/_workitems/edit/12345
//   https://{org}.visualstudio.com/{project}/_workitems/edit/12345
const WORKITEM_URL_RX = /_workitems\/edit\/(\d+)/i;
function parseWorkItemId(url) {
  if (!url || typeof url !== 'string') return null;
  const m = url.match(WORKITEM_URL_RX);
  return m ? m[1] : null;
}

// Normalize a title for grouping: strip leading GUIDs (Nightwatch CWE prefix),
// strip trailing "(Last validated ...)" / "(Last completed ...)" / "(IcM Team
// ...)" suffixes, strip trailing "- [ServiceName: ...]" markers, lowercase,
// collapse whitespace.
function baseTitle(t) {
  let s = String(t || '');
  s = s.replace(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\s*-\s*/i, '');
  s = s.replace(/\s*\(.*?(validated|completed|IcM Team).*?\)/gi, '');
  s = s.replace(/\s*-\s*\[ServiceName:.*$/i, '');
  s = s.replace(/\s+/g, ' ').trim().toLowerCase();
  return s;
}

// ── Pass 1: enrich each item with ADO work-item ID + ETA + source ─────────────
const enriched = items.map(it => {
  const url = it.URL || '';
  const legacy = (it.S360Dimensions && it.S360Dimensions.ADOWorkItemHTMLUrl) || '';

  const idFromUrl = parseWorkItemId(url);
  const idFromLegacy = parseWorkItemId(legacy);

  let adoId = null;
  let adoIdSource = null;
  if (idFromUrl) {
    adoId = idFromUrl;
    adoIdSource = 'URL';
    if (idFromLegacy && idFromLegacy !== idFromUrl) {
      console.error(`WARN: item ${it.KpiActionItemId} has conflicting work-item IDs (URL=${idFromUrl}, ADOWorkItemHTMLUrl=${idFromLegacy}). Using URL.`);
    }
  } else if (idFromLegacy) {
    adoId = idFromLegacy;
    adoIdSource = 'ADOWorkItemHTMLUrl';
  }

  return {
    raw: it,
    adoId,
    adoIdSource,
    eta: resolveETA(it),
    baseTitle: baseTitle(it.Title)
  };
});

// ── Pass 2: detect reused (shared/umbrella) ADO IDs ───────────────────────────
// If the same ADO ID appears across multiple distinct (KpiId, baseTitle,
// TargetId) tuples, it's a template/umbrella work item, NOT a per-finding bug.
// Don't let it collapse unrelated rows into one — fall back to non-WI grouping
// for those items but keep the ADO ID on each row for display.
const tuplesById = new Map();
for (const e of enriched) {
  if (!e.adoId) continue;
  const tuple = `${e.raw.KpiId}|${e.baseTitle}|${e.raw.TargetId}`;
  if (!tuplesById.has(e.adoId)) tuplesById.set(e.adoId, new Set());
  tuplesById.get(e.adoId).add(tuple);
}
const reusedIds = new Set();
for (const [id, tuples] of tuplesById) {
  if (tuples.size > 1) {
    reusedIds.add(id);
    console.error(`WARN: ADO work-item ${id} is referenced by ${tuples.size} distinct (KpiId,baseTitle,TargetId) tuples — treating as shared/umbrella, not per-finding.`);
  }
}

// ── Pass 3: URL coverage diagnostics for known per-finding KPIs ───────────────
const perFindingCoverage = new Map(); // kpiId -> { total, withId }
for (const e of enriched) {
  if (!PER_FINDING_KPIS.has(e.raw.KpiId)) continue;
  const c = perFindingCoverage.get(e.raw.KpiId) || { total: 0, withId: 0 };
  c.total++;
  if (e.adoId && !reusedIds.has(e.adoId)) c.withId++;
  perFindingCoverage.set(e.raw.KpiId, c);
}
for (const [kpiId, c] of perFindingCoverage) {
  if (c.withId < c.total) {
    const pct = ((c.withId / c.total) * 100).toFixed(0);
    console.error(`WARN: per-finding KPI ${kpiId} has ADO URL coverage ${c.withId}/${c.total} (${pct}%). Items without a URL will get their own rows (keyed by KpiActionItemId) rather than umbrella-merging — safer default.`);
  }
}

// ── Pass 4: compute group keys ────────────────────────────────────────────────
//
// Group-key rules, in priority order:
//   1. If item has a non-null adoId AND that ID is NOT reused across unrelated
//      tuples → groupKey = `wi:<adoId>` (per-finding: each work item = one row)
//   2. Else if KpiId is in PER_FINDING_KPIS (known per-finding KPI) → groupKey
//      = `pf:<KpiActionItemId>` (every item gets its own row; missing URL must
//      not cause umbrella collapse)
//   3. Else → groupKey = `nowi:<KpiId>|<baseTitle>|<TargetId>` (umbrella merge
//      by KPI + normalized title + target — the CFS-pipeline-style case)
//
// For category (1), the ADO ID becomes the grouping authority. For (3),
// multiple items become one row with `count = N` for display.
function computeGroupKey(e) {
  if (e.adoId && !reusedIds.has(e.adoId)) return `wi:${e.adoId}`;
  if (PER_FINDING_KPIS.has(e.raw.KpiId)) return `pf:${e.raw.KpiActionItemId}`;
  const tgt = e.raw.TargetType === 'Person' ? `Person:${e.raw.TargetId}` : (e.raw.TargetId || '');
  return `nowi:${e.raw.KpiId}|${e.baseTitle}|${tgt}`;
}

const groups = new Map(); // groupKey -> enriched[]
for (const e of enriched) {
  const k = computeGroupKey(e);
  if (!groups.has(k)) groups.set(k, []);
  groups.get(k).push(e);
}

// ── Pass 5: pick representative per group + emit row ──────────────────────────
const SLA_RANK = { OutOfSla: 0, ApproachingSla: 1, InSla: 2 };

function pickRepresentative(group) {
  // Worst SLA first, then earliest due date, then deterministic tiebreak on
  // KpiActionItemId (so identical input always yields identical reps).
  return [...group].sort((a, b) => {
    const ra = SLA_RANK[a.raw.SLAState] ?? 9;
    const rb = SLA_RANK[b.raw.SLAState] ?? 9;
    if (ra !== rb) return ra - rb;
    const da = String(a.raw.CurrentDueDate || '');
    const db = String(b.raw.CurrentDueDate || '');
    const dc = da.localeCompare(db);
    if (dc !== 0) return dc;
    return String(a.raw.KpiActionItemId || '').localeCompare(String(b.raw.KpiActionItemId || ''));
  })[0];
}

const reduced = [];
for (const [groupKey, group] of groups) {
  const rep = pickRepresentative(group);
  const r = rep.raw;
  // Pick the latest ETA across the group (safer than using the rep's alone —
  // gives the most up-to-date commitment if any group member has one). This is
  // a separate concern from the worst-SLA representative pick.
  let groupEta = null;
  for (const e of group) {
    if (e.eta && (!groupEta || e.eta.localeCompare(groupEta) > 0)) groupEta = e.eta;
  }
  reduced.push({
    groupKey,
    count: group.length,
    KpiId: r.KpiId,
    KpiActionItemId: r.KpiActionItemId,
    Title: r.Title,
    TargetType: r.TargetType,
    TargetId: r.TargetId,
    AssignedTo: r.AssignedTo,
    ActionOwnerAlias: (r.S360Dimensions && r.S360Dimensions.ActionOwnerAlias) || r.AssignedTo || null,
    ActionOwner: (r.S360Dimensions && r.S360Dimensions.ActionOwner) || null,
    CurrentDueDate: r.CurrentDueDate,
    SLAState: r.SLAState,
    ETA: groupEta,
    ADOWorkItemId: rep.adoId,
    ADOWorkItemIdSource: rep.adoIdSource, // "URL" | "ADOWorkItemHTMLUrl" | null
    ADOWorkItemHTMLUrl: (r.S360Dimensions && r.S360Dimensions.ADOWorkItemHTMLUrl) || null,
    URL: r.URL || null,
    ProgramName: kpiMeta[r.KpiId] || r.Title,
    Wave: (r.CustomDimensions && r.CustomDimensions.S360_WavesMetadata && r.CustomDimensions.S360_WavesMetadata[0] && r.CustomDimensions.S360_WavesMetadata[0].WaveDisplayName) || null,
    CurrentStatus: r.CurrentStatus || null,
    CurrentStatusAuthor: r.CurrentStatusAuthor || null,
    // Original IDs that fed into this row (for traceability + debugging)
    underlyingActionItemIds: group.map(e => e.raw.KpiActionItemId).sort()
  });
}

// ── Pass 6: deterministic output ordering ─────────────────────────────────────
// Sort: worst SLA, earliest due, program name, title, groupKey. Identical input
// must produce identical output across runs.
reduced.sort((a, b) => {
  const ra = SLA_RANK[a.SLAState] ?? 9;
  const rb = SLA_RANK[b.SLAState] ?? 9;
  if (ra !== rb) return ra - rb;
  const da = String(a.CurrentDueDate || '');
  const db = String(b.CurrentDueDate || '');
  const dc = da.localeCompare(db);
  if (dc !== 0) return dc;
  const pc = String(a.ProgramName || '').localeCompare(String(b.ProgramName || ''));
  if (pc !== 0) return pc;
  const tc = String(a.Title || '').localeCompare(String(b.Title || ''));
  if (tc !== 0) return tc;
  return a.groupKey.localeCompare(b.groupKey);
});

console.error(`Reduced ${items.length} items → ${reduced.length} logical rows`);
const collapsed = reduced.filter(r => r.count > 1);
if (collapsed.length) {
  console.error(`Collapsed groups (count > 1): ${collapsed.length}`);
  for (const r of collapsed) {
    console.error(`  [${r.count}x] ${r.SLAState} ${r.ProgramName.slice(0, 60)} :: ${String(r.Title).slice(0, 60)}`);
  }
}

// ── Write output ──────────────────────────────────────────────────────────────
const out = JSON.stringify(reduced, null, 2);
if (outputPath) {
  fs.writeFileSync(outputPath, out);
  console.error(`Wrote ${reduced.length} rows to ${outputPath}`);
} else {
  process.stdout.write(out + '\n');
}
