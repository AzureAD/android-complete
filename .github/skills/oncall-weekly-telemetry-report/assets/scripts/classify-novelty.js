#!/usr/bin/env node
/**
 * classify-novelty.js -- Answer "is this NEW this week, or has it been broken for weeks?"
 *
 * WHY THIS EXISTS
 * ---------------
 * bucket-trends.js answers "which direction has this moved over 60 days?" That is a
 * *direction* question. It cannot answer the question an on-call engineer actually asks
 * when triaging: "which of these 20 moving error codes started THIS week, and which have
 * I already been staring at for a month?"
 *
 * Without that split, the attention section degenerates into a volume-ranked list where a
 * flat-but-huge error code outranks a genuine step change, and every card carries the same
 * "needs owner triage" boilerplate. That is precisely the failure this script fixes.
 *
 * IT ALSO SUPPRESSES RATIO ARTIFACTS
 * ----------------------------------
 * A week-over-week percentage is meaningless when the PRIOR week was itself anomalous.
 * Real example from the 2026-07-30 broker run:
 *
 *   429                      300,664 299,965 892,839 974,980  11,512  32,530   2,724 -> 16,531
 *   temporarily_unavailable   30,257  29,962  37,263   6,168  41,971     141      71 -> 36,153
 *
 * Both reported as ~+400% WoW and led the regression list. Both are noise: `429` is
 * actually 94% BELOW its own 60-day median (it collapsed from ~975K in June), and
 * `temporarily_unavailable` merely returned to its normal ~36K band after two suppressed
 * weeks. Meanwhile the genuinely new regression that week -- the ipc_* family, flat for
 * seven straight weeks (cv 0.02-0.03) then stepping up 22% together -- sat at positions
 * #6, #9 and #10.
 *
 * A series with high variance gets `suppressRatio: true`. The playbook must NOT headline a
 * percentage for those; report the absolute level and its position within the historical
 * band instead.
 *
 * FAMILY CLUSTERING
 * -----------------
 * Related codes that move together are one root cause, not N findings. When >=2 keys
 * sharing a prefix all move the same direction in the same week, they are emitted as a
 * `families` entry with a summed series. One story, one card.
 *
 * IT ALSO DECIDES WHAT DESERVES A CHART (noise control)
 * ----------------------------------------------------
 * "Elevated" and "getting worse" are different questions, and conflating them is what made
 * the report re-triage the same known issues every week. `ONGOING` is therefore split:
 *
 *   ACCELERATING  elevated AND still climbing right now  -> promote, chart it, card it
 *   ONGOING       elevated but PLATEAUED                 -> collapse to a counted line
 *
 * Only NEW + ACCELERATING form the `attention` set. Everything else is reference material.
 * On the 2026-07-31 broker data this is 5 series out of 53, against 13 volume-ranked
 * attention rows before -- and the 5 are the real ones.
 *
 * Two false positives the ACCELERATING gate exists to stop, both observed on real data:
 *   authorization_pending                       down 37% WoW, 16% BELOW its own median
 *   IntuneAppProtectionPolicyRequiredException  flat (cv 0.08), +4.9% vs median, down 3.7% WoW
 * Both had a rising block-mean, which alone is not evidence of anything. Hence the gate
 * requires magnitude (ratio > 1.10), slope (recentRatio > 1.10) AND not-currently-falling.
 *
 * `weeksElevated` answers "how long has this been like this?" WITHOUT any persisted state --
 * it is derived from the same 9-week series, so two engineers on two machines get identical
 * answers and there is nothing to commit, sync, or go stale. It counts consecutive recent
 * weeks above the EARLY-window baseline (median of the first third). A series that never
 * left that baseline band reports the full window with `sustainedFullWindow: true`, i.e.
 * "at this level for as long as we can see" -- a standing condition, not this week's news.
 * The reference is the baseline, NOT the current value: "within 20% of current" is
 * meaningless for a flat series and made a code that stepped up this week claim it had been
 * elevated for seven.
 *
 * Input: the --json sidecar written by bucket-trends.js.
 *
 * Usage:
 *   node classify-novelty.js <bucket-trends-sidecar.json>
 *       [--floor=N]           # ignore keys whose current complete week is below N (default 5000)
 *       [--family-sep=_]      # token separator for family detection ('none' disables)
 *       [--top=N]             # rows printed per bucket (default 8)
 *       [--json=<path>]       # structured sidecar for programmatic use
 *       [--summary]           # counts + rows only, no series arrays
 *
 * Classification (evaluated in order, first match wins), computed on COMPLETE weeks only:
 *   VOLATILE   cv > 0.60                          -> ratio is noise; suppressRatio=true
 *   RECOVERY   prior week < 50% of median         -> returning to band, not a new break
 *              and current >= 70% of median
 *   NEW        current > 115% of median           -> stable baseline then a clean step
 *              and cv < 0.25
 *   ONGOING    late-third mean > 115% early-third -> already climbing; not new
 *   IMPROVING  current < 80% of median
 *   STABLE     otherwise
 *
 * The cv < 0.25 guard on NEW is what stops a jittery series from being called a step
 * change. A series must have been genuinely boring before a jump counts as news.
 */
const fs = require('fs');

const args = process.argv.slice(2);
const file = args.find(a => !a.startsWith('--'));
const floor = +((args.find(a => a.startsWith('--floor=')) || '').split('=')[1] || 5000);
const topN = +((args.find(a => a.startsWith('--top=')) || '').split('=')[1] || 8);
const jsonOut = (args.find(a => a.startsWith('--json=')) || '').split('=')[1];
const summary = args.includes('--summary');
const famSepRaw = (args.find(a => a.startsWith('--family-sep=')) || '').split('=')[1];
const famSep = famSepRaw === undefined ? '_' : famSepRaw;
const familiesEnabled = famSep !== 'none' && famSep !== '';

if (!file) {
  console.error('Usage: node classify-novelty.js <bucket-trends-sidecar.json> [--floor=N] [--family-sep=_|none] [--top=N] [--json=path] [--summary]');
  process.exit(1);
}

const d = JSON.parse(fs.readFileSync(file, 'utf8'));
if (!d.buckets) {
  console.error('Input does not look like a bucket-trends.js --json sidecar (no .buckets).');
  process.exit(1);
}

// ---- stats helpers -------------------------------------------------------
const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
const median = a => {
  const s = [...a].sort((x, y) => x - y);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
};
const stdev = a => {
  const m = mean(a);
  return Math.sqrt(mean(a.map(v => (v - m) ** 2)));
};
const pct = v => (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';

/**
 * `series` follows DISPLAY weeks, which include the in-progress partial week when
 * bucket-trends.js ran with --include-partial-end. Classifying on a partial week would
 * read as a fake collapse, so trim to the classify-week count.
 */
const completeLen = (d.classifyWeeks || d.weeks || []).length;

function classify(code, series) {
  const comp = series.slice(0, completeLen);
  if (comp.length < 4) return null;              // too little history to say anything honest
  const cur = comp[comp.length - 1];
  const hist = comp.slice(0, -1);
  const prev = hist[hist.length - 1];
  const med = median(hist);
  if (med <= 0) return null;

  const m = mean(hist);
  const cv = m > 0 ? stdev(hist) / m : 99;
  const ratio = cur / med;

  const third = Math.max(1, Math.floor(hist.length / 3));
  const early = mean(hist.slice(0, third));
  const late = mean(hist.slice(-third));
  const climb = early > 0 ? late / early : 1;

  // --- Is it STILL climbing, or did it climb and then plateau? ---------------
  // `climb` looks at the whole history, so a code that stepped up 6 weeks ago and has been
  // flat ever since still reads as "climbing" forever. That is exactly what made the report
  // re-triage the same known issues every week. Compare the most recent block against the
  // block before it to separate "getting worse now" from "bad, but stable".
  const win = Math.min(3, Math.floor(comp.length / 2));
  const recentBlock = mean(comp.slice(-win));
  const priorBlock = mean(comp.slice(-2 * win, -win));
  const recentRatio = priorBlock > 0 ? recentBlock / priorBlock : 1;

  // --- How long has this been at its current level? --------------------------
  // Reference is the EARLY-window baseline (median of the first third), not the current
  // value: "within 20% of current" is meaningless for a low-variance series, where a code
  // that stepped up this week still looks like it has been here for the whole window.
  // Derived purely from the series already in hand -- NO persisted state, so two engineers
  // on two machines get identical answers and there is nothing to commit or sync.
  const baseline = median(comp.slice(0, third)) || med;
  let weeksElevated, sustainedFullWindow;
  if (cur < baseline * 1.15) {
    // Never left its own early band inside the observable window. Honest ceiling:
    // "at this level for as long as we can see" -- a standing condition, not this week's news.
    weeksElevated = comp.length;
    sustainedFullWindow = true;
  } else {
    weeksElevated = 0;
    for (let i = comp.length - 1; i >= 0; i--) {
      if (comp[i] >= baseline * 1.15) weeksElevated++;
      else break;
    }
    sustainedFullWindow = weeksElevated >= comp.length;
  }

  let label;
  if (cv > 0.60) label = 'VOLATILE';
  else if (prev < med * 0.5 && ratio >= 0.7) label = 'RECOVERY';
  else if (ratio > 1.15 && cv < 0.25) label = 'NEW';
  else if (climb > 1.15) {
    // ACCELERATING must mean "getting worse RIGHT NOW", and by a margin worth a card.
    // Two observed false positives this gate exists to stop:
    //   authorization_pending  -- down 37% WoW, 16% BELOW its median, but block means drifted up.
    //   IntuneAppProtectionPolicyRequiredException -- flat (cv 0.08), only +4.9% vs its own
    //     median, down 3.7% WoW, yet a slow multi-week ramp made it outrank the real finding.
    // So require all three: meaningfully elevated, still climbing, and not currently falling.
    // ratio > 1.10 is the magnitude bar -- within 10% of its own median is not a story.
    const stillRising = ratio > 1.10 && recentRatio > 1.10 && cur >= prev * 0.95;
    label = stillRising ? 'ACCELERATING' : 'ONGOING';
  }
  else if (ratio < 0.8) label = 'IMPROVING';
  else label = 'STABLE';

  return {
    code, label,
    current: cur,
    prior: prev,
    median: Math.round(med),
    cv: +cv.toFixed(2),
    vsMedian: +(ratio - 1).toFixed(3),
    wowWeek: prev > 0 ? +((cur - prev) / prev).toFixed(3) : null,
    weeksElevated,
    sustainedFullWindow,
    recentRatio: +recentRatio.toFixed(2),
    // A percentage off a depressed or wildly swinging base is not reportable as a headline.
    suppressRatio: label === 'VOLATILE' || label === 'RECOVERY',
    series: comp,
  };
}

// ---- classify every key across all trend buckets --------------------------
const all = [];
let belowFloor = 0;
for (const grp of Object.keys(d.buckets)) {
  for (const m of d.buckets[grp]) {
    const r = classify(m.code, m.series || []);
    if (!r) continue;
    if (r.current < floor) { belowFloor++; continue; }
    r.trendBucket = grp;
    all.push(r);
  }
}

// ---- family clustering ----------------------------------------------------
// Only cluster when members genuinely move together: >=2 members sharing a prefix AND
// agreeing on direction. Grouping divergent codes would invent a story that isn't there.
const families = [];
if (familiesEnabled) {
  const byPrefix = new Map();
  for (const r of all) {
    const parts = String(r.code).split(famSep);
    if (parts.length < 2) continue;
    const p = parts[0];
    if (!byPrefix.has(p)) byPrefix.set(p, []);
    byPrefix.get(p).push(r);
  }
  for (const [prefix, members] of byPrefix) {
    if (members.length < 2) continue;
    const labels = new Set(members.map(x => x.label));
    if (labels.size !== 1) continue;                       // must agree on direction
    const label = [...labels][0];
    if (label === 'STABLE') continue;                      // a family of nothing is not news
    const len = Math.min(...members.map(x => x.series.length));
    const summed = Array.from({ length: len }, (_, i) =>
      members.reduce((s, x) => s + x.series[i], 0));
    const cur = summed[len - 1], prev = summed[len - 2];
    families.push({
      family: prefix + famSep + '*',
      label,
      members: members.map(x => x.code),
      current: cur,
      prior: prev,
      wowWeek: prev > 0 ? +((cur - prev) / prev).toFixed(3) : null,
      series: summed,
    });
  }
}

// ---- output ---------------------------------------------------------------
// ORDER is also the report's priority order. Only NEW and ACCELERATING earn a chart and a
// card; everything below the ATTENTION line is reference material that must collapse.
const ORDER = ['NEW', 'ACCELERATING', 'ONGOING', 'VOLATILE', 'RECOVERY', 'IMPROVING', 'STABLE'];
const ATTENTION = ['NEW', 'ACCELERATING'];
const HEAD = {
  NEW:          'NEW THIS WEEK  (stable baseline -> clean step change; lead with these)',
  ACCELERATING: 'ACCELERATING   (elevated AND still climbing; promote -- this is getting worse)',
  ONGOING:      'ONGOING        (elevated but PLATEAUED; known/steady -- collapse, do not re-triage)',
  VOLATILE:     'VOLATILE       (high variance; % is noise -- report absolute level, not ratio)',
  RECOVERY:     'RECOVERY       (returning to normal band after a dip; not a regression)',
  IMPROVING:    'IMPROVING      (below historical band)',
  STABLE:       'STABLE         (within band)',
};

const counts = ORDER.map(l => `${l}=${all.filter(r => r.label === l).length}`).join('  ');
console.log(`\nNovelty classification (floor=${floor.toLocaleString()}, complete weeks=${completeLen}):  ${counts}`);
if (belowFloor) console.log(`(${belowFloor} key(s) skipped below floor)`);

// Order by novelty FIRST, volume only as a tiebreak within a label. Sorting this list by
// device count alone is a known, reported defect: it lets a high-volume ACCELERATING code
// outrank a genuine NEW step change, and because the report drops the tail when the list
// exceeds the 8-row cap, the dropped rows are the lowest-volume ones -- which is exactly
// where NEW codes land. A NEW code is by definition low-volume relative to something that
// has been climbing for weeks.
const attention = all
  .filter(r => ATTENTION.includes(r.label))
  .sort((a, b) => ORDER.indexOf(a.label) - ORDER.indexOf(b.label) || b.current - a.current);
console.log(`ATTENTION set (NEW + ACCELERATING) = ${attention.length} of ${all.length} series.` +
  (attention.length === 0 ? '  -> QUIET WEEK: report should be short.' : ''));

for (const label of ORDER) {
  const rows = all.filter(r => r.label === label).sort((a, b) => b.current - a.current);
  if (!rows.length) continue;
  console.log(`\n### ${HEAD[label]}`);
  for (const r of rows.slice(0, topN)) {
    const ratioTxt = r.suppressRatio ? '(ratio suppressed)' : `WoW ${pct(r.wowWeek)}`;
    const age = r.sustainedFullWindow ? `>=${r.weeksElevated}w` : `${r.weeksElevated}w`;
    console.log(`  ${r.code.padEnd(44)} cur=${r.current.toLocaleString().padStart(11)}  vs median ${pct(r.vsMedian).padStart(8)}  cv=${String(r.cv).padStart(4)}  elev=${age.padStart(4)}  ${ratioTxt}`);
    if (!summary) console.log(`      ${r.series.map(v => v.toLocaleString()).join('  ')}`);
  }
  if (rows.length > topN) console.log(`  ... and ${rows.length - topN} more`);
}

if (families.length) {
  console.log(`\n### FAMILIES (related keys moving together -- report as ONE finding, one root cause)`);
  for (const f of families.sort((a, b) => b.current - a.current)) {
    console.log(`  ${f.family.padEnd(44)} ${f.label}  cur=${f.current.toLocaleString()}  WoW ${pct(f.wowWeek)}  [${f.members.join(', ')}]`);
    if (!summary) console.log(`      ${f.series.map(v => v.toLocaleString()).join('  ')}`);
  }
}

if (jsonOut) {
  fs.writeFileSync(jsonOut, JSON.stringify({
    floor,
    completeWeeks: completeLen,
    weeks: (d.classifyWeeks || d.weeks || []).slice(0, completeLen),
    metric: d.metric,
    key: d.key,
    counts: Object.fromEntries(ORDER.map(l => [l, all.filter(r => r.label === l).length])),
    // The report's attention section is exactly this list -- nothing else earns a card or a
    // chart. Emitting it here (rather than leaving each playbook to re-derive it) is what
    // keeps "what needs attention" from drifting back into a volume-ranked dump.
    attentionLabels: ATTENTION,
    attention: attention.map(r => r.code),
    quietWeek: attention.length === 0,
    items: all,
    families,
  }, null, 2));
  console.log(`\nWrote novelty sidecar -> ${jsonOut}`);
}
