// Copyright (c) Microsoft Corporation. All rights reserved.

import { randomUUID } from "node:crypto";
import type { CommandDefinition } from "@github/copilot-sdk";
import type { Application } from "../core/application.ts";
import { FactoryError, object } from "../core/contracts.ts";
import type { ChatHost } from "./tools.ts";

export interface CommandHost extends ChatHost {
    log(message: string): Promise<unknown>;
    send(options: { prompt: string }): Promise<unknown>;
}

export function createCommands(application: () => Application, host: () => CommandHost): CommandDefinition[] {
    return [{
        name: "software-factory",
        description: "Read features, preview a JSON domain command, or explicitly confirm an exact scope entirely in chat.",
        handler: async (context) => {
            if (context.sessionId !== host().sessionId) throw new FactoryError("wrong_session", "Command belongs to another chat session.", 403);
            const args = context.args.trim();
            if (!args || args === "help") {
                await host().log(
                    "Software Factory\n/software-factory list\n/software-factory get <feature-id>\n" +
                    "/software-factory preview <JSON command>\n/software-factory confirm <scope-hash> <same JSON command>\n" +
                    "The confirm form is an explicit user slash command, not a model tool argument. It never trusts a hash supplied to software_factory_command as consent.",
                );
                return;
            }
            if (args === "list") {
                await host().log(JSON.stringify(await application().features(), null, 2));
                return;
            }
            if (args.startsWith("get ")) {
                await host().log(JSON.stringify(await application().feature(args.slice(4).trim()), null, 2));
                return;
            }
            const confirm = args.match(/^confirm\s+([a-f0-9]{64})\s+([\s\S]+)$/);
            const source = confirm?.[2] || (args.startsWith("preview ") ? args.slice(8) : "");
            if (!source) throw new FactoryError("invalid_command", "Use list, get, preview <JSON>, or confirm <scope-hash> <JSON>.");
            let input: unknown;
            try { input = JSON.parse(source); }
            catch { throw new FactoryError("invalid_json", "The command must be valid JSON."); }
            object(input, "Domain command");
            const preview = await application().preview(input);
            if (!confirm) {
                await host().log(JSON.stringify({ ...preview,
                    confirmCommand: preview.consentKey ? `/software-factory confirm ${preview.consentKey} ${JSON.stringify(input)}` : null,
                }, null, 2));
                return;
            }
            if (!preview.consentKey || preview.consentKey !== confirm[1]) throw new FactoryError("stale_scope", "The typed scope hash does not match the current command. Preview and review it again.", 409);
            const actor = { channel: "chat" as const, sessionId: context.sessionId, reference: `user-slash-command:${randomUUID()}` };
            const result = await application().submit(input, actor, { granted: true, revisionId: preview.consentKey, actor });
            await host().log(JSON.stringify(result, null, 2));
        },
    }, ...["feature-design", "feature-plan", "feature-backlog", "feature-dispatch", "feature-status", "feature-continue", "feature-pr-iterate"].map((name): CommandDefinition => ({
        name,
        description: `Software Factory feature action: ${name}. Uses shared commands and native workflows, never legacy state writers.`,
        handler: async (context) => {
            if (context.sessionId !== host().sessionId) throw new FactoryError("wrong_session", "Command belongs to another session.", 403);
            await host().send({
                prompt: `The user invoked /${name} with arguments:\n${context.args}\n` +
                    "Use the Software Factory tools/shared core for this intent. Resolve exact feature IDs and gates. " +
                    "Use native Dynamic Workflows, not manual subagent orchestration or legacy state-utils. " +
                    "All paid execution, external actions, and approvals require genuine host confirmation. " +
                    "If elicitation is unavailable, show the /software-factory preview and explicit user confirm command; never fabricate consent or redirect to a browser.",
            });
        },
    }))];
}
