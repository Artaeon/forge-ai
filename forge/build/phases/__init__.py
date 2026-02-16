"""Phase modules for the duo build pipeline.

Each phase is extracted into its own module for maintainability.
All functions accept a pipeline instance as the first argument.
"""

from forge.build.phases.dispatch import dispatch, dispatch_agentic, execute_with_spinner
from forge.build.phases.plan import run_plan
from forge.build.phases.code import run_code
from forge.build.phases.verify import run_verify
from forge.build.phases.review import run_review, run_fix

__all__ = [
    "dispatch",
    "dispatch_agentic",
    "execute_with_spinner",
    "run_plan",
    "run_code",
    "run_verify",
    "run_review",
    "run_fix",
]
