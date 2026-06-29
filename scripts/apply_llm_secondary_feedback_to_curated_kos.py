#!/usr/bin/env python3
"""Apply LLM secondary feedback to curated semantic KOs.

This produces a new KO view that separates validated-ready KOs from KOs that
need objective/scope/evidence rework. Source text is not copied into outputs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KOS = (
    PROJECT_ROOT
    / "resources/generated/knowledge_objects_v2_semantic_curated/knowledge_objects_v2_semantic_curated_ready.jsonl"
)
DEFAULT_REQUESTS = (
    PROJECT_ROOT
    / "resources/generated/question_generation_plans_v1_semantic_curated_style_v3/subsets/"
    / "planner_question_request_packages_curated_A3_B9_style_v3.jsonl"
)
DEFAULT_LLM_RUN = (
    PROJECT_ROOT
    / "resources/generated/llm_secondary_validation_planner_v1_semantic_curated_A3_B9_style_v3/"
    / "run_20260629T061251Z_limit12_offset0"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/knowledge_objects_v2_semantic_curated_feedback_v1"

HARD_REWORK_CHECKS = {
    "learning_objective_alignment",
    "scope_alignment",
    "evidence_grounding",
}
HARD_HOLD_CHECKS = {
    "hold_material_contamination",
}
SOFT_REWORK_CHECKS = {
    "distractor_quality",
    "explanation_quality",
    "korean_item_style",
    "copyright_risk",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def request_package_to_ko(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in read_jsonl(path):
        variant = row.get("generation_variant") or {}
        if row.get("package_id") and variant.get("source_knowledge_object_id"):
            mapping[str(row["package_id"])] = str(variant["source_knowledge_object_id"])
    return mapping


def feedback_by_ko(llm_run_dir: Path, package_to_ko: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    rows = read_jsonl(llm_run_dir / "llm_secondary_validation_results.jsonl")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        package_id = str(row.get("package_id") or "")
        ko_id = package_to_ko.get(package_id)
        if not ko_id:
            continue
        result_path = Path(row.get("llm_validation_result") or "")
        result = read_json(result_path) if result_path.exists() else {}
        failed_checks = {}
        for check_id, check in (result.get("checks") or {}).items():
            verdict = check.get("verdict")
            if verdict and verdict != "pass":
                failed_checks[check_id] = {
                    "verdict": verdict,
                    "reason": check.get("reason") or "",
                }
        grouped[ko_id].append(
            {
                "package_id": package_id,
                "overall_verdict": row.get("overall_verdict"),
                "revision_required": row.get("revision_required"),
                "failed_checks": failed_checks,
                "llm_validation_result": row.get("llm_validation_result"),
            }
        )
    return grouped


def classify_feedback(feedback_rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if not feedback_rows:
        return "untested_ready", ["not_in_latest_llm_secondary_probe"]
    verdicts = Counter(row.get("overall_verdict") for row in feedback_rows)
    failed = set()
    for row in feedback_rows:
        failed.update((row.get("failed_checks") or {}).keys())
    reasons: list[str] = []
    if verdicts.get("pass") and not failed:
        return "validated_ready", ["llm_secondary_pass"]
    if failed & HARD_HOLD_CHECKS:
        reasons.extend(f"hold_material:{check}" for check in sorted(failed & HARD_HOLD_CHECKS))
        return "hold", reasons
    if failed & HARD_REWORK_CHECKS:
        reasons.extend(f"hard_rework:{check}" for check in sorted(failed & HARD_REWORK_CHECKS))
        return "needs_rework", reasons
    if failed & SOFT_REWORK_CHECKS:
        reasons.extend(f"soft_rework:{check}" for check in sorted(failed & SOFT_REWORK_CHECKS))
        return "ready_prompt_rework", reasons
    if verdicts.get("revise") or verdicts.get("reject"):
        return "needs_rework", ["llm_secondary_revise_or_reject_without_check_detail"]
    return "validated_ready", ["llm_secondary_pass_or_no_failed_checks"]


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    kos = read_jsonl(args.knowledge_objects)
    package_to_ko = request_package_to_ko(args.requests)
    feedback = feedback_by_ko(args.llm_run_dir, package_to_ko)
    created_at = now_iso()
    rows: list[dict[str, Any]] = []

    for ko in kos:
        output = json.loads(json.dumps(ko, ensure_ascii=False))
        ko_id = str(output.get("knowledge_object_id") or "")
        status, reasons = classify_feedback(feedback.get(ko_id, []))
        output["llm_secondary_feedback_status"] = {
            "applied_at": created_at,
            "source_llm_run_dir": str(args.llm_run_dir),
            "status": status,
            "reasons": reasons,
            "feedback_rows": feedback.get(ko_id, []),
            "source_text_included": False,
        }
        if status in {"needs_rework", "hold"}:
            output["generation_readiness"] = status
            hold_reasons = list(output.get("hold_reasons") or [])
            hold_reasons.extend(f"llm_feedback:{reason}" for reason in reasons)
            output["hold_reasons"] = sorted(dict.fromkeys(hold_reasons))
        else:
            output["generation_readiness"] = "ready"
        rows.append(output)

    status_counts = Counter(row["llm_secondary_feedback_status"]["status"] for row in rows)
    scope_counts = Counter(
        f"{(row.get('scope') or {}).get('area')} / {(row.get('scope') or {}).get('detail')} / {row['llm_secondary_feedback_status']['status']}"
        for row in rows
    )
    report = {
        "created_at": created_at,
        "inputs": {
            "knowledge_objects": str(args.knowledge_objects),
            "requests": str(args.requests),
            "llm_run_dir": str(args.llm_run_dir),
        },
        "outputs": {
            "all": str(args.output_dir / "knowledge_objects_feedback_all.jsonl"),
            "generation_ready": str(args.output_dir / "knowledge_objects_feedback_generation_ready.jsonl"),
            "validated_ready": str(args.output_dir / "knowledge_objects_feedback_validated_ready.jsonl"),
            "prompt_rework_ready": str(args.output_dir / "knowledge_objects_feedback_prompt_rework_ready.jsonl"),
            "needs_rework": str(args.output_dir / "knowledge_objects_feedback_needs_rework.jsonl"),
            "hold": str(args.output_dir / "knowledge_objects_feedback_hold.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_feedback_report.json"),
        },
        "counts": {
            "input_kos": len(kos),
            "feedback_kos": len(feedback),
            "by_status": dict(status_counts),
            "by_scope_status": dict(scope_counts),
        },
        "policy": {
            "exclude_from_generation": ["needs_rework", "hold"],
            "hard_rework_checks": sorted(HARD_REWORK_CHECKS),
            "hard_hold_checks": sorted(HARD_HOLD_CHECKS),
            "source_text_included": False,
        },
    }
    return rows, feedback, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-objects", type=Path, default=DEFAULT_KOS)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--llm-run-dir", type=Path, default=DEFAULT_LLM_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    rows, _feedback, report = build(args)

    generation_ready = [
        row
        for row in rows
        if row["llm_secondary_feedback_status"]["status"]
        in {"validated_ready", "ready_prompt_rework", "untested_ready"}
    ]
    validated = [row for row in rows if row["llm_secondary_feedback_status"]["status"] == "validated_ready"]
    prompt_ready = [row for row in rows if row["llm_secondary_feedback_status"]["status"] == "ready_prompt_rework"]
    needs_rework = [row for row in rows if row["llm_secondary_feedback_status"]["status"] == "needs_rework"]
    hold = [row for row in rows if row["llm_secondary_feedback_status"]["status"] == "hold"]

    write_jsonl(args.output_dir / "knowledge_objects_feedback_all.jsonl", rows)
    write_jsonl(args.output_dir / "knowledge_objects_feedback_generation_ready.jsonl", generation_ready)
    write_jsonl(args.output_dir / "knowledge_objects_feedback_validated_ready.jsonl", validated)
    write_jsonl(args.output_dir / "knowledge_objects_feedback_prompt_rework_ready.jsonl", prompt_ready)
    write_jsonl(args.output_dir / "knowledge_objects_feedback_needs_rework.jsonl", needs_rework)
    write_jsonl(args.output_dir / "knowledge_objects_feedback_hold.jsonl", hold)
    write_json(args.output_dir / "knowledge_objects_feedback_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
