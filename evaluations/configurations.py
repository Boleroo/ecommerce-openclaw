from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EvaluationConfiguration:
    name: Literal["baseline", "improved"]
    structured_observations: bool
    recoverable_error_correction: bool
    enhanced_tool_descriptions: bool
    max_recovery_turns: int
    backend_security_controls: bool = True


CONFIGURATIONS = {
    "baseline": EvaluationConfiguration("baseline", False, False, False, 0),
    "improved": EvaluationConfiguration("improved", True, True, True, 2),
}


def get_configuration(name: str) -> EvaluationConfiguration:
    try:
        return CONFIGURATIONS[name]
    except KeyError:
        raise ValueError(f"Unknown evaluation configuration: {name}") from None
