# External References

Everything the Release Orchestrator depends on that lives **outside this codebase**.
If any of these change (URL moved, DL renamed, pipeline re-IDed, template edited,
access revoked), the orchestrator can silently break — so they're catalogued here.
Review this list when something stops working or when onboarding a new release owner.

Legend for **Access**: `anon` = no auth · `az` = Azure CLI signed-in user ·
`AAD-SSO` = browser Microsoft sign-in · `MCP` = via an MCP server · `Google` = Google account (not automatable in Scout).

## Azure DevOps orgs & projects (we work across TWO)

| Project | Org | Host aliases | How we read it |
|---|---|---|---|
| **Engineering** | identitydivision | `identitydivision.visualstudio.com` = `dev.azure.com/identitydivision` | ADO **MCP** (bound here) or `az` |
| **One** | msazure | `msazure.visualstudio.com` = `dev.azure.com/msazure` | **`az` only** — the ADO MCP can't reach it |

> The ADO **MCP is bound to identitydivision/Engineering**. Calling it for project **One** fails with `TF200016: project does not exist`. Reads against **msazure/One** must use the **`az` CLI** (verified working as the signed-in user, no Conditional-Access 401): `az pipelines build show`, and `az devops invoke --area build --resource timeline|logs`. Engineering resources: pipeline 3038, build def 2828. One resources: pipeline 405133 (localization), build def 397224, CG repo 104410, the Auth App git repo.

## Systems of record (read/write)

| Ref | What | Used by | Access | Notes |
|---|---|---|---|---|
| ADO pipeline **3038** | "Code Complete Calendar Checker" — CCD source of record | CCD seed, `set-ccd`, `skip-release`, Phase-0 `cron` (verify scheduled) | az | org identitydivision / project Engineering. Real writes gated by --confirm. `cron` step verifies a recent `schedule`-reason run. |
| ADO pipeline **405133** | Localization build (org msazure / project One) | Phase-1 `localization` | MCP (ADO) / az | triggered at noon on CCD with `isCreatePrSelected=true`; polled every 10 min (3h timeout → email engineer); its **OneLocBuild@3** task logs `Pull request created with ID '<n>'` — that PR (`…/pullrequest/<n>`) is posted to the Code reviews chat for review |
| ADO build def **2828** | Auth Client Android build (org identitydivision / project Engineering) | readiness `build_access` | az | access check only |
| ADO build def **397224** | Android Build Release (org msazure / project One) | readiness `build_access` | az | access check only |
| ADO wiki **IdentityWiki.wiki** page **59148** | "Monthly Releases Payloads History" (parent) | Phase-0 `wiki` agent | az (`az devops wiki`) | child page `<Month> <Year> Release`; dup-safe numbering |
| **ICM team 78848** | "Auth Client Android Shield" on-call roster | readiness `oncall_now` | MCP (ICM) | primary = index 0 of currentOnCallContacts |
| **ADX cluster** idsharedeus2.eastus2 / db d496be22d62a46b0a3cf67ea2e736fd8 | release telemetry | readiness `adx_access` | MCP (Kusto) | `print 1` access probe |

## External web pages (scraped / linked)

| Ref | URL | Used by | Access | Notes |
|---|---|---|---|---|
| CCOA No-Fly Zones | https://prod.change-manager.msidentity.com/ccoa-periods | Phase-0 `lockdown` | AAD-SSO | scraped via browser; only Production-env periods block |
| **Component Governance alerts** (governed repo **104410** = AD-MFA-phonefactor-phoneApp-android, branch `working`) | https://msazure.governance.visualstudio.com/{One projId}/_apis/ComponentGovernance/GovernedRepositories/104410/Branches/working/Alerts | Phase-0 `cg` | az (`az rest`) | read-only report; active alerts by severity. projId=b32aa71e-…, resource=499b84ac-… |
| **`release` variable group 40** | https://identitydivision.visualstudio.com/Engineering/_library?...variableGroupId=40&path=release | Phase-0 `flight_reminder` (link only) | az/web | feature owners update local flights here — release engineer does NOT |
| Flight pre-mortem example doc | https://microsoft-my.sharepoint-df.com/:w:/p/rapong/cQpEZp0cXp1sQYo4A4M3PQWCEgUCDj364FJa-rq-msg59WlBsw | Phase-0 `flight_reminder` (link only) | AAD-SSO | example shared with feature owners |
| Localization instructions | https://eng.ms/docs/.../combined-release-checklist/localization | Phase-0 `flight_reminder` (link) · Phase-1 `localization` (manual-steps fallback + timeout email) | AAD-SSO | confirmed valid 2026-07-29 |
| **Teams chat: "Android Core Team"** | thread `19:976a859f167f44e59c4ceca8b1d23581@thread.v2` | Phase-0 `flight_reminder` target | MCP (WorkIQ) | real target; redirect for tests via the `send_to` mock knob |
| **Teams chat: "Code reviews"** | thread `19:meeting_Y2Y3OGRjZGMtZGVkYi00MTkzLThhZjktNDAxYWVkMjZlMmE3@thread.v2` | Phase-1 `pr_reminder` target | MCP (WorkIQ) | CCD PR-merge reminder; redirect for tests via the `send_to` mock knob |
| **CCD-delay / cherry-pick approver: Moumita Ghosh** | moghosh@microsoft.com | Phase-1 `pr_reminder` (named in message) | — | both a CCD delay and a post-CCD cherry-pick require her approval |
| **EcsFlight.kt** (Auth App ECS flights) | https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android?path=/.../ecs/entities/EcsFlight.kt&version=GBworking | Phase-0 `flight_reminder` bullet 4 (link only) | az/web | reviewers check its history since last code complete |
| Early code-complete notice template | https://eng.ms/docs/.../combined-release-checklist/early-code-complete-notice-email-template | Phase-0 `notice` | AAD-SSO | copied locally to `templates/early-code-complete-notice.md` — **re-sync if upstream edits** |
| Hotfix cherry-pick guide | https://eng.ms/docs/.../release/cherry-pick-to-hotfix-guidelines | link inside notice email body | AAD-SSO | referenced, not fetched |
| common-for-android changelog | https://raw.githubusercontent.com/AzureAD/microsoft-authentication-library-common-for-android/dev/changelog.txt | Phase-0 `breaking` | anon | breaking = `[MAJOR]` in `vNext` |
| Play Console vitals | (Google Play Console) | Phase-0 `vitals` (#8) | Google | **NOT automatable in Scout** (Google auth wall) |

## Outbound email recipients

> Runs are real — these recipients receive the notice. To test without emailing
> the DL, redirect with the `send_to` mock knob (`mocks.local.yaml`).

| Step | To | Notes |
|---|---|---|
| Phase-0 `notice` (early code-complete) | androididentity@microsoft.com ("Azure Identity Android SDK"), jialh@microsoft.com | provided by release owner 2026-07-29 |
| Phase-1 `final_reminder` (CCD-day code-complete) | androididentity@microsoft.com ("Azure Identity Android SDK"), jialh@microsoft.com | same DL as `notice`; CCD-day "update" variant |

## Tooling / infra (provisioned by bootstrap)

| Ref | What | Notes |
|---|---|---|
| Agency CLI | provides the **ICM** and **Kusto** MCP servers | `agency mcp icm` / `agency mcp kusto`; auto-registered into `~/.scout/m-mcp-servers.json` by `cli infra` |
| Azure CLI (`az`) + `azure-devops` extension | pipeline + wiki reads/writes | signed-in user is the release owner |
| Scout | host for the skill + automations | `~/.scout`; bootstrap checks presence |
