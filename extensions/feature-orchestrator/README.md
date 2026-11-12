<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Replaced by Software Factory

The Feature Orchestrator VS Code extension is retired. Its source and package
registration were removed; [Software Factory](../../tools/software-factory/README.md)
is the feature lifecycle implementation for Copilot app, CLI chat, and the dashboard.

Disable or uninstall any previously installed `AzureAD.feature-orchestrator`
extension in VS Code. Updating this repository does not remove installed copies.
The old global-state hooks and dashboard are not a supported fallback.

Keep historical feature JSON and design documents if you need them.
Software Factory can explicitly preview/import historical JSON; it never
trusts an imported approval or infers ownership from the latest feature.
