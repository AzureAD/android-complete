#!/usr/bin/env node
// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
//
// fetch-items.js
// ----------------------------------------------------------------------------
// Consolidates one or more paginated `search_active_s360_kpi_action_items` MCP
// responses into a single JSON file and verifies pagination was completed.
//
// Background: the S360 MCP paginates at pageSize=50 and returns a `nextCursor`
// on every page except the last. Before this script existed, SKILL.md relied
// on a manual "loop until nextCursor is empty" instruction — a first-time user
// (Sowmya Malayanur, Jun 2026) missed it and shipped an incomplete report.
// This script + the guard in merge-items.js make that failure mode loud.
//
// Usage:
//   node fetch-items.js --input page1.json page2.json [pageN.json ...] \
//                       --output consolidated.json
//
// Each input file must be one of:
//   • Full MCP envelope: { result: { resources: [...], nextCursor?: "..." } }
//   • Mid envelope:      { resources: [...], nextCursor?: "..." }
//   • Bare array:        [...]           (assumed to be a complete final page)
//
// Exit behavior:
//   • Exits non-zero if the LAST input page still has a non-empty nextCursor.
//     The user must fetch the next page(s) and re-run.
//   • Emits a coverage summary to stderr (per-page counts + total).
//
// The output JSON is a bare array of items — same shape merge-items.js
// already accepts.

'use strict';

const fs = require('fs');
const path = require('path');

// ── CLI args ──────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const inputs = [];
let outputPath = null;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--output') {
    outputPath = args[++i];
  } else if (args[i] === '--input') {
    while (i + 1 < args.length && !args[i + 1].startsWith('--')) {
      inputs.push(args[++i]);
    }
  }
}

if (inputs.length === 0) {
  console.error('Usage: node fetch-items.js --input <page1.json> [page2.json ...] --output <consolidated.json>');
  console.error('');
  console.error('Save each MCP `search_active_s360_kpi_action_items` response to a separate file,');
  console.error('then pass them all in page order. This script verifies pagination completed.');
  process.exit(2);
}

// ── Envelope unwrap ───────────────────────────────────────────────────────────
// Returns { resources, nextCursor } regardless of envelope shape. A bare array
// input is treated as a complete final page (no nextCursor).
function unwrap(p) {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (Array.isArray(j)) return { resources: j, nextCursor: null };
  if (j && Array.isArray(j.resources)) return { resources: j.resources, nextCursor: j.nextCursor || null };
  if (j && j.result && Array.isArray(j.result.resources)) {
    return { resources: j.result.resources, nextCursor: j.result.nextCursor || null };
  }
  throw new Error(`Could not find a resources array in ${p}. Expected a top-level array, { resources: [...] }, or { result: { resources: [...] } }.`);
}

// ── Consolidate ───────────────────────────────────────────────────────────────
const all = [];
let lastCursor = null;
for (let i = 0; i < inputs.length; i++) {
  const { resources, nextCursor } = unwrap(inputs[i]);
  const isLast = i === inputs.length - 1;
  console.error(`Page ${i + 1} (${path.basename(inputs[i])}): ${resources.length} items${nextCursor ? ` [nextCursor present]` : ''}`);
  all.push(...resources);
  if (isLast) lastCursor = nextCursor;
}

// ── Hard-fail if the caller forgot to fetch every page ────────────────────────
if (lastCursor) {
  console.error('');
  console.error('ERROR: pagination incomplete.');
  console.error(`The last input page (${path.basename(inputs[inputs.length - 1])}) still has a nextCursor set.`);
  console.error(`Call \`mcp_s360-breeze-m_search_active_s360_kpi_action_items\` again with cursor="${lastCursor}",`);
  console.error('save the response as the next page file, then re-run this script with the additional --input file.');
  console.error('');
  console.error('Not enforcing this is how S360 reports have silently under-counted before (AB#3683197).');
  process.exit(1);
}

console.error(`Consolidated ${all.length} items across ${inputs.length} page(s). Pagination complete (last nextCursor is empty).`);

// ── Write ─────────────────────────────────────────────────────────────────────
const out = JSON.stringify(all, null, 2);
if (outputPath) {
  fs.writeFileSync(outputPath, out);
  console.error(`Wrote ${all.length} items to ${outputPath}`);
} else {
  process.stdout.write(out + '\n');
}
