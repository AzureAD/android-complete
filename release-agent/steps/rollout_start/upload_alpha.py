"""Step: `upload_alpha` — start Authenticator Upload Alpha - Google Play stage (`Upload_To_Alpha_GPS`)."""
from __future__ import annotations

from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "upload_alpha"
KIND = "scout"
NAME = 'Start Upload Alpha - Google Play'
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = False
WRITE_COMMAND = "start-upload-alpha"
STAGE_ID = P.AUTH_ALPHA_STAGE_ID
STAGE_NAME = P.AUTH_ALPHA_STAGE_NAME
AUTOMATION_LABEL = "upload-alpha"
PREVIOUS_STEP_ID = "signoff_start"
PREVIOUS_LABEL = "Release Sign Off"

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

KNOWLEDGE = {'summary': 'Start the Authenticator Upload Alpha - Google Play stage (Upload_To_Alpha_GPS) in pipeline 397224.',
 'what': 'Locates the exact Android Build Release run recorded by Release Sign Off and starts '
         "stage `Upload_To_Alpha_GPS` ('Upload Alpha - Google Play'), which uploads the signed "
         'production APKs to the Google Play Alpha track (0% user fraction).\n',
 'who': 'Scout performs the checked ADO stage-start write. It is not a human approval gate: '
        '`start-upload-alpha` recomputes the target, checkpoints the current plan hash and '
        'sends one Run-stage request.\n',
 'where': ['pipeline: msazure/One Android Build Release definition 397224',
           'stage_id: Upload_To_Alpha_GPS (Upload Alpha - Google Play)',
           "release branch: state.versions.authenticator ('release/YYYY/MM/DD')"],
 'how': 'Run step-action for rollout_start.upload_alpha. If it returns the checked follow-up, '
        'run `start-upload-alpha --release <id> --execute --auto-approve --executor '
        'upload-alpha-automation`. The command binds the selected build id, canonical stage '
        "identifier (`Upload_To_Alpha_GPS`) and current stage state before setting the stage "
        "state to `pending` (ADO's Run-stage operation), then verifies the stage is queued/running.\n"}
