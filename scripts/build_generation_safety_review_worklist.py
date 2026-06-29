#!/usr/bin/env python3
"""Build a metadata-only worklist for generation-safety review.

The source RAG dataset is approved for answer-evidence retrieval, but not for
automatic question generation. This script identifies which text chunks look
eligible for human/semantic review before promotion. It never copies source
content to outputs and never changes the source RAG files.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "generation_safety_review"

HOLD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "law_or_currentness_review",
        re.compile(
            r"(제\s*\d+\s*조|제\d+조|별표|시행규칙|시행령|"
            r"의료법|의료기사\s*등에\s*관한\s*법률|진단용\s*방사선\s*발생장치.*안전관리|"
            r"질병관리청|식품의약품안전처|보건복지부)",
            re.IGNORECASE,
        ),
    ),
    (
        "visual_table_caption_review",
        re.compile(r"(\[?\s*(그림|표)\s*[0-9]+(\s*[-–—]\s*[0-9]+)?|\b(fig|table)\.?\s*[0-9]+)", re.IGNORECASE),
    ),
    (
        "formula_or_equation_review",
        re.compile(
            r"(수식|공식|방정식|"
            r"[A-Za-zηλμρσθ]\s*=|"
            r"\d+(?:\.\d+)?\s*[×x]\s*10\s*[-−^]?\s*\d+)",
            re.IGNORECASE,
        ),
    ),
    (
        "numeric_unit_review",
        re.compile(
            r"\b\d+(?:\.\d+)?\s*"
            r"(kV|mA|mAs|Gy|Sv|Bq|keV|MeV|MHz|mmHg|mGy|mSv|MBq|GBq|cm|mm|sec|초)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "figure_derived_numeric_review",
        re.compile(r"(\d+\s*(?:도|°)|세그먼트|segment|partial\s+영상|partial\s+image)", re.IGNORECASE),
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def hold_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    content = str(row.get("content") or "")
    if row.get("chunk_type") and row.get("chunk_type") != "text":
        reasons.append("non_text_chunk")
    if not row.get("approved_for_rag_evidence"):
        reasons.append("not_rag_evidence_approved")
    if row.get("extraction_quality") and row.get("extraction_quality") != "high":
        reasons.append("not_high_quality")
    if row.get("scope_mapping_needs_review"):
        reasons.append("scope_mapping_needs_review")
    if row.get("scope_mapping_confidence") not in {"high", "medium"}:
        reasons.append("low_scope_mapping_confidence")
    if not all(row.get(key) for key in ["mapped_subject", "mapped_field", "mapped_area", "mapped_detail"]):
        reasons.append("incomplete_scope")
    for reason, pattern in HOLD_PATTERNS:
        if pattern.search(content):
            reasons.append(reason)
    return sorted(set(reasons))


def scope_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("mapped_period") or ""),
        str(row.get("mapped_subject") or ""),
        str(row.get("mapped_field") or ""),
        str(row.get("mapped_area") or ""),
        str(row.get("mapped_detail") or ""),
    )


def metadata_record(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    period, subject, field, area, detail = scope_key(row)
    return {
        "rag_input_id": row.get("rag_input_id"),
        "source_chunk_id": row.get("source_chunk_id"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "page_or_slide": row.get("page_or_slide"),
        "content_sha256": row.get("content_sha256"),
        "scope": {
            "period": period,
            "subject": subject,
            "field": field,
            "area": area,
            "detail": detail,
            "scope_id": row.get("mapped_scope_id"),
        },
        "scope_mapping_status": row.get("scope_mapping_status"),
        "scope_mapping_confidence": row.get("scope_mapping_confidence"),
        "extraction_quality": row.get("extraction_quality"),
        "approved_for_rag_evidence": bool(row.get("approved_for_rag_evidence")),
        "current_approved_for_generation": bool(row.get("approved_for_generation")),
        "generation_safety_review_status": "candidate_for_review" if not reasons else "hold_for_review",
        "hold_reasons": reasons,
        "source_text_included": False,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    rows = read_jsonl(args.rag)
    chunk_candidates: list[dict[str, Any]] = []
    chunk_holds: list[dict[str, Any]] = []
    per_scope: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    reason_counter: Counter[str] = Counter()

    for row in rows:
        reasons = hold_reasons(row)
        record = metadata_record(row, reasons)
        if reasons:
            chunk_holds.append(record)
            reason_counter.update(reasons)
        else:
            chunk_candidates.append(record)

        key = scope_key(row)
        if key not in per_scope:
            period, subject, field, area, detail = key
            per_scope[key] = {
                "scope": {
                    "period": period,
                    "subject": subject,
                    "field": field,
                    "area": area,
                    "detail": detail,
                    "scope_id": row.get("mapped_scope_id"),
                },
                "total_chunks": 0,
                "candidate_chunks": 0,
                "hold_chunks": 0,
                "hold_reason_counts": Counter(),
                "candidate_refs": [],
            }
        summary = per_scope[key]
        summary["total_chunks"] += 1
        if reasons:
            summary["hold_chunks"] += 1
            summary["hold_reason_counts"].update(reasons)
        else:
            summary["candidate_chunks"] += 1
            if len(summary["candidate_refs"]) < args.max_refs_per_scope:
                summary["candidate_refs"].append(
                    {
                        "rag_input_id": row.get("rag_input_id"),
                        "source_file": row.get("source_file"),
                        "page_or_slide": row.get("page_or_slide"),
                        "scope_mapping_confidence": row.get("scope_mapping_confidence"),
                    }
                )

    scope_rows: list[dict[str, Any]] = []
    for summary in per_scope.values():
        summary = dict(summary)
        summary["hold_reason_counts"] = dict(summary["hold_reason_counts"])
        if summary["candidate_chunks"] >= args.min_refs_per_scope:
            summary["recommended_next_action"] = "semantic_review_then_promote_generation_safe_refs"
            summary["package_rebuild_priority"] = "high"
        elif summary["candidate_chunks"] > 0:
            summary["recommended_next_action"] = "find_or_split_more_clean_text_chunks"
            summary["package_rebuild_priority"] = "medium"
        else:
            summary["recommended_next_action"] = "manual_review_or_ocr_semantic_chunking_required"
            summary["package_rebuild_priority"] = "low"
        scope_rows.append(summary)

    scope_rows.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(item["package_rebuild_priority"], 3),
            -item["candidate_chunks"],
            item["scope"]["subject"],
            item["scope"]["field"],
            item["scope"]["area"],
            item["scope"]["detail"],
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = args.output_dir / "generation_safety_chunk_candidates.jsonl"
    holds_path = args.output_dir / "generation_safety_chunk_holds.jsonl"
    scope_path = args.output_dir / "generation_safety_scope_worklist.jsonl"
    report_json_path = args.output_dir / "generation_safety_review_report.json"
    report_md_path = args.output_dir / "generation_safety_review_report.md"

    write_jsonl(candidates_path, chunk_candidates)
    write_jsonl(holds_path, chunk_holds)
    write_jsonl(scope_path, scope_rows)

    priority_counts = Counter(row["package_rebuild_priority"] for row in scope_rows)
    report = {
        "created_at": now_iso(),
        "inputs": {"rag": str(args.rag)},
        "outputs": {
            "chunk_candidates": str(candidates_path),
            "chunk_holds": str(holds_path),
            "scope_worklist": str(scope_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": {
            "input_chunks": len(rows),
            "chunk_candidates": len(chunk_candidates),
            "chunk_holds": len(chunk_holds),
            "scopes": len(scope_rows),
            "scope_priority_counts": dict(priority_counts),
            "hold_reason_counts": dict(reason_counter),
        },
        "policy": {
            "source_text_included": False,
            "source_rag_files_modified": False,
            "automatic_generation_approval_granted": False,
            "min_refs_per_scope_for_package_rebuild": args.min_refs_per_scope,
        },
    }
    write_json(report_json_path, report)
    write_markdown(report_md_path, report, scope_rows)
    return report


def write_markdown(path: Path, report: dict[str, Any], scope_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Generation Safety Review Worklist",
        "",
        "이 보고서는 RAG 근거 chunk를 자동 문제 생성 근거로 승격할 수 있는지 검토하기 위한 메타데이터 목록이다.",
        "원문 텍스트는 포함하지 않는다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## High Priority Scopes",
            "",
            "| subject | field | area | detail | candidates | holds | action |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for row in scope_rows[:40]:
        scope = row["scope"]
        lines.append(
            "| {subject} | {field} | {area} | {detail} | {candidate_chunks} | {hold_chunks} | {action} |".format(
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                candidate_chunks=row["candidate_chunks"],
                hold_chunks=row["hold_chunks"],
                action=row["recommended_next_action"],
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-refs-per-scope", type=int, default=2)
    parser.add_argument("--max-refs-per-scope", type=int, default=12)
    args = parser.parse_args()
    report = build(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
