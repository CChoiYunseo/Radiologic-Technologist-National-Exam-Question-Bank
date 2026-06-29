#!/usr/bin/env python3
"""Analyze LLM secondary verdicts and turn revise reasons into package fixes.

The outputs contain verdict metadata and improvement labels only. They do not
copy textbook source text.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "resources/generated/folder_detail_llm_secondary_validation_run_20260629T014304Z_llm_first_check"
    / "run_20260629T015017Z_limit10_offset0"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/package_quality_feedback_v2"

CHECK_TO_ACTION = {
    "evidence_grounding": "review_or_replace_use_direct_refs",
    "answer_uniqueness": "narrow_answerable_point",
    "distractor_quality": "improve_distractor_points",
    "explanation_quality": "add_wrong_option_exclusion_plan",
    "korean_item_style": "tighten_exam_style_and_option_homogeneity",
    "hold_material_contamination": "move_to_hold_or_add_forbidden_points",
    "scope_alignment": "lower_objective_link_confidence",
    "learning_objective_alignment": "lower_objective_link_confidence",
    "copyright_risk": "rewrite_with_more_distance_from_source",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_no}")
            rows.append(value)
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def source_rebuild_id(package_id: str) -> str:
    prefix = "semantic_pilot_"
    marker = "_v"
    value = package_id
    if value.startswith(prefix):
        value = value[len(prefix) :]
    if marker in value:
        value = value.rsplit(marker, 1)[0]
    return value


def verdict_rows(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in [
        "llm_secondary_pass_index.jsonl",
        "llm_secondary_revise_index.jsonl",
        "llm_secondary_reject_index.jsonl",
        "llm_secondary_error_index.jsonl",
    ]:
        rows.extend(read_jsonl(run_dir / name))
    return rows


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows = verdict_rows(args.run_dir)
    reason_rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        package_id = str(row.get("package_id") or "")
        rebuild_id = source_rebuild_id(package_id)
        scope = {key: row.get(key) or "" for key in ["period", "subject", "field", "area", "detail", "scope_id"]}
        bucket = grouped.setdefault(
            rebuild_id,
            {
                "source_package_rebuild_id": rebuild_id,
                "scope": scope,
                "package_ids": [],
                "verdict_counts": Counter(),
                "check_counts": Counter(),
                "recommended_actions": Counter(),
                "evidence_grounding_reasons": [],
                "distractor_quality_reasons": [],
                "style_reasons": [],
                "hold_reasons": [],
            },
        )
        bucket["package_ids"].append(package_id)
        bucket["verdict_counts"][row.get("overall_verdict") or row.get("status") or "unknown"] += 1
        if row.get("overall_verdict") not in {"revise", "reject"}:
            continue

        result_path = Path(row.get("llm_validation_result") or "")
        result = read_json(result_path) if result_path.exists() else {}
        for check_id, check in (result.get("checks") or {}).items():
            if not isinstance(check, dict):
                continue
            verdict = check.get("verdict")
            if verdict == "pass":
                continue
            action = CHECK_TO_ACTION.get(check_id, "manual_review")
            reason = str(check.get("reason") or "")
            reason_row = {
                "source_package_rebuild_id": rebuild_id,
                "package_id": package_id,
                "validation_package_id": row.get("validation_package_id"),
                "overall_verdict": row.get("overall_verdict"),
                "check_id": check_id,
                "check_verdict": verdict,
                "recommended_action": action,
                "reason": reason,
                "scope": scope,
                "source_text_included": False,
            }
            reason_rows.append(reason_row)
            bucket["check_counts"][check_id] += 1
            bucket["recommended_actions"][action] += 1
            if check_id == "evidence_grounding":
                bucket["evidence_grounding_reasons"].append(reason)
            elif check_id == "distractor_quality":
                bucket["distractor_quality_reasons"].append(reason)
            elif check_id == "korean_item_style":
                bucket["style_reasons"].append(reason)
            elif check_id == "hold_material_contamination":
                bucket["hold_reasons"].append(reason)

    worklist: list[dict[str, Any]] = []
    for item in grouped.values():
        check_counts = item["check_counts"]
        actions = item["recommended_actions"]
        evidence_rework = check_counts.get("evidence_grounding", 0) > 0
        hold_risk = check_counts.get("hold_material_contamination", 0) > 0
        worklist.append(
            {
                "source_package_rebuild_id": item["source_package_rebuild_id"],
                "scope": item["scope"],
                "package_ids": sorted(set(item["package_ids"])),
                "verdict_counts": dict(item["verdict_counts"]),
                "check_counts": dict(check_counts),
                "recommended_actions": dict(actions),
                "package_quality_action": (
                    "hold_until_evidence_replaced"
                    if hold_risk
                    else "requires_use_direct_ref_review"
                    if evidence_rework
                    else "improve_planner_distractors_and_style"
                    if actions
                    else "keep_ready"
                ),
                "objective_link_confidence_delta": "lower" if check_counts.get("scope_alignment") or check_counts.get("learning_objective_alignment") else "keep",
                "use_direct_policy": "recheck_or_downgrade" if evidence_rework else "keep",
                "distractor_policy": "expand_or_replace" if check_counts.get("distractor_quality") else "keep",
                "forbidden_points_to_add": ["표·그림·수식·법규·수치 기준 의존 가능성 재검토"] if hold_risk else [],
                "source_text_included": False,
            }
        )

    report = {
        "created_at": now_iso(),
        "inputs": {"run_dir": str(args.run_dir)},
        "outputs": {
            "revise_reason_index": str(args.output_dir / "baseline_revise_reason_index.jsonl"),
            "package_improvement_worklist": str(args.output_dir / "package_improvement_worklist.jsonl"),
            "report_json": str(args.output_dir / "baseline_quality_report.json"),
            "report_md": str(args.output_dir / "baseline_quality_report.md"),
        },
        "counts": {
            "verdict_rows": len(rows),
            "reason_rows": len(reason_rows),
            "packages_with_feedback": len(worklist),
            "overall_verdicts": dict(Counter(row.get("overall_verdict") or row.get("status") for row in rows)),
            "checks": dict(Counter(row["check_id"] for row in reason_rows)),
            "recommended_actions": dict(Counter(row["recommended_action"] for row in reason_rows)),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
            "purpose": "Use reviewer feedback to improve Knowledge Objects and Safe Packages.",
        },
    }
    return reason_rows, worklist, report


def write_markdown(path: Path, report: dict[str, Any], worklist: list[dict[str, Any]]) -> None:
    lines = [
        "# Baseline Quality Report",
        "",
        "LLM 2차 검증 결과를 Safe Package 개선 작업으로 변환했다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Package Actions", "", "| package | scope | action | checks |", "|---|---|---|---|"])
    for row in worklist:
        scope = row.get("scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        lines.append(
            f"| {row.get('source_package_rebuild_id')} | {scope_label} | {row.get('package_quality_action')} | {row.get('check_counts')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    reason_rows, worklist, report = build(args)
    write_jsonl(args.output_dir / "baseline_revise_reason_index.jsonl", reason_rows)
    write_jsonl(args.output_dir / "package_improvement_worklist.jsonl", worklist)
    write_json(args.output_dir / "baseline_quality_report.json", report)
    write_markdown(args.output_dir / "baseline_quality_report.md", report, worklist)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
