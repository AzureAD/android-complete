// Copyright (c) Microsoft Corporation. All rights reserved.

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({ plugins: [react()], build: { sourcemap: false } });
