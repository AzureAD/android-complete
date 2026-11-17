"""Step: `signoff_start` — start Authenticator Release Sign Off from pipeline 397224."""
from __future__ import annotations

from orchestrator.step_context import StepContext
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "signoff_start"
KIND = "scout"
NAME = 'Start Release Sign Off'
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = False
WRITE_COMMAND = "start-release-signoff"
STAGE_NAME = P.AUTH_SIGNOFF_STAGE_NAME
AUTOMATION_LABEL = "release-signoff"

MOCKABLE = release_stage.MOCKABLE
stage_started = release_stage.stage_started
stage_failed = release_stage.stage_failed


def resolve_target(context: StepContext):
    return release_stage.resolve_pinned_target(context, stage_name=STAGE_NAME)


def build(context: StepContext):
    return release_stage.build_pinned_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
    )

KNOWLEDGE = {'summary': 'Start the Authenticator Release Sign Off stage in pipeline 397224.',
 'what': 'Locates the Android Build Release run for this Authenticator release in msazure/One '
         "pipeline 397224 and starts the stage named 'Release Sign Off'. Today the matching "
         'run is selected by the Authenticator release branch; when pipeline 397224 is '
         'resource-linked to AndroidBuildBroker1ES, Scout prefers the run whose pipeline '
         'resource matches `state.pipeline_runs.final_auth.authenticator_build_id`.\n',
 'who': 'Scout performs the checked ADO stage-start write. It is not a human approval gate: '
        'the stage start runs through `start-release-signoff`, which recomputes the target, '
        'checkpoints the current plan hash and sends one Run-stage request.\n',
 'where': ['signoff pipeline: msazure/One Android Build Release definition 397224',
           'stage: Release Sign Off',
           "release branch: state.versions.authenticator ('release/YYYY/MM/DD')",
           'future resource match: state.pipeline_runs.final_auth.authenticator_build_id'],
 'how': 'Run step-action for rollout_start.signoff_start. If it returns the checked follow-up, '
        'run `start-release-signoff --release <id> --execute --auto-approve --executor '
        'release-signoff-automation`. The command binds the selected build id, stage identity '
        "and current stage state before setting the stage state to `pending` (ADO's Run-stage "
        'operation), then verifies the stage is queued/running. If no matching run or stage is '
        'found, the step blocks for release-owner investigation instead of starting a '
        'different run.\n',
 'consequences': 'A real WRITE to Azure DevOps: it starts the Release Sign Off stage on the '
                 'selected pipeline-397224 run. An uncertain provider response is recorded as '
                 'attention and must be inspected before retrying.\n',
 'faqs': [{'q': 'Why is pipeline 397224 involved if the final Authenticator build came from '
                '475778?',
           'a': 'Pipeline 475778 supplies the app build/version evidence. Pipeline 397224 is '
                'the Release Sign Off pipeline/stage. Once 397224 links to '
                'AndroidBuildBroker1ES as a resource, Scout matches the 397224 run back to the '
                'captured 475778 build id.'}]}
