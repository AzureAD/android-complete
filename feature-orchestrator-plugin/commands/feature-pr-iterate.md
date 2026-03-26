---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Review a PR and iterate with Copilot coding agent"
---

# PR Iteration

Help review and iterate on a pull request from the Copilot coding agent.

**First**: Read `.github/orchestrator-config.json` for repo slug mapping.

1. Ask which PR to review (or detect from context/state)
2. Fetch PR details:
   ```powershell
   gh pr view <number> --repo "<slug>" --json title,body,url,state,reviews,comments
   gh pr diff <number> --repo "<slug>"
   ```
3. Fetch all review comments:
   ```powershell
   gh api "/repos/<slug>/pulls/<number>/comments" --jq '.[].body'
   ```
4. Present findings and use `askQuestion`:
   ```
   askQuestion({
     question: "How would you like to handle this PR?",
     options: [
       { label: "🤖 Delegate to Copilot", description: "Post @copilot comment with feedback" },
       { label: "📋 Analyze comments", description: "Show review comments with proposed resolutions" },
       { label: "✅ Approve", description: "Approve the PR" },
       { label: "🔄 Request changes", description: "Request specific changes" }
     ]
   })
   ```
5. Execute the chosen action
