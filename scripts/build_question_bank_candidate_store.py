#!/usr/bin/env python3
"""Build a pending expert-review question-bank candidate store.

The store contains only LLM/Harness-passed generated drafts. It is not a final
approved question bank.
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
DEFAULT_ORIGINAL_LLM_RUN_DIR = (
    PROJECT_ROOT
    / "resources/generated/review_candidates/llm_secondary_validation_runs/run_20260624T074838Z_limitall_offset0"
)
DEFAULT_REVISED_LLM_RUN_DIR = (
    PROJECT_ROOT
    / "resources/generated/review_candidates/llm_secondary_validation_runs/revised_after_harness/run_20260624T083255Z_limitall_offset0"
)
DEFAULT_VISUAL_PASS_INDEX = (
    PROJECT_ROOT
    / "resources/generated/visual_question_drafts/visual_question_drafts_combined/passed_visual_drafts_all.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/question_bank_candidates"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def option_at(options: Any, index: int) -> str:
    if isinstance(options, list) and 1 <= index <= len(options):
        return str(options[index - 1])
    if isinstance(options, dict):
        return str(options.get(str(index)) or options.get(index) or "")
    return ""


def evidence_refs_for_store(evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in evidence_rows:
        rag_input_id = str(row.get("rag_input_id") or "")
        if not rag_input_id or rag_input_id in seen:
            continue
        seen.add(rag_input_id)
        mapped_scope = row.get("mapped_scope") or {}
        quality = row.get("quality") or {}
        refs.append(
            {
                "rag_input_id": rag_input_id,
                "source_chunk_id": row.get("source_chunk_id") or "",
                "source_file": row.get("source_file") or "",
                "source_path": row.get("source_path") or "",
                "page_or_slide": row.get("page_or_slide") or 0,
                "content_sha256": row.get("content_sha256") or "",
                "mapped_scope_id": mapped_scope.get("scope_id") or "",
                "mapping_confidence": mapped_scope.get("mapping_confidence") or "",
                "approved_for_rag_evidence": bool(quality.get("approved_for_rag_evidence")),
                "approved_for_generation": bool(quality.get("approved_for_generation")),
            }
        )
    return refs


def candidate_id_for(source_stage: str, row: dict[str, Any], item: dict[str, Any]) -> str:
    base = "|".join(
        [
            source_stage,
            str(row.get("package_id") or ""),
            str(row.get("validation_package_id") or ""),
            str(item.get("stem") or ""),
        ]
    )
    return "qbc_" + short_hash(base)


def build_candidate(
    row: dict[str, Any],
    source_stage: str,
    validation_package: dict[str, Any],
    llm_result: dict[str, Any],
    harness_report: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    item = validation_package.get("draft_item") or {}
    evidence_rows = validation_package.get("evidence_for_review") or []
    evidence_refs = evidence_refs_for_store(evidence_rows)
    candidate_id = candidate_id_for(source_stage, row, item)
    options = item.get("options") or []
    source_paths = dict(validation_package.get("source_paths") or {})
    source_paths.update(
        {
            "validation_package_snapshot": str(Path(row["run_dir"]) / "validation_package_snapshot.json"),
            "llm_validation_result": row.get("llm_validation_result") or "",
            "validation_run_report": row.get("validation_run_report") or "",
        }
    )
    if harness_report:
        source_paths["harness_report"] = source_paths.get("revision_harness_report") or ""

    llm_summary = {
        "overall_verdict": llm_result.get("overall_verdict"),
        "revision_required": llm_result.get("revision_required"),
        "notes": llm_result.get("notes") or "",
        "checks": llm_result.get("checks") or {},
    }
    harness_summary = (harness_report or {}).get("summary") or validation_package.get("harness_summary") or {}
    validation_summary = {
        "harness": harness_summary,
        "llm_secondary": llm_summary,
    }
    candidate = {
        "id": candidate_id,
        "status": "pending_expert_review",
        "source_stage": source_stage,
        "package_id": row.get("package_id") or "",
        "review_candidate_id": row.get("review_candidate_id") or "",
        "validation_package_id": row.get("validation_package_id") or "",
        "revision_package_id": validation_package.get("revision_package_id") or "",
        "period": item.get("period") or row.get("period") or "",
        "subject": item.get("subject") or row.get("subject") or "",
        "field": item.get("field") or row.get("field") or "",
        "area": item.get("area") or row.get("area") or "",
        "detail": item.get("detail") or row.get("detail") or "",
        "scope_id": item.get("scope_id") or row.get("scope_id") or "",
        "learning_objective_id": item.get("learning_objective_id") or row.get("learning_objective_id") or "",
        "question_type": item.get("question_type") or row.get("question_type") or "",
        "competency_type": item.get("competency_type") or "",
        "difficulty": item.get("difficulty") or row.get("difficulty") or "",
        "stem": item.get("stem") or "",
        "option_1": option_at(options, 1),
        "option_2": option_at(options, 2),
        "option_3": option_at(options, 3),
        "option_4": option_at(options, 4),
        "option_5": option_at(options, 5),
        "options_json": options,
        "answer": int(item.get("answer") or 0),
        "explanation": item.get("explanation") or "",
        "distractor_strategy": item.get("distractor_strategy") or "",
        "evidence_refs_json": evidence_refs,
        "source_paths_json": source_paths,
        "validation_summary_json": validation_summary,
        "candidate_payload_json": {
            "draft_item": item,
            "policy": {
                "final_approved": False,
                "status": "pending_expert_review",
                "rag_use": "answer_evidence_only",
            },
        },
        "created": now_iso(),
        "updated": now_iso(),
    }

    evidence_records = []
    for index, ref in enumerate(evidence_refs, start=1):
        evidence_records.append(
            {
                "id": f"{candidate_id}_ev_{index:02d}",
                "candidate_id": candidate_id,
                "rag_input_id": ref["rag_input_id"],
                "source_chunk_id": ref["source_chunk_id"],
                "source_file": ref["source_file"],
                "source_path": ref["source_path"],
                "page_or_slide": int(ref.get("page_or_slide") or 0),
                "content_sha256": ref["content_sha256"],
                "mapped_scope_id": ref["mapped_scope_id"],
                "mapping_confidence": ref["mapping_confidence"],
                "evidence_role": "answer_evidence",
                "created": now_iso(),
            }
        )

    validation_records = [
        {
            "id": f"{candidate_id}_val_harness",
            "candidate_id": candidate_id,
            "validation_stage": "rule_harness",
            "validator_type": "deterministic",
            "verdict": "pass" if harness_summary.get("overall_pass") is True else "",
            "passed": 1 if harness_summary.get("overall_pass") is True else 0,
            "revision_required": 0,
            "result_path": source_paths.get("harness_report") or "",
            "summary_json": harness_summary,
            "created": now_iso(),
        },
        {
            "id": f"{candidate_id}_val_llm2",
            "candidate_id": candidate_id,
            "validation_stage": "llm_secondary",
            "validator_type": "llm",
            "verdict": str(llm_result.get("overall_verdict") or ""),
            "passed": 1 if llm_result.get("overall_verdict") == "pass" else 0,
            "revision_required": 1 if llm_result.get("revision_required") else 0,
            "result_path": row.get("llm_validation_result") or "",
            "summary_json": llm_summary,
            "created": now_iso(),
        },
    ]
    return candidate, evidence_records, validation_records


def visual_candidate_id_for(row: dict[str, Any], item: dict[str, Any]) -> str:
    base = "|".join(
        [
            "visual_draft",
            str(row.get("package_id") or ""),
            str(row.get("source_visual_approval_id") or ""),
            str(item.get("stem") or ""),
        ]
    )
    return "qbc_" + short_hash(base)


def build_visual_candidate(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    run_dir = Path(row["run_dir"])
    item = read_json(run_dir / "draft_item.json")
    package = read_json(run_dir / "request_package_snapshot.json")
    validation_report = read_json(run_dir / "visual_validation_report.json")
    visual = package.get("visual_evidence") or {}
    summary = visual.get("summary") or {}
    approval_id = row.get("source_visual_approval_id") or package.get("source_visual_approval_id") or ""
    candidate_id = visual_candidate_id_for(row, item)
    options = item.get("options") or []
    evidence_refs = [
        {
            "rag_input_id": approval_id,
            "source_chunk_id": package.get("source_visual_chunk_id") or "",
            "source_file": visual.get("source_file") or "",
            "source_path": visual.get("source_path") or "",
            "page_or_slide": visual.get("page_or_slide") or 0,
            "content_sha256": "",
            "mapped_scope_id": item.get("scope_id") or "",
            "mapping_confidence": package.get("scope_mapping_method") or "",
            "approved_for_rag_evidence": False,
            "approved_for_generation": True,
            "evidence_type": "approved_structured_visual",
            "visual_kind": visual.get("visual_kind") or "",
        }
    ]
    source_paths = {
        "visual_question_package_snapshot": str(run_dir / "request_package_snapshot.json"),
        "visual_draft_item": str(run_dir / "draft_item.json"),
        "visual_validation_report": str(run_dir / "visual_validation_report.json"),
        "visual_codex_last_message": str(run_dir / "codex_last_message.json"),
        "visual_batch_report": str(Path(row.get("batch_dir") or "") / "batch_report.json") if row.get("batch_dir") else "",
    }
    harness_summary = validation_report.get("summary") or row.get("validation_summary") or {}
    validation_summary = {
        "visual_harness": harness_summary,
        "llm_secondary": {
            "overall_verdict": "not_run",
            "revision_required": None,
            "notes": "시각자료 초안은 시각자료 전용 Harness 통과 후 전문가 검수 대기 상태로 저장했습니다.",
            "checks": {},
        },
    }
    candidate = {
        "id": candidate_id,
        "status": "pending_expert_review",
        "source_stage": "visual_draft",
        "package_id": row.get("package_id") or "",
        "review_candidate_id": "",
        "validation_package_id": approval_id,
        "revision_package_id": "",
        "period": item.get("period") or "",
        "subject": item.get("subject") or "",
        "field": item.get("field") or "",
        "area": item.get("area") or "",
        "detail": item.get("detail") or "",
        "scope_id": item.get("scope_id") or "",
        "learning_objective_id": item.get("learning_objective_id") or "",
        "question_type": item.get("question_type") or "",
        "competency_type": item.get("competency_type") or "",
        "difficulty": item.get("difficulty") or "",
        "stem": item.get("stem") or "",
        "option_1": option_at(options, 1),
        "option_2": option_at(options, 2),
        "option_3": option_at(options, 3),
        "option_4": option_at(options, 4),
        "option_5": option_at(options, 5),
        "options_json": options,
        "answer": int(item.get("answer") or 0),
        "explanation": item.get("explanation") or "",
        "distractor_strategy": item.get("distractor_strategy") or "",
        "evidence_refs_json": evidence_refs,
        "source_paths_json": source_paths,
        "validation_summary_json": validation_summary,
        "candidate_payload_json": {
            "draft_item": item,
            "visual_evidence_summary": {
                "visual_kind": visual.get("visual_kind") or "",
                "source_file": visual.get("source_file") or "",
                "page_or_slide": visual.get("page_or_slide") or "",
                "caption": summary.get("caption") or "",
                "nearby_text_summary": summary.get("nearby_text_summary") or "",
                "semantic_description": summary.get("semantic_description") or "",
                "structure_summary": summary.get("structure_summary") or "",
                "formula_plain_text": summary.get("formula_plain_text") or "",
                "variables": summary.get("variables") or {},
                "table_json": summary.get("table_json") or [],
                "embedded_text_candidates": summary.get("embedded_text_candidates") or [],
                "allowed_question_modes": visual.get("allowed_question_modes") or [],
            },
            "policy": {
                "final_approved": False,
                "generation_type": "visual_draft",
                "review_status": "pending_expert_review",
                "harness_status": "passed" if harness_summary.get("overall_pass") is True else "not_passed",
                "source_visual_reuse_allowed": False,
                "structured_visual_summary_only": True,
            },
        },
        "created": now_iso(),
        "updated": now_iso(),
    }
    evidence_records = [
        {
            "id": f"{candidate_id}_ev_01",
            "candidate_id": candidate_id,
            "rag_input_id": approval_id,
            "source_chunk_id": package.get("source_visual_chunk_id") or "",
            "source_file": visual.get("source_file") or "",
            "source_path": visual.get("source_path") or "",
            "page_or_slide": int(visual.get("page_or_slide") or 0),
            "content_sha256": "",
            "mapped_scope_id": item.get("scope_id") or "",
            "mapping_confidence": package.get("scope_mapping_method") or "",
            "evidence_role": "approved_structured_visual",
            "created": now_iso(),
        }
    ]
    validation_records = [
        {
            "id": f"{candidate_id}_val_visual_harness",
            "candidate_id": candidate_id,
            "validation_stage": "visual_harness",
            "validator_type": "deterministic",
            "verdict": "pass" if harness_summary.get("overall_pass") is True else "fail",
            "passed": 1 if harness_summary.get("overall_pass") is True else 0,
            "revision_required": 0 if harness_summary.get("overall_pass") is True else 1,
            "result_path": str(run_dir / "visual_validation_report.json"),
            "summary_json": harness_summary,
            "created": now_iso(),
        },
        {
            "id": f"{candidate_id}_val_expert_review",
            "candidate_id": candidate_id,
            "validation_stage": "expert_review",
            "validator_type": "human",
            "verdict": "pending",
            "passed": 0,
            "revision_required": 0,
            "result_path": "",
            "summary_json": {
                "review_status": "pending_expert_review",
                "reason": "시각자료 기반 문항은 자동 검증 통과 후 전문가 검수 전까지 최종 승인하지 않습니다.",
            },
            "created": now_iso(),
        },
    ]
    return candidate, evidence_records, validation_records


def sqlite_schema() -> str:
    return """
CREATE TABLE IF NOT EXISTS question_bank_candidate (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source_stage TEXT NOT NULL,
    package_id TEXT,
    review_candidate_id TEXT,
    validation_package_id TEXT,
    revision_package_id TEXT,
    period TEXT,
    subject TEXT,
    field TEXT,
    area TEXT,
    detail TEXT,
    scope_id TEXT,
    learning_objective_id TEXT,
    question_type TEXT,
    competency_type TEXT,
    difficulty TEXT,
    stem TEXT NOT NULL,
    option_1 TEXT,
    option_2 TEXT,
    option_3 TEXT,
    option_4 TEXT,
    option_5 TEXT,
    options_json TEXT,
    answer INTEGER,
    explanation TEXT,
    distractor_strategy TEXT,
    evidence_refs_json TEXT,
    source_paths_json TEXT,
    validation_summary_json TEXT,
    candidate_payload_json TEXT,
    created TEXT,
    updated TEXT
);
CREATE TABLE IF NOT EXISTS question_bank_candidate_evidence (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    rag_input_id TEXT,
    source_chunk_id TEXT,
    source_file TEXT,
    source_path TEXT,
    page_or_slide INTEGER,
    content_sha256 TEXT,
    mapped_scope_id TEXT,
    mapping_confidence TEXT,
    evidence_role TEXT,
    created TEXT
);
CREATE TABLE IF NOT EXISTS question_bank_candidate_validation (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    validation_stage TEXT,
    validator_type TEXT,
    verdict TEXT,
    passed INTEGER,
    revision_required INTEGER,
    result_path TEXT,
    summary_json TEXT,
    created TEXT
);
CREATE INDEX IF NOT EXISTS idx_qbc_status ON question_bank_candidate(status);
CREATE INDEX IF NOT EXISTS idx_qbc_scope ON question_bank_candidate(period, subject, field, area, detail);
CREATE INDEX IF NOT EXISTS idx_qbc_subject ON question_bank_candidate(subject);
CREATE INDEX IF NOT EXISTS idx_qbc_source_stage ON question_bank_candidate(source_stage);
CREATE INDEX IF NOT EXISTS idx_qbc_evidence_candidate ON question_bank_candidate_evidence(candidate_id);
CREATE INDEX IF NOT EXISTS idx_qbc_evidence_rag ON question_bank_candidate_evidence(rag_input_id);
CREATE INDEX IF NOT EXISTS idx_qbc_validation_candidate ON question_bank_candidate_validation(candidate_id);
"""


def reset_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(sqlite_schema())
    return conn


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    encoded_rows = []
    for row in rows:
        encoded = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                encoded[key] = dumps(value)
            else:
                encoded[key] = value
        encoded_rows.append(encoded)
    keys = list(encoded_rows[0].keys())
    placeholders = ",".join("?" for _ in keys)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(keys)}) VALUES ({placeholders})"
    conn.executemany(sql, [[row.get(key) for key in keys] for row in encoded_rows])


def build_markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# 문제은행 후보 저장소 생성 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 후보 상태: `pending_expert_review`",
        f"- 후보 문항 수: {counts['candidates']}",
        f"- 근거 참조 수: {counts['evidence_refs']}",
        f"- 검증 기록 수: {counts['validation_records']}",
        f"- 기존 LLM pass 후보: {counts['source_stage_counts'].get('initial_llm_pass', 0)}",
        f"- 재작성 후 LLM pass 후보: {counts['source_stage_counts'].get('revised_llm_pass', 0)}",
        f"- 시각자료 초안 후보: {counts['source_stage_counts'].get('visual_draft', 0)}",
        "",
        "## 산출물",
    ]
    for label, path in report["outputs"].items():
        lines.append(f"- {label}: `{path}`")
    lines.extend(
        [
            "",
            "## 주의",
            "- 이 저장소는 전문가 검수 대기 후보이며 최종 승인 문제은행이 아닙니다.",
            "- 근거 원문 전체가 아니라 RAG 근거 참조와 위치 메타데이터를 저장합니다.",
            "- 전문가 검수 화면 구현 전까지 JSONL/SQLite를 기준 저장소로 사용합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-llm-run-dir", type=Path, default=DEFAULT_ORIGINAL_LLM_RUN_DIR)
    parser.add_argument("--revised-llm-run-dir", type=Path, default=DEFAULT_REVISED_LLM_RUN_DIR)
    parser.add_argument("--visual-pass-index", type=Path, default=DEFAULT_VISUAL_PASS_INDEX)
    parser.add_argument("--skip-visual", action="store_true", help="Do not include visual_draft candidates.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    original_pass_rows = read_jsonl(args.original_llm_run_dir / "llm_secondary_pass_index.jsonl")
    revised_pass_rows = read_jsonl(args.revised_llm_run_dir / "llm_secondary_pass_index.jsonl")

    candidates: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    validation_records: list[dict[str, Any]] = []

    for row in original_pass_rows:
        validation_package = read_json(Path(row["run_dir"]) / "validation_package_snapshot.json")
        llm_result = read_json(Path(row["llm_validation_result"]))
        candidate, evidence, validations = build_candidate(
            row,
            "initial_llm_pass",
            validation_package,
            llm_result,
            None,
        )
        candidates.append(candidate)
        evidence_records.extend(evidence)
        validation_records.extend(validations)

    for row in revised_pass_rows:
        validation_package = read_json(Path(row["run_dir"]) / "validation_package_snapshot.json")
        llm_result = read_json(Path(row["llm_validation_result"]))
        harness_path = Path(validation_package.get("source_paths", {}).get("revision_harness_report") or "")
        if not harness_path.exists():
            harness_path = Path(row["run_dir"]) / "missing_revision_harness_report.json"
            harness_report = {"summary": {}}
        else:
            harness_report = read_json(harness_path)
        candidate, evidence, validations = build_candidate(
            row,
            "revised_llm_pass",
            validation_package,
            llm_result,
            harness_report,
        )
        candidates.append(candidate)
        evidence_records.extend(evidence)
        validation_records.extend(validations)

    if not args.skip_visual and args.visual_pass_index.exists():
        for row in read_jsonl(args.visual_pass_index):
            candidate, evidence, validations = build_visual_candidate(row)
            candidates.append(candidate)
            evidence_records.extend(evidence)
            validation_records.extend(validations)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.output_dir / "question_bank_candidates.sqlite"
    candidate_jsonl = args.output_dir / "question_bank_candidate_records.jsonl"
    evidence_jsonl = args.output_dir / "question_bank_candidate_evidence.jsonl"
    validation_jsonl = args.output_dir / "question_bank_candidate_validation.jsonl"
    schema_path = args.output_dir / "question_bank_candidate_schema.sql"
    report_json = args.output_dir / "question_bank_candidate_store_report.json"
    report_md = args.output_dir / "question_bank_candidate_store_report.md"

    conn = reset_db(db_path)
    try:
        insert_rows(conn, "question_bank_candidate", candidates)
        insert_rows(conn, "question_bank_candidate_evidence", evidence_records)
        insert_rows(conn, "question_bank_candidate_validation", validation_records)
        conn.commit()
    finally:
        conn.close()

    write_jsonl(candidate_jsonl, candidates)
    write_jsonl(evidence_jsonl, evidence_records)
    write_jsonl(validation_jsonl, validation_records)
    schema_path.write_text(sqlite_schema().strip() + "\n", encoding="utf-8")

    source_stage_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    subject_counts: dict[str, int] = {}
    for candidate in candidates:
        source_stage_counts[candidate["source_stage"]] = source_stage_counts.get(candidate["source_stage"], 0) + 1
        status_counts[candidate["status"]] = status_counts.get(candidate["status"], 0) + 1
        subject_counts[candidate["subject"]] = subject_counts.get(candidate["subject"], 0) + 1
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "inputs": {
            "original_llm_pass_index": str(args.original_llm_run_dir / "llm_secondary_pass_index.jsonl"),
            "revised_llm_pass_index": str(args.revised_llm_run_dir / "llm_secondary_pass_index.jsonl"),
            "visual_pass_index": str(args.visual_pass_index) if not args.skip_visual else "",
        },
        "outputs": {
            "sqlite_db": str(db_path),
            "candidate_jsonl": str(candidate_jsonl),
            "evidence_jsonl": str(evidence_jsonl),
            "validation_jsonl": str(validation_jsonl),
            "schema_sql": str(schema_path),
            "json_report": str(report_json),
            "markdown_report": str(report_md),
        },
        "counts": {
            "candidates": len(candidates),
            "evidence_refs": len(evidence_records),
            "validation_records": len(validation_records),
            "status_counts": status_counts,
            "subject_counts": subject_counts,
            "source_stage_counts": source_stage_counts,
        },
        "policy": {
            "candidate_status": "pending_expert_review",
            "final_question_bank_approved": False,
            "source_full_text_stored": False,
            "rag_use": "answer_evidence_only",
            "visual_generation_type": "visual_draft",
            "visual_review_status": "pending_expert_review",
        },
    }
    write_json(report_json, report)
    report_md.write_text(build_markdown_report(report), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
