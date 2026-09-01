#!/usr/bin/env node
/*
 * fetch-appcenter-crashes.js — pull Authenticator crash data from App Center Diagnostics
 * and emit the SAME array-form JSON that run-kql.ps1 produces, so compare-versions.js and
 * the report-fill flow consume it unchanged:
 *     { "results": { "items": [ [col0,col1,...], [row...], ... ] } }   // row 0 = column names
 *
 * Why App Center (not Play Console): App Center's Diagnostics/errorGroups API is the only
 * source that returns DETAILED crash clusters (exception type, crashing class/method/line,
 * per-version counts, device counts) filterable by app version — Play Console exports only
 * aggregate numbers, never per-crash detail. App Center *Analytics* (crash_counts /
 * crashfree_users / sessions) is RETIRED (410/404 or drained to ~0), so DO NOT use it; a true
 * crash-free rate must take its denominator from Kusto telemetry (active devices per AppVersion)
 * — see assets/docs/crash-sources.md. Scope is Authenticator ONLY (Broker is not a store app).
 *
 * Auth: an App Center read-only User API token. Resolution order:
 *   1) --token-file <path>   2) $APPCENTER_API_TOKEN   3) ~/.android-release-reports/appcenter.token
 * The token is a SECRET — keep it out of the repo and never echo it.
 *
 * Modes:
 *
 *  groups — top crash clusters for ONE version. Each row's crashSharePct is its share of that
 *           version's total crashes (over all fetched groups), mirroring the Broker error-movers
 *           "device-share" idea so growth is normalized for cohort size.
 *     node fetch-appcenter-crashes.js groups --owner authapp-t7qc \
 *          --app Microsoft-Authenticator-Android-Prod-App-Center \
 *          --version 6.2606.3817 --days 14 --top 15 --out groups-new.json
 *
 *  enrich — for the top-N crash signatures on ONE version, pull what the list view can't show:
 *           the per-group DAILY TREND (errorCountsPerDay → rising / decaying / spike-then-decay /
 *           steady — separates an early-rollout spike from a sustained regression, pattern P4) and
 *           an instance-sampled OS-major + device-model CONCENTRATION (errorGroups/{id}/errors →
 *           is the crash one OS / one OEM, pattern P6). App Center's aggregate operatingSystemCounts
 *           / modelCounts / affectedDeviceCounts endpoints 404 for this app, so the per-group daily
 *           series and a capped instance sample are the only routes to trend + dimensions.
 *     node fetch-appcenter-crashes.js enrich --owner authapp-t7qc \
 *          --app Microsoft-Authenticator-Android-Prod-App-Center \
 *          --version 6.2606.3817 --days 14 --top 8 --out crash-enrich.json
 *
 *  diff   — pair TWO versions (--version = rolling out, --base = previous) by crash SIGNATURE
 *           (codeRaw/label, aggregating sub-groups). NOTE: App Center's errorGroupId is
 *           version-scoped (0 id overlap across versions), so the cross-version join MUST be on
 *           the signature, not the id. Computes each cluster's crash-share on each version + the
 *           delta (pp), and tags status (new / regressed / improved / flat / gone). Output is
 *           already paired, so compare-versions.js movers ranks it directly:
 *     node fetch-appcenter-crashes.js diff --owner authapp-t7qc \
 *          --app Microsoft-Authenticator-Android-Prod-App-Center \
 *          --version 6.2606.3817 --base 6.2605.3042 --days 14 \
 *          --devices-new 12619500 --devices-base 74905873 --out crash-diff.json
 *     node compare-versions.js movers --file crash-diff.json \
 *          --key-col label --first-col basePer1k --second-col newPer1k \
 *          --delta-col rateDeltaPer1k --lower-is-better true --top 10
 *
 *   --devices-new / --devices-base are OPTIONAL Kusto active-device denominators (run
 *   authenticator-crash-denominator.kql). When supplied, diff computes the honest
 *   crashes-per-1k-active-devices RATE and derives status/ranking from it instead of from
 *   crash-share. Without them, status falls back to crash-share deltas (less reliable when
 *   the two versions' total crash pools differ greatly in size).
 *
 *  newcrashes — "what crashes are GENUINELY NEW in this release?" Anti-joins the new version's
 *           signatures against the UNION of several recent PRIOR versions (NOT just the immediate
 *           baseline). This exists because App Center's per-version firstOccurrence is the version's
 *           ROLLOUT date, NOT the signature's app-history first-seen — so `diff` can mark a crash
 *           "new" (absent from the single base) when it actually predates that base by months
 *           (verified: the okhttp http-cache journal IOException shows firstOccurrence = rollout day
 *           on a young build yet exists on every version back many releases). A signature earns
 *           "genuinely-new" only when it is absent from ALL listed priors within the 27-day API
 *           window AND present on the new build. Defaults to --page-cap 0 (exhaust) so a "new"
 *           verdict can't miss a low-count prior occurrence.
 *     node fetch-appcenter-crashes.js newcrashes --owner authapp-t7qc \
 *          --app Microsoft-Authenticator-Android-Prod-App-Center \
 *          --version 6.2606.3817 --priors 6.2605.3042,6.2605.2973,6.2604.2550,6.2603.1485 \
 *          --days 14 --min-count 5 --devices-new 34580000 --out new-crashes.json
 *
 *  signature — cross-version presence of ONE crash SIGNATURE (+ optional daily trend on the primary
 *           version): "is crash X specific to this release, or pre-existing across versions?" Pages
 *           each version's groups and aggregates every group whose codeRaw / class.method:line /
 *           exceptionMessage / exceptionType contains --match. Pass --trend to tag the primary
 *           version's daily series (rising / decaying / spike-then-decay / steady).
 *     node fetch-appcenter-crashes.js signature --owner authapp-t7qc \
 *          --app Microsoft-Authenticator-Android-Prod-App-Center \
 *          --version 6.2606.3817 --priors 6.2605.3042,6.2605.2973,6.2604.2550,6.2603.1485 \
 *          --match "FileSystem$1.rename" --days 27 --trend --out sig.json
 *
 * --out is optional; without it the JSON goes to stdout. Totals/diagnostics go to stderr.
 */

const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.startsWith('--')) { const k = t.slice(2); const v = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? argv[++i] : true; a[k] = v; }
    else a._.push(t);
  }
  return a;
}

function resolveToken(a) {
  if (a['token-file']) return fs.readFileSync(a['token-file'], 'utf8').trim();
  if (process.env.APPCENTER_API_TOKEN) return process.env.APPCENTER_API_TOKEN.trim();
  const def = path.join(os.homedir(), '.android-release-reports', 'appcenter.token');
  if (fs.existsSync(def)) return fs.readFileSync(def, 'utf8').trim();
  throw new Error('No App Center token. Pass --token-file, set $APPCENTER_API_TOKEN, or place it at ' + def);
}

function getJson(url, token) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'X-API-Token': token, 'Accept': 'application/json' } }, res => {
      let buf = '';
      res.on('data', d => buf += d);
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return reject(new Error('HTTP ' + res.statusCode + ' for ' + url + ' :: ' + buf.slice(0, 300)));
        }
        try { resolve(JSON.parse(buf)); } catch (e) { reject(new Error('Bad JSON from ' + url + ': ' + e.message)); }
      });
    }).on('error', reject);
  });
}

const API = 'https://api.appcenter.ms/v0.1';

// Normalize App Center's relative nextLink (it comes back with an extra "/api" prefix that 404s
// against the public host) to an absolute URL.
const absLink = next => next ? (next.startsWith('http') ? next : 'https://api.appcenter.ms' + next.replace(/^\/api\//, '/')) : null;

// Fetch ALL error groups for a version (follows nextLink) so the crash TOTAL — the denominator for
// crash-share and the numerator for the per-1k rate — is over the full set, not just the first page.
// App Center has NO working aggregate total endpoint for this app (errorCounts / affectedDeviceCounts
// 404; version-level errorCountsPerDay drains to 0), so summing every page is the only accurate total.
// pageCap === 0 (or 'all') ⇒ exhaust (hard safety stop at 100 pages ≈ 10k groups). Groups the team has
// triaged as noise (hidden, or state === "Ignored") are dropped by default so they don't inflate the
// rate — pass includeHidden to keep them. Returns the filtered groups; logs drops + truncation.
async function fetchGroups(owner, app, version, startIso, token, pageCap, includeHidden) {
  const base = `${API}/apps/${owner}/${app}/errors/errorGroups`;
  let url = `${base}?version=${encodeURIComponent(version)}&start=${encodeURIComponent(startIso)}&$top=100&$orderby=${encodeURIComponent('count desc')}`;
  const cap = (pageCap === 0 || pageCap === 'all') ? 100 : (pageCap || 12);
  const raw = [];
  let page = 0;
  for (; page < cap && url; page++) {
    const r = await getJson(url, token);
    for (const g of (r.errorGroups || [])) raw.push(g);
    url = absLink(r.nextLink);
  }
  const groups = includeHidden ? raw : raw.filter(g => g.hidden !== true && g.state !== 'Ignored');
  const dropped = raw.length - groups.length;
  if (dropped) process.stderr.write(`  (${version}) dropped ${dropped} hidden/ignored group(s) from totals\n`);
  if (url) process.stderr.write(`  (${version}) WARNING: hit page cap ${cap} with more pages remaining — total is UNDERCOUNTED; pass --page-cap 0 to exhaust\n`);
  return groups;
}

const labelOf = g => g.codeRaw || g.exceptionMethod || g.exceptionType || g.errorGroupId;
// The first-party crash site as class.method:line — the most actionable attribution detail in the
// list view. NOTE App Center's exceptionClassMethod / exceptionAppCode are BOOLEAN flags (not frame
// strings); the frame lives in exceptionClassName + exceptionMethod + exceptionLine.
const appFrameOf = g => {
  const cls = (typeof g.exceptionClassName === 'string' && g.exceptionClassName) ? g.exceptionClassName : '';
  const mth = (typeof g.exceptionMethod === 'string' && g.exceptionMethod) ? g.exceptionMethod : '';
  const frame = (cls && mth) ? `${cls}.${mth}` : (cls || mth || (typeof g.codeRaw === 'string' ? g.codeRaw : ''));
  return (frame && g.exceptionLine) ? `${frame}:${g.exceptionLine}` : frame;
};
const pct = (n, d) => d > 0 ? +(100 * n / d).toFixed(2) : 0;

// Aggregate version-scoped errorGroups into ONE entry per crash SIGNATURE (codeRaw/label), since the
// same crash gets a different errorGroupId on every version. Sums count/devices across sub-groups that
// share a crashing frame; keeps the earliest firstOccurrence + latest lastOccurrence + the sub-group ids.
function aggBySig(groups) {
  const m = new Map();
  for (const g of groups) {
    const key = labelOf(g);
    let e = m.get(key);
    if (!e) { e = { label: key, exceptionType: g.exceptionType || '', appCodeFrame: appFrameOf(g), exceptionMessage: (g.exceptionMessage || '').slice(0, 160), firstOccurrence: '', lastOccurrence: '', count: 0, devices: 0, ids: [] }; m.set(key, e); }
    e.count += g.count || 0;
    e.devices += g.deviceCount || 0;             // sum is an upper bound (a device can hit >1 sub-group)
    if (!e.exceptionType) e.exceptionType = g.exceptionType || '';
    if (!e.appCodeFrame) e.appCodeFrame = appFrameOf(g);
    if (!e.exceptionMessage) e.exceptionMessage = (g.exceptionMessage || '').slice(0, 160);
    if (g.firstOccurrence && (!e.firstOccurrence || g.firstOccurrence < e.firstOccurrence)) e.firstOccurrence = g.firstOccurrence;
    if (g.lastOccurrence && (!e.lastOccurrence || g.lastOccurrence > e.lastOccurrence)) e.lastOccurrence = g.lastOccurrence;
    if (g.errorGroupId) e.ids.push(g.errorGroupId);
  }
  return m;
}

function startIsoFromArgs(a) {
  if (a.start) return a.start;
  const days = parseInt(a.days || '14', 10);
  return new Date(Date.now() - days * 86400000).toISOString().replace(/\.\d+Z$/, 'Z');
}

// --page-cap N (default 12) or 0/"all" to exhaust. Higher = more accurate total (App Center has no
// working aggregate-total endpoint for this app).
function pageCapFromArgs(a, dflt) {
  const v = a['page-cap'];
  if (v === undefined) return dflt;
  if (v === 'all' || v === '0' || v === 0) return 0;
  return parseInt(v, 10);
}

function emit(obj, out) {
  const json = JSON.stringify(obj);
  if (out) { fs.writeFileSync(out, json, 'utf8'); process.stderr.write('Saved -> ' + out + '\n'); }
  else process.stdout.write(json + '\n');
}

async function groupsMode(a, token) {
  const startIso = startIsoFromArgs(a);
  const top = parseInt(a.top || '15', 10);
  const groups = await fetchGroups(a.owner, a.app, a.version, startIso, token, pageCapFromArgs(a, 12), !!a['include-hidden']);
  const total = groups.reduce((s, g) => s + (g.count || 0), 0);
  process.stderr.write(`version ${a.version}: ${groups.length} crash groups, ${total} total crashes since ${startIso}\n`);
  const cols = ['errorGroupId', 'exceptionType', 'label', 'appCodeFrame', 'exceptionMessage', 'count', 'deviceCount', 'crashSharePct', 'firstOccurrence', 'lastOccurrence', 'appBuild', 'state'];
  const rows = groups.slice(0, top).map(g => [
    g.errorGroupId, g.exceptionType || '', labelOf(g), appFrameOf(g), (g.exceptionMessage || '').slice(0, 160),
    g.count || 0, g.deviceCount || 0, pct(g.count || 0, total),
    g.firstOccurrence || '', g.lastOccurrence || '', g.appBuild || '', g.state || ''
  ]);
  emit({ meta: { version: a.version, totalCrashes: total, groupCount: groups.length, start: startIso }, results: { items: [cols, ...rows] } }, a.out);
}

async function diffMode(a, token) {
  if (!a.base) throw new Error('diff mode needs --base <previous version>');
  const startIso = startIsoFromArgs(a);
  const top = parseInt(a.top || '20', 10);
  const cap = pageCapFromArgs(a, 12);
  // OPTIONAL Kusto denominators (active devices per version). When supplied, the honest
  // crashes-per-1k-active-devices RATE is computed and drives status/ranking — crash-SHARE
  // alone is misleading when the two versions' total crash pools differ in size (a signature
  // can take a bigger SHARE of a much smaller pool while its per-device rate actually drops).
  const devNew = a['devices-new'] ? parseFloat(a['devices-new']) : null;
  const devBase = a['devices-base'] ? parseFloat(a['devices-base']) : null;
  const haveRate = devNew > 0 && devBase > 0;
  const [gNew, gBase] = await Promise.all([
    fetchGroups(a.owner, a.app, a.version, startIso, token, cap, !!a['include-hidden']),
    fetchGroups(a.owner, a.app, a.base, startIso, token, cap, !!a['include-hidden']),
  ]);
  const totNew = gNew.reduce((s, g) => s + (g.count || 0), 0);
  const totBase = gBase.reduce((s, g) => s + (g.count || 0), 0);
  process.stderr.write(`new ${a.version}: ${totNew} crashes / ${gNew.length} groups ; base ${a.base}: ${totBase} crashes / ${gBase.length} groups\n`);
  if (haveRate) {
    process.stderr.write(`rate: new ${(1000 * totNew / devNew).toFixed(2)}/1k (${devNew} dev) vs base ${(1000 * totBase / devBase).toFixed(2)}/1k (${devBase} dev)\n`);
  }

  // App Center's errorGroupId is VERSION-SCOPED (verified: 0 id overlap across versions, 116
  // codeRaw/label overlap), so join cross-version on the crash SIGNATURE (codeRaw/label),
  // aggregating sub-groups that share a crashing frame.
  const agg = groups => {
    const m = new Map();
    for (const g of groups) {
      const key = labelOf(g);
      let e = m.get(key);
      if (!e) { e = { label: key, exceptionType: g.exceptionType || '', appCodeFrame: '', exceptionMessage: '', firstOccurrence: '', count: 0, devices: 0 }; m.set(key, e); }
      e.count += g.count || 0;
      e.devices += g.deviceCount || 0;            // sum is an upper bound (a device can hit >1 sub-group)
      if (!e.exceptionType) e.exceptionType = g.exceptionType || '';
      if (!e.appCodeFrame) e.appCodeFrame = appFrameOf(g);
      if (!e.exceptionMessage) e.exceptionMessage = (g.exceptionMessage || '').slice(0, 160);
      // earliest first-seen across sub-groups — lets the report tell a genuinely-new signature
      // from one that merely fell out of the (capped) base list.
      if (g.firstOccurrence && (!e.firstOccurrence || g.firstOccurrence < e.firstOccurrence)) e.firstOccurrence = g.firstOccurrence;
    }
    return m;
  };
  const mNew = agg(gNew), mBase = agg(gBase);
  const keys = new Set([...mNew.keys(), ...mBase.keys()]);
  const rows = [...keys].map(k => {
    const n = mNew.get(k), b = mBase.get(k);
    const newCount = n ? n.count : 0, baseCount = b ? b.count : 0;
    const newShare = pct(newCount, totNew), baseShare = pct(baseCount, totBase);
    const shareDeltaPp = +(newShare - baseShare).toFixed(2);
    const newPer1k = haveRate ? +(1000 * newCount / devNew).toFixed(3) : null;
    const basePer1k = haveRate ? +(1000 * baseCount / devBase).toFixed(3) : null;
    const rateDeltaPer1k = haveRate ? +(newPer1k - basePer1k).toFixed(3) : null;
    let status;
    if (haveRate) {
      // Status from the per-device RATE (honest), not share.
      const rel = basePer1k > 0 ? (newPer1k - basePer1k) / basePer1k : (newPer1k > 0 ? Infinity : 0);
      if (baseCount === 0 && newCount > 0) status = 'new';
      else if (newCount === 0 && baseCount > 0) status = 'gone';
      else if (rel >= 0.15 && rateDeltaPer1k >= 0.02) status = 'regressed';
      else if (rel <= -0.15 && rateDeltaPer1k <= -0.02) status = 'improved';
      else status = 'flat';
    } else {
      if (baseCount === 0 && newCount > 0) status = 'new';
      else if (newCount === 0 && baseCount > 0) status = 'gone';
      else if (shareDeltaPp >= 0.5) status = 'regressed';
      else if (shareDeltaPp <= -0.5) status = 'improved';
      else status = 'flat';
    }
    return { label: k, exceptionType: (n && n.exceptionType) || (b && b.exceptionType) || '', appCodeFrame: (n && n.appCodeFrame) || (b && b.appCodeFrame) || '', exceptionMessage: (n && n.exceptionMessage) || (b && b.exceptionMessage) || '', firstOccurrenceNew: (n && n.firstOccurrence) || '', baseCount, newCount, basePer1k, newPer1k, rateDeltaPer1k, baseShare, newShare, shareDeltaPp, newDevices: n ? n.devices : 0, status };
  });
  // Sort by prevalence on the NEW build (per-1k rate if known, else crash-share); movers
  // re-ranks by its own delta-col internally, so this governs only the human-readable file.
  rows.sort((x, y) => (haveRate ? (y.newPer1k - x.newPer1k) : (y.newShare - x.newShare)));

  const cols = ['label', 'exceptionType', 'appCodeFrame', 'exceptionMessage', 'firstOccurrenceNew', 'baseCount', 'newCount', 'basePer1k', 'newPer1k', 'rateDeltaPer1k', 'baseSharePct', 'newSharePct', 'shareDeltaPp', 'newDevices', 'status'];
  const items = rows.slice(0, top).map(r => [
    r.label, r.exceptionType, r.appCodeFrame, r.exceptionMessage, r.firstOccurrenceNew,
    r.baseCount, r.newCount, r.basePer1k, r.newPer1k, r.rateDeltaPer1k,
    r.baseShare, r.newShare, r.shareDeltaPp, r.newDevices, r.status
  ]);
  emit({ meta: { version: a.version, base: a.base, totalCrashesNew: totNew, totalCrashesBase: totBase, devicesNew: devNew, devicesBase: devBase, newRatePer1k: haveRate ? +(1000 * totNew / devNew).toFixed(3) : null, baseRatePer1k: haveRate ? +(1000 * totBase / devBase).toFixed(3) : null, start: startIso }, results: { items: [cols, ...items] } }, a.out);
}

// Classify a per-group daily series into a trend tag (pattern P4: tell an early-rollout spike that
// decays from a sustained regression). Compares the first vs second half of the window and where the
// peak day sits.
function trendOf(days) {
  const nz = days.filter(d => d.count > 0);
  const total = days.reduce((s, d) => s + d.count, 0);
  if (total === 0) return { trend: 'none', total: 0, peakDay: '', lastDay: '', half1: 0, half2: 0 };
  const mid = Math.floor(days.length / 2);
  const half1 = days.slice(0, mid).reduce((s, d) => s + d.count, 0);
  const half2 = days.slice(mid).reduce((s, d) => s + d.count, 0);
  const peak = days.reduce((p, d) => d.count > p.count ? d : p, days[0]);
  const peakIdx = days.indexOf(peak);
  const tail = days.slice(-3).reduce((s, d) => s + d.count, 0) / Math.min(3, days.length);
  let trend;
  if (total < 30) trend = 'low-volume';
  else if (peakIdx < days.length - 3 && tail < peak.count * 0.4) trend = 'spike-then-decay';
  else if (half2 >= half1 * 1.5) trend = 'rising';
  else if (half1 >= half2 * 1.5) trend = 'decaying';
  else trend = 'steady';
  const dt = d => (d.datetime || '').slice(0, 10);
  return { trend, total, peakDay: dt(peak), lastDay: dt(nz.length ? nz[nz.length - 1] : peak), half1, half2 };
}

// For the top-N signatures on ONE version, pull the per-group daily TREND (P4) and an instance-sampled
// OS-major + device-model CONCENTRATION (P6) — the diagnostics the list view can't give. Aggregate
// endpoints (operatingSystemCounts/modelCounts) 404 for this app, so dimensions come from a capped
// sample of errorGroups/{id}/errors instances (osVersion / deviceName / country).
async function enrichMode(a, token) {
  const startIso = startIsoFromArgs(a);
  const top = parseInt(a.top || '8', 10);
  const instPages = parseInt(a['instance-pages'] || '4', 10); // 4 pages ≈ 400 instances sampled / group
  const base = `${API}/apps/${a.owner}/${a.app}/errors`;
  const groups = await fetchGroups(a.owner, a.app, a.version, startIso, token, pageCapFromArgs(a, 12), !!a['include-hidden']);
  const picked = groups.slice(0, top);
  process.stderr.write(`enrich ${a.version}: ${picked.length} top signatures (trend + instance-sampled OS/model)\n`);
  const items = [];
  for (const g of picked) {
    const id = g.errorGroupId;
    let trend = { trend: 'n/a', peakDay: '', lastDay: '', half1: 0, half2: 0 };
    try {
      const d = await getJson(`${base}/errorGroups/${id}/errorCountsPerDay?version=${encodeURIComponent(a.version)}&start=${encodeURIComponent(startIso)}`, token);
      trend = trendOf(d.errors || []);
    } catch (e) { process.stderr.write(`  trend ${id}: ${e.message}\n`); }
    // instance sample → OS-major + model concentration
    const oss = {}, mods = {}; let sampled = 0;
    let url = `${base}/errorGroups/${id}/errors?version=${encodeURIComponent(a.version)}&start=${encodeURIComponent(startIso)}&$top=100`;
    for (let p = 0; p < instPages && url; p++) {
      let r; try { r = await getJson(url, token); } catch (e) { break; }
      for (const ev of (r.errors || [])) { sampled++; const om = String(ev.osVersion || '').split('.')[0] || '?'; oss[om] = (oss[om] || 0) + 1; const dn = ev.deviceName || '?'; mods[dn] = (mods[dn] || 0) + 1; }
      url = absLink(r.nextLink);
    }
    const top1 = o => { const e = Object.entries(o).sort((x, y) => y[1] - x[1])[0]; return e ? { k: e[0], pct: sampled ? +(100 * e[1] / sampled).toFixed(1) : 0 } : { k: '', pct: 0 }; };
    const o1 = top1(oss), m1 = top1(mods);
    items.push([
      labelOf(g), g.exceptionType || '', appFrameOf(g), g.count || 0, g.deviceCount || 0,
      trend.trend, trend.peakDay, trend.lastDay,
      o1.k, o1.pct, m1.k, m1.pct, sampled
    ]);
  }
  const cols = ['label', 'exceptionType', 'appCodeFrame', 'count', 'deviceCount', 'trend', 'peakDay', 'lastDay', 'topOsMajor', 'osConcentrationPct', 'topModel', 'modelConcentrationPct', 'sampleN'];
  emit({ meta: { version: a.version, start: startIso, signatures: picked.length, note: 'OS/model are instance-sampled concentrations (capped), not exact totals' }, results: { items: [cols, ...items] } }, a.out);
}

// "What crashes are GENUINELY NEW in this release?" — anti-join the new version's signatures against
// the UNION of several recent PRIOR versions (not just the immediate baseline). App Center's per-version
// firstOccurrence is the version's ROLLOUT date, NOT the signature's app-history first-seen, so a crash
// can show firstOccurrence INSIDE the window yet be many releases old (verified live: the okhttp
// http-cache journal IOException had firstOccurrence = rollout day on the new build but exists on every
// recent version). Only "absent from ALL listed priors within the 27-day API window AND present on the
// new build" earns "genuinely-new". Still-active prior versions keep throwing structural/environmental
// crashes, so the 27-day anti-join catches them; a defect introduced THIS release is absent from priors.
async function newCrashesMode(a, token) {
  if (!a.priors) throw new Error('newcrashes needs --priors <v1,v2,...> (recent prior versions to anti-join against)');
  const startIso = startIsoFromArgs(a);
  const cap = pageCapFromArgs(a, 0);            // exhaust by default — a "new" verdict must not miss a prior occurrence
  const top = parseInt(a.top || '40', 10);
  const minCount = parseInt(a['min-count'] || '5', 10);
  const devNew = a['devices-new'] ? parseFloat(a['devices-new']) : null;
  const priors = String(a.priors).split(',').map(s => s.trim()).filter(Boolean);
  const [gNew, ...gPriorsArr] = await Promise.all([
    fetchGroups(a.owner, a.app, a.version, startIso, token, cap, !!a['include-hidden']),
    ...priors.map(v => fetchGroups(a.owner, a.app, v, startIso, token, cap, !!a['include-hidden'])),
  ]);
  const mNew = aggBySig(gNew);
  const priorHits = new Map();                  // signature -> { versions:[], maxCount }
  priors.forEach((v, i) => {
    for (const [sig, e] of aggBySig(gPriorsArr[i])) {
      let p = priorHits.get(sig); if (!p) { p = { versions: [], maxCount: 0 }; priorHits.set(sig, p); }
      p.versions.push(v); p.maxCount = Math.max(p.maxCount, e.count);
    }
  });
  process.stderr.write(`new ${a.version}: ${mNew.size} signatures; priors [${priors.join(', ')}] contribute ${priorHits.size} signatures since ${startIso}\n`);
  // A native crash whose only frame is a raw address (e.g. "0x1d0c37a8 + 481192") or a bare signal
  // (SIGABRT/SIGSEGV/minidump) has a signature that DIFFERS across builds (the address is relocated
  // per binary), so it ALWAYS anti-joins as "absent from priors" — a false genuinely-new. Tag those
  // native/unsymbolized so the actionable JAVA-frame new crashes stand out; a native row needs OS/model
  // + count corroboration (enrich), not the signature anti-join, to judge whether it is truly new.
  const isNative = (label, type) => /^0x[0-9a-f]+\b/i.test(String(label || '')) || /SIG|minidump|native|SEGV|SIGABRT|ABRT|ILL_|BUS_|TRAP/i.test(String(type || ''));
  const rows = [...mNew.values()].map(e => {
    const hit = priorHits.get(e.label);
    const newPer1k = devNew > 0 ? +(1000 * e.count / devNew).toFixed(3) : null;
    const frameKind = isNative(e.label, e.exceptionType) ? 'native' : 'java';
    let verdict;
    if (e.count < minCount) verdict = 'low-volume';
    else if (hit) verdict = 'pre-existing';
    else verdict = frameKind === 'native' ? 'new-native?' : 'genuinely-new';
    return { ...e, frameKind, newPer1k, priorVersionsHit: hit ? hit.versions.join(',') : '', maxPriorCount: hit ? hit.maxCount : 0, verdict };
  });
  // Actionable java-frame new crashes first, then native-suspect, then pre-existing, then low-volume.
  const rank = v => v === 'genuinely-new' ? 0 : v === 'new-native?' ? 1 : v === 'pre-existing' ? 2 : 3;
  rows.sort((x, y) => rank(x.verdict) - rank(y.verdict) || y.count - x.count);
  const nNew = rows.filter(r => r.verdict === 'genuinely-new').length;
  const nNative = rows.filter(r => r.verdict === 'new-native?').length;
  process.stderr.write(`  => ${nNew} genuinely-new java-frame signature(s) + ${nNative} native-unsymbolized suspect(s), >= ${minCount} crashes\n`);
  const cols = ['label', 'exceptionType', 'appCodeFrame', 'exceptionMessage', 'firstSeenNew', 'newCount', 'newDevices', 'newPer1k', 'frameKind', 'priorVersionsHit', 'maxPriorCount', 'verdict'];
  const items = rows.slice(0, top).map(r => [r.label, r.exceptionType, r.appCodeFrame, r.exceptionMessage, r.firstOccurrence, r.count, r.devices, r.newPer1k, r.frameKind, r.priorVersionsHit, r.maxPriorCount, r.verdict]);
  emit({ meta: { version: a.version, priors, start: startIso, devicesNew: devNew, genuinelyNew: nNew, newNativeSuspect: nNative, note: 'genuinely-new = JAVA-frame signature absent from ALL listed priors within the 27-day API window. native/hex-frame rows (verdict new-native?) have build-unique signatures and ALWAYS anti-join as new — corroborate with enrich (OS/model + count), not the signature. firstSeenNew is the version ROLLOUT date, not app-history first-seen.' }, results: { items: [cols, ...items] } }, a.out);
}

// Cross-version presence of ONE crash SIGNATURE (+ optional daily trend on the primary version):
// "is crash X specific to this release, or pre-existing across versions?" Pages each version's groups
// and aggregates every group whose codeRaw / class.method:line / exceptionMessage / exceptionType
// contains --match (case-insensitive). The primary version is --version; --priors is the comparison set.
async function signatureMode(a, token) {
  if (!a.match) throw new Error('signature mode needs --match <substring of codeRaw / frame / message / type>');
  const startIso = startIsoFromArgs(a);
  const cap = pageCapFromArgs(a, 0);
  const needle = String(a.match).toLowerCase();
  const hit = g => [g.codeRaw, appFrameOf(g), g.exceptionMessage, g.exceptionType].some(s => String(s || '').toLowerCase().includes(needle));
  const versions = [a.version, ...String(a.priors || '').split(',').map(s => s.trim()).filter(Boolean)];
  process.stderr.write(`signature "${a.match}" across ${versions.length} version(s) since ${startIso}\n`);
  const rows = [];
  let primaryId = null;
  for (const v of versions) {
    const gs = await fetchGroups(a.owner, a.app, v, startIso, token, cap, !!a['include-hidden']);
    const matched = gs.filter(hit);
    const count = matched.reduce((s, g) => s + (g.count || 0), 0);
    const devices = matched.reduce((s, g) => s + (g.deviceCount || 0), 0);
    const first = matched.map(g => g.firstOccurrence).filter(Boolean).sort()[0] || '';
    const last = matched.map(g => g.lastOccurrence).filter(Boolean).sort().slice(-1)[0] || '';
    const id = matched[0] ? matched[0].errorGroupId : '';
    if (v === a.version) primaryId = id;
    rows.push([v, matched.length > 0 ? 'YES' : 'no', matched.length, count, devices, first, last, id]);
    process.stderr.write(`  ${v}: ${matched.length} group(s), ${count} crashes, ${devices} devices\n`);
  }
  let trend = null;
  if ((a.trend === true || a.trend === 'true') && primaryId) {
    try {
      const d = await getJson(`${API}/apps/${a.owner}/${a.app}/errors/errorGroups/${primaryId}/errorCountsPerDay?version=${encodeURIComponent(a.version)}&start=${encodeURIComponent(startIso)}`, token);
      trend = { ...trendOf(d.errors || []), series: (d.errors || []).map(e => [String(e.datetime).slice(0, 10), e.count]) };
      process.stderr.write(`  trend(${a.version}): ${trend.trend} (peak ${trend.peakDay}, last ${trend.lastDay})\n`);
    } catch (e) { process.stderr.write(`  trend err: ${e.message}\n`); }
  }
  const cols = ['version', 'found', 'matchedGroups', 'count', 'devices', 'firstOccurrence', 'lastOccurrence', 'errorGroupId'];
  emit({ meta: { match: a.match, primaryVersion: a.version, start: startIso, trend, note: 'firstOccurrence is the version ROLLOUT date, not app-history first-seen' }, results: { items: [cols, ...rows] } }, a.out);
}

async function main() {
  const a = parseArgs(process.argv.slice(2));
  const mode = a._[0];
  if (!mode || !a.owner || !a.app || !a.version) {
    console.error('usage: fetch-appcenter-crashes.js <groups|diff|enrich|newcrashes|signature> --owner <o> --app <a> --version <v> [--base <v>] [--priors <v1,v2,...>] [--match <s>] [--trend] [--days 14] [--top N] [--min-count 5] [--page-cap N|0] [--devices-new N] [--include-hidden] [--out f.json]');
    process.exit(2);
  }
  const token = resolveToken(a);
  if (mode === 'groups') await groupsMode(a, token);
  else if (mode === 'diff') await diffMode(a, token);
  else if (mode === 'enrich') await enrichMode(a, token);
  else if (mode === 'newcrashes') await newCrashesMode(a, token);
  else if (mode === 'signature') await signatureMode(a, token);
  else { console.error('unknown mode: ' + mode); process.exit(2); }
}
main().catch(e => { console.error('ERROR: ' + e.message); process.exit(1); });
