#!/usr/bin/env node
// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
//
// merge-items.js
// ----------------------------------------------------------------------------
// Merges service-targeted and person-targeted S360 API responses, filters
// person-targeted items down to team-relevant ones, and deduplicates by
// KpiActionItemId.
//
// Implements SKILL.md Steps 1b (filter) and 1c (merge). Encoded as a script so
// the same logic runs every week — see Step 2's "CRITICAL exception" for the
// rationale.
//
// Usage:
//   node merge-items.js --service service.json --person person.json \
//                       --team team.json --output merged.json
//
// Inputs:
//   --service  Raw response from search_active_s360_kpi_action_items keyed by
//              targetIds (the 3 Android Auth service tree GUIDs).
//   --person   Raw response from the same tool keyed by assignedTo (team
//              aliases).
//   --team     JSON file: { aliases, nameMap, serviceIds?, tenantPatterns? }.
//              serviceIds and tenantPatterns default to Android Auth values if
//              omitted.
//   --output   Path to write the merged JSON array. If omitted, prints to stdout.
//
// Each response may be:
//   • Full MCP envelope: { result: { resources: [...] } }
//   • Mid envelope:      { resources: [...] }
//   • Bare array:        [...]
// All three are accepted.
//
// Diagnostics (counts, dropped items) are written to stderr so stdout stays
// machine-parseable.

'use strict';

const fs = require('fs');
const path = require('path');

// ── Defaults ──────────────────────────────────────────────────────────────────
const DEFAULT_SERVICE_IDS = [
  '937cdc57-1253-4b55-878e-5854368926a2', // AuthN SDK - ADAL Android
  '8d0d308e-cd5c-44a3-9518-43eeeb424b57', // AuthN SDK - MSAL Android
  '0b97f26e-fcfc-4ed1-95e9-1dca3a2fde3b'  // Microsoft Authenticator - Android
];
const DEFAULT_TENANT_PATTERNS = ['auth client', 'msal', 'adal', 'authenticator'];

// ── CLI args ──────────────────────────────────────────────────────────────────
function getArg(name) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : null;
}

const servicePath = getArg('service');
const personPath = getArg('person');
const teamPath = getArg('team');
const outputPath = getArg('output');

if (!servicePath || !personPath || !teamPath) {
  console.error('Usage: node merge-items.js --service <svc.json> --person <per.json> --team <team.json> [--output <merged.json>]');
  process.exit(1);
}

// ── Load inputs ───────────────────────────────────────────────────────────────
function loadResources(p) {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (Array.isArray(j)) return j;
  if (j && j.resources && Array.isArray(j.resources)) return j.resources;
  if (j && j.result && j.result.resources && Array.isArray(j.result.resources)) return j.result.resources;
  throw new Error(`Could not find a resources array in ${p}. Expected one of: top-level array, { resources: [...] }, or { result: { resources: [...] } }`);
}

const svcItems = loadResources(servicePath);
const perItems = loadResources(personPath);
const team = JSON.parse(fs.readFileSync(teamPath, 'utf8'));

const teamAliases = new Set((team.aliases || []).map(a => String(a).toLowerCase()));
const serviceIds = new Set(((team.serviceIds && team.serviceIds.length) ? team.serviceIds : DEFAULT_SERVICE_IDS).map(s => String(s).toLowerCase()));
const tenantPatterns = (team.tenantPatterns && team.tenantPatterns.length) ? team.tenantPatterns : DEFAULT_TENANT_PATTERNS;

if (teamAliases.size === 0) {
  console.error('WARN: team.aliases is empty — every person-targeted item will be dropped unless it matches a service ID or tenant pattern.');
}

console.error(`Loaded ${svcItems.length} service-targeted items from ${path.basename(servicePath)}`);
console.error(`Loaded ${perItems.length} person/assignedTo items from ${path.basename(personPath)}`);
console.error(`Team: ${teamAliases.size} aliases, ${serviceIds.size} service IDs, ${tenantPatterns.length} tenant patterns`);

// ── Filter person items to team-relevant ──────────────────────────────────────
// IMPORTANT: AssignedTo alone is NOT a sufficient signal. The person query
// already filters by assignedTo, so every item has a team-alias AssignedTo —
// but many of those items are for OTHER teams the person also belongs to.
// Require at least one direct relevance signal:
//   • Person-targeted AND TargetId is a team alias  (on-call style items)
//   • TargetId is one of our service IDs            (mis-bucketed service items)
//   • TenantName matches one of our tenant patterns (catch-all by team name)
const droppedReasons = { noSignal: 0 };
const droppedSamples = [];

function isTeamRelevant(it) {
  const tgt = String(it.TargetId || '').toLowerCase();
  const tenant = String((it.CustomDimensions && it.CustomDimensions.TenantName) || '').toLowerCase();

  if (it.TargetType === 'Person' && teamAliases.has(tgt)) return true;
  if (serviceIds.has(tgt)) return true;
  if (tenant && tenantPatterns.some(p => tenant.includes(String(p).toLowerCase()))) return true;

  return false;
}

const filteredPer = [];
for (const it of perItems) {
  if (isTeamRelevant(it)) {
    filteredPer.push(it);
  } else {
    droppedReasons.noSignal++;
    if (droppedSamples.length < 5) {
      droppedSamples.push({
        KpiActionItemId: it.KpiActionItemId,
        Title: String(it.Title || '').slice(0, 80),
        AssignedTo: it.AssignedTo,
        TargetType: it.TargetType,
        TargetId: it.TargetId
      });
    }
  }
}

console.error(`Person items filtered to: ${filteredPer.length} (dropped ${droppedReasons.noSignal} as not team-relevant)`);
if (droppedSamples.length) {
  console.error(`Sample dropped items (first ${droppedSamples.length}):`);
  for (const d of droppedSamples) console.error(`  - ${d.KpiActionItemId} | AssignedTo=${d.AssignedTo} | ${d.Title}`);
}

// ── Merge + dedupe by KpiActionItemId ─────────────────────────────────────────
// Stable order: service items first, then filtered person items, both sorted
// by KpiActionItemId. Determinism is important — same input → same output.
function stableSort(items) {
  return [...items].sort((a, b) => String(a.KpiActionItemId || '').localeCompare(String(b.KpiActionItemId || '')));
}

const combined = [...stableSort(svcItems), ...stableSort(filteredPer)];

const seen = new Map();
let dupes = 0;
for (const it of combined) {
  const id = it.KpiActionItemId || `__nokey__|${it.KpiId}|${it.TargetId}|${it.Title}`;
  if (seen.has(id)) {
    dupes++;
    continue;
  }
  seen.set(id, it);
}
const merged = [...seen.values()];

console.error(`Merged unique: ${merged.length} (deduped ${dupes} cross-source duplicates)`);

// ── Write output ──────────────────────────────────────────────────────────────
const out = JSON.stringify(merged, null, 2);
if (outputPath) {
  fs.writeFileSync(outputPath, out);
  console.error(`Wrote ${merged.length} items to ${outputPath}`);
} else {
  process.stdout.write(out + '\n');
}
