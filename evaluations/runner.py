from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from evaluations.adapters.offline import OfflineReplayAdapter
from evaluations.adapters.openclaw import OpenClawAgentAdapter
from evaluations.bad_cases import failed_case_ids, write_bad_case
from evaluations.configurations import CONFIGURATIONS, get_configuration
from evaluations.environment import CaseEnvironment
from evaluations.graders import grade_case
from evaluations.metrics import calculate_metrics, compare_metrics
from evaluations.schema import EvaluationCase


ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "cases"
CASE_LIST = TypeAdapter(list[EvaluationCase])


def case_json_schema() -> dict:
    return TypeAdapter(EvaluationCase).json_schema()


def load_cases(directory: Path = DEFAULT_CASES) -> list[EvaluationCase]:
    validator = Draft202012Validator(case_json_schema())
    cases = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path} must contain a JSON array")
        for index, item in enumerate(payload):
            errors = sorted(validator.iter_errors(item), key=lambda error: list(error.path))
            if errors:
                raise ValueError(f"{path}:{index}: {errors[0].message}")
        cases.extend(CASE_LIST.validate_python(payload))
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate evaluation case_id")
    return cases


def select_cases(cases, *, category=None, case_id=None, rerun=None):
    selected = cases
    if category:
        selected = [case for case in selected if case.category == category]
    if case_id:
        selected = [case for case in selected if case.case_id == case_id]
    if rerun:
        wanted = set(failed_case_ids(rerun))
        selected = [case for case in selected if case.case_id in wanted]
    if not selected:
        raise ValueError("No evaluation cases matched the selection")
    return selected


def _state_diff(initial, final):
    return {key: {"initial": initial.get(key), "final": final.get(key)}
            for key in sorted(set(initial) | set(final)) if initial.get(key) != final.get(key)}


def run_cases(cases: list[EvaluationCase], *, configuration: str = "improved",
              mode: str = "offline", report_dir: Path | None = None,
              allow_real_agent: bool = False, model: str | None = None,
              max_turns: int = 1) -> dict:
    config = get_configuration(configuration)
    adapter = (OfflineReplayAdapter() if mode == "offline" else OpenClawAgentAdapter(
        allow_real_agent=allow_real_agent, model=model, max_turns=max_turns,
    ))
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    report_dir = report_dir or Path("evaluation-results")
    bad_path = report_dir / f"{run_id}-bad-cases.jsonl"
    results = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"phase6-{case.case_id}-") as temporary:
            environment = CaseEnvironment(Path(temporary), case.authenticated_user.identity,
                                          case.initial_database_state)
            try:
                initial = environment.snapshot()
                trace = adapter.run(case, config, environment.execute, environment.runtime_context(mode))
                final = environment.snapshot()
            finally:
                environment.close()
        grade = grade_case(case, trace, initial, final)
        record = {
            "run_id": run_id, "timestamp": timestamp, "case_id": case.case_id,
            "category": case.category, "configuration": configuration, "mode": mode,
            "status": "PASS" if grade.passed else "FAIL",
            "real_agent_status": "REQUIRES_LLM" if mode == "offline" and case.grading_rules.requires_real_agent else "EVALUATED",
            "checks": grade.checks, "diagnostics": grade.diagnostics,
            "user_input": case.user_input, "expected": {
                "tools": case.expected_tools,
                "arguments": [item.model_dump(mode="json") for item in case.expected_arguments],
                "observation": case.expected_observation,
                "final_state": case.expected_final_state,
            },
            "actual": trace.model_dump(mode="json"),
            "state_diff": _state_diff(initial, final),
        }
        results.append(record)
        if not grade.passed:
            write_bad_case(bad_path, record)
    metrics = calculate_metrics(results, mode=mode, configuration=configuration)
    return {
        "schema_version": 1, "run_id": run_id, "timestamp": timestamp,
        "mode": mode, "configuration": config.__dict__,
        "metrics": metrics, "results": results,
        "bad_cases": str(bad_path) if bad_path.exists() else None,
    }


def write_report(report: dict, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{report['run_id']}-{report['mode']}-{report['configuration']['name']}"
    json_path, markdown_path = directory / f"{stem}.json", directory / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metrics = report["metrics"]
    lines = [
        f"# Evaluation {report['run_id']}", "",
        f"- Mode: **{report['mode']}**",
        f"- Configuration: **{report['configuration']['name']}**",
        f"- Scope: **{metrics['performance_scope']}**",
        f"- Total: {metrics['total']}",
        f"- Passed / failed: {metrics['passed']} / {metrics['failed']}",
        f"- Real-agent verification required: {metrics['requires_llm']}", "",
        "Offline replay results validate infrastructure and are not real-model performance measurements.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def build_parser():
    parser = argparse.ArgumentParser(description="Phase 6 Agent evaluation runner")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline")
    parser.add_argument("--configuration", choices=["baseline", "improved", "both"], default="improved")
    parser.add_argument("--category", choices=[
        "tool_selection", "argument_extraction", "context_isolation",
        "prompt_injection", "approval_bypass", "cross_tenant",
    ])
    parser.add_argument("--case")
    parser.add_argument("--rerun-failed", type=Path)
    parser.add_argument("--report-dir", type=Path, default=Path("evaluation-results"))
    parser.add_argument("--allow-real-agent", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=1)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    cases = select_cases(load_cases(), category=args.category, case_id=args.case, rerun=args.rerun_failed)
    names = list(CONFIGURATIONS) if args.configuration == "both" else [args.configuration]
    reports = []
    for name in names:
        report = run_cases(
            cases, configuration=name, mode=args.mode, report_dir=args.report_dir,
            allow_real_agent=args.allow_real_agent, model=args.model, max_turns=args.max_turns,
        )
        paths = write_report(report, args.report_dir)
        reports.append(report)
        print(f"{name}: {report['metrics']['passed']}/{report['metrics']['total']} offline checks passed; {paths[0]}")
    if len(reports) == 2:
        comparison = {
            "scope": "REAL_AGENT" if args.mode == "real" else "OFFLINE_INFRASTRUCTURE_ONLY",
            "baseline_run_id": reports[0]["run_id"], "improved_run_id": reports[1]["run_id"],
            "metrics": compare_metrics(reports[0]["metrics"], reports[1]["metrics"]),
        }
        path = args.report_dir / f"comparison-{reports[0]['run_id']}-{reports[1]['run_id']}.json"
        path.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"comparison: {path}")


if __name__ == "__main__":
    main()
