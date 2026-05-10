#!/usr/bin/env node
/**
 * summarize-attribution.js — Roll up WoW attribution slices for spike-attribution cards.
 *
 * TWO INPUT MODES:
 *
 * 1) Per-dim files (legacy mode): one Kusto JSON per dimension, each tagged with
 *    --label=. Use this when you ran 7 separate per-dim queries.
 *
 *    node summarize-attribution.js \
 *      --label=span <span.json> \
 *      --label=calling_app <app.json> \
 *      --label=active_broker <ab.json> \
 *      --label=broker_version <ver.json> \
 *      --label=acct_type <acct.json> \
 *      --label=shared_dev <shared.json> \
 *      --label=client_sku <sku.json>
 *
 *    Per-file schema: row[0] must include `error_code`, `wk`/`week`, `devs`/`countDevices`,
 *    and exactly one trailing string column (the dimension value).
 *
 * 2) Union mode (NEW, recommended for 2-week WoW attribution — one query covers all dims):
 *
 *    node summarize-attribution.js --union <attribution-union.json>
 *
 *    Expected schema (any column order):
 *       dim          string  -- short label e.g. 'span', 'calling_app', 'broker_ver'
 *       wk | week    datetime
 *       error_code   string  (or `error_type` — use --key=error_type to switch)
 *       val_string   string  } EITHER `val_string`+`val_bool` (Kusto union of
 *       val_bool     bool    } mixed-type slice columns) ...
 *       val          string  } ... OR a single `val` column
 *       devs         long    (use `dcount_hll(hll_merge(countDevicesHll))` upstream)
 *       errs         long    (optional — request count, used for retry-storm detection)
 *
 *    The union form is what Step 5 of SKILL.md now recommends — 1 round-trip vs 7.
 *    See assets/queries/attr-union-by-dim.kql.
 *
 * Output: per error_code, per dimension, the top-5 values for each week (prior + curr),
 * concentration % of curr-week total, and Δd / Δr vs prior week.
 *
 * IMPORTANT: when you build the source query, ALWAYS use
 *     dcount_hll(hll_merge(countDevicesHll))
 * for distinct device counts (HLL merging). `sum(countDevices)` double-counts!
 */
const fs = require('fs');

// --- arg parsing ---------------------------------------------------------
const argv = process.argv.slice(2);
const inputs = []; // per-dim mode: { label, file }
let pendingLabel = null;
let unionFile = null;
let keyCol = 'error_code';   // override with --key=error_type for type cards
let topN = 5;
for (const a of argv) {
  if (a === '--union') { /* next non-flag arg is the file */ pendingLabel = '__UNION__'; continue; }
  if (a.startsWith('--union=')) { unionFile = a.split('=')[1]; pendingLabel = null; continue; }
  if (a.startsWith('--key='))   { keyCol   = a.split('=')[1]; continue; }
  if (a.startsWith('--top='))   { topN     = parseInt(a.split('=')[1], 10) || 5; continue; }
  if (a.startsWith('--label=')) { pendingLabel = a.split('=')[1]; continue; }
  if (pendingLabel === '__UNION__') { unionFile = a; pendingLabel = null; continue; }
  inputs.push({ label: pendingLabel || 'unknown', file: a });
  pendingLabel = null;
}

if (!unionFile && inputs.length === 0) {
  console.error('Usage:\n  node summarize-attribution.js --union <file.json> [--key=error_code|error_type] [--top=N]\n  node summarize-attribution.js --label=<dim1> file1.json --label=<dim2> file2.json ...');
  process.exit(1);
}

// --- helpers --------------------------------------------------------------
function fmt(n) {
  if (n == null) return '–';
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}
function pct(num, den) { return den ? (100 * num / den).toFixed(1) : '0.0'; }
function delta(curr, prior) {
  if (prior == null || prior === 0) return curr ? 'NEW' : '–';
  return ((curr - prior) / prior * 100).toFixed(1) + '%';
}

// --- per-dim file loader (legacy mode) ------------------------------------
function loadSliceFile({ label, file }) {
  const d = JSON.parse(fs.readFileSync(file, 'utf8'));
  const rows = d.results.items;
  const schema = rows[0];
  const cols = Object.keys(schema);
  const idxCode = cols.indexOf(keyCol);
  let idxWeek = cols.indexOf('wk'); if (idxWeek < 0) idxWeek = cols.indexOf('week');
  let idxDevs = cols.indexOf('devs'); if (idxDevs < 0) idxDevs = cols.indexOf('countDevices');
  let idxErrs = cols.indexOf('errs'); if (idxErrs < 0) idxErrs = cols.indexOf('countOverall');
  if (idxCode < 0 || idxWeek < 0 || idxDevs < 0) {
    throw new Error(`${file}: schema must include ${keyCol}, wk|week, devs|countDevices. Got [${cols.join(', ')}]`);
  }
  const idxDim = cols.findIndex((c, i) =>
    i !== idxCode && i !== idxWeek && i !== idxDevs && i !== idxErrs && schema[c] === 'string');
  if (idxDim < 0) throw new Error(`${file}: no string dimension column found`);

  const map = {};
  for (const r of rows.slice(1)) {
    const code = r[idxCode], wk = r[idxWeek];
    const dim = (r[idxDim] === null || r[idxDim] === '') ? '(blank)' : r[idxDim];
    const devs = r[idxDevs] || 0;
    const errs = idxErrs >= 0 ? (r[idxErrs] || 0) : 0;
    const slot = ((map[code] ||= {})[wk] ||= {})[dim] ||= { devs: 0, errs: 0 };
    slot.devs += devs; slot.errs += errs;
  }
  return { label, map };
}

// --- union-mode loader (NEW) ---------------------------------------------
function loadUnion(file) {
  const d = JSON.parse(fs.readFileSync(file, 'utf8'));
  const rows = d.results.items;
  const schema = rows[0];
  const cols = Object.keys(schema);
  const idx = name => cols.indexOf(name);
  const idxDim  = idx('dim');
  const idxCode = idx(keyCol);
  let idxWeek = idx('wk'); if (idxWeek < 0) idxWeek = idx('week');
  let idxDevs = idx('devs'); if (idxDevs < 0) idxDevs = idx('countDevices');
  let idxErrs = idx('errs'); if (idxErrs < 0) idxErrs = idx('countOverall');
  const idxValS = idx('val_string') >= 0 ? idx('val_string') : idx('val');
  const idxValB = idx('val_bool');
  if (idxDim < 0 || idxCode < 0 || idxWeek < 0 || idxDevs < 0 || idxValS < 0) {
    throw new Error(`Union file ${file}: schema must include dim, ${keyCol}, wk|week, devs|countDevices, val_string|val (and optionally val_bool). Got [${cols.join(', ')}]`);
  }
  // perDim[label].map[code][wk][dimVal] = { devs, errs }
  const byDim = {};
  for (const r of rows.slice(1)) {
    const label = r[idxDim];
    const code  = r[idxCode];
    const wk    = r[idxWeek];
    const valS  = r[idxValS];
    const valB  = idxValB >= 0 ? r[idxValB] : null;
    let v;
    if (valS !== null && valS !== undefined && valS !== '') v = valS;
    else if (valB !== null && valB !== undefined)           v = String(valB);
    else                                                    v = '(blank)';
    const devs = r[idxDevs] || 0;
    const errs = idxErrs >= 0 ? (r[idxErrs] || 0) : 0;
    const target = byDim[label] ||= { label, map: {} };
    const slot = ((target.map[code] ||= {})[wk] ||= {})[v] ||= { devs: 0, errs: 0 };
    slot.devs += devs; slot.errs += errs;
  }
  return Object.values(byDim);
}

const slices = unionFile ? loadUnion(unionFile) : inputs.map(loadSliceFile);

// --- output --------------------------------------------------------------
const universe = {};
for (const s of slices) {
  for (const [code, wks] of Object.entries(s.map)) {
    for (const wk of Object.keys(wks)) ((universe[code] ||= {})[wk] = true);
  }
}
const codes = Object.keys(universe).sort();

for (const code of codes) {
  const wks = Object.keys(universe[code]).sort();
  const prior = wks[0], curr = wks[wks.length - 1];
  console.log(`\n========== ${code}    (prior=${prior?.slice(0,10)}  curr=${curr?.slice(0,10)}) ==========`);
  for (const s of slices) {
    const priorMap = s.map[code]?.[prior] || {};
    const currMap  = s.map[code]?.[curr]  || {};
    const allVals = new Set([...Object.keys(priorMap), ...Object.keys(currMap)]);
    if (allVals.size === 0) continue;
    const totC = Object.values(currMap).reduce((a, b) => a + b.devs, 0);
    const rows = [...allVals].map(v => ({
      v,
      pDev: priorMap[v]?.devs || 0,
      cDev: currMap[v]?.devs  || 0,
      pErr: priorMap[v]?.errs || 0,
      cErr: currMap[v]?.errs  || 0,
    })).sort((a, b) => b.cDev - a.cDev).slice(0, topN);
    console.log(`\n  -- ${s.label}  (curr-total devices=${fmt(totC)})`);
    for (const r of rows) {
      const share = pct(r.cDev, totC);
      console.log(`     ${share.padStart(5)}%  ${fmt(r.cDev).padStart(8)}d   d_dev ${delta(r.cDev, r.pDev).padStart(8)}   d_req ${delta(r.cErr, r.pErr).padStart(8)}   ${r.v}`);
    }
  }
}
