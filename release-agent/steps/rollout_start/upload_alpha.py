"""Step: `upload_alpha` — start Authenticator Upload Alpha stage."""
from __future__ import annotations

from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "upload_alpha"
KIND = "scout"
WRITE_COMMAND = "start-upload-alpha"
STAGE_NAME = P.AUTH_ALPHA_STAGE_NAME
AUTOMATION_LABEL = "upload-alpha"
PREVIOUS_STEP_ID = "upload_whats_new"
PREVIOUS_LABEL = "Upload What's New"

MOCKABLE = release_stage.MOCKABLE
stage_started = release_stage.stage_started
stage_failed = release_stage.stage_failed
resolve_target, build = release_stage.make_chained_stage_step(
    step_id=ID,
    write_command=WRITE_COMMAND,
    stage_name=STAGE_NAME,
    label=AUTOMATION_LABEL,
    previous_step_id=PREVIOUS_STEP_ID,
    previous_label=PREVIOUS_LABEL,
)
