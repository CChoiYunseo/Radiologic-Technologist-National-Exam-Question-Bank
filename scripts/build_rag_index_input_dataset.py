#!/usr/bin/env python3
"""Build the text-only RAG index input dataset from approved candidate records."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_READY_INDEX = (
    PROJECT_ROOT
    / "resources"
    / "extracted"
    / "rag_candidate_regroup"
    / "rag_ready_candidate_index.jsonl"
)
DEFAULT_OCR_CHUNKS = (
    PROJECT_ROOT
    / "resources"
    / "extracted"
    / "subject_references_ocr_full_incremental"
    / "ocr_chunks_all.jsonl"
)
DEFAULT_MANUAL_CHUNKS = (
    PROJECT_ROOT
    / "resources"
    / "extracted"
    / "subject_references_multimodal_full_incremental"
    / "manual_review_overrides"
    / "manual_chunks.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "__parse_error__": str(exc),
                        "__line_number__": line_number,
                        "__source_path__": str(path),
                    }
                )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def short_hash(text: str) -> str:
    return sha256_hex(text)[:16]


def source_name(row: dict[str, Any]) -> str:
    return row.get("source_file") or row.get("source_pdf") or ""


def page_number(row: dict[str, Any]) -> Any:
    return row.get("page_or_slide", row.get("page"))


def content_text(row: dict[str, Any]) -> str:
    return row.get("content") or row.get("text") or ""


def record_id(row: dict[str, Any]) -> str:
    return row.get("chunk_id") or row.get("id") or ""


def resolve_project_path(value: str | None) -> tuple[str | None, bool | None]:
    if not value:
        return None, None
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path), path.exists()


def load_chunk_lookup(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    lookup: dict[str, dict[str, Any]] = {}
    duplicate_ids: Counter[str] = Counter()
    for path in paths:
        for row in read_jsonl(path):
            if "__parse_error__" in row:
                continue
            rid = record_id(row)
            if not rid:
                continue
            if rid in lookup:
                duplicate_ids[rid] += 1
                continue
            lookup[rid] = row
    return lookup, dict(duplicate_ids)


def build_rag_row(candidate: dict[str, Any], chunk: dict[str, Any], created_at: str) -> dict[str, Any]:
    text = content_text(chunk).strip()
    source_path, source_path_exists = resolve_project_path(chunk.get("source_path") or candidate.get("source_path"))
    source_file = source_name(chunk) or candidate.get("source_file")
    page = page_number(chunk) if page_number(chunk) is not None else candidate.get("page_or_slide")
    source_chunk_id = record_id(chunk) or candidate.get("record_id")
    rag_input_id = f"rag_input_{short_hash(source_chunk_id + '|' + source_file + '|' + str(page))}"

    return {
        "rag_input_id": rag_input_id,
        "source_chunk_id": source_chunk_id,
        "document_id": chunk.get("document_id"),
        "source_file": source_file,
        "source_path": source_path,
        "source_path_exists": source_path_exists,
        "material_folder": chunk.get("material_folder"),
        "page_or_slide": page,
        "chunk_type": chunk.get("chunk_type", candidate.get("chunk_type", "text")),
        "content": text,
        "content_chars": len(text),
        "content_sha256": sha256_hex(text),
        "extraction_method": chunk.get("extraction_method"),
        "extraction_quality": chunk.get("extraction_quality", candidate.get("extraction_quality")),
        "confidence_score": chunk.get("confidence_score", candidate.get("confidence_score")),
        "needs_review": chunk.get("needs_review", False),
        "review_reason": chunk.get("review_reason", ""),
        "source_kind": candidate.get("source_kind"),
        "candidate_record_id": candidate.get("record_id"),
        "candidate_reasons": candidate.get("reasons", []),
        "candidate_rag_status": candidate.get("rag_status"),
        "approved_for_rag_evidence": True,
        "approved_for_generation": False,
        "rag_use_policy": "Evidence retrieval only. Do not copy source wording into generated questions, options, or explanations.",
        "copyright_use_policy": chunk.get(
            "copyright_use_policy",
            "Use as internal evidence only; generate all questions, options, and explanations in new wording.",
        ),
        "created_at": created_at,
    }


def validate_candidate(candidate: dict[str, Any], chunk: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    if candidate.get("rag_status") != "ready_for_rag_evidence":
        reasons.append("candidate_not_ready")
    if chunk is None:
        reasons.append("source_chunk_missing")
        return reasons

    text = content_text(chunk).strip()
    if not text:
        reasons.append("empty_content")
    if len(text) < 80:
        reasons.append("too_short")
    if chunk.get("chunk_type") not in (None, "text"):
        reasons.append("non_text_chunk")
    if chunk.get("needs_review") is True:
        reasons.append("chunk_needs_review")
    if chunk.get("extraction_quality") != "high":
        reasons.append("chunk_quality_not_high")
    if "법규" in source_name(chunk):
        reasons.append("legal_source_excluded")

    candidate_hash = candidate.get("content_sha256_16")
    if candidate_hash and candidate_hash != short_hash(text):
        reasons.append("candidate_content_hash_mismatch")

    source_path, source_path_exists = resolve_project_path(chunk.get("source_path") or candidate.get("source_path"))
    if source_path_exists is False:
        reasons.append("source_path_missing")

    if candidate.get("source_file") and candidate.get("source_file") != source_name(chunk):
        reasons.append("source_file_mismatch")
    candidate_page = candidate.get("page_or_slide")
    chunk_page = page_number(chunk)
    if candidate_page is not None and chunk_page is not None and int(candidate_page) != int(chunk_page):
        reasons.append("page_mismatch")

    return reasons


def build_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# RAG 인덱스 입력 데이터셋 생성 보고서",
        "",
        f"생성 시각: {report['created_at']}",
        "",
        "## 입력",
        f"- RAG ready 후보: {report['input_counts']['ready_candidates']}개",
        f"- OCR chunk lookup: {report['input_counts']['ocr_chunks']}개",
        f"- 수동 본문 chunk lookup: {report['input_counts']['manual_chunks']}개",
        "",
        "## 출력",
        f"- RAG 입력 chunk: {report['output_counts']['rag_index_input_rows']}개",
        f"- 제외 후보: {report['output_counts']['excluded_candidates']}개",
        f"- 중복 제거: {report['output_counts']['duplicate_candidates']}개",
        "",
        "## 품질/문서 분포",
    ]
    for source, count in report["source_file_counts"].items():
        lines.append(f"- {source}: {count}개")
    lines.extend(["", "## 제외 사유"])
    if report["exclude_reason_counts"]:
        for reason, count in report["exclude_reason_counts"].items():
            lines.append(f"- {reason}: {count}개")
    else:
        lines.append("- 없음")
    lines.extend(
        [
            "",
            "## 저장 파일",
            f"- RAG 입력 JSONL: {report['outputs']['rag_index_input_jsonl']}",
            f"- 제외 후보 JSONL: {report['outputs']['excluded_candidates_jsonl']}",
            f"- JSON 보고서: {report['outputs']['report_json']}",
            f"- Markdown 보고서: {report['outputs']['report_md']}",
            "",
            "## 사용 정책",
            "- 이 데이터셋은 RAG 근거 검색 입력용입니다.",
            "- 문제, 보기, 해설 문장을 원문에서 복사하거나 재서술하는 용도로 승인하지 않습니다.",
            "- 표, 수식, 그림, 법규성 자료, 검토 대기 자료는 입력에서 제외했습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready-index", type=Path, default=DEFAULT_READY_INDEX)
    parser.add_argument("--ocr-chunks", type=Path, default=DEFAULT_OCR_CHUNKS)
    parser.add_argument("--manual-chunks", type=Path, default=DEFAULT_MANUAL_CHUNKS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    created_at = datetime.now(timezone.utc).isoformat()
    ready_candidates = read_jsonl(args.ready_index)
    ocr_chunks = read_jsonl(args.ocr_chunks)
    manual_chunks = read_jsonl(args.manual_chunks)
    chunk_lookup, duplicate_source_chunk_ids = load_chunk_lookup([args.ocr_chunks, args.manual_chunks])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    rag_rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    duplicate_candidates = 0

    for candidate in ready_candidates:
        candidate_id = candidate.get("record_id")
        if not candidate_id:
            excluded.append(
                {
                    "record_id": None,
                    "source_file": candidate.get("source_file"),
                    "page_or_slide": candidate.get("page_or_slide"),
                    "exclude_reasons": ["missing_candidate_record_id"],
                }
            )
            continue
        if candidate_id in seen_candidate_ids:
            duplicate_candidates += 1
            excluded.append(
                {
                    "record_id": candidate_id,
                    "source_file": candidate.get("source_file"),
                    "page_or_slide": candidate.get("page_or_slide"),
                    "exclude_reasons": ["duplicate_candidate_record_id"],
                }
            )
            continue
        seen_candidate_ids.add(candidate_id)

        chunk = chunk_lookup.get(candidate_id)
        exclude_reasons = validate_candidate(candidate, chunk)
        if exclude_reasons:
            excluded.append(
                {
                    "record_id": candidate_id,
                    "source_file": candidate.get("source_file"),
                    "page_or_slide": candidate.get("page_or_slide"),
                    "source_kind": candidate.get("source_kind"),
                    "exclude_reasons": sorted(set(exclude_reasons)),
                }
            )
            continue
        if chunk is None:
            continue
        rag_rows.append(build_rag_row(candidate, chunk, created_at))

    rag_rows.sort(key=lambda row: (row.get("source_file") or "", int(row.get("page_or_slide") or 0), row["source_chunk_id"]))
    excluded.sort(key=lambda row: (row.get("source_file") or "", int(row.get("page_or_slide") or 0), row.get("record_id") or ""))

    rag_path = args.output_dir / "rag_index_input.jsonl"
    excluded_path = args.output_dir / "rag_index_input_excluded.jsonl"
    report_json_path = args.report_dir / "rag_index_input_report.json"
    report_md_path = args.report_dir / "rag_index_input_report.md"

    write_jsonl(rag_path, rag_rows)
    write_jsonl(excluded_path, excluded)

    report = {
        "version": 1,
        "created_at": created_at,
        "inputs": {
            "ready_index": str(args.ready_index),
            "ocr_chunks": str(args.ocr_chunks),
            "manual_chunks": str(args.manual_chunks),
        },
        "outputs": {
            "rag_index_input_jsonl": str(rag_path),
            "excluded_candidates_jsonl": str(excluded_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "input_counts": {
            "ready_candidates": len(ready_candidates),
            "ocr_chunks": len(ocr_chunks),
            "manual_chunks": len(manual_chunks),
            "chunk_lookup": len(chunk_lookup),
        },
        "output_counts": {
            "rag_index_input_rows": len(rag_rows),
            "excluded_candidates": len(excluded),
            "duplicate_candidates": duplicate_candidates,
        },
        "source_file_counts": dict(Counter(row["source_file"] for row in rag_rows)),
        "quality_counts": dict(Counter(row.get("extraction_quality") for row in rag_rows)),
        "source_kind_counts": dict(Counter(row.get("source_kind") for row in rag_rows)),
        "exclude_reason_counts": dict(Counter(reason for row in excluded for reason in row["exclude_reasons"])),
        "duplicate_source_chunk_ids": duplicate_source_chunk_ids,
        "integrity_checks": {
            "empty_content_rows": sum(1 for row in rag_rows if not row["content"].strip()),
            "source_path_missing_rows": sum(1 for row in rag_rows if row.get("source_path_exists") is False),
            "non_high_quality_rows": sum(1 for row in rag_rows if row.get("extraction_quality") != "high"),
            "needs_review_rows": sum(1 for row in rag_rows if row.get("needs_review") is True),
            "approved_for_generation_rows": sum(1 for row in rag_rows if row.get("approved_for_generation") is True),
            "legal_source_rows": sum(1 for row in rag_rows if "법규" in (row.get("source_file") or "")),
        },
        "policy": {
            "rag_use": "answer evidence retrieval only",
            "question_generation": "not approved by this dataset",
            "copyright": "do not copy source wording into generated questions/options/explanations",
        },
    }

    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_md_path.write_text(build_markdown_report(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "rag_index_input_rows": len(rag_rows),
                "excluded_candidates": len(excluded),
                "rag_index_input_jsonl": str(rag_path),
                "report_json": str(report_json_path),
                "report_md": str(report_md_path),
                "integrity_checks": report["integrity_checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
