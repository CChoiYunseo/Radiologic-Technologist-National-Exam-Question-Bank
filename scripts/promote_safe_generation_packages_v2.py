#!/usr/bin/env python3
"""Promote Safe Generation Package v2 drafts to strict candidates conservatively."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRAFTS = PROJECT_ROOT / "resources" / "generated" / "knowledge_objects_v1" / "safe_generation_packages_v2_draft.jsonl"
DEFAULT_OBJECTS = PROJECT_ROOT / "resources" / "generated" / "knowledge_objects_v1" / "knowledge_objects_v1.jsonl"
DEFAULT_MAPPED_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "safe_generation_packages_v2"

HARD_HOLDS = {
    "empty_detail",
    "law_or_currentness",
    "numeric_or_formula_review",
    "learning_objective_scope_mismatch",
}
REVIEW_HOLDS = {
    "scope_uncertain",
    "visual_or_table_formula_review",
    "source_marked_hold_review",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mapped_generation_approvals(mapped_rag: Path) -> dict[str, bool]:
    approvals: dict[str, bool] = {}
    for row in read_jsonl(mapped_rag):
        rag_input_id = row.get("rag_input_id")
        if rag_input_id:
            approvals[str(rag_input_id)] = bool(row.get("approved_for_generation"))
    return approvals


def source_quality_counts(package: dict[str, Any], approvals: dict[str, bool]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for ref in package.get("source_chunk_refs") or []:
        if ref.get("is_generation_safe_candidate"):
            counts["generation_safe"] += 1
            if approvals.get(str(ref.get("rag_input_id") or "")):
                counts["generation_safe_and_approved"] += 1
            else:
                counts["generation_safe_but_not_rag_generation_approved"] += 1
        if ref.get("scope_mapping_confidence") == "high" and not ref.get("scope_mapping_needs_review"):
            counts["high_scope"] += 1
        if ref.get("scope_mapping_needs_review"):
            counts["needs_review"] += 1
    return counts


def evaluate_package(package: dict[str, Any], approvals: dict[str, bool]) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    holds = set(package.get("hold_reasons") or [])
    quality = source_quality_counts(package, approvals)

    if package.get("status") != "reviewable":
        reasons.append(f"draft_status_not_reviewable:{package.get('status')}")
    if not package.get("learning_objective"):
        reasons.append("missing_learning_objective")
    hard = sorted(holds & HARD_HOLDS)
    if hard:
        reasons.append("hard_holds:" + ",".join(hard))
    if quality["generation_safe"] < 2:
        reasons.append("generation_safe_refs_lt_2")
    if quality["generation_safe_and_approved"] < 2:
        reasons.append("generation_safe_rag_generation_approved_refs_lt_2")
    if quality["high_scope"] < 2:
        reasons.append("high_confidence_scope_refs_lt_2")
    if quality["needs_review"] > 0:
        reasons.append("source_refs_need_scope_review")

    review = sorted(holds & REVIEW_HOLDS)
    if review:
        warnings.append("review_holds:" + ",".join(review))

    if reasons:
        return "not_promoted", reasons, warnings
    if warnings:
        return "strict_candidate_with_review_notes", reasons, warnings
    return "strict_candidate", reasons, warnings


def promoted_payload(package: dict[str, Any], status: str, warnings: list[str]) -> dict[str, Any]:
    payload = dict(package)
    payload["promotion_status"] = status
    payload["promoted_at"] = now_iso()
    payload["strict_candidate_policy"] = {
        "question_generation_allowed": status in {"strict_candidate", "strict_candidate_with_review_notes"},
        "pilot_limit_per_scope": 5,
        "requires_harness": True,
        "requires_llm_secondary_validation": True,
        "requires_expert_review_before_release": True,
        "warnings": warnings,
    }
    return payload


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    packages = read_jsonl(args.drafts)
    objects = {row.get("object_id"): row for row in read_jsonl(args.objects)}
    approvals = mapped_generation_approvals(args.mapped_rag)
    promoted: list[dict[str, Any]] = []
    not_promoted: list[dict[str, Any]] = []

    for package in packages:
        status, reasons, warnings = evaluate_package(package, approvals)
        object_id = (package.get("knowledge_object_ids") or [""])[0]
        scope = package.get("scope") or {}
        record = {
            "package_id": package.get("package_id"),
            "knowledge_object_id": object_id,
            "scope": scope,
            "draft_status": package.get("status"),
            "promotion_status": status,
            "not_promoted_reasons": reasons,
            "promotion_warnings": warnings,
            "learning_objective": package.get("learning_objective"),
            "source_quality_counts": dict(source_quality_counts(package, approvals)),
            "hold_reasons": package.get("hold_reasons") or [],
            "concept_type": (objects.get(object_id) or {}).get("concept_type"),
            "source_text_included": False,
        }
        if status in {"strict_candidate", "strict_candidate_with_review_notes"}:
            promoted.append(promoted_payload(package, status, warnings))
        else:
            not_promoted.append(record)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "drafts": str(args.drafts),
            "objects": str(args.objects),
            "mapped_rag": str(args.mapped_rag),
        },
        "outputs": {
            "strict_candidates_jsonl": str(args.output_dir / "safe_generation_packages_v2_strict_candidates.jsonl"),
            "not_promoted_jsonl": str(args.output_dir / "safe_generation_packages_v2_not_promoted.jsonl"),
            "report_json": str(args.output_dir / "safe_generation_package_promotion_report.json"),
            "report_md": str(args.output_dir / "safe_generation_package_promotion_report.md"),
        },
        "counts": {
            "draft_packages": len(packages),
            "strict_candidates": len(promoted),
            "not_promoted": len(not_promoted),
            "promotion_status": dict(Counter([row.get("promotion_status") for row in not_promoted] + [row.get("promotion_status") for row in promoted])),
            "not_promoted_reasons": dict(Counter(reason for row in not_promoted for reason in row.get("not_promoted_reasons", []))),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
            "strict_candidates_are_pilot_only": True,
            "rag_input_approved_for_generation_required": True,
        },
    }
    return promoted, not_promoted, report


def write_markdown(path: Path, promoted: list[dict[str, Any]], not_promoted: list[dict[str, Any]], report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Safe Generation Package v2 Promotion Report",
        "",
        "이 보고서는 문제를 생성하지 않고, v2 초안을 파일럿 생성 후보로 승격할 수 있는지만 판정한다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Strict Candidates",
        "",
        "| package_id | status | subject | field | area | detail | objective | warnings |",
        "|---|---|---|---|---|---|---|---|",
    ])
    for row in promoted:
        scope = row.get("scope") or {}
        objective = (row.get("learning_objective") or {}).get("objective") or ""
        warnings = "; ".join(row.get("strict_candidate_policy", {}).get("warnings") or [])
        lines.append(
            "| {package_id} | {status} | {subject} | {field} | {area} | {detail} | {objective} | {warnings} |".format(
                package_id=row.get("package_id"),
                status=row.get("promotion_status"),
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                objective=objective,
                warnings=warnings,
            )
        )
    lines.extend([
        "",
        "## Top Not Promoted",
        "",
        "| package_id | subject | field | area | detail | reasons | holds |",
        "|---|---|---|---|---|---|---|",
    ])
    for row in not_promoted[:40]:
        scope = row.get("scope") or {}
        lines.append(
            "| {package_id} | {subject} | {field} | {area} | {detail} | {reasons} | {holds} |".format(
                package_id=row.get("package_id"),
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                reasons="; ".join(row.get("not_promoted_reasons") or []),
                holds=", ".join(row.get("hold_reasons") or []),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS)
    parser.add_argument("--objects", type=Path, default=DEFAULT_OBJECTS)
    parser.add_argument("--mapped-rag", type=Path, default=DEFAULT_MAPPED_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    promoted, not_promoted, report = build(args)
    write_jsonl(args.output_dir / "safe_generation_packages_v2_strict_candidates.jsonl", promoted)
    write_jsonl(args.output_dir / "safe_generation_packages_v2_not_promoted.jsonl", not_promoted)
    write_json(args.output_dir / "safe_generation_package_promotion_report.json", report)
    write_markdown(args.output_dir / "safe_generation_package_promotion_report.md", promoted, not_promoted, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
