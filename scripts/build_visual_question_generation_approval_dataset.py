#!/usr/bin/env python3
"""Promote manually structured visual chunks into a safe generation dataset.

The script does not modify the original manual override files. It creates a
separate approval dataset for visual/table/formula question-generation
packages, with conservative hold reasons for items that still need expert or
vision-model review.
"""

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
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "resources"
    / "extracted"
    / "subject_references_multimodal_full_incremental"
    / "manual_review_overrides"
    / "manual_visual_chunks.jsonl"
)
DEFAULT_POLICY = PROJECT_ROOT / "resources" / "rules" / "visual_question_generation_approval_policy.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "visual_question_generation_approvals"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_no}")
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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def visual_kind(row: dict[str, Any]) -> str:
    return str(row.get("chunk_type") or row.get("visual_type") or "").strip()


def structured(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("structured_content")
    return value if isinstance(value, dict) else {}


def structured_field(row: dict[str, Any], key: str) -> Any:
    if key in row:
        return row.get(key)
    return structured(row).get(key)


def caption_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("title"),
        row.get("caption"),
        row.get("caption_or_nearby_context"),
        structured(row).get("caption"),
        structured(row).get("nearby_text_summary"),
        row.get("context_summary"),
        row.get("structure_summary"),
    ]
    return " ".join(text_of(part) for part in parts if text_of(part))


def has_any_structured_description(row: dict[str, Any]) -> bool:
    data = structured(row)
    if text_of(row.get("structure_summary")) or text_of(row.get("semantic_description")):
        return True
    for key in [
        "caption",
        "nearby_text_summary",
        "semantic_description",
        "formula_plain_text",
        "table_json",
        "variables",
        "embedded_text_candidates",
    ]:
        if key in data and data.get(key) not in (None, "", [], {}):
            return True
    return False


def has_required_fields(row: dict[str, Any], required: list[str]) -> bool:
    for key in required:
        value = structured_field(row, key)
        if value in (None, "", [], {}):
            return False
    return True


def contains_hold_keyword(row: dict[str, Any], keywords: list[str]) -> bool:
    haystack = caption_text(row)
    return any(re.search(re.escape(keyword), haystack, flags=re.IGNORECASE) for keyword in keywords)


def approval_decision(row: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, list[str], str, list[str]]:
    kind = visual_kind(row)
    reasons: list[str] = []
    allowed_modes: list[str] = []

    if not kind:
        reasons.append("missing_visual_kind")
    if not has_any_structured_description(row):
        reasons.append("missing_structured_visual_description")
    if truthy(row.get("contains_legal_or_current_standard")) or truthy(row.get("legal_currentness_check_required")):
        reasons.append("legal_or_current_standard_requires_review")
    if truthy(row.get("contains_numeric_or_formula_metric")) or truthy(row.get("numeric_formula_currentness_check_required")):
        reasons.append("numeric_formula_metric_requires_review")

    hold_keywords = policy.get("hold_keywords") or []
    if contains_hold_keyword(row, hold_keywords):
        reasons.append("standard_or_threshold_keyword_requires_review")

    if kind in set(policy.get("default_hold_modalities") or []):
        if not text_of(structured(row).get("semantic_description")):
            reasons.append("image_semantic_interpretation_not_approved")

    approved_modalities = policy.get("approved_modalities") or {}
    modality_policy = approved_modalities.get(kind)
    if not modality_policy:
        reasons.append("modality_not_auto_approved")
    else:
        required = modality_policy.get("required_structured_fields") or []
        if not has_required_fields(row, required):
            reasons.append("required_structured_fields_missing")
        allowed_modes = modality_policy.get("allowed_question_modes") or []

    if kind in {"diagram", "graph", "chart", "diagram_table"}:
        # A manually structured schematic can be used without reading the
        # original image pixels again, but pure photos stay held above.
        if text_of(structured(row).get("nearby_text_summary")):
            reasons = [reason for reason in reasons if reason != "image_semantic_interpretation_not_approved"]

    approved = len(reasons) == 0
    approval_status = (
        policy.get("approval_status", "pre_expert_visual_generation_approved")
        if approved
        else "held_for_additional_visual_or_expert_review"
    )
    return approved, reasons, approval_status, allowed_modes


def make_summary(row: dict[str, Any]) -> dict[str, Any]:
    data = structured(row)
    return {
        "caption": text_of(row.get("caption") or data.get("caption") or row.get("title")),
        "nearby_text_summary": text_of(data.get("nearby_text_summary") or row.get("context_summary") or row.get("nearby_text")),
        "semantic_description": text_of(row.get("semantic_description") or data.get("semantic_description")),
        "structure_summary": text_of(row.get("structure_summary")),
        "formula_plain_text": text_of(data.get("formula_plain_text")),
        "variables": data.get("variables") or {},
        "table_json": data.get("table_json") or [],
        "embedded_text_candidates": data.get("embedded_text_candidates") or row.get("embedded_text_candidates") or [],
        "visual_modality": data.get("visual_modality") or row.get("visual_type") or row.get("chunk_type") or "",
    }


def make_record(row: dict[str, Any], approved: bool, reasons: list[str], status: str, modes: list[str]) -> dict[str, Any]:
    source_id = row.get("chunk_id") or row.get("id") or short_hash(row)
    record_id = "vqga_" + short_hash({"source_id": source_id, "source_file": row.get("source_file"), "page": row.get("page_or_slide")})
    summary = make_summary(row)
    return {
        "approval_id": record_id,
        "source_visual_chunk_id": source_id,
        "approved_for_visual_question_generation": approved,
        "approval_status": status,
        "approval_reasons": ["manual_structured_visual_record_sufficient"] if approved else [],
        "hold_reasons": reasons,
        "allowed_question_modes": modes if approved else [],
        "visual_kind": visual_kind(row),
        "source_file": row.get("source_file") or row.get("source_pdf") or "",
        "source_path": row.get("source_path") or row.get("source_pdf_path") or "",
        "page_or_slide": row.get("page_or_slide") or row.get("page") or row.get("printed_page_number") or "",
        "linked_scope": row.get("linked_scope") or row.get("secondary_linked_scope") or {},
        "linked_learning_objective": row.get("linked_learning_objective") or {},
        "scope_link_confidence": row.get("scope_link_confidence") or "",
        "objective_link_confidence": row.get("objective_link_confidence") or "",
        "visual_evidence_summary": summary,
        "source_record_flags": {
            "needs_vision_model": truthy(row.get("needs_vision_model")) or truthy(row.get("requires_vision_model_review")),
            "requires_human_review": truthy(row.get("requires_human_review")),
            "contains_legal_or_current_standard": truthy(row.get("contains_legal_or_current_standard")),
            "legal_currentness_check_required": truthy(row.get("legal_currentness_check_required")),
            "contains_numeric_or_formula_metric": truthy(row.get("contains_numeric_or_formula_metric")),
            "numeric_formula_currentness_check_required": truthy(row.get("numeric_formula_currentness_check_required")),
        },
        "question_generation_policy": {
            "copy_source_visual": False,
            "copy_source_wording": False,
            "use_structured_description_only": True,
            "final_expert_approval": False,
        },
        "created_at": now_iso(),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 시각자료 문항 생성 승인 데이터셋 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 시각자료: {report['counts']['input_visual_chunks']}",
        f"- 승인: {report['counts']['approved']}",
        f"- 보류: {report['counts']['held']}",
        "",
        "## 승인 유형",
    ]
    for key, value in report["approved_kind_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 보류 사유"])
    for key, value in report["hold_reason_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## 산출물",
            f"- 승인 JSONL: `{report['outputs']['approved_jsonl']}`",
            f"- 보류 JSONL: `{report['outputs']['held_jsonl']}`",
            f"- 보고서 JSON: `{report['outputs']['report_json']}`",
            "",
            "## 주의",
            "- 이 데이터셋은 시각자료 기반 문항 생성용 사전 승인 데이터셋입니다.",
            "- 최종 문제은행 승인이나 전문가 검수 통과를 의미하지 않습니다.",
            "- 원본 이미지·표·수식 자체를 복제하지 않고 구조화 설명만 생성 근거로 사용합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    policy = read_json(args.policy)
    rows = read_jsonl(args.input)
    approved_rows: list[dict[str, Any]] = []
    held_rows: list[dict[str, Any]] = []

    for row in rows:
        approved, reasons, status, modes = approval_decision(row, policy)
        record = make_record(row, approved, reasons, status, modes)
        if approved:
            approved_rows.append(record)
        else:
            held_rows.append(record)

    approved_path = args.output_dir / "visual_question_generation_approved.jsonl"
    held_path = args.output_dir / "visual_question_generation_held.jsonl"
    report_json = args.output_dir / "visual_question_generation_approval_report.json"
    report_md = args.output_dir / "visual_question_generation_approval_report.md"

    write_jsonl(approved_path, approved_rows)
    write_jsonl(held_path, held_rows)

    report = {
        "version": "2026-06-25",
        "created_at": now_iso(),
        "inputs": {
            "manual_visual_chunks": str(args.input),
            "policy": str(args.policy),
        },
        "outputs": {
            "approved_jsonl": str(approved_path),
            "held_jsonl": str(held_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "input_visual_chunks": len(rows),
            "approved": len(approved_rows),
            "held": len(held_rows),
        },
        "approved_kind_counts": dict(Counter(row["visual_kind"] for row in approved_rows)),
        "held_kind_counts": dict(Counter(row["visual_kind"] for row in held_rows)),
        "hold_reason_counts": dict(Counter(reason for row in held_rows for reason in row["hold_reasons"])),
        "policy": {
            "final_expert_approval": False,
            "student_visible": False,
            "use_for_generation_packages": True,
            "use_source_visual_directly": False,
        },
    }
    write_json(report_json, report)
    write_text(report_md, markdown_report(report))
    print(json.dumps({"outputs": report["outputs"], "counts": report["counts"], "approved_kind_counts": report["approved_kind_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
