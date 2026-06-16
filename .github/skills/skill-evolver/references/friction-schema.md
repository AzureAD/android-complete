# Friction Event Schema

One JSON object per line in `~/.skill-evolution/journal.jsonl`. Written only via
`journal-utils.js record` (single writer). Fields auto-filled by the store are marked *(auto)*.

## Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | *(auto)* `fr-<base36ts>-<rand>` |
| `ts` | number | *(auto)* epoch ms |
| `iso` | string | *(auto)* ISO-8601 timestamp |
| `skill` | string | Owning skill; defaults to the active-skill marker, else `"unknown"` |
| `tool` | string or null | Tool involved (e.g. `powershell`, `ado-wit_create_work_item`) |
| `eventType` | enum | See catalog below; invalid values coerced to `note` |
| `severity` | enum | `low`, `medium`, or `high` (default `medium`) |
| `expected` | string | What should have happened |
| `actual` | string | What actually happened |
| `detail` | string | Error text / context snippet (truncated ~1200 chars) |
| `turnsCost` | number | Approx. extra turns the friction cost (default 0) |
| `fixHint` | string | Optional concrete suggestion for the fix |
| `source` | enum | `hook`, `agent`, `cli`, or `user` |
| `sessionId` | string or null | Optional session correlation id |

## eventType catalog

| Type | Use when | Typical fix target |
|------|----------|--------------------|
| `tool_error` | A tool/command failed or returned an error | Skill step, script, or environment |
| `retry` | The same operation needed repeated attempts | Skill step clarity / determinism |
| `user_correction` | The user redirected the approach | Skill instructions / defaults |
| `dead_end` | An approach was pursued then abandoned | Skill decision guidance |
| `missing_context` | Needed info the skill should have supplied | Skill body / references |
| `ambiguity` | A clarifying question was required that a better instruction would prevent | Skill instructions |
| `trigger_miss` | The skill failed to activate (or the wrong skill fired) | Skill `description` frontmatter |
| `skill_step_mismatch` | A documented step contradicted reality (wrong path/API/command) | Skill step / references |
| `note` | Free-form observation that doesn't fit above | Triage during retrospective |

## Severity guidance

- `high` — blocked progress, caused a wrong result, or wasted many turns.
- `medium` — slowed things down, required a workaround.
- `low` — minor friction, cosmetic, or easily self-corrected.
