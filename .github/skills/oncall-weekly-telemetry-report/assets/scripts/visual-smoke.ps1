<#
.SYNOPSIS
    Visual / layout smoke test for the OCE weekly report. Optional sibling of
    validate-report.ps1 — catches rendered-layout bugs that pure HTML/CSS
    validation can't see.

.DESCRIPTION
    Uses Playwright (headless Chromium) to:
      1. Open the report at 1400 px viewport width (the target media size).
      2. Wait for the footer JS to render all sparklines.
      3. Run two DOM-based layout checks:
         a. Element overflow — for every .dim and .attr-card, check that no
            descendant element's bounding box extends beyond the container's
            client width. Catches the "long calling-app name bleeds out of
            the dim card" regression.
         b. Card adjacency — check that consecutive .attr-card siblings have
            at least 8 px of vertical gap. Catches the "cards touching" regression.
      4. Capture a full-page screenshot to ~/android-oce-reports/_visual/
         for manual review.

    Installation note: requires Node.js + Playwright. The script auto-installs
    Playwright + Chromium on first run via `npm install --no-save`.

.PARAMETER Path
    Absolute path to the report HTML. Defaults to the most recent
    oncall-wow-report-*.html under ~/android-oce-reports/.

.PARAMETER ScreenshotOnly
    Skip the layout checks; just capture the screenshot.

.EXAMPLE
    .\visual-smoke.ps1
    # checks the latest report + writes ~/android-oce-reports/_visual/oncall-wow-report-<sunday>.png

.EXAMPLE
    .\visual-smoke.ps1 -Path C:\path\to\report.html

.NOTES
    Treat warnings as advisory. The script returns 0 on success, 1 on hard
    layout violations (overflow > 4 px, adjacent cards with gap < 8 px).
#>
[CmdletBinding()]
param(
  [string]$Path,
  [switch]$ScreenshotOnly
)
$ErrorActionPreference = 'Stop'

if (-not $Path) {
  $reportDir = Join-Path $env:USERPROFILE 'android-oce-reports'
  $latest = Get-ChildItem $reportDir -Filter 'oncall-wow-report-*.html' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $latest) { throw "No report found in $reportDir. Pass -Path explicitly." }
  $Path = $latest.FullName
}
if (-not (Test-Path $Path)) { throw "Report not found: $Path" }

$screenshotDir = Join-Path $env:USERPROFILE 'android-oce-reports\_visual'
New-Item -ItemType Directory -Force $screenshotDir | Out-Null
$reportBase = [IO.Path]::GetFileNameWithoutExtension($Path)
$screenshot = Join-Path $screenshotDir "$reportBase.png"

# Locate or install Playwright in a per-skill node_modules cache
$cacheDir = Join-Path $env:LOCALAPPDATA 'oce-skill-playwright'
New-Item -ItemType Directory -Force $cacheDir | Out-Null
if (-not (Test-Path (Join-Path $cacheDir 'node_modules\playwright'))) {
  Write-Host "Installing Playwright + Chromium (one-time, into $cacheDir)..."
  Push-Location $cacheDir
  try {
    if (-not (Test-Path 'package.json')) { '{"name":"oce-visual-smoke","version":"0.0.0","private":true}' | Set-Content 'package.json' }
    npm install --no-save playwright | Out-Null
    npx playwright install chromium | Out-Null
  } finally { Pop-Location }
}

# Build the JS test inline so the .ps1 is self-contained
$jsScript = @'
const { chromium } = require(require('path').join(process.env.OCE_PWCACHE, 'node_modules', 'playwright'));
const fs = require('fs');
(async () => {
  const file = process.argv[2];
  const screenshotPath = process.argv[3];
  const screenshotOnly = process.argv[4] === 'true';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto('file://' + file);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(500); // give sparkline JS a beat
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log('SCREENSHOT ' + screenshotPath);

  if (screenshotOnly) { await browser.close(); return; }

  const issues = await page.evaluate(() => {
    const out = { overflow: [], adjacent: [] };
    // 1. Overflow check: every .dim / .attr-card must contain its descendants
    for (const sel of ['.dim', '.attr-card']) {
      document.querySelectorAll(sel).forEach((el, idx) => {
        const elRect = el.getBoundingClientRect();
        el.querySelectorAll('*').forEach(child => {
          const r = child.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) return;
          // Allow 4 px tolerance for sub-pixel rendering
          const overflowRight = r.right - elRect.right;
          if (overflowRight > 4) {
            // Identify offending element
            const ident = (child.tagName + (child.className ? '.' + String(child.className).split(' ').join('.') : '')).slice(0, 80);
            const txt = (child.textContent || '').trim().slice(0, 60);
            out.overflow.push({ sel, idx, overflowRight: Math.round(overflowRight), tag: ident, text: txt });
          }
        });
      });
    }
    // 2. Adjacent .attr-card check
    const cards = Array.from(document.querySelectorAll('.attr-card'));
    for (let i = 1; i < cards.length; i++) {
      const prevR = cards[i - 1].getBoundingClientRect();
      const currR = cards[i].getBoundingClientRect();
      const gap = currR.top - prevR.bottom;
      if (gap < 8) {
        out.adjacent.push({ prevIdx: i - 1, currIdx: i, gap: Math.round(gap) });
      }
    }
    return out;
  });

  console.log('ISSUES ' + JSON.stringify(issues));
  await browser.close();
})();
'@

$jsFile = Join-Path $env:TEMP 'oce-visual-smoke.js'
$jsScript | Set-Content $jsFile -Encoding utf8

$env:OCE_PWCACHE = $cacheDir
$absPath = (Resolve-Path $Path).Path.Replace('\', '/')
$absShot = (Resolve-Path $screenshotDir).Path.Replace('\', '/') + '/' + [IO.Path]::GetFileName($screenshot)

$result = node $jsFile $absPath $absShot $ScreenshotOnly.IsPresent.ToString().ToLower() 2>&1
$result | ForEach-Object { Write-Host $_ }
Remove-Item $jsFile -Force -ErrorAction SilentlyContinue

if ($ScreenshotOnly) { Write-Host "Screenshot saved: $screenshot"; exit 0 }

$issuesLine = $result | Where-Object { $_ -match '^ISSUES ' }
$issues = ($issuesLine -replace '^ISSUES ', '') | ConvertFrom-Json
$overflowCount = if ($issues.overflow) { @($issues.overflow).Count } else { 0 }
$adjCount      = if ($issues.adjacent) { @($issues.adjacent).Count } else { 0 }

Write-Host ""
Write-Host "Visual smoke summary:"
Write-Host "  Screenshot:        $screenshot"
Write-Host "  Overflow issues:   $overflowCount"
Write-Host "  Adjacent gaps <8px: $adjCount"

if ($overflowCount -gt 0) {
  Write-Host ""
  Write-Host "Overflow details (showing first 10):" -ForegroundColor Yellow
  $issues.overflow | Select-Object -First 10 | ForEach-Object {
    Write-Host ("  [{0} #{1}] +{2}px overflow: <{3}> text='{4}'" -f $_.sel, $_.idx, $_.overflowRight, $_.tag, $_.text) -ForegroundColor Yellow
  }
}
if ($adjCount -gt 0) {
  Write-Host ""
  Write-Host "Adjacent cards with insufficient gap (showing first 5):" -ForegroundColor Yellow
  $issues.adjacent | Select-Object -First 5 | ForEach-Object {
    Write-Host ("  cards #{0} -> #{1}: gap={2}px (need >=8)" -f $_.prevIdx, $_.currIdx, $_.gap) -ForegroundColor Yellow
  }
}

if ($overflowCount -gt 0 -or $adjCount -gt 0) {
  Write-Host ""
  Write-Host "Hard layout issues detected. Open $screenshot to inspect." -ForegroundColor Red
  exit 1
}
Write-Host "No hard layout issues." -ForegroundColor Green
exit 0
