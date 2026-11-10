from __future__ import annotations

from evaluations.adapters.base import AgentAdapter, ToolExecutor
from evaluations.configurations import EvaluationConfiguration
from evaluations.schema import EvaluationCase, EvaluationTrace, ToolEvent


class OfflineReplayAdapter(AgentAdapter):
    """Replays deterministic Agent decisions; real calls use production infrastructure."""

    mode = "offline"

    def run(self, case: EvaluationCase, configuration: EvaluationConfiguration,
            execute_tool: ToolExecutor, runtime_context: dict | None = None) -> EvaluationTrace:
        events = []
        errors = list(case.offline_trace.errors)
        for call in case.offline_trace.calls:
            try:
                observation = execute_tool(call.tool, call.arguments) if call.execute else call.simulated_observation
            except Exception as error:  # normalized so one bad case does not abort a run
                observation = {"success": False, "error": {"code": "ADAPTER_ERROR", "message": str(error)}}
                errors.append(type(error).__name__)
            events.append(ToolEvent(
                tool=call.tool,
                arguments=call.arguments,
                observation=observation or {},
                trace_id=(observation or {}).get("trace_id"),
                decision_source="offline_replay",
                business_execution="real" if call.execute else "simulated",
            ))
        return EvaluationTrace(
            case_id=case.case_id,
            user_input=case.user_input,
            agent_response=case.offline_trace.response,
            tool_events=events,
            trace_ids=[event.trace_id for event in events if event.trace_id],
            execution_status=case.offline_trace.status,
            errors=errors,
            final_outcome={"configuration": configuration.name},
            mode="offline",
            simulated_agent_decisions=True,
        )
