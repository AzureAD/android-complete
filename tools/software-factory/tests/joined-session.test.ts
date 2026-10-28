// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fixture } from "./helpers.ts";

test("joined chat execution and dashboard resume use one native session and original journal with zero agents", () => {
    const f = fixture();
    try {
        const output = execFileSync(process.execPath, ["--input-type=module", "-e", `
            import assert from "node:assert/strict";
            import path from "node:path";
            import { mkdirSync,writeFileSync } from "node:fs";
            const { Application, defaultWorkspace } = await import(process.argv[1]);
            const { createClient, sessionConfiguration } = await import(process.argv[2]);
            const { sourcePins } = await import(process.argv[3]);
            const { createReaderTools, FACTORY_READER } = await import(process.argv[4]);
            const home = process.argv[5];
            const source = path.join(home,"source");
            mkdirSync(source);
            writeFileSync(path.join(source,"A.kt"),"val nativeNeedle = true;");
            const deniedFile=path.join(source,"Denied.kt");
            writeFileSync(deniedFile,"fixture content must not bypass native permission denial");
            const workspace=defaultWorkspace();
            const options={database:path.join(home,"registry.sqlite"),workspace};
            const app=new Application(options);
            const dashboard=new Application(options);
            const client=createClient(workspace,home);
            const waitFor=async(predicate)=>{const end=Date.now()+15000;while(!predicate()){if(Date.now()>end)throw new Error("Owner handoff timed out.");await new Promise(r=>setTimeout(r,50));}};
            try{
                await client.start();
                const config=sessionConfiguration(workspace,"Joined zero-agent fixture",[source]);
                const permission=config.onPermissionRequest;
                config.onPermissionRequest=(request,invocation)=>request.kind==="read"&&request.path===deniedFile
                    ? {kind:"reject",feedback:"Native read denial fixture."} : permission(request,invocation);
                const session=await client.createSession(config);
                const actor={channel:"chat",reference:"zero-agent-fixture",sessionId:session.sessionId};
                const feature=app.execute({kind:"create_feature",requestId:"joined",input:{title:"Joined fixture",requirements:[{id:"R1",text:"Run no agents."}],blockingUnknowns:[]}},actor);
                app.execute({kind:"approve_requirements",featureId:feature.id,expectedRevision:feature.current.id},actor,{granted:true,revisionId:feature.current.id,actor});
                const read=app.lifecycle.begin(feature.id,"check",actor,{[source]:"fixture"}, {},undefined,{sessionId:session.sessionId,kind:"chat",established:true});
                const readers=createReaderTools(()=>app,()=>session);
                assert.match(await readers[0].handler({featureId:feature.id,executionId:read.id,path:path.join(source,"A.kt")}),/nativeNeedle/);
                assert.match(await readers[1].handler({featureId:feature.id,executionId:read.id,path:source,pattern:"*.kt"}),/A.kt/);
                assert.match(await readers[2].handler({featureId:feature.id,executionId:read.id,path:source,pattern:"nativeNeedle"}),/nativeNeedle/);
                const nested=await session.rpc.tools.execute({name:"software_factory_read",arguments:{featureId:feature.id,executionId:read.id,path:path.join(source,"A.kt")}});
                assert.match(typeof nested==="string"?nested:nested.textResultForLlm,/nativeNeedle/,"The registered worker tool must support native delegation from inside its external callback.");
                await assert.rejects(()=>readers[0].handler({featureId:feature.id,executionId:read.id,path:deniedFile}),/denied|permission|reject/i);
                app.lifecycle.store.updateExecution(feature.id,read.id,{status:"cancelled"});
                await app.bindChatSession(session);
                const runtime=await app.runtime();
                const started=await runtime.start(feature.id,"check",actor);
                await waitFor(()=>app.lifecycle.store.execution(feature.id,started.id).status==="completed"||app.lifecycle.store.execution(feature.id,started.id).status==="error");
                await runtime.control(feature.id,started.id,"refresh",actor);
                const actual=app.lifecycle.store.execution(feature.id,started.id);
                assert.equal(actual.status,"completed",actual.error);
                assert.equal(actual.sessionId,session.sessionId);
                assert.equal(actual.sessionKind,"chat");
                assert.equal(actual.registration.readerAgent,FACTORY_READER);
                assert.equal(actual.launchContext,undefined,"Joined execution must not launch a second SDK runtime.");
                assert.equal(actual.usage.subagents,0);
                const queued=await (await dashboard.runtime()).start(feature.id,"check",{channel:"web",reference:"zero-agent-dashboard-start"});
                assert.equal(queued.sessionId,session.sessionId);
                await waitFor(()=>app.lifecycle.store.execution(feature.id,queued.id).status==="completed"||app.lifecycle.store.execution(feature.id,queued.id).status==="error");
                assert.equal(app.lifecycle.store.execution(feature.id,queued.id).status,"completed");
                const paused=await session.workflow.run("software-factory-check",{args:{pause:true},notifyOnComplete:false});
                assert.equal(paused.status,"paused");
                const pins=await sourcePins(app.lifecycle);
                const tracked=app.lifecycle.begin(feature.id,"check",actor,pins);
                app.lifecycle.store.updateExecution(feature.id,tracked.id,{status:"paused",runtimeId:paused.runId});
                const command={kind:"control_run",featureId:feature.id,executionId:tracked.id,action:"resume",limits:{maxAiCredits:null}};
                const webActor={channel:"web",reference:"isolated-dashboard"};
                await dashboard.dispatch(command,webActor,{granted:true,revisionId:dashboard.prepare(command).consentKey,actor:webActor});
                await waitFor(()=>app.lifecycle.store.execution(feature.id,tracked.id).status==="completed"||app.lifecycle.store.execution(feature.id,tracked.id).status==="error");
                await runtime.control(feature.id,tracked.id,"refresh",actor);
                const resumed=app.lifecycle.store.execution(feature.id,tracked.id);
                assert.equal(resumed.status,"completed",resumed.error);
                assert.equal(resumed.runtimeId,paused.runId);
                assert.equal(resumed.sessionId,session.sessionId);
                assert.equal(resumed.usage.subagents,0);
                assert.equal((await client.listSessions()).length,1,"Dashboard control must not create another session.");
                assert.equal(app.lifecycle.store.state(feature.id).controls.at(-1).status,"completed");
                console.log(JSON.stringify({sameSession:true,sameRun:true,nativeReads:true,agentsStarted:0}));
            }finally{
                await dashboard.shutdown();
                await app.shutdown();
                const errors=await client.stop();
                if(errors.length)throw new AggregateError(errors,"Joined fixture cleanup failed.");
            }
        `, new URL("../core/application.ts", import.meta.url).href, new URL("../runtime/host.ts", import.meta.url).href,
        new URL("../runtime/controller.ts", import.meta.url).href, new URL("../runtime/reader.ts", import.meta.url).href, f.directory], {
            cwd: f.workspace, encoding: "utf8", timeout: 90_000, maxBuffer: 128 * 1024,
            env: { ...process.env, COPILOT_HOME: path.join(f.directory, "copilot-home"), SOFTWARE_FACTORY_OWNED_RUNTIME: "1" },
        });
        assert.deepEqual(JSON.parse(output), { sameSession: true, sameRun: true, nativeReads: true, agentsStarted: 0 });
    } finally { f.cleanup(); }
});
