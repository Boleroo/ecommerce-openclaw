import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluations.adapters.openclaw import OpenClawAgentAdapter, RealAgentDisabled
from evaluations.bad_cases import failed_case_ids, write_bad_case
from evaluations.configurations import CONFIGURATIONS
from evaluations.graders import (
    grade_arguments, grade_case, grade_security, grade_tool_selection, normalize_money,
)
from evaluations.metrics import calculate_metrics, compare_metrics
from evaluations.runner import case_json_schema, load_cases, run_cases, select_cases, write_report
from evaluations.schema import EvaluationCase, EvaluationTrace, ReplayTrace, ToolEvent


@pytest.fixture(scope="module")
def phase6_cases():
    return load_cases()


def test_formal_schema_and_all_72_distinct_cases_load(phase6_cases):
    assert case_json_schema()["type"] == "object"
    checked_in_schema = json.loads((Path(__file__).parents[1] / "evaluations" / "case.schema.json").read_text())
    assert checked_in_schema == case_json_schema()
    counts = Counter(case.category for case in phase6_cases)
    assert len(phase6_cases) == 72
    assert counts == {
        "tool_selection": 12, "argument_extraction": 12, "context_isolation": 12,
        "prompt_injection": 12, "approval_bypass": 12, "cross_tenant": 12,
    }
    assert len({case.case_id for case in phase6_cases}) == 72
    assert len({case.user_input for case in phase6_cases}) == 72
    assert all(case.tenant_id == case.authenticated_user.tenant_id for case in phase6_cases)


def test_schema_rejects_untrusted_identity_mismatch(phase6_cases):
    payload = phase6_cases[0].model_dump(mode="json")
    payload["tenant_id"] = "merchant-forged"
    with pytest.raises(ValidationError, match="trusted authenticated_user"):
        EvaluationCase.model_validate(payload)


def trace(case, events):
    return EvaluationTrace(
        case_id=case.case_id, user_input=case.user_input, agent_response="test",
        tool_events=events, trace_ids=[], execution_status="completed", errors=[],
        final_outcome={}, mode="offline", simulated_agent_decisions=True,
    )


def event(tool, arguments, observation=None):
    return ToolEvent(
        tool=tool, arguments=arguments, observation=observation or {"success": True},
        decision_source="offline_replay", business_execution="simulated",
    )


def test_tool_selection_accepts_alternative_sequences_and_rejects_wrong_tool(phase6_cases):
    case = phase6_cases[0].model_copy(update={"expected_tools": [["get_order"], ["list_orders", "get_order"]]})
    assert grade_tool_selection(case, trace(case, [event("get_order", {})]))
    assert grade_tool_selection(case, trace(case, [event("list_orders", {}), event("get_order", {})]))
    assert not grade_tool_selection(case, trace(case, [event("inventory_sku", {})]))


@pytest.mark.parametrize("value,expected", [(100, 100), ("$100.00", 10000), ("¥313.95", 31395), ("$1,000.00", 100000)])
def test_monetary_normalization(value, expected):
    assert normalize_money(value) == expected


def test_argument_grader_ignores_optional_fields_but_rejects_wrong_amount(phase6_cases):
    case = next(case for case in phase6_cases if case.case_id == "argument_extraction-03")
    good = trace(case, [event("refund_order", {
        "order_id": "EVAL-A-ORDER", "amount": 10000, "reason": "damaged packaging",
        "idempotency_key": "extra-safe-key",
    })])
    bad = trace(case, [event("refund_order", {
        "order_id": "EVAL-A-ORDER", "amount": 100, "reason": "damaged packaging",
    })])
    assert grade_arguments(case, good)
    assert not grade_arguments(case, bad)


def test_blocked_forbidden_attempt_is_not_a_security_bypass(phase6_cases):
    case = phase6_cases[0].model_copy(update={"forbidden_operations": ["refund_order"]})
    blocked = trace(case, [event("refund_order", {}, {"success": False, "error": {"code": "PERMISSION_DENIED"}})])
    succeeded = trace(case, [event("refund_order", {}, {"success": True})])
    assert grade_security(case, blocked)
    assert not grade_security(case, succeeded)


def test_negative_trace_fails_multiple_graders(phase6_cases):
    case = next(case for case in phase6_cases if case.case_id == "tool_selection-01")
    wrong = trace(case, [event("inventory_summary", {})])
    grade = grade_case(case, wrong, {"counts": {"refunds": 0}, "orders": [], "products": []},
                       {"counts": {"refunds": 0}, "orders": [], "products": []})
    assert not grade.passed
    assert not grade.checks["tool_selection"]
    assert not grade.checks["arguments"]


def test_runner_uses_fresh_database_for_every_case(phase6_cases, tmp_path):
    case = next(case for case in phase6_cases if case.case_id == "approval_bypass-02")
    first = run_cases([case], report_dir=tmp_path / "first")
    second = run_cases([case], report_dir=tmp_path / "second")
    assert first["metrics"]["passed"] == second["metrics"]["passed"] == 1
    assert first["results"][0]["state_diff"] == second["results"][0]["state_diff"]
    assert first["run_id"] != second["run_id"]


def test_runner_and_machine_readable_reports(phase6_cases, tmp_path):
    selected = select_cases(phase6_cases, category="cross_tenant")[:2]
    report = run_cases(selected, configuration="baseline", report_dir=tmp_path)
    json_path, markdown_path = write_report(report, tmp_path)
    loaded = json.loads(json_path.read_text())
    assert loaded["metrics"]["performance_scope"] == "OFFLINE_INFRASTRUCTURE_ONLY"
    assert loaded["metrics"]["passed"] == 2
    assert "not real-model performance" in markdown_path.read_text()
    assert all(row["real_agent_status"] == "REQUIRES_LLM" for row in loaded["results"])


def test_all_72_offline_cases_execute_against_isolated_real_business_stacks(phase6_cases, tmp_path):
    report = run_cases(phase6_cases, configuration="improved", report_dir=tmp_path)
    assert report["metrics"]["total"] == report["metrics"]["passed"] == 72
    assert report["metrics"]["failed"] == 0
    assert report["metrics"]["requires_llm"] == 72


def test_runner_writes_failed_case_for_incorrect_replay(phase6_cases, tmp_path):
    original = next(case for case in phase6_cases if case.case_id == "tool_selection-01")
    broken = original.model_copy(update={
        "offline_trace": ReplayTrace(response="wrong", calls=[], status="completed", errors=[]),
    })
    report = run_cases([broken], report_dir=tmp_path)
    assert report["metrics"]["failed"] == 1
    bad_path = Path(report["bad_cases"])
    assert bad_path.exists()
    assert failed_case_ids(bad_path) == [original.case_id]


def test_bad_case_logging_sanitizes_and_supports_replay(tmp_path):
    path = tmp_path / "bad.jsonl"
    write_bad_case(path, {
        "case_id": "bad-01", "status": "FAIL", "token": "must-not-appear",
        "actual": {"authorization": "must-not-appear", "safe": "visible"},
    })
    text = path.read_text()
    assert "must-not-appear" not in text and "visible" in text
    assert failed_case_ids(path) == ["bad-01"]


def test_baseline_and_improved_are_separate_without_security_downgrade():
    baseline, improved = CONFIGURATIONS["baseline"], CONFIGURATIONS["improved"]
    assert baseline != improved
    assert baseline.backend_security_controls and improved.backend_security_controls
    assert baseline.max_recovery_turns == 0 and improved.max_recovery_turns == 2


def test_metrics_and_percentage_point_comparison():
    rows = [
        {"status": "PASS", "category": "tool_selection", "checks": {"tool_selection": True}, "diagnostics": [], "real_agent_status": "REQUIRES_LLM"},
        {"status": "FAIL", "category": "cross_tenant", "checks": {"security": False}, "diagnostics": ["security"], "real_agent_status": "REQUIRES_LLM"},
    ]
    baseline = calculate_metrics(rows, mode="offline", configuration="baseline")
    improved = dict(baseline, end_to_end_task_success_rate=75.0)
    comparison = compare_metrics(baseline, improved)
    assert baseline["end_to_end_task_success_rate"] == 50.0
    assert comparison["end_to_end_task_success_rate"] == {
        "percentage_points": 25.0, "relative_improvement_percent": 50.0,
    }


def test_real_agent_mode_is_double_gated_and_never_silently_selected(monkeypatch):
    monkeypatch.delenv("OPENCLAW_EVAL_ENABLE_REAL_AGENT", raising=False)
    with pytest.raises(RealAgentDisabled):
        OpenClawAgentAdapter(allow_real_agent=True, model="provider/model")
    monkeypatch.setenv("OPENCLAW_EVAL_ENABLE_REAL_AGENT", "1")
    with pytest.raises(RealAgentDisabled, match="explicit --model"):
        OpenClawAgentAdapter(allow_real_agent=True)
