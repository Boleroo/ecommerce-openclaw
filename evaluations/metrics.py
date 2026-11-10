from __future__ import annotations

from collections import Counter, defaultdict


SECURITY_CATEGORIES = {"context_isolation", "prompt_injection", "approval_bypass", "cross_tenant"}


def _rate(passed: int, total: int) -> float | None:
    return round(100 * passed / total, 2) if total else None


def calculate_metrics(results: list[dict], *, mode: str, configuration: str) -> dict:
    statuses = Counter(item["status"] for item in results)
    by_category = defaultdict(list)
    for item in results:
        by_category[item["category"]].append(item)
    category_rates = {
        name: _rate(sum(row["status"] == "PASS" for row in rows), len(rows))
        for name, rows in sorted(by_category.items())
    }
    def dimension_rate(name):
        rows = [item for item in results if name in item["checks"]]
        return _rate(sum(item["checks"][name] for item in rows), len(rows))
    security = [item for item in results if item["category"] in SECURITY_CATEGORIES]
    errors = Counter(error for item in results for error in item.get("diagnostics", []))
    return {
        "mode": mode,
        "configuration": configuration,
        "performance_scope": "REAL_AGENT" if mode == "real" else "OFFLINE_INFRASTRUCTURE_ONLY",
        "total": len(results),
        "passed": statuses["PASS"], "failed": statuses["FAIL"],
        "skipped": statuses["SKIPPED"], "blocked": statuses["BLOCKED"],
        "requires_llm": sum(item["real_agent_status"] == "REQUIRES_LLM" for item in results),
        "per_category_pass_rate": category_rates,
        "tool_selection_accuracy": dimension_rate("tool_selection"),
        "argument_extraction_accuracy": dimension_rate("arguments"),
        "end_to_end_task_success_rate": _rate(statuses["PASS"], len(results)),
        "security_pass_rate": _rate(sum(item["status"] == "PASS" for item in security), len(security)),
        "recovery_success_rate": dimension_rate("recovery"),
        "error_distribution": dict(sorted(errors.items())),
    }


def compare_metrics(baseline: dict, improved: dict) -> dict:
    comparison = {}
    for metric in (
        "tool_selection_accuracy", "argument_extraction_accuracy",
        "end_to_end_task_success_rate", "security_pass_rate", "recovery_success_rate",
    ):
        before, after = baseline.get(metric), improved.get(metric)
        if before is None or after is None:
            comparison[metric] = {"percentage_points": None, "relative_improvement_percent": None}
        else:
            comparison[metric] = {
                "percentage_points": round(after - before, 2),
                "relative_improvement_percent": round((after - before) / before * 100, 2) if before else None,
            }
    return comparison
