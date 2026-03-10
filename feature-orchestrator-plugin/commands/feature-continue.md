---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Resume working on a feature from its current step"
---

# Continue Feature

Resume working on a feature from its current step.

1. Set state util path: `$su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"`
2. Read feature state: `node $su list-features`
3. If multiple features exist, use `askQuestion` to let the user pick one
4. Read the selected feature: `node $su get-feature "<name>"`
4. Determine the current step and show pipeline progress:

   | Step | Next Action |
   |------|-------------|
   | `designing` | Continue writing the design spec |
   | `design_review` | Design is written — ask if approved or needs revision |
   | `plan_review` | Plan is ready — ask if approved or needs revision |
   | `backlog_review` | PBIs created — ask about dispatching |
   | `monitoring` | Check PR statuses |
   | `completed` | Feature is done! Show summary |

5. Resume from the appropriate phase
