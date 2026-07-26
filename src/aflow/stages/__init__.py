"""A-Flow workflow stages, executed in canonical order."""
from .intake import CanonicalIntake, create_intake
from .operation_setup import OperationSetup, setup_operation
from .preserve_plan import preserve_original_plan
from .evidence import RepositoryEvidence, collect_repository_evidence
from .gap_discovery import GapRecord, discover_gaps
from .gap_resolution import ResolutionRecord, resolve_gaps
from .evaluation import EvaluationResult, evaluate_plan
from .success_criteria import SuccessCriterion, derive_success_criteria
from .validation import ValidationSpec, develop_validations
from .fixtures import FixtureCase, develop_fixtures
from .companion import assemble_companion_package

# Helper for core.py
def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)

__all__ = [
    "CanonicalIntake",
    "OperationSetup",
    "RepositoryEvidence",
    "GapRecord",
    "ResolutionRecord",
    "EvaluationResult",
    "SuccessCriterion",
    "ValidationSpec",
    "FixtureCase",
    "create_intake",
    "setup_operation",
    "preserve_original_plan",
    "collect_repository_evidence",
    "discover_gaps",
    "resolve_gaps",
    "evaluate_plan",
    "derive_success_criteria",
    "develop_validations",
    "develop_fixtures",
    "assemble_companion_package",
    "_contains_any",
]
