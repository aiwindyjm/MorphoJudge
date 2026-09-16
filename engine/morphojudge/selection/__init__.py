"""Selection layer: deterministic file selection and coverage summary."""

from . import rules  # noqa: F401
from .rules import SelectionRules  # noqa: F401
from .service import decide_selection, plan_analysis_selection, preview_selection  # noqa: F401
