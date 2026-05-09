#!/usr/bin/env node
// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
//
// S360 Weekly Report Generator
// Reads a JSON data file and produces an Outlook-compatible HTML report.
//
// Usage:
//   node generate-report.js --input data.json --output report.html
//   node generate-report.js --input data.json  (writes to stdout)
//
// Input JSON schema:
// {
//   "reportDate": "2026-04-08",          // ISO date string
//   "items": [{ ... }],                  // Active S360 items (see below)
//   "resolved": [{ title, assignee, pbi? }],  // Resolved since last week
//   "nameMap": { "alias": "Full Name" }, // Alias → display name
//   "newItems": [{ title, service }]     // NEW this week (for diff section)
// }
//
// Item shape:
// {
//   title, shortTitle, service, ownerAlias, ownerName,
//   sla ("OutOfSla"|"ApproachingSla"|"InSla"),
//   due (ISO date), eta (ISO date|null),
//   pbi (number|null), isNew (boolean),
//   s360Url, program, programDesc, subtitle
// }

'use strict';

const fs = require('fs');
const path = require('path');

// ── CLI Args ──────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf('--' + name);
  return idx >= 0 && idx + 1 < args.length ? args[idx + 1] : null;
}

const inputPath = getArg('input');
const outputPath = getArg('output');

if (!inputPath) {
  console.error('Usage: node generate-report.js --input <data.json> [--output <report.html>]');
  process.exit(1);
}

// ── Load Data ─────────────────────────────────────────────────────────────────
const data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const { reportDate, items, resolved = [], nameMap = {}, newItems = [] } = data;

const reportDateObj = new Date(reportDate);

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(d) {
  if (!d) return null;
  if (d === 'N/A') return 'N/A';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function fmtDateLong(d) {
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function daysBetween(a, b) {
  return Math.round((new Date(b) - new Date(a)) / 86400000);
}

function pbiUrl(id) {
  return `https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/${id}`;
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function slaStyle(sla) {
  if (sla === 'OutOfSla') return {
    rowBg: '#fff5f5', rowBgAlt: '#fff5f5', leftBorder: '#cf222e',
    badgeBg: '#cf222e', badgeColor: '#fff', label: 'MISSED'
  };
  if (sla === 'ApproachingSla') return {
    rowBg: '#fff8f0', rowBgAlt: '#fff8f0', leftBorder: '#e65100',
    badgeBg: '#e65100', badgeColor: '#fff', label: 'NEAR SLA'
  };
  return {
    rowBg: '#ffffff', rowBgAlt: '#fafafa', leftBorder: '#2e7d32',
    badgeBg: '#e8f5e9', badgeColor: '#2e7d32', label: 'IN SLA'
  };
}

// ── Statistics ─────────────────────────────────────────────────────────────────
const total = items.length;
const outCount = items.filter(i => i.sla === 'OutOfSla').length;
const nearCount = items.filter(i => i.sla === 'ApproachingSla').length;
const inCount = items.filter(i => i.sla === 'InSla').length;
const noEta = items.filter(i => !i.eta).length;
const newPbiCount = items.filter(i => i.isNew).length;

const outPct = total > 0 ? Math.max(Math.round(outCount / total * 100), outCount > 0 ? 5 : 0) : 0;
const nearPct = total > 0 ? Math.max(Math.round(nearCount / total * 100), nearCount > 0 ? 8 : 0) : 0;
const inPct = Math.max(100 - outPct - nearPct, 0);

// ── Group by Program ──────────────────────────────────────────────────────────
const programs = {};
items.forEach(it => {
  if (!programs[it.program]) programs[it.program] = { items: [], desc: it.programDesc, worstSla: 3 };
  programs[it.program].items.push(it);
  const s = it.sla === 'OutOfSla' ? 1 : it.sla === 'ApproachingSla' ? 2 : 3;
  if (s < programs[it.program].worstSla) programs[it.program].worstSla = s;
});
const sortedPrograms = Object.entries(programs).sort((a, b) => a[1].worstSla - b[1].worstSla);

// ── Ownership ─────────────────────────────────────────────────────────────────
const owners = {};
items.forEach(it => {
  if (!owners[it.ownerAlias]) owners[it.ownerAlias] = { name: it.ownerName, total: 0, out: 0, near: 0, inSla: 0, noEta: 0 };
  owners[it.ownerAlias].total++;
  if (it.sla === 'OutOfSla') owners[it.ownerAlias].out++;
  else if (it.sla === 'ApproachingSla') owners[it.ownerAlias].near++;
  else owners[it.ownerAlias].inSla++;
  if (!it.eta) owners[it.ownerAlias].noEta++;
});
const sortedOwners = Object.entries(owners).sort((a, b) => {
  if (b[1].out !== a[1].out) return b[1].out - a[1].out;
  if (b[1].near !== a[1].near) return b[1].near - a[1].near;
  return b[1].total - a[1].total;
});

// ── Build HTML ────────────────────────────────────────────────────────────────
let html = '';

const shortDate = fmtDateLong(reportDate);

// Page wrapper + header
html += `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>S360 Weekly Report \u2014 ${shortDate}</title>
</head>
<body style="margin:0; padding:0; background-color:#f0f2f5; font-family:'Segoe UI', Arial, sans-serif; line-height:1.5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f0f2f5;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="960" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff; border-radius:12px; border:1px solid #d0d0d0;">

<tr><td>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td bgcolor="#0078d4" style="height:6px; font-size:1px; line-height:1px; border-radius:12px 12px 0 0;">&nbsp;</td></tr>
  </table>
</td></tr>
<tr>
  <td style="padding:36px 56px 0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="middle">
          <p style="margin:0 0 2px 0; font-size:13px; font-weight:600; text-transform:uppercase; letter-spacing:1.5px; color:#0078d4;">S360 Weekly Report</p>
          <h1 style="margin:0 0 4px 0; font-size:28px; color:#1a1a1a; font-weight:700;">Android Auth Team</h1>
        </td>
        <td width="200" valign="middle" align="right">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="#0078d4" style="padding:8px 20px; color:#ffffff; font-size:12px; font-weight:600; border-radius:16px;">
              Week of ${shortDate}
            </td>
          </tr></table>
        </td>
      </tr>
    </table>
    <p style="margin:16px 0 0 0; font-size:13px;">
      Services: <strong>AuthN SDK - MSAL Android</strong> &bull; <strong>AuthN SDK - ADAL Android</strong> &bull; <strong>Microsoft Authenticator - Android</strong>
    </p>
  </td>
</tr>`;

// Summary cards
const cards = [
  { count: total, label: 'Total Items', bg: '', border: '#e1e4e8', accent: '#0078d4' },
  { count: outCount, label: 'Out of SLA', bg: '#fef2f2', border: '#fca5a5', accent: '#cf222e' },
  { count: nearCount, label: 'Approaching', bg: '#fffbeb', border: '#fcd34d', accent: '#e65100' },
  { count: inCount, label: 'In SLA', bg: '#f0fdf4', border: '#86efac', accent: '#2e7d32' },
  { count: noEta, label: 'No ETA', bg: '#fffbeb', border: '#fcd34d', accent: '#bf8700' }
];

html += `\n<tr><td style="padding:28px 56px 0 56px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>`;
cards.forEach(c => {
  const bgAttr = c.bg ? ` bgcolor="${c.bg}"` : '';
  html += `
      <td width="165" valign="top">
        <table cellpadding="0" cellspacing="0" border="0" width="165"${bgAttr} style="border:1px solid ${c.border}; border-top:3px solid ${c.accent}; border-radius:12px;">
        <tr><td style="padding:20px 16px; text-align:center; height:56px;" valign="middle">
          <p style="margin:0; font-size:34px; font-weight:800; color:${c.accent};">${c.count}</p>
          <p style="margin:6px 0 0 0; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;"><em>${c.label}</em></p>
        </td></tr>
        </table>
      </td>`;
});
html += `
    </tr>
  </table>
</td></tr>`;

// Severity bar
if (total > 0) {
  html += `
<tr>
  <td style="padding:20px 56px 0 56px;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="height:28px; border-radius:6px; overflow:hidden;">
      <tr>`;
  if (outCount > 0) html += `
        <td width="${outPct}%" bgcolor="#cf222e" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">${outCount}</td>`;
  if (nearCount > 0) html += `
        <td width="${nearPct}%" bgcolor="#e65100" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">${nearCount}</td>`;
  if (inCount > 0) html += `
        <td width="${inPct}%" bgcolor="#2e7d32" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">${inCount} In SLA</td>`;
  html += `
      </tr>
    </table>
  </td>
</tr>`;
}

// Divider helper
const divider = `
<tr><td style="padding:28px 56px 0 56px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td bgcolor="#e8e8e8" style="height:1px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
  </table>
</td></tr>`;

html += divider;

// Resolved Since Last Week callout
html += `
<tr>
  <td style="padding:28px 56px 0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="4" bgcolor="#2da44e" style="font-size:1px;">&nbsp;</td>
        <td bgcolor="#e8f5e9" style="padding:16px 20px;">
          <p style="margin:0 0 8px 0; font-size:14px; font-weight:700;">&#x2705; Resolved Since Last Week (${resolved.length} items)</p>
          <p style="margin:0; font-size:13px; line-height:1.6;">`;
if (resolved.length > 0) {
  resolved.forEach(r => {
    const displayName = nameMap[r.assignee] || r.assignee;
    const pbiLink = r.pbi ? ` &mdash; <a href="${pbiUrl(r.pbi)}" style="color:#0078d4;">AB#${r.pbi}</a>` : '';
    html += `<b>${esc(r.title)}</b>${pbiLink} &mdash; ${esc(displayName)} (${esc(r.assignee)}) &mdash; <b style="color:#2e7d32;">Done</b><br>`;
  });
} else {
  html += `<em>No resolved items were detected this week.</em>`;
}
html += `</p>
        </td>
      </tr>
    </table>
  </td>
</tr>`;

// Week-over-week diff: new items this week
if (newItems.length > 0) {
  html += `
<tr>
  <td style="padding:16px 56px 0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="4" bgcolor="#0078d4" style="font-size:1px;">&nbsp;</td>
        <td bgcolor="#ddf4ff" style="padding:16px 20px;">
          <p style="margin:0 0 8px 0; font-size:14px; font-weight:700;">&#x1F4E5; New This Week (${newItems.length} items)</p>
          <p style="margin:0; font-size:13px; line-height:1.6;">`;
  newItems.forEach(n => {
    html += `<b>${esc(n.title)}</b> &mdash; ${esc(n.service)}<br>`;
  });
  html += `</p>
        </td>
      </tr>
    </table>
  </td>
</tr>`;
}

// Needs Attention (Out of SLA cards)
const outOfSlaItems = items.filter(i => i.sla === 'OutOfSla');
if (outOfSlaItems.length > 0) {
  html += `
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h3 style="margin:0 0 4px 0; font-size:16px; color:#1a1a1a; font-weight:700;">Needs Attention</h3>
    <p style="margin:0 0 0 0; font-size:12px;"><em>Items past due or approaching their SLA deadline</em></p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 14px 0;">
      <tr><td bgcolor="#cf222e" style="height:3px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>`;

  outOfSlaItems.forEach(item => {
    const daysOverdue = daysBetween(item.due, reportDate);
    const etaDisplay = item.eta
      ? fmtDate(item.eta)
      : `<span style="color:#bf8700; font-weight:600;">\u26a0 No ETA</span>`;
    const newBadge = item.isNew ? '&#x1F195;' : '';
    const pbiChip = item.pbi
      ? `<a href="${pbiUrl(item.pbi)}" style="color:#0078d4; text-decoration:none; font-weight:600;">AB#${item.pbi}</a> ${newBadge}`
      : '<em>None</em>';

    html += `
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:2px solid #cf222e; border-radius:10px; overflow:hidden; margin-bottom:12px;">
      <tr>
        <td bgcolor="#fef2f2" style="padding:20px 24px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td valign="top" width="65%">
                <p style="margin:0 0 8px 0; font-size:16px; font-weight:700;">
                  <a href="${item.s360Url}" style="color:#1a1a1a; text-decoration:none;">${esc(item.title)}</a>
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
                  <tr>
                    <td bgcolor="#cf222e" style="padding:3px 10px; color:#fff; font-size:10px; font-weight:bold; border-radius:12px;">MISSED SLA</td>
                    <td style="padding:0 0 0 8px; font-size:12px;"><em>${esc(item.service)}</em></td>
                  </tr>
                </table>
                <p style="margin:0; font-size:13px;"><b>Due:</b> <b style="color:#cf222e;">${fmtDate(item.due)}</b> (${daysOverdue} days overdue)</p>
                <p style="margin:4px 0 0 0; font-size:13px;"><b>ETA:</b> ${etaDisplay}</p>
                <p style="margin:4px 0 0 0; font-size:13px;"><b>Owner:</b> ${esc(item.ownerName)} (${esc(item.ownerAlias)})</p>
              </td>
              <td valign="top" width="35%" align="right">
                <table cellpadding="0" cellspacing="0" border="0">
                  <tr><td style="padding:6px 14px; font-size:13px; border:1px solid #d0d7de; border-radius:16px; background:#fff;">
                    ${pbiChip}
                  </td></tr>
                </table>
                <p style="margin:10px 0 0 0; font-size:12px;"><em>${esc(item.program)}</em></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>`;
  });

  html += `
  </td>
</tr>`;
}

html += divider;

// Items by Compliance Area
sortedPrograms.forEach(([progName, prog]) => {
  const count = prog.items.length;
  html += `
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h3 style="margin:0 0 4px 0; font-size:16px; color:#1a1a1a; font-weight:700;">${esc(progName)}</h3>
    <p style="margin:0 0 0 0; font-size:12px;"><em>${esc(prog.desc)} &mdash; ${count} item(s)</em></p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 14px 0;">
      <tr><td bgcolor="#0078d4" style="height:3px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="28%">Title</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="12%">Service</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="14%">Owner</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="8%">SLA</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="9%">Due</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="10%">ETA</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="19%">PBI</td>
      </tr>`;

  prog.items.forEach((it, idx) => {
    const s = slaStyle(it.sla);
    const bg = idx % 2 === 0 ? s.rowBg : s.rowBgAlt;
    const etaCell = it.eta
      ? fmtDate(it.eta)
      : `<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#bf8700" style="padding:2px 8px; font-size:10px; font-weight:600; color:#fff; border-radius:4px;">No ETA</td></tr></table>`;
    const pbiCell = it.pbi
      ? (it.isNew
          ? `<a href="${pbiUrl(it.pbi)}" style="color:#0078d4; font-weight:600; text-decoration:none;">AB#${it.pbi}</a> &#x1F195;`
          : `<a href="${pbiUrl(it.pbi)}" style="color:#0078d4; font-weight:600; text-decoration:none;">AB#${it.pbi}</a>`)
      : '<em>None</em>';
    const dueBold = new Date(it.due) < reportDateObj
      ? `<b style="color:#cf222e;">${fmtDate(it.due)}</b>`
      : fmtDate(it.due);

    html += `
      <tr>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:13px; border:1px solid #e8e8e8; border-left:4px solid ${s.leftBorder};">
          <a href="${it.s360Url}" style="color:#24292f; font-weight:600; text-decoration:none;">${esc(it.shortTitle || it.title)}</a>${it.subtitle ? `<br><span style="font-size:11px;"><em>${esc(it.subtitle)}</em></span>` : ''}
        </td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">${esc(it.service)}</td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">${esc(it.ownerName)}<br><span style="font-size:11px;"><em>(${esc(it.ownerAlias)})</em></span></td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="${s.badgeBg}" style="padding:2px 8px; color:${s.badgeColor}; font-size:10px; font-weight:bold; border-radius:4px;">${s.label}</td></tr></table>
        </td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">${dueBold}</td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">${etaCell}</td>
        <td bgcolor="${bg}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">${pbiCell}</td>
      </tr>`;
  });

  html += `
    </table>
  </td>
</tr>`;
});

html += divider;

// Ownership Breakdown
html += `
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h2 style="margin:0 0 16px 0; font-size:16px; color:#1a1a1a; border-bottom:2px solid #0078d4; padding-bottom:8px;">&#128101; Ownership Breakdown</h2>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;">Assignee</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">Total</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128308;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128992;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128994;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">No ETA</td>
      </tr>`;

sortedOwners.forEach(([alias, o], idx) => {
  const zebra = idx % 2 === 0 ? '#ffffff' : '#fafafa';
  const outCellBg = o.out > 0 ? '#fef2f2' : zebra;
  const outStyle = o.out > 0 ? 'font-weight:700; color:#cf222e;' : '';
  const nearCellBg = o.near > 0 ? '#fffbeb' : zebra;
  const nearStyle = o.near > 0 ? 'font-weight:600;' : '';
  const etaStyle = o.noEta > 0 ? 'font-weight:600; color:#bf8700;' : '';

  html += `
      <tr>
        <td bgcolor="${zebra}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8;">${esc(o.name)} <span style="font-size:11px;"><em>(${esc(alias)})</em></span></td>
        <td bgcolor="${zebra}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center;"><b>${o.total}</b></td>
        <td bgcolor="${outCellBg}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; ${outStyle}">${o.out}</td>
        <td bgcolor="${nearCellBg}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; ${nearStyle}">${o.near}</td>
        <td bgcolor="${zebra}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center;">${o.inSla}</td>
        <td bgcolor="${zebra}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; ${etaStyle}">${o.noEta}</td>
      </tr>`;
});

html += `
    </table>
  </td>
</tr>`;

html += divider;

// Action Required callouts
const noEtaItems = items.filter(i => !i.eta);
const newPbiItems = items.filter(i => i.isNew);

html += `
<tr><td style="padding:28px 56px 0 56px;">
  <h3 style="margin:0 0 16px 0; font-size:16px; color:#1a1a1a; font-weight:700;">Action Required</h3>`;

// Missing ETA callout
if (noEtaItems.length > 0) {
  html += `
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">
    <tr>
      <td width="4" bgcolor="#bf8700" style="font-size:1px;">&nbsp;</td>
      <td bgcolor="#fffbeb" style="padding:16px 20px;">
        <p style="margin:0 0 6px 0; font-size:14px; font-weight:700;">${noEtaItems.length} Items Missing ETA</p>
        <p style="margin:0; font-size:13px;">${noEtaItems.map(i => `${esc(i.shortTitle || i.title)} (${esc(i.ownerAlias)})`).join(' &bull; ')}</p>
      </td>
    </tr>
  </table>`;
}

// New PBIs callout
if (newPbiItems.length > 0) {
  html += `
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">
    <tr>
      <td width="4" bgcolor="#0078d4" style="font-size:1px;">&nbsp;</td>
      <td bgcolor="#ddf4ff" style="padding:16px 20px;">
        <p style="margin:0 0 6px 0; font-size:14px; font-weight:700;">${newPbiItems.length} New PBIs Created</p>
        <p style="margin:0; font-size:13px;">${newPbiItems.filter(i => i.pbi).map(i => `<a href="${pbiUrl(i.pbi)}" style="color:#0078d4;">AB#${i.pbi}</a>`).join(' &bull; ')}</p>
      </td>
    </tr>
  </table>`;
}

html += `</td></tr>`;

// Footer
html += `
<tr>
  <td style="padding:32px 56px 40px 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="#e8e8e8" style="height:1px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
      <tr>
        <td valign="middle"><p style="margin:0; font-size:11px;"><em>Auto-generated by S360 Reporter &bull; ${shortDate}</em></p></td>
        <td valign="middle" align="right"><p style="margin:0; font-size:11px;">
          <a href="https://s360.msftcloudes.com" style="color:#0078d4; text-decoration:none;">S360 Dashboard</a> &bull;
          <a href="https://dev.azure.com/IdentityDivision/Engineering/_queries/query/" style="color:#0078d4; text-decoration:none;">ADO Board</a>
        </p></td>
      </tr>
    </table>
  </td>
</tr>`;

// Close wrapper
html += `
</table>
</td></tr>
</table>
</body>
</html>`;

// ── Output ────────────────────────────────────────────────────────────────────
if (outputPath) {
  fs.writeFileSync(outputPath, html, 'utf8');
  console.log('Report saved to ' + outputPath);
  console.log('Size: ' + (Buffer.byteLength(html, 'utf8') / 1024).toFixed(1) + ' KB');
} else {
  process.stdout.write(html);
}
