#!/usr/bin/env node
/**
 * summarize-attribution.js — Roll up WoW attribution slices for spike-attribution cards.
 *
 * Reads N Kusto MCP JSON output files, each with a `--label=...` tag describing what
 * dimension it slices, and prints a per-(error_code, week, dimension) breakdown.
 *
 * Each input is the JSON file produced by the Kusto MCP tool. The first row of
 * `results.items` is the schema; the remaining rows are positional arrays.
 *
 * The script auto-detects schema by looking at the column names of row[0]:
 *   - It expects exactly one column named `error_code`.
 *   - It expects exactly one column named `wk` or `week` (datetime).
 *   - It expects exactly one numeric column named `devs` or `countDevices`.
 *   - The remaining 1–2 string columns are treated as the slicing dimension.
 *
 * Usage:
 *   node summarize-attribution.js \
 *     --label=span <span.json> \
 *     --label=calling_app <app.json> \
 *     --label=active_broker <ab.json> \
 *     --label=broker_version <ver.json>
 *
 * Output: per error_code, per week, the top-5 values of each dimension by devs and
 * their share-of-total. Use this to fill in attr-card dim rows.
 *
 * IMPORTANT: when you build the source query, ALWAYS use
 *     dcount_hll(hll_merge(countDevicesHll))
 * for distinct device counts (HLL merging). `sum(countDevices)` double-counts!
 */
const fs = require('fs');

const inputs = []; // { label, file }
let pendingLabel = null;
for (const a of process.argv.slice(2)) {
  if (a.startsWith('--label=')) { pendingLabel = a.split('=')[1]; continue; }
  inputs.push({ label: pendingLabel || 'unknown', file: a });
  pendingLabel = null;
}

if (inputs.length === 0) {
  console.error('Usage: node summarize-attribution.js --label=<dim1> file1.json --label=<dim2> file2.json ...');
  process.exit(1);
}

function loadSlice({ label, file }) {
  const d = JSON.parse(fs.readFileSync(file, 'utf8'));
  const rows = d.results.items;
  const schema = rows[0]; // object: { col: type, ... }
  const cols = Object.keys(schema);
  const idxCode = cols.indexOf('error_code');
  let idxWeek = cols.indexOf('wk'); if (idxWeek < 0) idxWeek = cols.indexOf('week');
  let idxDevs = cols.indexOf('devs'); if (idxDevs < 0) idxDevs = cols.indexOf('countDevices');
  if (idxCode < 0 || idxWeek < 0 || idxDevs < 0) {
    throw new Error(`${file}: schema must include error_code, wk|week, devs|countDevices. Got [${cols.join(', ')}]`);
  }
  // The "dimension" column is the first string col that isn't error_code/week
  const idxDim = cols.findIndex((c, i) => i !== idxCode && i !== idxWeek && i !== idxDevs && schema[c] === 'string');
  if (idxDim < 0) throw new Error(`${file}: no string dimension column found`);

  const map = {}; // code -> wk -> dim -> devs
  for (const r of rows.slice(1)) {
    const code = r[idxCode], wk = r[idxWeek], dim = r[idxDim] || '(blank)', devs = r[idxDevs] || 0;
    ((map[code] ||= {})[wk] ||= {})[dim] = (map[code][wk][dim] || 0) + devs;
  }
  return { label, dimColumn: cols[idxDim], map };
}

const slices = inputs.map(loadSlice);

// Collect (code, week) universe
const universe = {};
for (const s of slices) {
  for (const [code, wks] of Object.entries(s.map)) {
    for (const wk of Object.keys(wks)) {
      ((universe[code] ||= {})[wk] = true);
    }
  }
}

const codes = Object.keys(universe).sort();
for (const code of codes) {
  console.log(`\n========== ${code} ==========`);
  const wks = Object.keys(universe[code]).sort();
  for (const wk of wks) {
    console.log(`\n  --- week ${wk.slice(0, 10)} ---`);
    for (const s of slices) {
      const dim = s.map[code]?.[wk] || {};
      const total = Object.values(dim).reduce((x, y) => x + y, 0);
      if (total === 0) continue;
      console.log(`    [${s.label}]  total=${total.toLocaleString()}`);
      Object.entries(dim)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .forEach(([k, v]) => {
          const pct = (v / total * 100).toFixed(1);
          console.log(`       ${pct.padStart(5)}%  ${k}  (${v.toLocaleString()})`);
        });
    }
  }
}
