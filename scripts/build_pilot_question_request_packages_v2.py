#!/usr/bin/env python3
"""Adapt strict Safe Generation Package v2 candidates into pilot request packages."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRICT = PROJECT_ROOT / "resources" / "generated" / "safe_generation_packages_v2" / "safe_generation_packages_v2_strict_candidates.jsonl"
DEFAULT_MAPPED_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "pilot_question_request_packages_v2"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
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


def period_for_subject(subject: str) -> str:
    if subject == "방사선이론":
        return "1교시"
    if subject == "방사선응용":
        return "2교시"
    return ""


def load_scope_ids(mapped_rag: Path) -> dict[tuple[str, str, str, str], str]:
    scope_ids: dict[tuple[str, str, str, str], str] = {}
    for row in read_jsonl(mapped_rag):
        key = (
            str(row.get("mapped_subject") or "").strip(),
            str(row.get("mapped_field") or "").strip(),
            str(row.get("mapped_area") or "").strip(),
            str(row.get("mapped_detail") or "").strip(),
        )
        scope_id = str(row.get("mapped_scope_id") or "").strip()
        if all(key) and scope_id and key not in scope_ids:
            scope_ids[key] = scope_id
    return scope_ids


def normalize_learning_objective(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    return {
        "learning_objective_id": item.get("learning_objective_id") or item.get("objective_id"),
        "objective_id": item.get("objective_id") or item.get("learning_objective_id"),
        "objective": item.get("objective"),
        "level": item.get("level"),
        "major_unit": item.get("major_unit"),
        "unit": item.get("unit"),
        "keywords": item.get("keywords") or [],
        "mapping_method": "safe_generation_package_v2_promotion",
        "recommended_question_types": ["개념형", "비교형"],
    }


def normalize_question_type(qtype: str) -> str:
    aliases = {
        "상황판단형": "검사절차형",
        "절차이해형": "검사절차형",
        "원리이해형": "개념형",
    }
    return aliases.get(qtype, qtype)


def evidence_refs(package: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    refs = []
    for ref in package.get("source_chunk_refs") or []:
        if not ref.get("is_generation_safe_candidate"):
            continue
        refs.append(
            {
                "rag_input_id": ref.get("rag_input_id"),
                "source_chunk_id": ref.get("source_chunk_id"),
                "source_file": ref.get("source_file"),
                "source_path": ref.get("source_path"),
                "page_or_slide": ref.get("page_or_slide"),
                "content_sha256": ref.get("content_sha256"),
                "scope_mapping_confidence": ref.get("scope_mapping_confidence"),
                "scope_mapping_status": ref.get("scope_mapping_status"),
                "scope_mapping_needs_review": ref.get("scope_mapping_needs_review"),
            }
        )
        if len(refs) >= limit:
            break
    return refs


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strict_rows = read_jsonl(args.strict_candidates)
    scope_ids = load_scope_ids(args.mapped_rag)
    packages: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in strict_rows:
        refs = evidence_refs(row, args.evidence_limit)
        scope = dict(row.get("scope") or {})
        scope.setdefault("period", period_for_subject(scope.get("subject", "")))
        if not scope.get("period"):
            scope["period"] = period_for_subject(scope.get("subject", ""))
        scope_id_key = (
            str(scope.get("subject") or "").strip(),
            str(scope.get("field") or "").strip(),
            str(scope.get("area") or "").strip(),
            str(scope.get("detail") or "").strip(),
        )
        scope.setdefault("scope_id", scope_ids.get(scope_id_key, ""))
        if not scope.get("scope_id"):
            skipped.append({"package_id": row.get("package_id"), "reason": "missing_scope_id"})
            continue
        if len(refs) < 2:
            skipped.append({"package_id": row.get("package_id"), "reason": "safe_evidence_refs_lt_2"})
            continue
        learning_objective = normalize_learning_objective(row.get("learning_objective"))
        allowed_qtypes = []
        for raw_qtype in row.get("allowed_question_types", ["개념형"]):
            qtype = normalize_question_type(raw_qtype)
            if qtype not in {"영상해석형", "계산형", "법규형"}:
                allowed_qtypes.append(qtype)
        allowed_qtypes = allowed_qtypes or ["개념형"]
        answerable_points = row.get("answerable_points") or []
        misconception_candidates = row.get("misconception_candidates") or []
        for variant_no in range(1, args.pilot_limit_per_scope + 1):
            focus = answerable_points[(variant_no - 1) % len(answerable_points)] if answerable_points else ""
            misconception = (
                misconception_candidates[(variant_no - 1) % len(misconception_candidates)]
                if misconception_candidates
                else ""
            )
            qtype = allowed_qtypes[(variant_no - 1) % len(allowed_qtypes)]
            package = {
                "package_id": f"pilot_{row.get('package_id')}_v{variant_no:02d}",
                "created_at": now_iso(),
                "mode": "pilot_generation_package_v2",
                "package_status": "ready_strict_pilot",
                "requested_scope": scope,
                "evidence_refs": refs,
                "min_evidence_count": 2,
                "recommended_generation_settings": {
                    "difficulty_candidates": row.get("difficulty_candidates") or ["하", "중"],
                    "question_type_candidates": [qtype],
                    "learning_objective_candidates": [learning_objective] if learning_objective else [],
                    "required_evidence_types": ["전공 근거 자료"],
                },
                "generation_constraints": {
                    "answer_evidence_index_for_validation_only": True,
                    "approved_for_generation": True,
                    "pilot_only": True,
                    "pilot_limit_per_scope": args.pilot_limit_per_scope,
                    "generation_candidate_index_required": True,
                    "must_not_copy_source_sentences": True,
                    "must_write_new_wording": True,
                    "rag_use": "answer_evidence_only",
                    "visual_table_formula_law_materials_excluded": True,
                },
                "generation_variant": {
                    "variant_no": variant_no,
                    "source_package_id": row.get("package_id"),
                    "promotion_status": row.get("promotion_status"),
                    "knowledge_object_ids": row.get("knowledge_object_ids"),
                    "focus_answerable_point": focus,
                    "focus_misconception": misconception,
                    "misconception_candidates": misconception_candidates,
                    "answerable_points": answerable_points,
                    "forbidden_points": row.get("forbidden_points") or [],
                },
                "rag_index_policy": {
                    "generation_policy": "pilot_uses_generation_safe_refs_only",
                    "generation_safe_filter_enabled": True,
                },
            }
            packages.append(package)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "strict_candidates": str(args.strict_candidates),
            "mapped_rag": str(args.mapped_rag),
        },
        "outputs": {
            "pilot_packages_jsonl": str(args.output_dir / "pilot_question_request_packages_ready.jsonl"),
            "skipped_jsonl": str(args.output_dir / "pilot_question_request_packages_skipped.jsonl"),
            "report_json": str(args.output_dir / "pilot_question_request_package_report.json"),
            "report_md": str(args.output_dir / "pilot_question_request_package_report.md"),
        },
        "counts": {
            "strict_candidates": len(strict_rows),
            "pilot_packages": len(packages),
            "skipped": len(skipped),
            "by_subject": dict(Counter((pkg.get("requested_scope") or {}).get("subject") for pkg in packages)),
        },
        "policy": {
            "question_generation_performed": False,
            "source_text_included": False,
            "pilot_limit_per_scope": args.pilot_limit_per_scope,
        },
    }
    return packages, skipped, report


def write_markdown(path: Path, packages: list[dict[str, Any]], skipped: list[dict[str, Any]], report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Pilot Question Request Package Report",
        "",
        "이 보고서는 strict 후보를 파일럿 문제 생성 요청 패키지로 변환한 결과이다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Pilot Packages",
        "",
        "| package_id | subject | field | area | detail | objective | evidence refs |",
        "|---|---|---|---|---|---|---:|",
    ])
    for pkg in packages:
        scope = pkg.get("requested_scope") or {}
        objective = ((pkg.get("recommended_generation_settings") or {}).get("learning_objective_candidates") or [{}])[0].get("objective") or ""
        lines.append(
            "| {package_id} | {subject} | {field} | {area} | {detail} | {objective} | {refs} |".format(
                package_id=pkg.get("package_id"),
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                objective=objective,
                refs=len(pkg.get("evidence_refs") or []),
            )
        )
    if skipped:
        lines.extend(["", "## Skipped", ""])
        for item in skipped:
            lines.append(f"- {item.get('package_id')}: {item.get('reason')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-candidates", type=Path, default=DEFAULT_STRICT)
    parser.add_argument("--mapped-rag", type=Path, default=DEFAULT_MAPPED_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--evidence-limit", type=int, default=4)
    parser.add_argument("--pilot-limit-per-scope", type=int, default=5)
    args = parser.parse_args()
    packages, skipped, report = build(args)
    write_jsonl(args.output_dir / "pilot_question_request_packages_ready.jsonl", packages)
    write_jsonl(args.output_dir / "pilot_question_request_packages_skipped.jsonl", skipped)
    write_json(args.output_dir / "pilot_question_request_package_report.json", report)
    write_markdown(args.output_dir / "pilot_question_request_package_report.md", packages, skipped, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
