"""Step: `upload_whats_new` — start Authenticator Upload What's New stage."""
from __future__ import annotations

from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "upload_whats_new"
KIND = "scout"
WRITE_COMMAND = "start-upload-whats-new"
STAGE_NAME = P.AUTH_WHATS_NEW_STAGE_NAME
AUTOMATION_LABEL = "upload-whats-new"
PREVIOUS_STEP_ID = "signoff_start"
PREVIOUS_LABEL = "Release Sign Off"

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
