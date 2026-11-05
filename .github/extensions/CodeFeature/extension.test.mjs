// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("registered extension directly relays real workflow milestones without progress invalidation events or a JDK prompt", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "extension-live-milestones-"));
    try {
        const sdk = String.raw`
export function defineWorkflow(definition) { return definition; }
export async function joinSession(config) {
    globalThis.fixtureConfig = config;
    return globalThis.fixtureSession;
}
`;
        writeFileSync(path.join(root, "package.json"), '{"type":"module"}');
        writeFileSync(path.join(root, "extension.js"), sdk);
        const fixture = String.raw`
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const root = process.argv[2];
const source = process.argv[3];
process.env.COPILOT_SDK_PATH = root;
process.env.JAVA_HOME = path.join(root, "inherited-invalid-jdk");
const originalJavaHome = process.env.JAVA_HOME;
const jdk = path.join(root, "jdk21");
fs.mkdirSync(path.join(jdk, "bin"), {recursive:true});
fs.writeFileSync(path.join(jdk, "release"), 'JAVA_VERSION="21.0.4"\n');
for (const tool of ["java", "javac"]) fs.writeFileSync(path.join(jdk, "bin", tool + (process.platform === "win32" ? ".exe" : "")), "");
process.env.PATH = path.join(jdk, "bin") + path.delimiter + process.env.PATH;
const workspace = path.join(root, "workspace");
fs.mkdirSync(path.join(workspace, "common"), {recursive:true});
fs.mkdirSync(path.join(workspace, "sdk"));
fs.writeFileSync(path.join(workspace, "common", "gradlew.bat"), "");
process.env.ANDROID_HOME = path.join(workspace, "sdk");
const logs = [];
const messages = [];
let returned = false;
let uiQuestions = 0;
const session = {
    sessionId: "extension-registration-fixture",
    ui: {elicitation: async () => {uiQuestions++; throw new Error("Compatible installed JDK must not prompt");}},
    on: () => () => {}, // The extension receives NO ephemeral workflow progress events.
    send: async (message) => {
        assert.equal(returned, false, "Phase updates must be admitted before workflow settlement");
        messages.push(message);
        return "message-" + messages.length;
    },
    log: async (message) => logs.push(message),
    workflow: {
        listRuns: async () => ({runs:[], omittedOlder:0, oldestSeq:null}),
        getRun: async (runId) => ({runId, status:"running"}),
        getRunDetail: async () => ({workflowName:"code-feature"}),
        getRunProgress: async () => ({records:[], hasMoreNewer:false}),
        waitForRun: async () => new Promise(() => {}),
    },
};
globalThis.fixtureSession = session;
await import(pathToFileURL(source).href);
const workflow = globalThis.fixtureConfig.workflows.find(item => item.meta.name === "code-feature");
assert.ok(workflow);
const task = {
    title:"Fixture", revision:"v1", workItemId:12345,
    approval:{approvedRevision:"v1", approvedBy:"fixture", reference:"fixture"},
    requirements:[{id:"R1", text:"Fixture requirement"}],
    targets:[{id:"common",repository:"common",localPath:"common",baseBranch:"dev",workItemId:12345,title:"Fixture",description:"Fixture",
        acceptanceCriteria:["Source fixture"],verificationCommands:[]}],
    blockingUnknowns:[],
};
const implementation = {
    taskRevision:"v1", targets:[{
        targetId:"common",repository:"common",summary:"Authored fixture",
        files:[{path:"FooTest.kt",action:"create",content:"// Copyright Microsoft\nclass FooTest\n",summary:"Test source"}],
        testsAddedOrUpdated:["FooTest"],verificationCommandsRun:[],
        pr:{title:"Fixture",summary:"Fixture",testPlan:"Unverified",breakingChanges:"None",rolloutAndFlag:"not-applicable"},
    }],
    traceability:[{requirementId:"R1",targetIds:["common"],filePaths:["FooTest.kt"],testLocations:["FooTest"],verification:"Source reviewed only"}],
    assumptions:[],openDecisions:[],blockingUnknowns:[],
};
const evidence = {
    approvalVerification:{verified:true,revision:"v1",reference:"fixture",rationale:"Fixture approval"},
    references:[{id:"E1",repository:"common",revision:"fixture-source",path:"Foo.kt",locator:"Foo"}],
    facts:[{statement:"Fixture source",referenceIds:["E1"]}],conflicts:[],assumptions:[],unknowns:[],blockingUnknowns:[],
};
const rubric = ["approvedScopeAndRequirements","architectureAndApiCompatibility","correctnessAndEdgeCases","concurrencyAndThreadSafety",
    "securityPrivacyAndLogging","performanceAndResourceBounds","telemetryAndObservability","unitTestsAndVerification","prReadinessAndTraceability"];
const review = {criteria:rubric.map(id=>({id,pass:true})),
    acceptanceCriteria:[{targetId:"common",criterion:"Source fixture",passed:true,evidence:"Source only"}],findings:[],blockingUnknowns:[]};
const results = [evidence,implementation,review];
const journal = new Map();
const ctx = {
    runId:"extension-run",
    args:{task,options:{workspaceRoot:workspace,allowLocalOnly:true,createPullRequests:false,observePullRequests:false,verifyPrerequisites:true}},
    session,
    phase:()=>{},log:async text=>logs.push(text),
    step:async(key,producer,options)=>{
        if(options?.volatile)return producer();
        if(!journal.has(key))journal.set(key,await producer());
        return journal.get(key);
    },
    agent:async()=>{
        assert.ok(results.length,"Unexpected model callback");
        return results.shift();
    },
};
const result = await workflow.run(ctx);
returned = true;
assert.equal(result.deliveryStatus,"prepared-for-review");
assert.equal(uiQuestions,0);
assert.equal(process.env.JAVA_HOME,originalJavaHome);
assert.equal(messages.length,6);
assert.ok(messages.every(message=>message.source==="system"&&message.mode==="immediate"&&message.prompt.includes("[workflow-milestone:")));
assert.ok(messages[0].prompt.includes("Build toolchain selected"));
assert.ok(messages[0].prompt.includes(jdk));
assert.ok(messages.at(-1).prompt.includes("Pull request packet prepared"));
assert.ok(messages.some(message=>message.prompt.includes("Local verification: common")&&message.prompt.includes("deferred")));
assert.equal(results.length,0);
console.log(JSON.stringify({milestones:messages.length,jdkQuestions:uiQuestions,globalJavaHomeUnchanged:true,deliveryStatus:result.deliveryStatus}));
`;
        const file = path.join(root, "registration.mjs");
        writeFileSync(file, fixture);
        const output = execFileSync(process.execPath, [
            file, root, fileURLToPath(new URL("./extension.mjs", import.meta.url)),
        ], { cwd: root, encoding: "utf8", stdio: "pipe", timeout: 30_000 });
        const result = JSON.parse(output.trim());
        assert.deepEqual(result, { milestones: 6, jdkQuestions: 0, globalJavaHomeUnchanged: true, deliveryStatus: "prepared-for-review" });
    } finally { rmSync(root, { recursive: true, force: true }); }
});
