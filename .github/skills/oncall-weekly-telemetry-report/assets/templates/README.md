# `assets/templates/` — copy-paste HTML snippets

These are raw HTML fragments designed to be copied verbatim into the working
report file and then have `{{TOKENS}}` replaced. The CSS classes they reference
are defined in [`../report-template.html`](../report-template.html) — do not
restyle per week.

| File | When to use |
|---|---|
| [`spike-card.html`](spike-card.html) | One per regressing `error_code` or `error_type`. The 7 dim blocks + 8th-for-types and the Code Attribution block are MANDATORY (per SKILL.md). |
| [`traffic-attr-card.html`](traffic-attr-card.html) | One per error whose spike is traffic-driven (per-app volume up, per-request failure rate flat). |
| [`sparkline-footer.html`](sparkline-footer.html) | Paste once, immediately before `</body>`. Uses string concatenation (no JS template literals) so it survives PowerShell here-string composition. |

Final-pass sanity check before saving the report:

```pwsh
Select-String -Path <report-file> -Pattern '\{\{|EXAMPLE CONTENT BELOW'
# expected: 0 matches
```
