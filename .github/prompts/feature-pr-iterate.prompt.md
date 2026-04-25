---
agent: feature-orchestrator
description: "Review a PR and post feedback for Copilot coding agent to iterate on"
---

# PR Iteration

## Your Task

Help the developer review and iterate on an agent-created pull request.
The user will provide the feature name, repo, and PR number below.

**Step 0**: Load feature context for deeper understanding:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```
This returns the feature state including:
- **Design spec path** (`artifacts.design.docPath`) — read this file to understand the intended design
- **PBI details** (`artifacts.pbis`) — understand what this PR is supposed to implement
- **Other PRs** (`artifacts.agentPrs`) — related work in the feature

**Read the design spec** from the `docPath` to understand the architectural decisions,
requirements, and intended approach. This context is essential for evaluating whether
the PR correctly implements the design and for proposing informed resolutions to comments.

**Step 1**: Fetch the PR details, diff, and **all review comments**:
```powershell
gh pr view <prNumber> --repo "<full-repo-slug>" --json title,body,state,additions,deletions,changedFiles,url
gh pr diff <prNumber> --repo "<full-repo-slug>" | head -200
```

Then fetch **all review comments and conversation threads**:
```powershell
gh api "/repos/<owner>/<repo>/pulls/<prNumber>/comments" --jq '.[] | {path: .path, line: .line, body: .body, user: .user.login, created_at: .created_at}' 2>&1
gh api "/repos/<owner>/<repo>/issues/<prNumber>/comments" --jq '.[] | {body: .body, user: .user.login, created_at: .created_at}' 2>&1
gh pr view <prNumber> --repo "<full-repo-slug>" --json reviews --jq '.reviews[] | {state: .state, body: .body, author: .author.login}'
```

This gives you:
- **Inline review comments** (file-specific feedback from reviewers)
- **General PR comments** (conversation thread)
- **Review decisions** (approved, changes requested, commented)

Repo slug mapping:
- `common` → `AzureAD/microsoft-authentication-library-common-for-android`
- `msal` → `AzureAD/microsoft-authentication-library-for-android`
- `broker` → `identity-authnz-teams/ad-accounts-for-android`
- `adal` → `AzureAD/azure-activedirectory-library-for-android`
- `authenticator` → `AzureAD/microsoft-authenticator-for-android`

Discover the GitHub username from `.github/developer-local.json`, or `gh auth status`.
Switch account before any gh commands: `gh auth switch --user <username>`

**Step 2**: Ask the developer how they want to handle the review feedback:

```
askQuestion({
  question: "How would you like to handle the review feedback on this PR?",
  options: [
    { label: "🤖 Delegate to Copilot", description: "Tag @copilot to address all review comments automatically" },
    { label: "📋 Show me the analysis first", description: "Present review feedback with proposed resolutions, then decide", recommended: true },
    { label: "✅ Looks good — approve", description: "Approve the PR as-is" }
  ]
})
```

### If "Delegate to Copilot":

Compose a single comprehensive `@copilot` comment that summarizes ALL reviewer feedback
and post it on the PR:
```powershell
gh pr comment <prNumber> --repo "<slug>" --body "@copilot Please address the following review feedback:

1. [summary of comment 1 with file/line reference]
2. [summary of comment 2 with file/line reference]
...

Please fix all of the above and push updated commits."
```

Confirm the comment was posted and that the coding agent will pick it up.

### If "Show me the analysis first":

Analyze the PR and present a comprehensive review summary:

**For each reviewer comment:**
1. Show the comment (who said what, on which file/line)
2. **Propose a resolution** — analyze the code and suggest what should change
3. If a code change is needed, show a concrete code snippet or approach
4. If the comment is a question, provide a clear answer based on codebase context

Present as:
```
### Reviewer Feedback & Proposed Resolutions

**Comment 1** — @reviewer on `src/MyFile.java:42`
> "This should handle null case"
**Resolution**: Add a null check before accessing the field. Proposed change:
`if (value != null) { ... }`

**Comment 2** — @reviewer (general)
> "Missing unit tests for the retry logic"
**Resolution**: Add tests for success, failure, and max-retry scenarios in `IpcRetryTest.java`.
```

Also include:
- Overall PR summary (title, +/- lines, changed files)
- Any patterns or systemic issues across the comments
- Your recommendation

Then ask what to do next:
```
askQuestion({
  question: "How would you like to proceed?",
  options: [
    { label: "🤖 Delegate to Copilot agent", description: "Post @copilot comment with all the feedback to fix remotely" },
    { label: "💻 Implement locally", description: "Checkout the branch and make changes in VS Code" },
    { label: "✏️ I'll write custom feedback", description: "Let me type exactly what to tell the agent" }
  ]
})
```

**If "Delegate to Copilot agent"**: Post the structured feedback as an `@copilot` comment (same as above).

**If "Implement locally"**: Checkout the PR branch in the correct repo directory:
```powershell
gh pr checkout <prNumber> --repo "<full-repo-slug>"
```
Run this in the correct sub-repo directory (common/, msal/, broker/, adal/, authenticator/).
Then tell the developer: "Branch checked out. Make your changes, commit, and push."

**If "I'll write custom feedback"**: Ask the developer to type their feedback, then post it
as an `@copilot` comment on the PR.

### If "Approve":

```powershell
gh pr review <prNumber> --repo "<slug>" --approve
```

Confirm the approval.

## Final Step

After any action, confirm what was done and suggest:
"Use the feature detail panel's ↻ Refresh button to update PR status."
