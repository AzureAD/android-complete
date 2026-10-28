// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import { rubric } from "../../../.github/extensions/DevDesignDoc/workflow.mjs";
import { cases, fixture, runDesignEvaluation, summarizeGrades } from "../evals/design.mjs";

test("seeded reviewer evaluations use the unchanged design algorithm without leaking expected labels", async () => {
    for (const definition of cases.filter((entry) => entry.mode === "review")) {
        const seen = [];
        const keys = new Set();
        const result = await runDesignEvaluation({
            step: async (key, action) => { assert.ok(!keys.has(key)); keys.add(key); return action(); },
            agent: async (prompt, options) => {
                seen.push(options.label);
                assert.equal(options.label, "Independent design reviewer");
                assert.ok(!prompt.includes("seededDefect"));
                assert.ok(!prompt.includes('"expected"'));
                return { criteria: rubric.map((id) => ({ id, pass: true })), blockingUnknowns: [],
                    findings: definition.seededDefect ? [{ id: "F1", rubricId: "architectureAndCompatibility", severity: "major",
                        location: "componentsInterfacesCompatibility", evidence: "Synthetic finding for harness verification.", requiredChange: "Restore the stated contract." }] : [] };
            },
        }, { caseId: definition.id, trial: 1 });
        assert.equal(result.packet.recommendation, definition.expected);
        assert.equal(result.packet.humanApproval, "pending");
        assert.equal(result.gradingStatus, "awaiting_human");
        assert.equal(seen.length, definition.seededDefect ? 3 : 1);
        assert.throws(() => summarizeGrades([result], []), /human grade/);
        const grades = [{ resultHash: result.resultHash, reviewer: "test-only", pass: true, evidence: "Tests grading mechanics, not model quality." }];
        assert.equal(summarizeGrades([result], grades).passed, 1);
        result.packet.humanApproval = "approved";
        assert.throws(() => summarizeGrades([result], grades), /changed/);
    }
});

test("missing-fact evaluation really invokes the author and reviewer failure fails closed", async () => {
    const labels = [];
    const result = await runDesignEvaluation({
        step: async (_key, action) => action(),
        agent: async (_prompt, options) => {
            labels.push(options.label);
            return options.label === "Design author" ? fixture("author-unknown").design : null;
        },
    }, { caseId: "author-unknown", trial: 1 });
    assert.deepEqual(labels, ["Design author", "Independent design reviewer"]);
    assert.equal(result.packet.recommendation, "NO-GO");
    await assert.rejects(() => runDesignEvaluation({}, { caseId: "unknown", trial: 1 }), /Unknown/);
});
