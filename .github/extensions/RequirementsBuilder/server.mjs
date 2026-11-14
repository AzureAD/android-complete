// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { fields, steps, summary, exportMarkdown, applyUpdate, startAssistance, acceptProposal, assistancePrompt } from "./model.mjs";

export function viewState(draft) {
    return { draft, fields, steps, summary: summary(draft), markdown: exportMarkdown(draft) };
}

export async function startServer({ store, documentId, instanceId, send }) {
    const token = randomBytes(32).toString("hex");
    let origin;
    const server = createServer(async (request, response) => {
        const json = (status, result) => {
            response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
            response.end(JSON.stringify(result));
        };
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("Referrer-Policy", "no-referrer");
        response.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'");
        try {
            if (request.headers.host !== new URL(origin).host || (request.headers.origin && request.headers.origin !== origin)) return json(403, { error: "Untrusted request origin." });
            const url = new URL(request.url, origin);
            if (request.method === "GET" && ["/app.js", "/style.css"].includes(url.pathname)) {
                response.setHeader("Content-Type", url.pathname.endsWith(".js") ? "text/javascript" : "text/css");
                response.end(await readFile(new URL(`.${url.pathname}`, import.meta.url)));
                return;
            }
            if (request.method === "GET" && url.pathname === "/" && url.searchParams.get("token") === token) {
                response.setHeader("Content-Type", "text/html; charset=utf-8");
                response.end(await readFile(new URL("./index.html", import.meta.url)));
                return;
            }
            if (request.headers.authorization !== `Bearer ${token}`) return json(403, { error: "Invalid canvas authorization." });
            if (request.method === "GET" && url.pathname === "/state") return json(200, viewState(await store.read(documentId)));
            if (request.method !== "POST") return json(404, { error: "Unknown canvas route." });
            if (!(request.headers["content-type"] ?? "").startsWith("application/json")) return json(415, { error: "JSON is required." });
            const chunks = [];
            let size = 0;
            for await (const chunk of request) {
                size += chunk.length;
                if (size > 262144) return json(413, { error: "Request exceeds 256 KiB." });
                chunks.push(chunk);
            }
            const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
            if (url.pathname === "/export") return json(200, await store.export(documentId, input.revision));
            if (!["/update", "/accept", "/reject", "/assist", "/cancel"].includes(url.pathname)) return json(404, { error: "Unknown canvas route." });
            const draft = await store.mutate(documentId, input.revision, (state) => {
                switch (url.pathname) {
                    case "/update": return applyUpdate(state, input.change);
                    case "/accept": return acceptProposal(state, input.fieldId);
                    case "/reject":
                        if (!state.proposals[input.fieldId]) throw new Error("No proposal exists for this field.");
                        delete state.proposals[input.fieldId]; state.request = null; break;
                    case "/assist": return startAssistance(state, input.fieldId);
                    case "/cancel": state.request = null; break;
                }
            });
            if (url.pathname === "/assist") {
                try {
                    await send(assistancePrompt(draft, input.fieldId, instanceId));
                } catch (error) {
                    await store.mutate(documentId, draft.revision, (state) => {
                        state.request = { ...state.request, status: "failed" };
                    });
                    throw new Error(`AI request could not be submitted: ${error.message}`);
                }
            }
            json(200, viewState(draft));
        } catch (error) {
            json(400, { error: error.message });
        }
    });
    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
    });
    origin = `http://127.0.0.1:${server.address().port}`;
    return { server, origin, url: `${origin}/?token=${token}`, documentId };
}
