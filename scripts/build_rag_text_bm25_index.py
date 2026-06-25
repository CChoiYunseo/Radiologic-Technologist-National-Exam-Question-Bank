#!/usr/bin/env python3
"""Build a text-only BM25 RAG search index from mapped RAG input chunks."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"


TEXT_CHUNK_TYPES = {"text", "ocr_text", "body_text"}
READY_STATUS = "ready_for_rag_evidence"

HOLD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "hold_legal_or_statutory",
        re.compile(
            r"(제\s*\d+\s*조|제\d+조|별표|시행규칙|시행령|"
            r"의료법|모자보건법|지역보건법|원자력안전법|"
            r"의료기사\s*등에\s*관한\s*법률|"
            r"진단용\s*방사선\s*발생장치.*안전관리|"
            r"질병관리청|식품의약품안전처|보건복지부)",
            re.IGNORECASE,
        ),
    ),
    (
        "hold_visual_caption",
        re.compile(r"(\[?\s*(그림|표)\s*[0-9]+(\s*[-–—]\s*[0-9]+)?|\b(fig|table)\.?\s*[0-9]+)", re.IGNORECASE),
    ),
    (
        "hold_formula_or_equation",
        re.compile(
            r"(수식|공식|방정식|"
            r"[A-Za-zηλμρσθ]\s*=|"
            r"\d+(?:\.\d+)?\s*[×x]\s*10\s*[-−^]?\s*\d+)",
            re.IGNORECASE,
        ),
    ),
    (
        "hold_numeric_unit",
        re.compile(
            r"\b\d+(?:\.\d+)?\s*"
            r"(kV|mA|mAs|Gy|Sv|Bq|keV|MeV|MHz|mmHg|mGy|mSv|MBq|GBq)\b",
            re.IGNORECASE,
        ),
    ),
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def compact_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def inferred_period(subject: str) -> str:
    subject_key = compact_key(subject)
    if subject_key in {compact_key("방사선이론"), compact_key("의료법규")}:
        return "1교시"
    if subject_key == compact_key("방사선응용"):
        return "2교시"
    if subject_key == compact_key("실기시험"):
        return "3교시"
    return ""


def excerpt(content: str, limit: int = 360) -> str:
    text = compact_text(content)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def scope_text(row: dict[str, Any]) -> str:
    values = [
        row.get("mapped_subject"),
        row.get("mapped_field"),
        row.get("mapped_area"),
        row.get("mapped_detail"),
        row.get("source_file"),
    ]
    return " ".join(compact_text(value) for value in values if value)


def mapped_period(row: dict[str, Any]) -> str:
    selected_scope = row.get("selected_scope") if isinstance(row.get("selected_scope"), dict) else {}
    return (
        selected_scope.get("period")
        or row.get("mapped_period")
        or inferred_period(row.get("mapped_subject", ""))
    )


def include_row(row: dict[str, Any]) -> tuple[bool, str]:
    if not row.get("content"):
        return False, "empty_content"
    if row.get("approved_for_rag_evidence") is not True:
        return False, "not_approved_for_rag_evidence"
    if row.get("candidate_rag_status") != READY_STATUS:
        return False, "not_ready_for_rag_evidence"
    if row.get("approved_for_generation") is True:
        return False, "generation_approved_unexpected"
    if row.get("chunk_type") not in TEXT_CHUNK_TYPES:
        return False, "non_text_chunk_type"
    if row.get("extraction_quality") != "high":
        return False, "non_high_quality"
    if row.get("needs_review") is True:
        return False, "source_needs_review"
    if row.get("source_path_exists") is not True:
        return False, "source_path_missing"
    content = row.get("content", "")
    for reason, pattern in HOLD_PATTERNS:
        if pattern.search(content):
            return False, reason
    return True, "included"


def recreate_sqlite(db_path: Path, rows: list[dict[str, Any]], overwrite: bool) -> None:
    if db_path.exists():
        if not overwrite:
            raise FileExistsError(f"{db_path} already exists; pass --overwrite to replace it")
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE chunks (
                doc_id INTEGER PRIMARY KEY,
                rag_input_id TEXT NOT NULL UNIQUE,
                source_chunk_id TEXT,
                source_file TEXT NOT NULL,
                source_path TEXT NOT NULL,
                page_or_slide INTEGER,
                content_sha256 TEXT,
                content TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                mapped_period TEXT,
                mapped_subject TEXT,
                mapped_field TEXT,
                mapped_area TEXT,
                mapped_detail TEXT,
                mapped_scope_id TEXT,
                scope_mapping_status TEXT,
                scope_mapping_confidence TEXT,
                scope_mapping_needs_review INTEGER NOT NULL,
                extraction_quality TEXT,
                candidate_rag_status TEXT,
                approved_for_rag_evidence INTEGER NOT NULL,
                approved_for_generation INTEGER NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE rag_fts USING fts5(
                rag_input_id UNINDEXED,
                content,
                scope_text,
                tokenize='unicode61'
            )
            """
        )
        conn.execute("CREATE INDEX idx_chunks_source_page ON chunks(source_file, page_or_slide)")
        conn.execute("CREATE INDEX idx_chunks_scope ON chunks(mapped_period, mapped_subject, mapped_field, mapped_area, mapped_detail)")
        conn.execute("CREATE INDEX idx_chunks_mapping_status ON chunks(scope_mapping_status, scope_mapping_confidence)")

        for doc_id, row in enumerate(rows, start=1):
            metadata = {
                "rag_input_id": row.get("rag_input_id"),
                "source_chunk_id": row.get("source_chunk_id"),
                "source_file": row.get("source_file"),
                "source_path": row.get("source_path"),
                "page_or_slide": row.get("page_or_slide"),
                "content_sha256": row.get("content_sha256"),
                "document_id": row.get("document_id"),
                "material_folder": row.get("material_folder"),
                "source_kind": row.get("source_kind"),
                "chunk_type": row.get("chunk_type"),
                "extraction_method": row.get("extraction_method"),
                "extraction_quality": row.get("extraction_quality"),
                "candidate_rag_status": row.get("candidate_rag_status"),
                "candidate_reasons": row.get("candidate_reasons"),
                "rag_use_policy": row.get("rag_use_policy"),
                "copyright_use_policy": row.get("copyright_use_policy"),
                "mapped_period": mapped_period(row),
                "mapped_subject": row.get("mapped_subject"),
                "mapped_field": row.get("mapped_field"),
                "mapped_area": row.get("mapped_area"),
                "mapped_detail": row.get("mapped_detail"),
                "mapped_scope_id": row.get("mapped_scope_id"),
                "scope_mapping_status": row.get("scope_mapping_status"),
                "scope_mapping_confidence": row.get("scope_mapping_confidence"),
                "scope_mapping_needs_review": row.get("scope_mapping_needs_review"),
                "learning_objective_candidates": row.get("learning_objective_candidates"),
            }
            period = metadata["mapped_period"] or ""
            conn.execute(
                """
                INSERT INTO chunks (
                    doc_id, rag_input_id, source_chunk_id, source_file, source_path,
                    page_or_slide, content_sha256, content, excerpt,
                    mapped_period, mapped_subject, mapped_field, mapped_area, mapped_detail,
                    mapped_scope_id, scope_mapping_status, scope_mapping_confidence,
                    scope_mapping_needs_review, extraction_quality, candidate_rag_status,
                    approved_for_rag_evidence, approved_for_generation, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    row.get("rag_input_id", ""),
                    row.get("source_chunk_id", ""),
                    row.get("source_file", ""),
                    row.get("source_path", ""),
                    row.get("page_or_slide"),
                    row.get("content_sha256", ""),
                    row.get("content", ""),
                    excerpt(row.get("content", "")),
                    period,
                    row.get("mapped_subject", ""),
                    row.get("mapped_field", ""),
                    row.get("mapped_area", ""),
                    row.get("mapped_detail", ""),
                    row.get("mapped_scope_id", ""),
                    row.get("scope_mapping_status", ""),
                    row.get("scope_mapping_confidence", ""),
                    1 if row.get("scope_mapping_needs_review") else 0,
                    row.get("extraction_quality", ""),
                    row.get("candidate_rag_status", ""),
                    1 if row.get("approved_for_rag_evidence") else 0,
                    1 if row.get("approved_for_generation") else 0,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                "INSERT INTO rag_fts(rowid, rag_input_id, content, scope_text) VALUES (?, ?, ?, ?)",
                (doc_id, row.get("rag_input_id", ""), row.get("content", ""), scope_text(row)),
            )

        conn.execute("INSERT INTO rag_fts(rag_fts) VALUES ('optimize')")
        conn.commit()
    finally:
        conn.close()


def sqlite_integrity(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM rag_fts").fetchone()[0]
        non_text_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM chunks
            WHERE extraction_quality != 'high'
               OR candidate_rag_status != ?
               OR approved_for_rag_evidence != 1
               OR approved_for_generation != 0
            """,
            (READY_STATUS,),
        ).fetchone()[0]
        source_missing_rows = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source_path = '' OR source_path IS NULL"
        ).fetchone()[0]
        quick_query_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM rag_fts
            WHERE rag_fts MATCH ?
            """,
            ("방사선",),
        ).fetchone()[0]
        return {
            "chunk_count": chunk_count,
            "fts_count": fts_count,
            "policy_mismatch_rows": non_text_rows,
            "source_missing_rows": source_missing_rows,
            "quick_query_rows_for_방사선": quick_query_rows,
            "integrity_ok": chunk_count == fts_count and non_text_rows == 0 and source_missing_rows == 0,
        }
    finally:
        conn.close()


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 텍스트 기반 RAG BM25 인덱스 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 파일: `{report['inputs']['mapped_rag_input']}`",
        f"- 인덱스 DB: `{report['outputs']['sqlite_db']}`",
        f"- 입력 row: {report['counts']['input_rows']}",
        f"- 인덱싱 row: {report['counts']['indexed_rows']}",
        f"- 제외 row: {report['counts']['excluded_rows']}",
        "",
        "## 제외 사유",
    ]
    if report["exclude_reason_counts"]:
        for key, value in report["exclude_reason_counts"].items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- 없음")
    lines.extend(["", "## 매핑 신뢰도"])
    for key, value in report["scope_mapping_confidence_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 교시"])
    for key, value in report["period_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 정책 검증"])
    for key, value in report["integrity_checks"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## 주의",
            "- 이 인덱스는 텍스트 OCR chunk만 대상으로 합니다.",
            "- 표·수식·그림·시각자료 chunk는 입력 조건에서 제외됩니다.",
            "- 검색 결과는 RAG 근거 확인용이며 문제 생성 승인을 부여하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_report(
    input_path: Path,
    output_dir: Path,
    report_dir: Path,
    input_rows: list[dict[str, Any]],
    indexed_rows: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    db_path: Path,
) -> dict[str, Any]:
    report_json = report_dir / "rag_text_bm25_index_report.json"
    report_md = report_dir / "rag_text_bm25_index_report.md"
    manifest = output_dir / "manifest.json"
    integrity = sqlite_integrity(db_path)
    report = {
        "version": "2026-06-24",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "text_only_rag_search_index_bm25",
        "inputs": {
            "mapped_rag_input": str(input_path),
        },
        "outputs": {
            "sqlite_db": str(db_path),
            "manifest": str(manifest),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "input_rows": len(input_rows),
            "indexed_rows": len(indexed_rows),
            "excluded_rows": len(excluded),
        },
        "exclude_reason_counts": dict(Counter(item["reason"] for item in excluded)),
        "source_file_counts": dict(Counter(row.get("source_file", "") for row in indexed_rows)),
        "period_counts": dict(
            Counter(mapped_period(row) for row in indexed_rows)
        ),
        "subject_counts": dict(Counter(row.get("mapped_subject", "") for row in indexed_rows)),
        "scope_mapping_status_counts": dict(Counter(row.get("scope_mapping_status", "") for row in indexed_rows)),
        "scope_mapping_confidence_counts": dict(Counter(row.get("scope_mapping_confidence", "") for row in indexed_rows)),
        "chunk_type_counts": dict(Counter(row.get("chunk_type", "") for row in indexed_rows)),
        "quality_counts": dict(Counter(row.get("extraction_quality", "") for row in indexed_rows)),
        "integrity_checks": integrity,
        "policy": {
            "rag_use": "answer_evidence_only",
            "generation_approval": "not_granted",
            "index_type": "sqlite_fts5_bm25",
            "excluded_modalities": ["table", "formula", "figure", "diagram", "visual"],
        },
    }
    write_json(report_json, report)
    write_json(manifest, report)
    report_md.write_text(markdown_report(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing SQLite index")
    args = parser.parse_args()

    rows = read_jsonl(args.input)
    indexed_rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in rows:
        include, reason = include_row(row)
        if include:
            indexed_rows.append(row)
        else:
            excluded.append(
                {
                    "rag_input_id": row.get("rag_input_id"),
                    "source_file": row.get("source_file"),
                    "page_or_slide": row.get("page_or_slide"),
                    "reason": reason,
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.output_dir / "rag_text_bm25.sqlite"
    recreate_sqlite(db_path, indexed_rows, overwrite=args.overwrite)
    report = build_report(args.input, args.output_dir, args.report_dir, rows, indexed_rows, excluded, db_path)

    print(
        json.dumps(
            {
                "input_rows": report["counts"]["input_rows"],
                "indexed_rows": report["counts"]["indexed_rows"],
                "excluded_rows": report["counts"]["excluded_rows"],
                "sqlite_db": report["outputs"]["sqlite_db"],
                "integrity_ok": report["integrity_checks"]["integrity_ok"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
