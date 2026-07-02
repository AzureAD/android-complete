<#
.SYNOPSIS
    Validate a filled release-monitoring report before it ships.

.DESCRIPTION
    A LEAN structural/QA check (the oncall validator is heavier and tuned to its attribution
    cards). Checks that catch the mistakes that actually happen when an agent edits the
    template in place:

      ERRORS (exit 1):
        - Leftover template tokens: '{{', 'EXAMPLE CONTENT', literal '<FIRST>' / '<SECOND>'
          / '<TOKEN>' / '<bv>' / '<av>' placeholders OUTSIDE the leading HTML comment.
        - U+FFFD replacement char (mojibake from a bad heredoc round-trip).
        - Raw device/request counts leaking into user-facing prose
          ("585300000 devices" style) — should be humanized (585.3M).
        - A version under test was supplied but its string never appears in the body.
        - An app section was supposed to be present but its <section> anchor is missing.
        - A leaked App Center secret: an 'X-API-Token' header, 'APPCENTER_API_TOKEN', or a
          reference to the 'appcenter.token' file must never appear in a shipped report.

      WARNINGS (exit 0, but printed):
        - KPI tiles with no data-spark / data-trend attribute (soft — release KPIs are
          2-point compares, not series, so this is informational only).
        - 'Generated <strong>...</strong>' still at the template's placeholder date.
        - Fewer than 2 verdict callouts (.callout) — a release report should state a verdict.
        - AuthVersion given but no #auth-stability section (crash layer skipped — fine if no
          App Center token was available).
        - AuthVersion given but no #auth-broker section (broker-via-Authenticator health skipped).
        - A 40-hex-char string that could be an App Center token (verify it is not a secret).

.PARAMETER Path
    Path to the report HTML to validate. Required.

.PARAMETER BrokerVersion
    If provided, asserts the string appears in the body and the broker section is present.

.PARAMETER AuthVersion
    If provided, asserts the string appears in the body and the authenticator section is present.

.EXAMPLE
    .\validate-report.ps1 -Path ~/android-release-reports/release-report-broker-16.1.0-auth-6.2606.3817-2026-01-15.html `
        -BrokerVersion 16.1.0 -AuthVersion 6.2606.3817
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Path,
  [string]$BrokerVersion,
  [string]$AuthVersion
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Path)) { throw "Report not found: $Path" }

$text  = [IO.File]::ReadAllText($Path)
$errors = New-Object System.Collections.Generic.List[string]
$warns  = New-Object System.Collections.Generic.List[string]

# Strip the leading template HTML comment so its <bv>/<av>/<date> mentions don't false-positive.
$body = [regex]::Replace($text, '(?s)^.*?-->', '', 1)

# --- ERRORS ---
foreach ($tok in @('{{', 'EXAMPLE CONTENT', '<FIRST>', '<SECOND>', '<TOKEN>', '<bv>', '<av>', '<date>')) {
  if ($body.Contains($tok)) { $errors.Add("Leftover template token '$tok' in body.") }
}
if ($body.Contains([char]0xFFFD)) { $errors.Add("U+FFFD replacement char present (mojibake) — re-write file as UTF-8 no BOM.") }

# Raw long counts in prose: a run of >=7 digits immediately followed by ' device' / ' request' / ' req'
$rawCount = [regex]::Matches($body, '(?<![\d.])\d{7,}(?=\s*(?:devices?|requests?|reqs?)\b)', 'IgnoreCase')
if ($rawCount.Count -gt 0) {
  $sample = ($rawCount | Select-Object -First 3 | ForEach-Object { $_.Value }) -join ', '
  $errors.Add("Raw un-humanized count(s) in prose ($sample ...). Use M/K (e.g. 585.3M).")
}

if ($BrokerVersion) {
  if ($body -notmatch [regex]::Escape($BrokerVersion)) { $errors.Add("BrokerVersion '$BrokerVersion' never appears in body.") }
  if ($body -notmatch '(?i)id="broker|class="[^"]*broker') { $warns.Add("No broker section anchor (id/class 'broker') found.") }
}
if ($AuthVersion) {
  if ($body -notmatch [regex]::Escape($AuthVersion)) { $errors.Add("AuthVersion '$AuthVersion' never appears in body.") }
  if ($body -notmatch '(?i)id="auth|class="[^"]*auth') { $warns.Add("No authenticator section anchor (id/class 'auth') found.") }
  if ($body -notmatch 'id="auth-stability"') { $warns.Add("No #auth-stability section — Authenticator crash/stability layer not included (OK only if no App Center token was available).") }
  if ($body -notmatch 'id="auth-broker"') { $warns.Add("No #auth-broker section — broker-via-Authenticator (active-broker) health not included; add it to attribute broker movement to this app rollout.") }
}

# Leaked App Center secret — must never ship inside a report.
if ($body -match '(?i)X-API-Token')        { $errors.Add("'X-API-Token' header text present — an App Center secret may have leaked into the report.") }
if ($body -match '(?i)APPCENTER_API_TOKEN'){ $errors.Add("'APPCENTER_API_TOKEN' present — remove any token/secret references from the report.") }
if ($body -match '(?i)appcenter\.token')   { $errors.Add("Reference to the secret token file 'appcenter.token' present — remove it from the report.") }

# --- WARNINGS ---
$kpiCount   = ([regex]::Matches($body, 'class="kpi"')).Count
$sparkCount = ([regex]::Matches($body, 'data-spark|data-trend')).Count
if ($kpiCount -gt 0 -and $sparkCount -eq 0) {
  $warns.Add("$kpiCount KPI tile(s) but no data-spark/data-trend attributes (soft — OK for 2-point release compares).")
}
if ($body -match 'Generated\s+<strong>\s*2026-01-01\s*</strong>') {
  $warns.Add("Generated date still at the template placeholder (2026-01-01) — bootstrap should have stamped it.")
}
$callouts = ([regex]::Matches($body, 'class="callout')).Count
if ($callouts -lt 2) { $warns.Add("Only $callouts verdict callout(s) — a release report should state a clear verdict per app.") }

# Possible leaked token: a standalone 40-hex-char run (App Center tokens are 40 chars). Advisory —
# could also be a git SHA, so warn rather than fail.
$hex40 = [regex]::Matches($body, '(?<![A-Fa-f0-9])[A-Fa-f0-9]{40}(?![A-Fa-f0-9])')
if ($hex40.Count -gt 0) { $warns.Add("$($hex40.Count) 40-hex-char string(s) present — verify none is an App Center API token (secret).") }

# --- report ---
"Validating: $Path"
"  KPI tiles: $kpiCount   callouts: $callouts   spark/trend attrs: $sparkCount"
if ($warns.Count) { ""; "WARNINGS:"; $warns | ForEach-Object { "  ! $_" } }
if ($errors.Count) {
  ""; "ERRORS:"; $errors | ForEach-Object { "  X $_" }
  ""; "FAILED with $($errors.Count) error(s)."
  exit 1
}
""; "PASSED" + $(if ($warns.Count) { " with $($warns.Count) warning(s)." } else { "." })
exit 0
