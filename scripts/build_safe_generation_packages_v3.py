#!/usr/bin/env python3
"""Promote ready Knowledge Objects v2 into Safe Generation Package v3."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBJECTS = PROJECT_ROOT / "resources/generated/knowledge_objects_v2/knowledge_objects_v2.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/safe_generation_packages_v3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def question_type_for(obj: dict[str, Any]) -> str:
    tag = str(obj.get("content_tag") or "")
    if "구분" in tag or "비교" in tag:
        return "비교형"
    return "개념형"


def difficulty_for(obj: dict[str, Any]) -> str:
    distractors = obj.get("distractor_points") or []
    return "중" if len(distractors) >= 4 else "하"


def evidence_refs(obj: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for ref in obj.get("use_direct_refs") or []:
        refs.append({"rag_input_id": ref.get("rag_input_id"), "evidence_role": "use_direct"})
    return refs


def evaluate(obj: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if obj.get("generation_readiness") != "ready":
        reasons.append(f"knowledge_object_not_ready:{obj.get('generation_readiness')}")
    if len(obj.get("use_direct_refs") or []) < 2:
        reasons.append("use_direct_refs_lt_2")
    if len(obj.get("distractor_points") or []) < 3:
        reasons.append("distractor_points_lt_3")
    if obj.get("objective_link_confidence") != "direct":
        reasons.append("objective_link_not_direct")
    pair_quality = obj.get("direct_evidence_pair_quality") or {}
    ready_policy = obj.get("ready_policy") or {}
    if pair_quality:
        if pair_quality.get("risk_reasons"):
            reasons.extend(f"direct_evidence_risk:{reason}" for reason in pair_quality.get("risk_reasons") or [])
        min_score = float(ready_policy.get("min_direct_evidence_score") or 9.0)
        if float(pair_quality.get("score_min") or 0) < min_score:
            reasons.append("direct_evidence_score_below_threshold")
        min_focus_hits = int(ready_policy.get("min_focus_term_hits") or 1)
        if int(pair_quality.get("focus_term_hits_min") or 0) < min_focus_hits:
            reasons.append("objective_focus_terms_not_found_in_each_ref")
        if ready_policy.get("require_shared_focus_term", True) and not pair_quality.get("shared_focus_terms"):
            reasons.append("direct_refs_do_not_share_objective_focus_term")
    else:
        reasons.append("missing_direct_evidence_pair_quality")
    objective = obj.get("learning_objective") or {}
    min_objective_score = float((obj.get("ready_policy") or {}).get("min_objective_score") or 10.0)
    if float(objective.get("score") or 0) < min_objective_score:
        reasons.append("objective_score_below_threshold")
    return ("hold" if reasons else "ready", reasons)


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    objects = read_jsonl(args.knowledge_objects)
    ready: list[dict[str, Any]] = []
    hold: list[dict[str, Any]] = []

    for obj in objects:
        status, reasons = evaluate(obj)
        scope = dict(obj.get("scope") or {})
        package = {
            "package_id": stable_id("sgp3", {"ko": obj.get("knowledge_object_id"), "scope": scope}),
            "created_at": now_iso(),
            "version": "v3",
            "status": status,
            "knowledge_object_id": obj.get("knowledge_object_id"),
            "source_package_rebuild_id": obj.get("source_package_rebuild_id"),
            "requested_scope": scope,
            "learning_objective": obj.get("learning_objective") or {},
            "answerable_point": obj.get("answerable_point") or "",
            "evidence_refs": evidence_refs(obj),
            "use_direct_refs": obj.get("use_direct_refs") or [],
            "use_supporting_refs": obj.get("use_supporting_refs") or [],
            "direct_evidence_pair_quality": obj.get("direct_evidence_pair_quality") or {},
            "ready_policy": obj.get("ready_policy") or {},
            "distractor_points": obj.get("distractor_points") or [],
            "forbidden_points": obj.get("forbidden_points") or [],
            "recommended_generation_settings": {
                "question_type": question_type_for(obj),
                "difficulty": difficulty_for(obj),
                "question_type_candidates": [question_type_for(obj)],
                "difficulty_candidates": [difficulty_for(obj)],
                "learning_objective_candidates": [obj.get("learning_objective") or {}],
                "required_evidence_types": ["knowledge_object_v2_use_direct_text_evidence"],
            },
            "generation_limits": {
                "default_questions_per_package": 1,
                "max_questions_per_package": min(max(1, args.max_questions_per_package), 2),
                "same_objective_same_evidence_max_questions": 2,
            },
            "generation_constraints": {
                "approved_for_generation": status == "ready",
                "must_not_copy_source_sentences": True,
                "must_write_new_wording": True,
                "rag_use": "answer_evidence_only",
                "text_only_1_2_period": True,
                "visual_table_formula_law_materials_excluded": True,
                "planner_required": True,
                "requires_harness": True,
                "requires_llm_secondary_validation": True,
                "requires_expert_review_before_release": True,
                "strict_direct_evidence_quality_required": True,
            },
            "hold_reasons": reasons,
            "source_text_included": False,
            "question_generation_performed": False,
        }
        if status == "ready":
            ready.append(package)
        else:
            hold.append(package)

    report = {
        "created_at": now_iso(),
        "inputs": {"knowledge_objects": str(args.knowledge_objects)},
        "outputs": {
            "ready": str(args.output_dir / "safe_generation_packages_v3_ready.jsonl"),
            "hold": str(args.output_dir / "safe_generation_packages_v3_hold.jsonl"),
            "report_json": str(args.output_dir / "safe_generation_packages_v3_report.json"),
            "report_md": str(args.output_dir / "safe_generation_packages_v3_report.md"),
        },
        "counts": {
            "knowledge_objects": len(objects),
            "ready": len(ready),
            "hold": len(hold),
            "hold_reasons": dict(Counter(reason for pkg in hold for reason in pkg.get("hold_reasons") or [])),
        },
        "policy": {
            "question_generation_performed": False,
            "package_default_questions": 1,
            "package_max_questions": min(max(1, args.max_questions_per_package), 2),
            "ready_requires_use_direct_refs_gte_2": True,
            "ready_requires_objective_link_direct": True,
            "ready_requires_direct_evidence_pair_quality": True,
        },
    }
    return ready, hold, report


def write_markdown(path: Path, report: dict[str, Any], ready: list[dict[str, Any]], hold: list[dict[str, Any]]) -> None:
    lines = ["# Safe Generation Packages v3 Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Ready Packages", "", "| package | scope | question type | difficulty | max q |", "|---|---|---|---|---:|"])
    for pkg in ready:
        scope = pkg.get("requested_scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        settings = pkg.get("recommended_generation_settings") or {}
        limits = pkg.get("generation_limits") or {}
        lines.append(f"| {pkg.get('package_id')} | {scope_label} | {settings.get('question_type')} | {settings.get('difficulty')} | {limits.get('max_questions_per_package')} |")
    lines.extend(["", "## Held Packages", "", "| package | scope | reasons |", "|---|---|---|"])
    for pkg in hold:
        scope = pkg.get("requested_scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        lines.append(f"| {pkg.get('package_id')} | {scope_label} | {', '.join(pkg.get('hold_reasons') or [])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-objects", type=Path, default=DEFAULT_OBJECTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-questions-per-package", type=int, default=1)
    args = parser.parse_args()
    ready, hold, report = build(args)
    write_jsonl(args.output_dir / "safe_generation_packages_v3_ready.jsonl", ready)
    write_jsonl(args.output_dir / "safe_generation_packages_v3_hold.jsonl", hold)
    write_json(args.output_dir / "safe_generation_packages_v3_report.json", report)
    write_markdown(args.output_dir / "safe_generation_packages_v3_report.md", report, ready, hold)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
