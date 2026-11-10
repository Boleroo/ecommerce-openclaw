from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from evaluations.configurations import EvaluationConfiguration
from evaluations.schema import EvaluationCase, EvaluationTrace


ToolExecutor = Callable[[str, dict], dict]


class AgentAdapter(ABC):
    mode: str

    @abstractmethod
    def run(
        self, case: EvaluationCase, configuration: EvaluationConfiguration,
        execute_tool: ToolExecutor, runtime_context: dict | None = None,
    ) -> EvaluationTrace:
        raise NotImplementedError
