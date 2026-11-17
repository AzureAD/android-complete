<!-- Copyright (c) Microsoft Corporation. All rights reserved. Licensed under the MIT License. -->

# Requirements Builder

A team-shared Copilot **canvas extension**, not a standalone website or Android
app. It turns the [version-3 requirements template](../../../docs/Templates/requirements-template.md)
into five guided steps: Outcome, Scope, Journeys, Constraints and Review.
No dependency installation is needed; Copilot supplies the extension SDK.

## Open and resume

Reload extensions after changing these files. Ask Copilot:

> Open the Requirements Builder for draft `backup-restore-requirements`.

The canvas ID is `RequirementsBuilder`. Its open input is
`{"documentId":"backup-restore-requirements"}`. IDs contain 1-64 letters, digits,
underscores or hyphens and begin with a letter or digit. Opening the **same ID
in the same Copilot session** resumes its saved draft, including unreviewed
proposals. A panel's `instanceId` is not its document identity.

Drafts and exports are stored in the session workspace's
`files/requirements-builder/`, never in this public repository. Autosave runs
after a short pause; its status is visible. Keep the session to retain drafts.
There is no cross-session draft library, attachment uploader or link-fetching
feature in this initial version. Supply notes or authorized reference locations;
a supplied URL never counts as inspected evidence. The rough description is AI
context, not automatically a confirmed requirement.

## Human input and AI assistance

Start with a feature name and a rough paragraph. Each question has guidance and
an expandable illustrative example. Table rows expand into labelled fields, so
the human need not type Markdown. Choose Unknown for unresolved behavior, or
N/A with a reason. Selecting a non-UI feature preserves visual sections as N/A
and retains caller scenarios. Authenticator accessibility standards remain
mandatory; feature-specific criteria and planned, Unverified evidence are
captured separately.

**Ask AI to help draft this answer** submits a request to the current Copilot
conversation using the supported SDK `session.send` API. It may consume credits
and may wait behind active conversation work. It is not a background model,
costly dynamic workflow or external AI service. The agent answers the pending
request through the canvas's `propose` action. Suggested text or table rows are
displayed separately: only the human's **Accept as my reviewed answer** action
adds a proposal to the requirements. Reject is available. Editing while AI is
pending invalidates its request; late replies cannot overwrite edits. Cancel a
pending request if the conversation is interrupted. Edits also mark existing
proposals stale; they remain visible but cannot be accepted until regenerated.

The initial UX provides per-question help, not automatic full-document
generation. AI assistance is instructed not to invent behavior, success targets,
inspected sources, external guarantees or runtime passes. Human review is still
necessary; the extension does not prove semantic correctness or completeness.

## Review and export

The preview uses the actual checked-in template, retaining every heading,
table column, baseline instruction and flow checklist. Blank cells become
Unknown. Mandatory accessibility areas cannot be deleted; their detailed
baseline criteria remain alongside feature-specific answers in the export.
Export is a **Draft**, not approval. Unreviewed proposals and pending
assistance block export; unresolved requirements do not block draft export.
Download produces Markdown and saves the same content privately for handoff.
It does not publish to GitHub/ADO or start design, planning or implementation.

Visible gap counts are editing aids, not traceability, accessibility or release
certification. Review the complete exported document, its scenario/REQ/source
references, missing metadata, owned decisions and evidence status before design.
The live preview is escaped Markdown text rather than an HTML renderer; source
content cannot run scripts.

Concurrent saves use revision checks and an exclusive per-draft lock. A stale
save retains edits in the form; **Refresh saved draft** refreshes the base and
retries those edits. This explicitly reapplies local edits on the refreshed
draft: review them against another panel's changes. A leftover `.lock` file
after a process crash is surfaced as an error and requires inspection/removal
of that specific lock after confirming no writer is active. The extension does
not silently bypass locks. A changed template fingerprint rejects editing an
old draft until it is explicitly migrated; it never silently remaps fields.
Drafts are bounded to 512 KiB; AI source context is bounded to 64,000 characters.
Oversized content produces an explicit error, never silent truncation.

The renderer binds to loopback only, with per-panel authorization, Host/Origin
checks, bounded JSON input, no CORS and a restrictive content-security policy.
Tokens are transient and are not stored in draft files. Like other local
extensions, this is not a security boundary against processes running as the
same OS user.

## Validation

From the repository root:

```powershell
node --test .github\extensions\RequirementsBuilder\builder.test.mjs
node --check .github\extensions\RequirementsBuilder\extension.mjs
node --check .github\extensions\RequirementsBuilder\app.js
git diff --check
```

Tests cover template preservation, proposal approval separation/staleness,
N/A rules, Markdown cells, save/reload/conflicts, corrupt draft rejection,
HTTP authorization, AI request admission/failure and export boundaries.
They use a mocked conversation sender, not paid model-quality evaluation.
