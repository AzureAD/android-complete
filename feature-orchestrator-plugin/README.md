<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Replaced by Software Factory

Feature Orchestrator became [Software Factory](../tools/software-factory/README.md).
This plugin is retired, not an alternative execution mode or fallback.
Its registration, agents, skills, commands, hooks, and state writers were removed.

Remove any installed copy of the `feature-orchestrator` plugin and its
`chat.plugins.paths` entry from your editor configuration. Installed copies are
not uninstalled by a repository update. Use the `software-factory` agent in a
fresh trusted repository chat after installing Software Factory.

Historical JSON and local design files are not deleted or migrated automatically.
Use Software Factory's `preview_import` and separately confirmed `import_legacy`
commands. Imported records do not grant approval or restore an old execution.
