#!/usr/bin/env node
/*
 * compare-versions.js — release delta + classification engine.
 *
 * Reads the array-form JSON that run-kql.ps1 emits:
 *     { "results": { "items": [ [col0,col1,...], [row...], ... ] } }   // row 0 = column names
 * (Also tolerates the columns/rows object form just in case.)
 *
 * Two modes:
 *
 *  1) rows   — one row PER VERSION, metrics in columns. Computes first−second delta for
 *              each metric and classifies regression / improvement / flat with a pp
 *              threshold and a volume guard.
 *        node compare-versions.js rows --file r.json \
 *             --version-col broker_version --first 16.1.0 --second 16.0.1 \
 *             --metrics SilentDevReliability,InteractiveDevReliability \
 *             --lower-is-better DeviceErrorRate \
 *             --volume-col SilentDevices --volume-floor 1000 --threshold 1.0
 *
 *  2) movers — rows ALREADY paired (one row per error_code/scenario with first/second
 *              columns). Ranks by the delta column and tags direction.
 *        node compare-versions.js movers --file e.json \
 *             --key-col error_code --first-col firstShare --second-col secondShare \
 *             --delta-col shareDeltaPp --top 10 --threshold 0.5
 *
 * Output: JSON to stdout — a structured verdict array the report author pastes/reasons over.
 * Higher-is-better by default; pass --lower-is-better <cols> for latency/error-rate metrics.
 */

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.startsWith('--')) { const k = t.slice(2); const v = (argv[i+1] && !argv[i+1].startsWith('--')) ? argv[++i] : true; a[k] = v; }
    else a._.push(t);
  }
  return a;
}

function loadItems(file) {
  const raw = JSON.parse(require('fs').readFileSync(file, 'utf8'));
  let items = raw && raw.results && raw.results.items;
  if (!items) throw new Error('No results.items in ' + file);
  // object-form fallback: items = { columns:[{ColumnName}], rows:[[...]] }
  if (!Array.isArray(items)) {
    const cols = (items.columns || []).map(c => c.ColumnName || c.name || c);
    items = [cols, ...(items.rows || [])];
  }
  const cols = items[0];
  const rows = items.slice(1);
  return { cols, rows };
}

function idx(cols, name, label) {
  const i = cols.indexOf(name);
  if (i < 0) throw new Error('Column "' + name + '" not found (' + (label||'') + '). Available: ' + cols.join(', '));
  return i;
}

const num = v => { const n = parseFloat(String(v).replace(/[, %]/g, '')); return Number.isFinite(n) ? n : null; };

function classify(deltaPp, threshold, lowerIsBetter, lowVolume) {
  if (lowVolume) return 'low-volume';
  if (Math.abs(deltaPp) < threshold) return 'flat';
  const improved = lowerIsBetter ? deltaPp < 0 : deltaPp > 0;
  return improved ? 'improvement' : 'regression';
}

function rowsMode(a) {
  const { cols, rows } = loadItems(a.file);
  const vc = idx(cols, a['version-col'], 'version-col');
  const findRow = v => rows.find(r => String(r[vc]) === String(v));
  const r1 = findRow(a.first), r2 = findRow(a.second);
  if (!r1) throw new Error('first version "' + a.first + '" not in data');
  if (!r2) throw new Error('second version "' + a.second + '" not in data');
  const metrics = String(a.metrics || '').split(',').map(s => s.trim()).filter(Boolean);
  const lower = new Set(String(a['lower-is-better'] || '').split(',').map(s => s.trim()).filter(Boolean));
  const threshold = parseFloat(a.threshold || '1.0');
  const volFloor = a['volume-col'] ? parseFloat(a['volume-floor'] || '0') : null;
  const volIdx = a['volume-col'] ? idx(cols, a['volume-col'], 'volume-col') : -1;
  const firstVol = volIdx >= 0 ? num(r1[volIdx]) : null;
  const out = metrics.map(m => {
    const mi = idx(cols, m, 'metric');
    const f = num(r1[mi]), s = num(r2[mi]);
    const delta = (f != null && s != null) ? +(f - s).toFixed(4) : null;
    const lowVol = volFloor != null && firstVol != null && firstVol < volFloor;
    return {
      metric: m, first: f, second: s, deltaPp: delta,
      lowerIsBetter: lower.has(m),
      verdict: delta == null ? 'no-data' : classify(delta, threshold, lower.has(m), lowVol)
    };
  });
  return { mode: 'rows', first: a.first, second: a.second, firstVolume: firstVol, threshold, metrics: out };
}

function moversMode(a) {
  const { cols, rows } = loadItems(a.file);
  const kc = idx(cols, a['key-col'], 'key-col');
  const dc = idx(cols, a['delta-col'], 'delta-col');
  const fc = a['first-col'] ? idx(cols, a['first-col']) : -1;
  const sc = a['second-col'] ? idx(cols, a['second-col']) : -1;
  const threshold = parseFloat(a.threshold || '0.5');
  const top = parseInt(a.top || '10', 10);
  const lowerIsBetter = a['lower-is-better'] === true || a['lower-is-better'] === 'true';
  const all = rows.map(r => {
    const delta = num(r[dc]);
    return {
      key: r[kc],
      first: fc >= 0 ? num(r[fc]) : null,
      second: sc >= 0 ? num(r[sc]) : null,
      deltaPp: delta,
      verdict: delta == null ? 'no-data' : classify(delta, threshold, lowerIsBetter, false)
    };
  }).filter(x => x.deltaPp != null);
  all.sort((x, y) => Math.abs(y.deltaPp) - Math.abs(x.deltaPp));
  const regressions = all.filter(x => x.verdict === 'regression').slice(0, top);
  const improvements = all.filter(x => x.verdict === 'improvement').slice(0, top);
  return { mode: 'movers', threshold, top, regressions, improvements };
}

function main() {
  const a = parseArgs(process.argv.slice(2));
  const mode = a._[0];
  if (!a.file || !mode) {
    console.error('usage: compare-versions.js <rows|movers> --file <json> ...  (see header)');
    process.exit(2);
  }
  let res;
  if (mode === 'rows') res = rowsMode(a);
  else if (mode === 'movers') res = moversMode(a);
  else { console.error('unknown mode: ' + mode); process.exit(2); }
  process.stdout.write(JSON.stringify(res, null, 2) + '\n');
}
main();
