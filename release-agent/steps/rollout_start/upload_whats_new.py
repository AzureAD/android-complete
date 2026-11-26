"""Step: `upload_whats_new` — start Authenticator Upload What's New stage."""
from __future__ import annotations

from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "upload_whats_new"
KIND = "scout"
NAME = "Start Upload What's New"
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = False
WRITE_COMMAND = "start-upload-whats-new"
STAGE_ID = P.AUTH_WHATS_NEW_STAGE_ID
STAGE_NAME = P.AUTH_WHATS_NEW_STAGE_NAME
AUTOMATION_LABEL = "upload-whats-new"
PREVIOUS_STEP_ID = "verify_alpha_uploaded"
PREVIOUS_LABEL = "Verify Alpha APK upload"

stage_started = release_stage.stage_started
stage_failed = release_stage.stage_failed
resolve_target, build = release_stage.make_chained_stage_step(
    step_id=ID,
    write_command=WRITE_COMMAND,
    stage_name=STAGE_NAME,
    stage_id=STAGE_ID,
    label=AUTOMATION_LABEL,
    previous_step_id=PREVIOUS_STEP_ID,
    previous_label=PREVIOUS_LABEL,
)

KNOWLEDGE = {'summary': "Start the Authenticator Upload What's New stage (Upload_Whats_New) in pipeline 397224.",
 'what': 'Locates the exact Android Build Release run verified by `verify_alpha_uploaded` and '
         "starts stage `Upload_Whats_New` ('Upload What's New') after `Upload_To_Alpha_GPS` "
         'has completed.\n',
 'who': 'Scout performs the checked ADO stage-start write. It is not a human approval gate: '
        '`start-upload-whats-new` recomputes the target, checkpoints the current plan hash and '
        'sends one Run-stage request.\n',
 'where': ['pipeline: msazure/One Android Build Release definition 397224',
           "stage_id: Upload_Whats_New (Upload What's New)",
           "release branch: state.versions.authenticator ('release/YYYY/MM/DD')"],
 'how': 'Run step-action for rollout_start.upload_whats_new. If it returns the checked '
        'follow-up, run `start-upload-whats-new --release <id> --execute --auto-approve '
        '--executor upload-whats-new-automation`. The command binds the selected build id, '
        'canonical stage identifier (`Upload_Whats_New`) and current stage state before setting '
        "the stage state to `pending` (ADO's Run-stage operation), then verifies the stage is "
        'queued/running.\n'}
