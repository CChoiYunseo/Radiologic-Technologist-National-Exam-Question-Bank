#!/usr/bin/env python3
"""Build a unified review index and LLM second-pass validation packages.

This script consolidates generated draft items that already passed the
rule-based harness. It does not approve items and does not write to the final
question bank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ORIGINAL_PASSED = (
    PROJECT_ROOT
    / "resources/generated/question_drafts/batch_20260624T064123Z_limit32_offset0/passed_drafts_index.jsonl"
)
DEFAULT_BACKFILLED_PASSED = (
    PROJECT_ROOT
    / "resources/generated/question_drafts/batch_20260624T070615Z_limit10_offset0/passed_drafts_index.jsonl"
)
DEFAULT_DB = PROJECT_ROOT / "resources/extracted/rag_search_index_text_bm25/rag_text_bm25.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/review_candidates"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_no}")
            rows.append(data)
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def short_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def truncate_text(value: str, limit: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def load_passed_rows(paths: list[Path]) -> tuple[list[dict[str, Any]], int]:
    """Load passed rows and dedupe by package_id, preferring later inputs."""
    by_package: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for order, path in enumerate(paths):
        for row in read_jsonl(path):
            package_id = row.get("package_id")
            if not package_id:
                raise ValueError(f"Missing package_id in {path}")
            if package_id in by_package:
                duplicate_count += 1
            normalized = dict(row)
            normalized["source_passed_index"] = str(path)
            normalized["source_passed_index_order"] = order
            by_package[str(package_id)] = normalized
    rows = list(by_package.values())
    rows.sort(key=lambda row: (row["source_passed_index_order"], str(row["package_id"])))
    return rows, duplicate_count


def fetch_evidence_rows(
    conn: sqlite3.Connection,
    rag_input_ids: list[str],
    max_excerpt_chars: int,
) -> list[dict[str, Any]]:
    if not rag_input_ids:
        return []
    placeholders = ",".join("?" for _ in rag_input_ids)
    query = f"""
        SELECT
            rag_input_id,
            source_chunk_id,
            source_file,
            source_path,
            page_or_slide,
            content_sha256,
            excerpt,
            content,
            mapped_period,
            mapped_subject,
            mapped_field,
            mapped_area,
            mapped_detail,
            mapped_scope_id,
            scope_mapping_status,
            scope_mapping_confidence,
            extraction_quality,
            candidate_rag_status,
            approved_for_rag_evidence,
            approved_for_generation
        FROM chunks
        WHERE rag_input_id IN ({placeholders})
    """
    cursor = conn.execute(query, rag_input_ids)
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    by_id = {str(dict(zip(columns, row))["rag_input_id"]): dict(zip(columns, row)) for row in rows}
    evidence: list[dict[str, Any]] = []
    for rag_input_id in rag_input_ids:
        row = by_id.get(rag_input_id)
        if not row:
            evidence.append({"rag_input_id": rag_input_id, "lookup_status": "missing"})
            continue
        excerpt = row.get("excerpt") or row.get("content") or ""
        evidence.append(
            {
                "rag_input_id": row.get("rag_input_id"),
                "source_chunk_id": row.get("source_chunk_id"),
                "source_file": row.get("source_file"),
                "source_path": row.get("source_path"),
                "page_or_slide": row.get("page_or_slide"),
                "content_sha256": row.get("content_sha256"),
                "evidence_excerpt": truncate_text(str(excerpt), max_excerpt_chars),
                "mapped_scope": {
                    "period": row.get("mapped_period"),
                    "subject": row.get("mapped_subject"),
                    "field": row.get("mapped_field"),
                    "area": row.get("mapped_area"),
                    "detail": row.get("mapped_detail"),
                    "scope_id": row.get("mapped_scope_id"),
                    "mapping_status": row.get("scope_mapping_status"),
                    "mapping_confidence": row.get("scope_mapping_confidence"),
                },
                "quality": {
                    "extraction_quality": row.get("extraction_quality"),
                    "candidate_rag_status": row.get("candidate_rag_status"),
                    "approved_for_rag_evidence": bool(row.get("approved_for_rag_evidence")),
                    "approved_for_generation": bool(row.get("approved_for_generation")),
                },
                "lookup_status": "found",
            }
        )
    return evidence


def rag_input_ids_from_item(item: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for key in ("evidence_refs", "source_chunks"):
        for ref in item.get(key) or []:
            if not isinstance(ref, dict):
                continue
            rag_input_id = ref.get("rag_input_id")
            if rag_input_id and rag_input_id not in seen:
                seen.add(str(rag_input_id))
                ids.append(str(rag_input_id))
    return ids


def scope_from_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = ["period", "subject", "field", "area", "detail", "scope_id"]
    return {key: item.get(key) for key in keys}


def draft_item_for_review(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "period",
        "subject",
        "field",
        "area",
        "detail",
        "scope_id",
        "learning_objective_id",
        "question_type",
        "competency_type",
        "difficulty",
        "stem",
        "options",
        "answer",
        "explanation",
        "distractor_strategy",
    ]
    return {key: item.get(key) for key in keys}


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["overall_verdict", "checks", "revision_required", "notes"],
        "properties": {
            "overall_verdict": {"enum": ["pass", "revise", "reject"]},
            "revision_required": {"type": "boolean"},
            "checks": {
                "type": "object",
                "required": [
                    "scope_alignment",
                    "learning_objective_alignment",
                    "evidence_grounding",
                    "answer_uniqueness",
                    "distractor_quality",
                    "explanation_quality",
                    "copyright_risk",
                    "korean_item_style",
                    "hold_material_contamination",
                ],
                "additionalProperties": {
                    "type": "object",
                    "required": ["verdict", "reason"],
                    "properties": {
                        "verdict": {"enum": ["pass", "revise", "reject"]},
                        "reason": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
        },
    }


def build_llm_package(
    review_candidate: dict[str, Any],
    item: dict[str, Any],
    request_package: dict[str, Any],
    validation_report: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    validation_package_id = "llm2_" + short_hash(review_candidate["review_candidate_id"])
    return {
        "validation_package_id": validation_package_id,
        "review_candidate_id": review_candidate["review_candidate_id"],
        "package_id": review_candidate["package_id"],
        "mode": "llm_secondary_validation_input",
        "instructions": {
            "role": "방사선사 국가고시 1·2교시 텍스트 문항 2차 검증자",
            "task": "생성 초안이 출제범위, 학습목표, 근거, 문항작성 원칙에 맞는지 판정한다.",
            "do_not_rewrite_question_unless_requested": True,
            "do_not_approve_final_question_bank_storage": True,
            "rag_use": "정답 근거 검증용으로만 사용",
            "source_handling": [
                "근거 발췌문과 문항 문장의 과도한 유사성을 확인한다.",
                "표·그림·수식·법규·수치 보류 자료가 문항 근거로 섞였는지 확인한다.",
                "최종 판정은 pass, revise, reject 중 하나로 한다.",
            ],
        },
        "draft_item": draft_item_for_review(item),
        "requested_scope": request_package.get("requested_scope") or scope_from_item(item),
        "recommended_generation_settings": request_package.get("recommended_generation_settings") or {},
        "harness_summary": validation_report.get("summary") or {},
        "harness_findings": validation_report.get("findings") or [],
        "evidence_for_review": evidence_rows,
        "checks_requested": [
            "scope_alignment",
            "learning_objective_alignment",
            "evidence_grounding",
            "answer_uniqueness",
            "distractor_quality",
            "explanation_quality",
            "copyright_risk",
            "korean_item_style",
            "hold_material_contamination",
        ],
        "expected_output_schema": output_schema(),
    }


def build_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 32개 통과 초안 통합 인덱스 및 LLM 2차 검증 패키지 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 통합 후보 수: {report['counts']['review_candidates']}",
        f"- LLM 2차 검증 패키지 수: {report['counts']['llm_secondary_validation_packages']}",
        f"- 입력 통과 인덱스 수: {report['counts']['input_passed_indexes']}",
        f"- 입력 통과 행 수: {report['counts']['input_passed_rows']}",
        f"- 중복 제거 수: {report['counts']['deduplicated_rows']}",
        "",
        "## 산출물",
        f"- 통합 인덱스: `{report['outputs']['review_candidate_index']}`",
        f"- LLM 2차 검증 패키지: `{report['outputs']['llm_secondary_validation_packages']}`",
        f"- JSON 보고서: `{report['outputs']['json_report']}`",
        "",
        "## 상태",
        "- 모든 항목은 Harness 통과 초안이며, 최종 승인 문항이 아닙니다.",
        "- LLM 2차 검증은 아직 실행하지 않았습니다.",
        "- 표·그림·수식·법규·수치 보류 자료는 2차 검증에서 오염 여부를 다시 확인하도록 지시했습니다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--passed-index",
        action="append",
        type=Path,
        dest="passed_indexes",
        help="Passed draft JSONL index. Can be provided multiple times.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-evidence-excerpt-chars", type=int, default=700)
    args = parser.parse_args()

    passed_indexes = args.passed_indexes or [DEFAULT_ORIGINAL_PASSED, DEFAULT_BACKFILLED_PASSED]
    for path in passed_indexes:
        if not path.exists():
            raise FileNotFoundError(path)
    if not args.db.exists():
        raise FileNotFoundError(args.db)

    passed_rows, duplicate_count = load_passed_rows(passed_indexes)
    review_candidates: list[dict[str, Any]] = []
    llm_packages: list[dict[str, Any]] = []

    conn = sqlite3.connect(args.db)
    try:
        for row in passed_rows:
            draft_path = Path(row.get("draft_item") or row.get("revised_draft_item", ""))
            validation_path = Path(row.get("validation_report") or row.get("revision_harness_report", ""))
            row_run_dir = Path(row.get("run_dir") or draft_path.parent)
            request_path = row_run_dir / "request_package_snapshot.json"
            if not request_path.exists():
                request_path = row_run_dir / "revision_package_snapshot.json"
            item = read_json(draft_path)
            validation_report = read_json(validation_path)
            request_package = read_json(request_path) if request_path.exists() else {}
            evidence_ids = rag_input_ids_from_item(item)
            evidence_rows = fetch_evidence_rows(conn, evidence_ids, args.max_evidence_excerpt_chars)

            review_candidate_id = "rc_" + short_hash(f"{row['package_id']}|{draft_path}")
            review_candidate = {
                "review_candidate_id": review_candidate_id,
                "package_id": row["package_id"],
                "status": "harness_passed_pending_llm_review",
                "source_batch_dir": str(row_run_dir.parent),
                "run_dir": str(row_run_dir),
                "source_passed_index": row.get("source_passed_index", ""),
                "source_revision_package_id": row.get("revision_package_id", ""),
                "draft_item_path": str(draft_path),
                "validation_report_path": str(validation_path),
                "request_package_snapshot_path": str(request_path) if request_path.exists() else "",
                "scope": scope_from_item(item),
                "learning_objective_id": item.get("learning_objective_id"),
                "question_type": item.get("question_type"),
                "competency_type": item.get("competency_type"),
                "difficulty": item.get("difficulty"),
                "answer": item.get("answer"),
                "option_count": len(item.get("options") or []),
                "evidence_count": len(evidence_ids),
                "evidence_lookup": {
                    "found": sum(1 for evidence in evidence_rows if evidence.get("lookup_status") == "found"),
                    "missing": sum(1 for evidence in evidence_rows if evidence.get("lookup_status") == "missing"),
                },
                "harness_summary": validation_report.get("summary") or {},
                "llm_secondary_validation_status": "not_run",
            }
            llm_package = build_llm_package(
                review_candidate,
                item,
                request_package,
                validation_report,
                evidence_rows,
            )
            review_candidate["llm_secondary_validation_package_id"] = llm_package[
                "validation_package_id"
            ]
            review_candidates.append(review_candidate)
            llm_packages.append(llm_package)
    finally:
        conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index_path = args.output_dir / "review_candidate_index.jsonl"
    llm_packages_path = args.output_dir / "llm_secondary_validation_packages.jsonl"
    json_report_path = args.output_dir / "review_candidate_build_report.json"
    md_report_path = args.output_dir / "review_candidate_build_report.md"

    write_jsonl(index_path, review_candidates)
    write_jsonl(llm_packages_path, llm_packages)

    input_rows_count = sum(len(read_jsonl(path)) for path in passed_indexes)
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "inputs": {
            "passed_indexes": [str(path) for path in passed_indexes],
            "rag_bm25_db": str(args.db),
        },
        "outputs": {
            "review_candidate_index": str(index_path),
            "llm_secondary_validation_packages": str(llm_packages_path),
            "json_report": str(json_report_path),
            "markdown_report": str(md_report_path),
        },
        "counts": {
            "input_passed_indexes": len(passed_indexes),
            "input_passed_rows": input_rows_count,
            "deduplicated_rows": duplicate_count,
            "review_candidates": len(review_candidates),
            "llm_secondary_validation_packages": len(llm_packages),
            "harness_passed_pending_llm_review": len(review_candidates),
            "evidence_missing_total": sum(
                candidate["evidence_lookup"]["missing"] for candidate in review_candidates
            ),
        },
        "policy": {
            "final_question_bank_storage": False,
            "llm_secondary_validation_executed": False,
            "rag_use": "evidence_validation_only",
            "source_text_in_packages": "short_evidence_excerpts_only",
        },
    }
    write_json(json_report_path, report)
    md_report_path.write_text(build_markdown_report(report), encoding="utf-8")

    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
