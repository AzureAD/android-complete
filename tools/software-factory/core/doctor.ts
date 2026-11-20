// Copyright (c) Microsoft Corporation. All rights reserved.

import { runtimeHealth } from "./runtime-health.ts";

export function runtimeReport(host?: { sessionId: string; elicitation: boolean }) {
    return {
        product: "Software Factory",
        version: "0.1.0",
        node: process.version,
        nodeSupported: Number(process.versions.node.split(".")[0]) >= 24,
        runtime: runtimeHealth(),
        implemented: ["feature_registry", "requirements_revisions", "requirements_approval", "chat_tools", "local_ui",
            "native_dynamic_workflows", "plain_language_intake", "draft_success_criteria", "conversational_progress", "guarded_design", "reviewed_planning", "approval_checkpoints",
            "backlog_reconciliation", "provider_dispatch", "monitoring", "native_run_controls", "legacy_import", "local_schedules"],
        workflowExecution: {
            status: "native_sdk",
            sessionOwnership: host ? "current_joined_chat_adopted_for_new_stage_runs" : "existing_feature_owner_or_application_owned_session",
            parentChatVisibility: "Joined-chat runs use native phases and completion notifications. Legacy owned runs remain separate; app discovery of dashboard-owned sessions is not yet verified.",
            creditLimit: "No default AI-credit cap. Only an explicitly user-requested ceiling applies; usage is still metered.",
            readiness: "Verified in the actual owning session before start/resume. Installed binaries alone do not prove workflow registration.",
            names: ["software-factory-intake", "software-factory-design", "software-factory-plan", "software-factory-implement", "software-factory-monitor", "software-factory-check", "software-factory-evaluate-design"],
            reason: "Run npm run check:runtime for a real zero-agent registration/journal check. Paid runs and provider actions require exact human approval and working credentials.",
        },
        chat: host ? {
            sessionId: host.sessionId,
            connected: true,
            confirmationSupported: host.elicitation,
            approvalStatus: host.elicitation ? "host_confirmation_available" : "blocked: host does not support elicitation",
        } : {
            connected: false,
            approvalStatus: "Run software_factory_doctor in app/CLI chat to inspect that host's confirmation support.",
        },
    };
}
