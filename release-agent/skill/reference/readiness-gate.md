# Required readiness and point-of-use capabilities

The checklist distinguishes required release policy from integration capabilities.
Use `checklist --release <id> --attest-prompt --json` for the exact required checks
and outstanding human confirmations. Follow `pending_auto_checks` in the default
guided release flow before prompting. Do not run onboarding or every capability probe
for status, local report inspection, or recording a fact.

Required checks include the CCD, current on-call eligibility, and explicit human
readiness attestations. Never infer physical access, availability, portal review,
quality review, or approval. `sign --item <id> --note "<owner confirmation>"`
records only the named human facts; it does not probe integrations or advance phases.
Use `verify` explicitly when automatic verification is requested.

Items with `required: false` are labeled "when used": build access, registered MCPs,
M365 capability diagnostics, silent-run permissions, Teams notifications, and ADX.
Their failure is visible but does not close unrelated interactive work. The owning
operation still verifies its actual targets, evidence, and access before using them.
Do not turn this distinction into an evidence-free capability pass.

The returned prompt contains `confirm_items` and bounded `answers`. Record only
what the owner explicitly confirms. Always display `display_markdown` before an
actual readiness question/wait. `ready:false` can mean executable automatic checks
remain; exhaust them first. An unchanged failure requires input/repair, not a loop.
If a failed check needs owner repair, show the actual cause and a supported next
action. Do not repeatedly retry unchanged `failed_auto_checks`.

No blanket write authority follows from readiness. Every owning write step needs
its own exact preview, required review, host consent, durable attempt, and receipt.
Do not send test messages, create probe invitations, upload test files, broaden
permissions, or switch accounts to manufacture a readiness pass.

## Structured connector reads

Discover actual tools and use the supported canonical-to-exposed mapping.
Directory/roster and exact resource reads use structured WorkIQ fetch, through an
exposed MCP tool or the existing selected-profile native CLI:

```powershell
python -m orchestrator.cli workiq-read --scout-root <profile> --account <owner> --entity-url '<exact Graph-relative URL>'
```

The adapter verifies the launcher/wrapper and keeps arguments literal. Reuse its
fresh discovery descriptor with `--route-file <temporary-route.json>` for subsequent
pages; a changed launcher or stale observation requires rediscovery.

The agent, not the owner, constructs temporary observation envelopes:
`{version:1,tool,arguments,account,observed_at,result}`. Bind the exact requested
account/resource, complete every actual next page, and preserve provider errors.
Tool discovery/help is not access proof. WorkIQ Ask/people search is not roster or
identity evidence. Never acquire raw Graph tokens or use another transport after denial.
Keep private responses outside the repository and remove temporary evidence when done.

Full `m365-capabilities`/onboarding diagnostics remain available when explicitly
requested. Their complete profile/calendar/roster/Teams/report checks are not a
precondition for every release operation. Future meeting/chat destinations and actual
sends/uploads are checked again by their consuming operations.

## Optional unattended mode

Unattended startup additionally needs its canonical driver's creation/adoption
receipt and actual provider health. This is not a new human readiness attestation.
Missing or uncertain startup is not success; honor host consent and the owning
worker result. Interactive is the default and has no mandatory driver.
