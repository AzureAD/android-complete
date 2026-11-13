# Human-Gated AI  SDLC Orchestrator: Product and Implementation Spec

Oct 23, 2026 · @Moumita Ghosh

## Goal

We are building a system where AI agents do the work of each SDLC stage and a human approves every handoff, from requirements spec to a merged pull request. Nothing moves to the next stage on an agent's say-so.

Today this exists only as a process on paper. Three things are missing: something that remembers where each feature is, something that refuses to advance without an approval, and proof that the agents are reliable. This spec covers those three.

It is not a swarm of agents, and it does not replace CI, code review, or human approval. Agents prepare the work; humans decide.

## How it works

Every stage runs the same loop: an agent drafts, checks and an independent reviewer look for problems, the author fixes them within a retry limit, and a human then approves or requests changes.

&#91;embedded content: stage loop · 7 steps, 2 decisions\]

Agent review prepares the work for the human; it never approves progression. When the retry limit runs out, the stage goes to a human instead of looping.

## What to build

Five parts. The orchestrator is the only real infrastructure; the rest are small.

| Part | What it does | Rule it must enforce |
| --- | --- | --- |
| Stage contract | One page per stage: inputs, outputs, checks, permissions, who approves, retry limit | Written before any agent is built |
| Orchestrator | Runs the stage loop, saves state, pauses for approval, resumes | Reads the current stage from ADO, never from chat history or a second flag; retries never duplicate PBIs or PRs |
| Agents | Author (designs and plans), implementer (code and tests), reviewer (read-only, checks against a stage rubric) | The reviewer sees the artifact, rubric, and cited sources, never the author's reasoning |
| Validators | Deterministic checks: schema, links, build, tests, required fields | Run before the reviewer; failures go back to the author |
| Approval record | Stored on the ADO feature: stage, artifact hash, approver, decision, time, what it authorizes | One approval covers one exact version; a changed artifact needs a new one |

Stage states: not started, running, validating, awaiting approval, approved. Side states: needs revision, failed, blocked, cancelled. ADO is the single source of truth for the feature, its current stage, its artifacts, and every approval; the orchestrator keeps only a run and attempt log.

Start with two agents, not thirteen. Reuse the reviewer with a different rubric per stage, and add an agent only when a stage needs different tools or permissions.

## Runtime: Copilot dynamic workflows

Each stage that needs agent work runs as one GitHub Copilot dynamic workflow, currently in public preview. A workflow is a program, written as a Copilot extension, that mixes plain steps with agent work, returns structured results, and ends. The orchestrator moves a feature between stages; a workflow never does.

| Stage | Runs as |
| --- | --- |
| Requirements intake | A small workflow, or one agent call |
| Design | Workflow: author, validators, reviewer, bounded fix loop |
| Planning | Same workflow shape, with the planning rubric |
| Backlog creation in ADO | Steps only: create, read back, validate |
| Implementation | Workflow: implementer, tests, parallel review by coherent change area, each reviewer given the design and cross-file contracts |
| PR pipeline and merge | Existing CI and branch protection, not a workflow |

Every run follows one template: load the inputs by hash, author drafts, validators run, reviewer checks, fix loop up to the retry limit, write the result and evidence to ADO, end. A human approval in ADO starts the next run, pinned to the approved hash. We do not rely on a workflow pause to hold an approval, because the docs do not say where a paused run's state lives or whether it survives a restart.

**Team asset.** Workflows are committed to the repo at `.github/extensions/<name>/extension.mjs`, so nobody builds their own. They change through PRs under CODEOWNERS, because extensions run with each user's privileges. Teammates pull and reload to get updates, and the CLI needs its experimental flag. Rubrics and stage contracts stay as plain files in the repo. If several repos need the same workflows, package them as a plugin instead.

Ask Copilot to author each workflow directly as a project extension, not as a session copy to move later. Proposed layout:

| Path | Holds |
| --- | --- |
| `.github/extensions/sdlc-<stage>/extension.mjs` | Workflow code: ordering, validation, retries, result handling |
| `.github/skills/sdlc-workflow/` | How to do the domain work |
| `.github/agents/` | Role definitions and tool boundaries |
| `docs/sdlc/contracts` and `docs/sdlc/rubrics` | Stage contracts and rubrics |
| `evals/sdlc-workflow/` | Fixtures, graders, regression cases |

Each rubric lives in one place. The workflow, skill, and agent prompts point to it instead of copying it.

**What a run returns.** A structured result with the input and output artifact revisions, validation results and unresolved findings, the workflow and rubric versions, whether it is ready for human review or blocked, and the evidence the next stage needs. A completed workflow does not mean an approved stage, and permission to run a workflow is not acceptance of its output.

**Evals use the same workflows.** `copilot workflow run` can write a JSON result file, so a script can loop over the seeded cases and score the output.

**Spike (one to two days): the design stage only.** Take a supplied requirements file and an isolated output location. Draft a design, validate its structure and artifact identity, have a separate read-only reviewer assess it against the rubric, allow a bounded revision loop, and return the final artifact with any unresolved findings. Then stop for human approval, with no publishing or backlog creation.

Eval cases for the spike: a valid design, a planted defect, ambiguous requirements, a reviewer or tool failure, exhausted revisions, an unauthorized write, an interrupted run that resumes, and a stale input revision. Record eval results separately from the reviewer's verdicts; a reviewer saying pass is an output being tested.

**Also confirm in the spike:** structured output reliability; a different model for the reviewer; separate permissions for reviewer and author, since subagents inherit the session's grants; what a teammate sees the first time a repo extension loads, tested in a fresh teammate session; and how an extension is packaged and loaded as a plugin, before any move to one. `copilot workflow run` shows no permission prompts, so grant permissions up front, and do not set the untrusted-directory extension variable globally as a shortcut.

## Evals

Evals test the agents and the loop, not the feature. Each case has an expected answer that a human wrote first.

| Eval | Cases | Pass looks like |
| --- | --- | --- |
| Reviewer catches flaws | 5 to 10 past design docs. Each has a clean version plus versions with one planted flaw: no rollback plan, vague version assumption, missing cached-state case, untestable criterion | Every planted critical flaw flagged in the right section; at most 1 false hold in 10 clean docs; same verdict in 4 of 5 runs |
| Reviewer is not persuaded | The same flawed docs with confident, unsupported claims added by the author | Verdict does not change |
| Author does not invent | 3 to 5 past briefs, some with a fact deliberately missing | Gaps listed as open questions; zero invented versions or behaviors |
| Loop enforces approval | Stale approval, duplicate approve, interrupted run, changed requirements, resume the next day | Deterministic tests; all pass |

The numbers are starting proposals. Run a baseline, then freeze the real bar. A change to a prompt, model, or rubric means re-running the evals, and every flaw that slips through becomes a new case.

## Scope

Version 1 covers one slice: requirements spec in, design doc drafted, reviewed, and approved by a human.

**In version 1**

- Versioned requirements intake, checked for ambiguity; gaps are flagged, never silently rewritten
- Design author and reviewer agents, with the design rubric
- Approve and request-changes loop, with audit history
- The evals above

**Later, in this order**

1. Planning, with dependency and coverage checks
2. Backlog creation in ADO, with read-back validation instead of a second agent
3. Implementation with isolated workspaces, tests, and independent PR review

**Where it ends**

The cycle is done when the code is merged and the required tests pass in the PR pipeline. Release, rollout, and production monitoring are out of scope and stay with the existing release process.

## Build plan

Build in this order. Each step ends with something the engineer can demo.

1. **Stage contracts and the design rubric.** Done when two senior engineers grade the same 3 design docs with the rubric and mostly agree.
2. **Approval loop with a stub artifact, no agents yet.** Done when one artifact is produced, rejected, revised, approved, and resumed after a restart, with every state change and approval recorded on an ADO work item.
3. **Reviewer agent and its seeded-flaw evals.** Done when baseline catch and false-hold rates are recorded and the targets are frozen.
4. **Author agent and its brief evals.** Done when drafts list unknowns and invent nothing.
5. **Wire them together on one real feature.** A human grades every author draft and we track their edits. Each miss becomes an eval case.
6. **Go or no-go on planning,** the next stage, based on the numbers from steps 3 to 5.

Current stage and approvals live on the ADO work item from day one. The orchestrator's own store holds only run and attempt logs, so a simple file or table is enough at the start. Before step 3, run the one- to two-day spike described under Runtime and settle its open questions.

## Decisions needed

- [x] **Source of truth: ADO.** The ADO feature holds the current stage, artifact links and hashes, and every approval. The orchestrator keeps only a run and attempt log for retries and de-duplication.
- [x] **Runtime: Copilot dynamic workflows.** One run per stage; the orchestrator and ADO sit outside the workflow. Direction set, to confirm in the spike.
- [x] **Team asset.** Workflows are committed to the repo and reviewed by PR, not built per person.
- [ ] **Repo or plugin.** Commit to one repo's `.github/extensions/`, or package as a plugin if several repos need the same workflows? The workflow docs say a workflow can be packaged as a plugin; packaging and loading still need verifying.
- [ ] **ADO fields.** Which work item type and fields hold the stage, artifact hash, and approvals, and who can edit them by hand? A manual edit must not silently advance a stage.
- [ ] **Approvers.** Who approves each stage, and who covers exceptions?
- [ ] **Merge authority.** Does a human click merge after approving the PR, or may the orchestrator merge once approval and a green PR pipeline are in place? Branch protection stays in force either way.
- [ ] **Eval material.** Which 5 to 10 past design docs can we use, including ones tied to incidents or NO-GOs?
- [ ] **Retry limit.** How many author-reviewer rounds before the stage goes to a human? A starting proposal is 2.
- [ ] **Reviewer independence.** Use a separate prompt for the reviewer, and ideally a different model. A separate context helps but does not guarantee independence, which is why the reviewer has its own evals.
- [ ] **Integrations.** Which existing ADO and GitHub tooling can the workflows and the orchestrator call?
