from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Category = Literal[
    "tool_selection", "argument_extraction", "context_isolation",
    "prompt_injection", "approval_bypass", "cross_tenant",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AuthenticatedUser(StrictModel):
    identity: Literal["agent_a", "agent_b", "viewer_a", "admin_a"]
    user_id: str
    tenant_id: str
    role: Literal["viewer", "operator", "manager", "admin"]


class ExpectedArguments(StrictModel):
    tool: str
    arguments: dict[str, Any]
    relevant_only: bool = True


class ReplayCall(StrictModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    execute: bool = True
    simulated_observation: dict[str, Any] | None = None

    @model_validator(mode="after")
    def observation_for_simulation(self):
        if not self.execute and self.simulated_observation is None:
            raise ValueError("non-executed replay calls require simulated_observation")
        return self


class ReplayTrace(StrictModel):
    response: str
    calls: list[ReplayCall] = Field(default_factory=list)
    status: Literal["completed", "refused", "error"] = "completed"
    errors: list[str] = Field(default_factory=list)


class GradingRules(StrictModel):
    dimensions: list[Literal[
        "tool_selection", "arguments", "observation", "state", "security", "recovery"
    ]]
    requires_real_agent: bool = True
    state_unchanged: bool = False
    expected_refund_delta: int | None = None
    forbidden_attempt_is_failure: bool = False


class EvaluationCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    category: Category
    description: str = Field(min_length=10)
    user_input: str = Field(min_length=1)
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    authenticated_user: AuthenticatedUser
    tenant_id: str
    initial_database_state: dict[str, Any]
    expected_tools: list[list[str]]
    expected_arguments: list[ExpectedArguments] = Field(default_factory=list)
    expected_observation: dict[str, Any] = Field(default_factory=dict)
    expected_final_state: dict[str, Any] = Field(default_factory=dict)
    forbidden_operations: list[str] = Field(default_factory=list)
    grading_rules: GradingRules
    offline_trace: ReplayTrace
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def trusted_identity_is_consistent(self):
        if self.tenant_id != self.authenticated_user.tenant_id:
            raise ValueError("tenant_id must match trusted authenticated_user context")
        return self


class ToolEvent(StrictModel):
    tool: str
    arguments: dict[str, Any]
    observation: dict[str, Any]
    trace_id: str | None = None
    decision_source: Literal["offline_replay", "real_agent"]
    business_execution: Literal["real", "simulated"]


class EvaluationTrace(StrictModel):
    case_id: str
    user_input: str
    agent_response: str
    tool_events: list[ToolEvent]
    trace_ids: list[str]
    execution_status: Literal["completed", "refused", "error"]
    errors: list[str]
    final_outcome: dict[str, Any]
    mode: Literal["offline", "real"]
    simulated_agent_decisions: bool
