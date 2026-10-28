// Copyright (c) Microsoft Corporation. All rights reserved.

import { expect, test } from "@playwright/test";
import { execFileSync, spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Feature } from "../../core/contracts.ts";
import type { FactoryFeature } from "../../core/lifecycle-contracts.ts";

const cli = fileURLToPath(new URL("../../tests/browser-server.ts", import.meta.url));
let child: ChildProcess;
let directory: string;
let url: string;

function cachedFeatures(): { features: FactoryFeature[] } {
    return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { Application } = await import(process.argv[1]);
        const app = new Application();
        try { console.log(JSON.stringify({features: app.registry.list().map(feature => app.lifecycle.store.view(feature.id))})); } finally { app.close(); }
    `, new URL("../../core/application.ts", import.meta.url).href], {
        env: { ...process.env, SOFTWARE_FACTORY_HOME: directory }, encoding: "utf8",
    }));
}

test.beforeAll(async () => {
    directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-browser-"));
    child = spawn(process.execPath, [cli, "serve"], {
        env: { ...process.env, SOFTWARE_FACTORY_HOME: directory }, stdio: ["ignore", "pipe", "pipe", "ipc"],
    });
    url = await new Promise<string>((resolve, reject) => {
        let output = "";
        let errors = "";
        const timeout = setTimeout(() => reject(new Error(`Preview startup timed out: ${errors}`)), 15_000);
        child.stdout?.on("data", (chunk: Buffer) => {
            output += chunk.toString();
            const match = output.match(/Software Factory: (http:\/\/127\.0\.0\.1:\d+)/);
            if (match?.[1]) { clearTimeout(timeout); resolve(match[1]); }
        });
        child.stderr?.on("data", (chunk: Buffer) => { errors += chunk.toString(); });
        child.once("error", (error) => { clearTimeout(timeout); reject(error); });
        child.once("exit", (code) => { clearTimeout(timeout); reject(new Error(`Preview exited (${code}): ${errors}`)); });
    });
});

test.afterAll(async () => {
    if (child && child.exitCode === null && child.signalCode === null) {
        await new Promise<void>((resolve) => {
            child.once("exit", () => resolve());
            child.kill();
        });
    }
    if (directory) rmSync(directory, { recursive: true });
});

test("requirements, approval, shared-core reads, stale edits, and mobile layout", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    await expect(page.getByRole("heading", { name: "Give your next idea a home." })).toBeVisible();
    await page.getByRole("button", { name: "New feature", exact: true }).click();
    await page.getByLabel("Feature title").fill("IPC retry policy");
    await page.getByLabel("What are you trying to achieve?").fill("Make transient IPC failures recoverable without changing cancellation.");
    await page.getByLabel("What would success look like?").fill(
        "R1: Retry only transient IPC\n    errors.\nR2: Preserve existing cancellation behavior.",
    );
    await page.getByLabel("Scope decisions needed from you").fill("Which errors are transient?");
    await page.getByRole("button", { name: "Create feature", exact: true }).click();
    await expect(page.getByRole("heading", { name: "IPC retry policy", exact: true })).toBeVisible();
    await expect(page.locator(".requirement")).toHaveCount(2);
    await expect(page.locator(".requirement").first()).toContainText("Retry only transient IPC errors.");
    await expect(page.getByRole("button", { name: "Publish before approving", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Edit brief and criteria", exact: true }).click();
    await page.getByLabel("Scope decisions needed from you").fill("");
    await page.getByRole("button", { name: "Save new revision", exact: true }).click();
    await expect(page.locator(".feature-detail .eyebrow")).toContainText("REVISION 2");
    await page.getByLabel("ADO Feature work item ID").fill("123");
    await page.getByRole("button", { name: "Publish draft to ADO", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("local history is preserved");
    await page.getByRole("button", { name: "Publish to ADO", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Shared ADO Feature #123" })).toBeVisible();
    await page.getByRole("button", { name: "Approve for design", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("Does not start AI work");
    await page.getByRole("button", { name: "Approve scope", exact: true }).click();
    await expect(page.locator(".feature-detail .tag")).toHaveText("Ready for design");
    await expect(page.getByRole("heading", { name: "Ready, not running." })).toBeVisible();
    await expect(page.getByRole("region", { name: "Workflow lifecycle" })).toBeVisible();
    await page.getByRole("button", { name: "Run guarded design", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("may consume AI credits");
    await page.getByRole("button", { name: "Not now", exact: true }).click();
    await expect(page.getByRole("region", { name: "Workflow lifecycle" })).toContainText("Not started.");

    const saved = cachedFeatures();
    const feature = saved.features[0];
    expect(feature?.status).toBe("ready_for_design");
    expect(feature?.history).toHaveLength(1);
    expect(feature?.approvals).toHaveLength(1);
    if (!feature) throw new Error("Saved feature was missing.");
    await page.screenshot({ path: test.info().outputPath("software-factory-desktop.png"), fullPage: true });

    await page.getByRole("button", { name: "Edit brief and criteria", exact: true }).click();
    await page.getByLabel("Feature title").fill("Unsaved browser draft");
    const session = await page.request.get(`${url}/api/session`);
    const { csrf } = await session.json() as { csrf: string };
    const changed = await page.request.post(`${url}/api/commands`, {
        headers: { Origin: url, "X-Software-Factory-CSRF": csrf },
        data: {
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: {
                title: "Changed from another interface",
                requirements: feature.current.requirements, blockingUnknowns: [],
            },
        },
    });
    expect(changed.status()).toBe(200);
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("This feature changed since you started editing.");
    await page.getByRole("button", { name: "Save new revision", exact: true }).click();
    await expect(page.getByRole("alert").first()).toContainText("Requirements changed.");
    await expect(page.getByLabel("Feature title")).toHaveValue("Unsaved browser draft");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Changed from another interface" })).toBeVisible();
    await expect(page.locator(".feature-detail .tag")).toHaveText("Needs review");
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath("software-factory-mobile.png"), fullPage: true });
    expect(errors).toEqual([]);
});

test("a plain-language brief saves without criteria and Factory suggestions stay reviewable, not approved", async ({ page }) => {
    await page.goto(url);
    await page.getByRole("button", { name: "New feature", exact: true }).click();
    await page.getByLabel("Feature title").fill("Document local testing");
    const description = "Help a new contributor run Common's tests locally.\nDo not change production code or CI.";
    await page.getByLabel("What are you trying to achieve?").fill(description);
    await expect(page.getByLabel("What would success look like?")).not.toHaveAttribute("required", "");
    await page.getByRole("button", { name: "Create feature", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Document local testing", exact: true })).toBeVisible();
    await expect(page.locator(".feature-brief")).toContainText("Help a new contributor");
    await expect(page.getByRole("button", { name: "Publish before approving", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Run guarded design", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Draft success criteria with Factory", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("may consume AI credits");
    await expect(page.getByRole("dialog")).toContainText("Does not edit or approve the brief");
    await page.getByRole("button", { name: "Not now", exact: true }).click();
    const saved = cachedFeatures();
    const feature = saved.features.find((item) => item.current.title === "Document local testing")!;
    expect(feature.current.requirements).toEqual([]);
    expect(feature.approvals).toEqual([]);
    const application = new URL("../../core/application.ts", import.meta.url).href;
    execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { Application } = await import(process.argv[1]);
        const app = new Application();
        try {
            app.lifecycle.store.artifact(process.argv[2], "intake", process.argv[3], {
                summary: "A draft checklist to review and edit.",
                requirements: [
                    { id: "R1", text: "Document the verified prerequisites and test command." },
                    { id: "R2", text: "Leave production code and CI unchanged." }
                ],
                blockingUnknowns: []
            });
        } finally { app.close(); }
    `, application, feature.id, feature.current.id], {
        env: { ...process.env, SOFTWARE_FACTORY_HOME: directory }, encoding: "utf8",
    });
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByRole("region", { name: "Factory suggestions" })).toBeVisible();
    await expect(page.locator(".requirement")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Publish before approving", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Review and edit suggestions", exact: true }).click();
    await expect(page.getByLabel("What are you trying to achieve?")).toHaveValue(description);
    await expect(page.getByLabel("What would success look like?")).toHaveValue(
        "Document the verified prerequisites and test command.\nLeave production code and CI unchanged.",
    );
    await page.getByLabel("What would success look like?").fill(
        "Document the verified prerequisites and test command.\n- Keep production code, dependencies and CI unchanged.",
    );
    await page.getByRole("button", { name: "Save new revision", exact: true }).click();
    await expect(page.locator(".requirement")).toHaveCount(2);
    await expect(page.getByRole("region", { name: "Factory suggestions" })).toHaveCount(0);
    await expect(page.locator(".feature-detail .tag")).toHaveText("Needs review");
    await expect(page.getByRole("button", { name: "Publish before approving", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Run guarded design", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Edit brief and criteria", exact: true }).click();
    await expect(page.getByLabel("What would success look like?")).toHaveValue(
        "Document the verified prerequisites and test command.\nKeep production code, dependencies and CI unchanged.",
    );
    await page.screenshot({ path: test.info().outputPath("software-factory-plain-language-intake.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("idle dashboards discover chat work, show progress and failures, and preserve drafts during live updates", async ({ page }) => {
    await page.goto(url);
    await expect(page.getByText(/^Synced /)).toBeVisible();
    const application = new URL("../../core/application.ts", import.meta.url).href;
    const external = (script: string, ...args: string[]) => execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { Application } = await import(process.argv[1]);
        const app = new Application();
        const actor = { channel: "chat", sessionId: "chat-fixture", reference: crypto.randomUUID() };
        try { ${script} } finally { app.close(); }
    `, application, ...args], { env: { ...process.env, SOFTWARE_FACTORY_HOME: directory }, encoding: "utf8" });
    const state = JSON.parse(external(`
        const feature = app.execute({ kind: "create_feature", requestId: "background-chat", input: {
            title: "Broker throttling", description: "Honor eSTS Retry-After for matching silent requests. Leave interactive requests available."
        } }, actor);
        const run = app.lifecycle.begin(feature.id, "intake", actor);
        app.lifecycle.store.updateExecution(feature.id, run.id, { status: "running", phase: "Draft success criteria" });
        console.log(JSON.stringify({ featureId: feature.id, executionId: run.id }));
    `)) as { featureId: string; executionId: string };
    const featureLink = page.getByRole("navigation", { name: "Features" }).getByRole("button", { name: /Broker throttling/ });
    await expect(featureLink).toBeVisible({ timeout: 8000 });
    await featureLink.click();
    await expect(page.getByRole("heading", { name: "Factory is drafting your criteria" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Workflow lifecycle" })).toContainText("Started in Copilot chat");
    external(`
        app.lifecycle.store.updateExecution(process.argv[2], process.argv[3], {
            status: "error", phase: "Could not start workflow",
            error: "Could not resolve @github/copilot-sdk-win32-x64. Reinstall @github/copilot-sdk so its platform package is installed."
        });
    `, state.featureId, state.executionId);
    await expect(page.getByRole("heading", { name: "Draft success criteria failed" })).toBeVisible({ timeout: 8000 });
    await expect(page.getByRole("alert")).toContainText("copilot-sdk-win32-x64");
    await expect(page.getByRole("region", { name: "Feature activity" })).toContainText("Copilot chat");
    await expect(page.getByText("No native run ID was recorded.", { exact: false })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath("software-factory-shared-failure.png"), fullPage: true });
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("Draft success criteria with Factory?");
    await expect(page.getByRole("dialog")).toContainText("Honor eSTS Retry-After");
    await page.getByRole("button", { name: "Not now", exact: true }).click();
    await page.getByRole("button", { name: "Edit brief and criteria", exact: true }).click();
    await page.getByLabel("Feature title").fill("Unsaved work stays here");
    external(`
        const feature = app.registry.get(process.argv[2]);
        app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { title: "Scope updated in chat", description: "The updated Retry-After scope from chat." } }, actor);
    `, state.featureId);
    await expect(page.getByRole("alert")).toContainText("This feature changed since you started editing.", { timeout: 8000 });
    await expect(page.getByLabel("Feature title")).toHaveValue("Unsaved work stays here");
    await page.route("**/api/features*", (route) => route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ error: { code: "unavailable", message: "Connection lost" } }) }));
    await expect(page.getByRole("status").filter({ hasText: "Updates disconnected" })).toBeVisible({ timeout: 8000 });
    await expect(page.getByLabel("Feature title")).toHaveValue("Unsaved work stays here");
    await page.unroute("**/api/features*");
    await expect(page.getByText(/^Synced /)).toBeVisible({ timeout: 20000 });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("a completed design is readable before approval and approval offers a separate planning run", async ({ page }) => {
    const id = await new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Design fixture timed out.")), 10_000);
        child.once("message", (message: { featureId?: string; error?: string }) => {
            clearTimeout(timer);
            if (message.featureId) resolve(message.featureId); else reject(new Error(message.error || "Invalid fixture response."));
        });
        child.send("seed-design");
    });
    await page.goto(url);
    await page.getByRole("navigation", { name: "Features" }).getByRole("button", { name: /Readable design review/ }).first().click();
    await expect(page.getByRole("heading", { name: "Review the design" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Review design" })).toContainText("Keep the existing response contract intact.");
    await page.getByRole("button", { name: "Approve design", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("Does not start planning");
    await page.getByRole("dialog").getByRole("button", { name: "Approve design", exact: true }).click();
    await expect(page.getByRole("button", { name: "Create implementation plan", exact: true })).toBeVisible();
    const saved = cachedFeatures();
    const feature = saved.features.find((entry) => entry.id === id)!;
    expect(feature.factory.executions[0]?.status).toBe("completed");
    expect(feature.factory.controls).toHaveLength(0);
    await page.getByRole("button", { name: "Create implementation plan", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("Start implementation planning");
    await page.getByRole("button", { name: "Not now", exact: true }).click();
});

test("chat research and question rationale are visible before any native workflow exists", async ({ page }) => {
    const application = new URL("../../core/application.ts", import.meta.url).href;
    const chatTools = new URL("../../chat/tools.ts", import.meta.url).href;
    const external = (script: string, ...args: string[]) => execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { Application } = await import(process.argv[1]);
        const { createChatTools } = await import(process.argv[2]);
        const app = new Application();
        const actor = { channel: "chat", sessionId: "chat-progress-fixture", reference: crypto.randomUUID() };
        const host = { sessionId: actor.sessionId, capabilities: {}, ui: { confirm: async () => { throw new Error("Status cannot request approval."); } } };
        const report = createChatTools(() => app, () => host).find(tool => tool.name === "software_factory_report_progress");
        try { ${script} } finally { app.close(); }
    `, application, chatTools, ...args], { env: { ...process.env, SOFTWARE_FACTORY_HOME: directory }, encoding: "utf8" });
    const featureId = external(`
        const feature = app.execute({ kind: "create_feature", requestId: "visible-chat-research", input: {
            title: "Throttling scope research", description: "Honor Retry-After without blocking interactive requests."
        } }, actor);
        await report.handler({ featureId: feature.id, expectedRevision: feature.current.id, requestId: "research-start", progress: {
            stage: "research", status: "working",
            summary: "Inspecting Broker/Common and existing throttling behavior before recommending scope.",
            expectedOutput: "A recommended brief and success criteria, not implementation.",
            nextStep: "Present the recommended scope for review before design."
        } }, { sessionId: actor.sessionId, toolCallId: actor.reference });
        console.log(feature.id);
    `).trim();
    await page.goto(url);
    await page.getByRole("navigation", { name: "Features" }).getByRole("button", { name: /Throttling scope research/ }).click();
    await expect(page.getByRole("heading", { name: "Chat update: research and scope" })).toBeVisible();
    const progress = page.getByRole("region", { name: "Current chat progress" });
    await expect(progress).toContainText("A recommended brief and success criteria");
    await expect(progress).toContainText("Present the recommended scope for review");
    await expect(progress).toContainText("not proof of a running background agent");
    external(`
        const feature = app.registry.get(process.argv[3]);
        await report.handler({ featureId: feature.id, expectedRevision: feature.current.id, requestId: "scope-question", progress: {
            stage: "research", status: "waiting_for_user",
            summary: "Code research resolved request matching and retry handling. One rollout-scope decision remains.",
            expectedOutput: "A design-ready brief with an explicit compatibility boundary.",
            nextStep: "Save the selected scope and present it for approval.",
            question: {
                text: "Should the first rollout include legacy ADAL callers?",
                whyNeeded: "Legacy callers may lack the existing retry metadata; this changes compatibility scope.",
                recommendation: "Start with callers that support the existing retry metadata."
            }
        } }, { sessionId: actor.sessionId, toolCallId: actor.reference });
    `, featureId);
    await expect(page.getByRole("heading", { name: "Chat is waiting for your decision" })).toBeVisible({ timeout: 8000 });
    await expect(progress).toContainText("Should the first rollout include legacy ADAL callers?");
    await expect(progress).toContainText("this changes compatibility scope");
    await expect(progress).toContainText("Start with callers that support");
    await expect(progress).toContainText("Answer in the originating Copilot chat");
    await page.screenshot({ path: test.info().outputPath("software-factory-chat-context.png"), fullPage: true });
    external(`
        const feature = app.registry.get(process.argv[3]);
        app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id, input: {
            title: feature.current.title, description: "The user chose the supported-caller rollout.",
            requirements: [{ text: "Preserve the current supported-caller compatibility boundary." }]
        } }, actor);
    `, featureId);
    await expect(progress).toHaveCount(0, { timeout: 8000 });
    await expect(page.getByRole("button", { name: "Publish before approving", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Run guarded design", exact: true })).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("scope approval carries unresolved engineering tasks into design instead of requiring a completed design", async ({ page }) => {
    await page.goto(url);
    await page.getByRole("button", { name: "New feature", exact: true }).click();
    await page.getByLabel("Feature title").fill("Design-owned throttling tasks");
    await page.getByLabel("What are you trying to achieve?").fill("Throttle supported silent requests without changing interactive behavior.");
    await page.getByLabel("What would success look like?").fill("Interactive recovery remains usable during a cooldown.");
    await page.getByLabel("Work for the design agents").fill(
        "Trace IPC compatibility and original request-context propagation.\nDesign bounded-cache overflow without dropping active cooldowns.",
    );
    await page.getByRole("button", { name: "Create feature", exact: true }).click();
    await expect(page.getByRole("region", { name: "Design obligations" })).toContainText("bounded-cache overflow");
    await page.getByLabel("ADO Feature work item ID").fill("789");
    await page.getByRole("button", { name: "Publish draft to ADO", exact: true }).click();
    await page.getByRole("button", { name: "Publish to ADO", exact: true }).click();
    await expect(page.getByRole("button", { name: "Approve for design", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Approve for design", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("heading", { name: "Approve feature scope?" })).toBeVisible();
    await expect(page.getByRole("dialog").locator(".confirmation-brief")).toBeHidden();
    await expect(page.getByRole("dialog").locator("pre")).toBeHidden();
    await page.getByRole("dialog").screenshot({ path: test.info().outputPath("software-factory-readable-approval.png") });
    await page.getByRole("dialog").getByText("Review brief and success criteria", { exact: true }).click();
    await expect(page.getByRole("dialog").locator(".confirmation-brief")).toBeVisible();
    await expect(page.getByRole("dialog")).toContainText("Work for the design agents");
    await expect(page.getByRole("dialog")).toContainText("without dropping active cooldowns");
    await page.getByRole("button", { name: "Approve scope", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Ready, not running.", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Run guarded design", exact: true })).toBeEnabled();
    await page.screenshot({ path: test.info().outputPath("software-factory-design-obligations.png"), fullPage: true });
    const saved = cachedFeatures();
    const feature = saved.features.find((entry) => entry.current.title === "Design-owned throttling tasks")!;
    expect(feature.current.designTasks).toHaveLength(2);
    expect(feature.current.blockingUnknowns).toEqual([]);
    expect(feature.approvals).toHaveLength(1);
    expect(feature.factory.executions).toHaveLength(0);
});
