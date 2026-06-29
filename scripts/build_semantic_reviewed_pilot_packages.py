#!/usr/bin/env python3
"""Build pilot request packages from semantic-reviewed generation-safe refs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC_PASS = (
    PROJECT_ROOT
    / "resources/generated/generation_safety_semantic_review/run_20260626T030124Z/semantic_review_pass_package_candidates.jsonl"
)
DEFAULT_RAG = PROJECT_ROOT / "resources/extracted/rag_index_input/rag_index_input_mapped.jsonl"
DEFAULT_OBJECTIVES = PROJECT_ROOT / "resources/rules/learning_objectives.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/semantic_reviewed_pilot_question_packages/run_20260626T030124Z"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def short_hash(value: Any, length: int = 10) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def period_for_subject(subject: str) -> str:
    if subject == "방사선이론":
        return "1교시"
    if subject == "방사선응용":
        return "2교시"
    return ""


def objective_score(scope: dict[str, Any], objective: dict[str, Any]) -> int:
    score = 0
    detail = str(scope.get("detail") or "")
    area = str(scope.get("area") or "")
    field = str(scope.get("field") or "")
    haystack = " ".join(
        str(objective.get(key) or "")
        for key in ["major_unit", "field_hint", "unit", "learning_purpose", "objective", "raw_keyword"]
    )
    haystack += " " + " ".join(str(item) for item in objective.get("keywords") or [])
    if field and field in str(objective.get("field_hint") or ""):
        score += 2
    if area and area in str(objective.get("major_unit") or ""):
        score += 4
    if compact(detail) and compact(detail) == compact(objective.get("unit")):
        score += 8
    if detail and detail in haystack:
        score += 6
    if detail == "조영제" and "조영제" in str(objective.get("objective") or ""):
        score += 8
    if detail == "조영제" and any(term in str(objective.get("objective") or "") for term in ["소아", "노인", "아동", "이물질"]):
        score -= 10
    if detail == "공중보건총론" and "공중보건" in haystack:
        score += 4
    return score


def learning_objectives_for(scope: dict[str, Any], objectives: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored = [(objective_score(scope, objective), objective) for objective in objectives]
    selected = [objective for score, objective in sorted(scored, key=lambda item: (-item[0], item[1].get("objective_id", ""))) if score > 0]
    normalized: list[dict[str, Any]] = []
    for item in selected[:limit]:
        normalized.append(
            {
                "learning_objective_id": item.get("objective_id"),
                "objective_id": item.get("objective_id"),
                "objective": item.get("objective"),
                "level": item.get("level"),
                "major_unit": item.get("major_unit"),
                "unit": item.get("unit"),
                "keywords": item.get("keywords") or [],
                "mapping_method": "semantic_reviewed_scope_objective_score",
            }
        )
    return normalized


def normalized_pass_objective(pass_row: dict[str, Any]) -> dict[str, Any]:
    objective = pass_row.get("learning_objective") or {}
    if not isinstance(objective, dict) or not objective:
        return {}
    objective_id = objective.get("learning_objective_id") or objective.get("objective_id")
    return {
        "learning_objective_id": objective_id,
        "objective_id": objective_id,
        "objective": objective.get("objective"),
        "level": objective.get("level"),
        "major_unit": objective.get("major_unit"),
        "unit": objective.get("unit"),
        "keywords": objective.get("keywords") or [],
        "mapping_method": objective.get("mapping_method") or "semantic_pass_package_objective",
    }


def ensure_scope_id(scope: dict[str, Any]) -> str:
    if scope.get("scope_id"):
        return str(scope.get("scope_id"))
    basis = {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]}
    return f"folder_scope_{short_hash(basis)}"


def qtypes_for_scope(scope: dict[str, Any]) -> list[str]:
    if scope.get("detail") == "조영제":
        return ["개념형", "비교형"]
    return ["개념형"]


def build(args: argparse.Namespace) -> dict[str, Any]:
    semantic_pass_rows = read_jsonl(args.semantic_pass)
    rag_by_id = {str(row.get("rag_input_id")): row for row in read_jsonl(args.rag)}
    objectives = read_json(args.objectives).get("objectives") or []

    safe_rows: list[dict[str, Any]] = []
    answer_evidence_rows: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for pass_row in semantic_pass_rows:
        scope = dict(pass_row.get("scope") or {})
        scope["period"] = scope.get("period") or period_for_subject(scope.get("subject", ""))
        scope["scope_id"] = ensure_scope_id(scope)
        approved_refs = pass_row.get("approved_refs") or []
        pass_objective = normalized_pass_objective(pass_row)
        if pass_objective:
            objective_candidates = [pass_objective]
        else:
            objective_candidates = learning_objectives_for(scope, objectives, args.objective_limit)
        if len(approved_refs) < args.min_refs:
            skipped.append({"package_rebuild_id": pass_row.get("package_rebuild_id"), "reason": "approved_refs_lt_min"})
            continue
        if not objective_candidates:
            skipped.append({"package_rebuild_id": pass_row.get("package_rebuild_id"), "reason": "missing_learning_objective"})
            continue

        refs = []
        for approved in approved_refs:
            rag_input_id = str(approved.get("rag_input_id") or "")
            row = dict(rag_by_id.get(rag_input_id) or {})
            if not row.get("content"):
                continue
            row.update(
                {
                    "mapped_period": scope.get("period"),
                    "mapped_subject": scope.get("subject"),
                    "mapped_field": scope.get("field"),
                    "mapped_area": scope.get("area"),
                    "mapped_detail": scope.get("detail"),
                    "mapped_scope_id": scope.get("scope_id"),
                    "scope_mapping_status": "semantic_reviewed_detail_confirmed",
                    "scope_mapping_confidence": "high",
                    "scope_mapping_needs_review": False,
                    "needs_review": False,
                }
            )
            answer_row = dict(row)
            answer_row["approved_for_generation"] = False
            answer_row["candidate_rag_status"] = "ready_for_rag_evidence"
            answer_row["generation_review_status"] = "semantic_review_pass_answer_evidence_metadata"
            answer_row["generation_hold_reasons"] = []
            answer_evidence_rows.append(answer_row)

            row["approved_for_generation"] = True
            row["candidate_rag_status"] = "semantic_reviewed_generation_safe_pilot"
            row["generation_review_status"] = "semantic_review_pass"
            row["generation_review_source"] = str(args.semantic_pass)
            row["generation_hold_reasons"] = []
            safe_rows.append(row)
            refs.append(
                {
                    "rag_input_id": rag_input_id,
                    "source_chunk_id": row.get("source_chunk_id"),
                    "source_file": row.get("source_file"),
                    "source_path": row.get("source_path"),
                    "page_or_slide": row.get("page_or_slide"),
                    "content_sha256": row.get("content_sha256"),
                    "scope_mapping_confidence": row.get("scope_mapping_confidence"),
                    "scope_mapping_status": row.get("scope_mapping_status"),
                    "scope_mapping_needs_review": row.get("scope_mapping_needs_review"),
                }
            )
        if len(refs) < args.min_refs:
            skipped.append({"package_rebuild_id": pass_row.get("package_rebuild_id"), "reason": "source_rows_missing"})
            continue

        qtypes = qtypes_for_scope(scope)
        for variant_no in range(1, args.pilot_limit_per_scope + 1):
            objective = objective_candidates[(variant_no - 1) % len(objective_candidates)]
            qtype = qtypes[(variant_no - 1) % len(qtypes)]
            packages.append(
                {
                    "package_id": f"semantic_pilot_{pass_row.get('package_rebuild_id')}_v{variant_no:02d}",
                    "created_at": now_iso(),
                    "mode": "semantic_reviewed_pilot_generation_package",
                    "package_status": "ready_semantic_reviewed_pilot",
                    "requested_scope": scope,
                    "evidence_refs": refs[: args.max_refs],
                    "min_evidence_count": args.min_refs,
                    "recommended_generation_settings": {
                        "difficulty_candidates": ["하", "중"],
                        "question_type_candidates": [qtype],
                        "learning_objective_candidates": [objective],
                        "required_evidence_types": ["semantic-reviewed text evidence"],
                    },
                    "generation_constraints": {
                        "approved_for_generation": True,
                        "semantic_reviewed_generation_safe_pilot": True,
                        "requires_harness": True,
                        "requires_llm_secondary_validation": True,
                        "requires_expert_review_before_release": True,
                        "must_not_copy_source_sentences": True,
                        "must_write_new_wording": True,
                        "rag_use": "answer_evidence_only",
                        "visual_table_formula_law_materials_excluded": True,
                    },
                    "generation_variant": {
                        "variant_no": variant_no,
                        "source_package_rebuild_id": pass_row.get("package_rebuild_id"),
                        "semantic_review_status": pass_row.get("status"),
                    },
                    "rag_index_policy": {
                        "generation_policy": "semantic_reviewed_generation_safe_pilot_refs_only",
                        "generation_safe_filter_enabled": True,
                    },
                }
            )

    # Deduplicate safe rows by rag_input_id while keeping the first reviewed row.
    deduped_safe_rows: list[dict[str, Any]] = []
    deduped_answer_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in safe_rows:
        rag_input_id = str(row.get("rag_input_id") or "")
        if rag_input_id and rag_input_id not in seen:
            deduped_safe_rows.append(row)
            seen.add(rag_input_id)
    seen = set()
    for row in answer_evidence_rows:
        rag_input_id = str(row.get("rag_input_id") or "")
        if rag_input_id and rag_input_id not in seen:
            deduped_answer_rows.append(row)
            seen.add(rag_input_id)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    safe_dataset_path = args.output_dir / "semantic_reviewed_generation_safe_rag_input.jsonl"
    answer_dataset_path = args.output_dir / "semantic_reviewed_answer_evidence_rag_input.jsonl"
    packages_path = args.output_dir / "semantic_reviewed_pilot_question_request_packages.jsonl"
    skipped_path = args.output_dir / "semantic_reviewed_pilot_question_request_packages_skipped.jsonl"
    report_json_path = args.output_dir / "semantic_reviewed_pilot_package_report.json"
    report_md_path = args.output_dir / "semantic_reviewed_pilot_package_report.md"
    write_jsonl(safe_dataset_path, deduped_safe_rows)
    write_jsonl(answer_dataset_path, deduped_answer_rows)
    write_jsonl(packages_path, packages)
    write_jsonl(skipped_path, skipped)
    report = {
        "created_at": now_iso(),
        "inputs": {
            "semantic_pass": str(args.semantic_pass),
            "rag": str(args.rag),
            "objectives": str(args.objectives),
        },
        "outputs": {
            "semantic_reviewed_generation_safe_rag_input": str(safe_dataset_path),
            "semantic_reviewed_answer_evidence_rag_input": str(answer_dataset_path),
            "pilot_packages": str(packages_path),
            "skipped": str(skipped_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": {
            "semantic_pass_packages": len(semantic_pass_rows),
            "generation_safe_rows": len(deduped_safe_rows),
            "answer_evidence_rows": len(deduped_answer_rows),
            "pilot_packages": len(packages),
            "skipped": len(skipped),
            "by_subject": dict(Counter((pkg.get("requested_scope") or {}).get("subject") for pkg in packages)),
        },
        "policy": {
            "source_text_included_in_generation_safe_dataset": True,
            "source_text_included_in_package_report": False,
            "question_generation_performed": False,
            "expert_review_required_before_release": True,
        },
    }
    write_json(report_json_path, report)
    write_markdown(report_md_path, report, packages, skipped)
    return report


def write_markdown(path: Path, report: dict[str, Any], packages: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> None:
    lines = [
        "# Semantic Reviewed Pilot Package Report",
        "",
        "semantic review를 통과한 텍스트 근거만 별도 파일럿 패키지로 변환했다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Packages",
            "",
            "| package_id | subject | field | area | detail | question type | objective | refs |",
            "|---|---|---|---|---|---|---|---:|",
        ]
    )
    for pkg in packages:
        scope = pkg.get("requested_scope") or {}
        settings = pkg.get("recommended_generation_settings") or {}
        objective = (settings.get("learning_objective_candidates") or [{}])[0].get("objective") or ""
        qtype = (settings.get("question_type_candidates") or [""])[0]
        lines.append(
            "| {package_id} | {subject} | {field} | {area} | {detail} | {qtype} | {objective} | {refs} |".format(
                package_id=pkg.get("package_id"),
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                qtype=qtype,
                objective=objective,
                refs=len(pkg.get("evidence_refs") or []),
            )
        )
    if skipped:
        lines.extend(["", "## Skipped"])
        for item in skipped:
            lines.append(f"- {item.get('package_rebuild_id')}: {item.get('reason')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-pass", type=Path, default=DEFAULT_SEMANTIC_PASS)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--objectives", type=Path, default=DEFAULT_OBJECTIVES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-refs", type=int, default=2)
    parser.add_argument("--max-refs", type=int, default=4)
    parser.add_argument("--pilot-limit-per-scope", type=int, default=3)
    parser.add_argument("--objective-limit", type=int, default=6)
    args = parser.parse_args()
    report = build(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
