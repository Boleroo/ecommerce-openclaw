from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from evaluations.adapters.base import AgentAdapter, ToolExecutor
from evaluations.configurations import EvaluationConfiguration
from evaluations.schema import EvaluationCase, EvaluationTrace, ToolEvent


class RealAgentDisabled(RuntimeError):
    pass


class TraceCaptureUnavailable(RuntimeError):
    pass


class OpenClawAgentAdapter(AgentAdapter):
    """Phase 6B adapter. It is deliberately double-gated and never used by default."""

    mode = "real"

    def __init__(self, *, allow_real_agent: bool = False, model: str | None = None,
                 max_turns: int = 1, state_dir: Path | None = None):
        if not allow_real_agent or os.environ.get("OPENCLAW_EVAL_ENABLE_REAL_AGENT") != "1":
            raise RealAgentDisabled(
                "Real-agent evaluation requires both --allow-real-agent and "
                "OPENCLAW_EVAL_ENABLE_REAL_AGENT=1"
            )
        if not model:
            raise RealAgentDisabled("Real-agent evaluation requires an explicit --model")
        state_dir = state_dir or (Path(os.environ["OPENCLAW_EVAL_STATE_DIR"]) if os.environ.get("OPENCLAW_EVAL_STATE_DIR") else None)
        config_path = Path(os.environ["OPENCLAW_CONFIG_PATH"]) if os.environ.get("OPENCLAW_CONFIG_PATH") else None
        if state_dir is None or config_path is None:
            raise RealAgentDisabled(
                "Real-agent evaluation requires explicit isolated OPENCLAW_EVAL_STATE_DIR "
                "and OPENCLAW_CONFIG_PATH values"
            )
        if max_turns != 1:
            raise ValueError(
                "OpenClaw 2026.7.1 exposes no provider-independent internal tool-loop cap; "
                "the Phase 6B adapter therefore permits exactly one outer Agent turn per case"
            )
        self.model = model
        self.max_turns = 1
        self.state_dir = state_dir
        self.config_path = config_path

    def run(self, case: EvaluationCase, configuration: EvaluationConfiguration,
            execute_tool: ToolExecutor, runtime_context: dict | None = None) -> EvaluationTrace:
        if not runtime_context or not runtime_context.get("dispatcher_url") or not runtime_context.get("agent_token"):
            raise RealAgentDisabled("Real-agent mode requires the runner's isolated per-case Dispatcher runtime")
        environment = os.environ.copy()
        environment.update({
            "OPENCLAW_STATE_DIR": str(self.state_dir),
            "OPENCLAW_CONFIG_PATH": str(self.config_path),
            "OPENCLAW_ECOMMERCE_DISPATCHER_URL": runtime_context["dispatcher_url"],
            "OPENCLAW_ECOMMERCE_API_TOKEN": runtime_context["agent_token"],
        })
        command = [
            "openclaw", "agent", "--local", "--json", "--agent", "main", "--model", self.model,
            "--session-key", f"agent:main:eval-{case.case_id}", "--message", case.user_input,
        ]
        completed = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise RuntimeError("OpenClaw real-agent command failed; inspect sanitized runner diagnostics")
        payload = json.loads(completed.stdout)
        raw_events = payload.get("toolCalls") or payload.get("tool_events")
        if not isinstance(raw_events, list):
            raise TraceCaptureUnavailable(
                "OpenClaw JSON did not expose reliable tool-call traces; do not infer or fabricate them"
            )
        events = [ToolEvent(
            tool=item["name"], arguments=item.get("arguments", {}),
            observation=item.get("result", {}), trace_id=item.get("trace_id"),
            decision_source="real_agent", business_execution="real",
        ) for item in raw_events]
        return EvaluationTrace(
            case_id=case.case_id, user_input=case.user_input,
            agent_response=payload.get("response", ""), tool_events=events,
            trace_ids=[event.trace_id for event in events if event.trace_id],
            execution_status="completed", errors=[], final_outcome={},
            mode="real", simulated_agent_decisions=False,
        )
