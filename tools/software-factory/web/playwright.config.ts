// Copyright (c) Microsoft Corporation. All rights reserved.

import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./tests",
    workers: 1,
    retries: 0,
    use: { browserName: "chromium", channel: "msedge", headless: true, viewport: { width: 1440, height: 1100 } },
});
