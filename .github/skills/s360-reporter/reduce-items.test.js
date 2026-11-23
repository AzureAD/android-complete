// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

'use strict';

const assert = require('node:assert/strict');
const { mkdtempSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const SDL_ANNUAL_ASSESSMENT_KPI = '2d6597da-8e08-4495-a4e1-954f7697a4a8';

function reduce(items) {
  const directory = mkdtempSync(path.join(tmpdir(), 's360-reducer-'));
  const input = path.join(directory, 'input.json');
  const output = path.join(directory, 'output.json');

  try {
    writeFileSync(input, JSON.stringify(items));
    const result = spawnSync(
      process.execPath,
      [path.join(__dirname, 'reduce-items.js'), '--input', input, '--output', output],
      { encoding: 'utf8' }
    );
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(readFileSync(output, 'utf8'));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

function createSdlItem(overrides) {
  return {
    KpiId: SDL_ANNUAL_ASSESSMENT_KPI,
    Title: 'SDL Annual Assessment',
    TargetType: 'Service',
    TargetId: '8d0d308e-cd5c-44a3-9518-43eeeb424b57',
    AssignedTo: 'owner',
    SLAState: 'InSla',
    ...overrides
  };
}

test('keeps separate SDL activities that share a generic title and target', () => {
  const rows = reduce([
    createSdlItem({
      KpiActionItemId: 'threat-model-review',
      CurrentDueDate: '2026-10-16',
      ActionItem: 'Complete the SDL threat model review.'
    }),
    createSdlItem({
      KpiActionItemId: 'annual-assessment',
      CurrentDueDate: '2027-01-05',
      ActionItem: 'Onboard to 1CS and complete your SDL assessment.'
    })
  ]);

  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map(row => row.KpiActionItemId).sort(),
    ['annual-assessment', 'threat-model-review']
  );
  assert.ok(rows.every(row => row.usesGenericS360Title));
  assert.ok(rows.every(row => row.genericTitleSource === 'ActionItem'));
});

test('preserves each SDL activity source metadata', () => {
  const rows = reduce([
    createSdlItem({
      KpiActionItemId: 'annual-assessment',
      CurrentDueDate: '2027-01-05',
      ActionItem: 'Onboard to 1CS and complete your SDL assessment.',
      ActionItemSubtype: 'SDL.ManualActivities.Core',
      LiquidCopilot: '<a href="https://liquid.microsoft.com/Web/Compliance/Run/?product=PRD-156453420&amp;collection=MS.Security">Why am I getting this action?</a>',
      ReferenceLink: '<a href="https://aka.ms/sdlfaqmanual">How do I get help?</a>'
    })
  ]);

  assert.equal(rows[0].ActionItem, 'Onboard to 1CS and complete your SDL assessment.');
  assert.equal(rows[0].ActionItemSubtype, 'SDL.ManualActivities.Core');
  assert.equal(
    rows[0].ActionUrl,
    'https://liquid.microsoft.com/Web/Compliance/Run/?product=PRD-156453420&collection=MS.Security'
  );
  assert.equal(rows[0].ReferenceUrl, 'https://aka.ms/sdlfaqmanual');
});

test('rejects unsafe source URL schemes', () => {
  const rows = reduce([
    createSdlItem({
      KpiActionItemId: 'annual-assessment',
      CurrentDueDate: '2027-01-05',
      LiquidCopilot: '<a href="javascript:alert(1)">Open action</a>'
    })
  ]);

  assert.equal(rows[0].ActionUrl, null);
});

test('decodes numeric HTML entities in source URLs', () => {
  const rows = reduce([
    createSdlItem({
      KpiActionItemId: 'annual-assessment',
      CurrentDueDate: '2027-01-05',
      LiquidCopilot: '<a href="https://example.test/run?a=1&#38;b=2&#x26;c=3">Open action</a>'
    })
  ]);

  assert.equal(rows[0].ActionUrl, 'https://example.test/run?a=1&b=2&c=3');
});

test('uses ADO titles when distinct work items share the same action text', () => {
  const rows = reduce([
    createSdlItem({
      KpiActionItemId: 'finding-one',
      CurrentDueDate: '2027-01-05',
      ActionItem: 'Complete the SDL assessment.',
      URL: 'https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/1001'
    }),
    createSdlItem({
      KpiActionItemId: 'finding-two',
      CurrentDueDate: '2027-01-06',
      ActionItem: 'Complete the SDL assessment.',
      URL: 'https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/1002'
    })
  ]);

  assert.ok(rows.every(row => row.usesGenericS360Title));
  assert.ok(rows.every(row => row.genericTitleSource === 'AdoTitle'));
});
