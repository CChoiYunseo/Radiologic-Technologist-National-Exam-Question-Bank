#!/usr/bin/env python3
"""Audit coverage from extracted RAG chunks to generation-safe packages.

This report intentionally emits counts and metadata only. It does not copy
source textbook text into the report.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPED_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_BM25_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"
DEFAULT_GENERATION_SAFE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references_generation_safe"
DEFAULT_SCOPE = PROJECT_ROOT / "resources" / "extracted" / "sebuyeongyeok_verified_scope.json"
DEFAULT_PACKAGES = PROJECT_ROOT / "resources" / "generated" / "question_request_packages"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"
DEFAULT_PLANNING_DIR = PROJECT_ROOT / "resources" / "generated" / "knowledge_object_planning"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def scope_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("mapped_subject") or row.get("subject") or "").strip(),
        str(row.get("mapped_field") or row.get("field") or "").strip(),
        str(row.get("mapped_area") or row.get("area") or "").strip(),
        str(row.get("mapped_detail") or row.get("detail") or "").strip(),
    )


def package_scope_key(pkg: dict[str, Any]) -> tuple[str, str, str, str]:
    scope = pkg.get("requested_scope") or {}
    return (
        str(scope.get("subject") or "").strip(),
        str(scope.get("field") or "").strip(),
        str(scope.get("area") or "").strip(),
        str(scope.get("detail") or "").strip(),
    )


def load_scope_rows(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    rows = data.get("rows", []) if isinstance(data, dict) else data
    return [row for row in rows if isinstance(row, dict)]


def load_generation_safe_ids(db_dir: Path) -> set[str]:
    db_path = db_dir / "chunks.sqlite"
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT chunk_id, metadata_json FROM chunks").fetchall()
    finally:
        conn.close()
    ids: set[str] = set()
    for chunk_id, metadata_json in rows:
        rag_input_id = ""
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
                rag_input_id = str(metadata.get("rag_input_id") or "")
            except json.JSONDecodeError:
                rag_input_id = ""
        ids.add(rag_input_id or str(chunk_id))
    return {item for item in ids if item}


def load_bm25_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT rag_input_id FROM chunks").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows if row and row[0]}


def load_package_counts(packages_dir: Path) -> dict[tuple[str, str, str, str], Counter[str]]:
    status_files = {
        "ready_strict": packages_dir / "question_request_packages_ready_strict.jsonl",
        "ready_with_warnings": packages_dir / "question_request_packages_ready_with_warnings.jsonl",
        "ready_all": packages_dir / "question_request_packages_ready_all.jsonl",
        "rejected": packages_dir / "question_request_packages_rejected.jsonl",
        "excluded": packages_dir / "question_request_packages_excluded_scopes.jsonl",
    }
    counts: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    for status, path in status_files.items():
        for pkg in read_jsonl(path):
            counts[package_scope_key(pkg)][status] += 1
    return counts


def block_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not row["detail"]:
        reasons.append("세부영역 없음")
    if row["total_rag_chunks"] == 0:
        reasons.append("RAG 후보 없음")
    if row["rag_needs_review"] > 0:
        reasons.append("범위 매핑 검토 필요")
    if row["area_only_chunks"] > row["high_mapping_chunks"] + row["medium_mapping_chunks"]:
        reasons.append("세부영역 확신도 낮음")
    if row["generation_safe_chunks"] == 0:
        reasons.append("생성 안전 후보 없음")
    if row["bm25_generation_safe_intersection"] == 0:
        reasons.append("검색 인덱스와 생성 안전 후보 연결 없음")
    if row["strict_packages"] == 0 and row["warning_packages"] > 0:
        reasons.append("warning 패키지만 존재")
    if row["strict_packages"] == 0 and row["warning_packages"] == 0 and row["generation_safe_chunks"] > 0:
        reasons.append("생성 안전 후보가 패키지로 승격되지 않음")
    return reasons


def readiness(row: dict[str, Any]) -> str:
    if row["strict_packages"] > 0:
        return "ready_strict"
    if row["warning_packages"] > 0:
        return "ready_with_warnings"
    if row["generation_safe_chunks"] > 0:
        return "safe_chunks_not_packaged"
    if row["total_rag_chunks"] > 0:
        return "rag_only"
    return "no_rag"


def priority_score(row: dict[str, Any]) -> int:
    score = row["total_rag_chunks"] * 2
    score += row["bm25_chunks"] * 2
    score += row["high_mapping_chunks"] * 4
    score += row["medium_mapping_chunks"] * 2
    score += row["learning_objective_linked_chunks"] * 4
    score += row["generation_safe_chunks"] * 5
    score -= row["rag_needs_review"] * 2
    score -= row["area_only_chunks"]
    if row["strict_packages"] > 0:
        score -= 1000
    return score


def build_audit(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scope_rows = load_scope_rows(args.verified_scope)
    mapped_rows = read_jsonl(args.mapped_rag)
    bm25_ids = load_bm25_ids(args.bm25_db)
    generation_safe_ids = load_generation_safe_ids(args.generation_safe_vector_db)
    package_counts = load_package_counts(args.packages_dir)

    by_scope: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in scope_rows:
        key = (
            str(row.get("subject") or "").strip(),
            str(row.get("field") or "").strip(),
            str(row.get("area") or "").strip(),
            str(row.get("detail") or "").strip(),
        )
        if key not in by_scope:
            by_scope[key] = {
                "subject": key[0],
                "field": key[1],
                "area": key[2],
                "detail": key[3],
                "scope_question_count": row.get("question_count", ""),
                "total_rag_chunks": 0,
                "bm25_chunks": 0,
                "generation_safe_chunks": 0,
                "bm25_generation_safe_intersection": 0,
                "approved_for_generation_chunks": 0,
                "rag_evidence_chunks": 0,
                "rag_needs_review": 0,
                "high_mapping_chunks": 0,
                "medium_mapping_chunks": 0,
                "area_only_chunks": 0,
                "learning_objective_linked_chunks": 0,
                "hold_reason_chunks": 0,
                "source_files": Counter(),
            }

    for row in mapped_rows:
        key = scope_key(row)
        if key not in by_scope:
            by_scope[key] = {
                "subject": key[0],
                "field": key[1],
                "area": key[2],
                "detail": key[3],
                "scope_question_count": "",
                "total_rag_chunks": 0,
                "bm25_chunks": 0,
                "generation_safe_chunks": 0,
                "bm25_generation_safe_intersection": 0,
                "approved_for_generation_chunks": 0,
                "rag_evidence_chunks": 0,
                "rag_needs_review": 0,
                "high_mapping_chunks": 0,
                "medium_mapping_chunks": 0,
                "area_only_chunks": 0,
                "learning_objective_linked_chunks": 0,
                "hold_reason_chunks": 0,
                "source_files": Counter(),
            }
        target = by_scope[key]
        rag_input_id = str(row.get("rag_input_id") or "")
        target["total_rag_chunks"] += 1
        if rag_input_id in bm25_ids:
            target["bm25_chunks"] += 1
        if rag_input_id in generation_safe_ids:
            target["generation_safe_chunks"] += 1
            if rag_input_id in bm25_ids:
                target["bm25_generation_safe_intersection"] += 1
        if row.get("approved_for_generation"):
            target["approved_for_generation_chunks"] += 1
        if row.get("approved_for_rag_evidence"):
            target["rag_evidence_chunks"] += 1
        if row.get("scope_mapping_needs_review") or row.get("needs_review"):
            target["rag_needs_review"] += 1
        confidence = str(row.get("scope_mapping_confidence") or "")
        if confidence == "high":
            target["high_mapping_chunks"] += 1
        elif confidence == "medium":
            target["medium_mapping_chunks"] += 1
        elif confidence == "area_only":
            target["area_only_chunks"] += 1
        if row.get("learning_objective_candidates"):
            target["learning_objective_linked_chunks"] += 1
        if row.get("generation_hold_reasons"):
            target["hold_reason_chunks"] += 1
        source_file = str(row.get("source_file") or "").strip()
        if source_file:
            target["source_files"][source_file] += 1

    audit_rows: list[dict[str, Any]] = []
    for key, row in by_scope.items():
        package_counter = package_counts.get(key, Counter())
        source_files = row.pop("source_files")
        row["strict_packages"] = package_counter.get("ready_strict", 0)
        row["warning_packages"] = package_counter.get("ready_with_warnings", 0)
        row["all_ready_packages"] = package_counter.get("ready_all", 0)
        row["rejected_packages"] = package_counter.get("rejected", 0)
        row["excluded_packages"] = package_counter.get("excluded", 0)
        row["readiness"] = readiness(row)
        row["block_reasons"] = "; ".join(block_reasons(row))
        row["priority_score"] = priority_score(row)
        row["top_source_files"] = "; ".join(f"{name}:{count}" for name, count in source_files.most_common(3))
        audit_rows.append(row)

    audit_rows.sort(
        key=lambda item: (
            item["readiness"] != "ready_strict",
            -item["priority_score"],
            item["subject"],
            item["field"],
            item["area"],
            item["detail"],
        )
    )

    readiness_counts = Counter(row["readiness"] for row in audit_rows)
    summary = {
        "created_at": now_iso(),
        "inputs": {
            "mapped_rag": str(args.mapped_rag),
            "bm25_db": str(args.bm25_db),
            "generation_safe_vector_db": str(args.generation_safe_vector_db),
            "verified_scope": str(args.verified_scope),
            "packages_dir": str(args.packages_dir),
        },
        "counts": {
            "verified_scope_rows": len(scope_rows),
            "mapped_rag_rows": len(mapped_rows),
            "bm25_ids": len(bm25_ids),
            "generation_safe_ids": len(generation_safe_ids),
            "scope_rows_in_audit": len(audit_rows),
            "readiness_counts": dict(readiness_counts),
            "strict_package_scopes": sum(1 for row in audit_rows if row["strict_packages"] > 0),
            "warning_package_scopes": sum(1 for row in audit_rows if row["warning_packages"] > 0),
            "rag_only_scopes": sum(1 for row in audit_rows if row["readiness"] == "rag_only"),
            "no_rag_scopes": sum(1 for row in audit_rows if row["readiness"] == "no_rag"),
        },
        "policy": {
            "question_generation_performed": False,
            "source_text_emitted": False,
            "purpose": "Find bottlenecks before rebuilding semantic chunks and safe generation packages.",
        },
    }
    return audit_rows, summary


def write_markdown(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = summary["counts"]
    top_promote = [
        row for row in rows
        if row["readiness"] in {"rag_only", "safe_chunks_not_packaged", "ready_with_warnings"}
    ][:25]
    no_rag = [row for row in rows if row["readiness"] == "no_rag"][:30]

    lines = [
        "# Generation Coverage Audit",
        "",
        "이 보고서는 원문 내용을 복사하지 않고, 세부영역별 생성 가능성 메타데이터만 집계한다.",
        "",
        "## Summary",
        "",
        f"- verified scope rows: {counts['verified_scope_rows']}",
        f"- mapped RAG rows: {counts['mapped_rag_rows']}",
        f"- BM25 indexed ids: {counts['bm25_ids']}",
        f"- generation-safe ids: {counts['generation_safe_ids']}",
        f"- audit scope rows: {counts['scope_rows_in_audit']}",
        f"- strict package scopes: {counts['strict_package_scopes']}",
        f"- warning package scopes: {counts['warning_package_scopes']}",
        f"- RAG-only scopes: {counts['rag_only_scopes']}",
        f"- no-RAG scopes: {counts['no_rag_scopes']}",
        "",
        "## Readiness Counts",
        "",
    ]
    for key, value in sorted(counts["readiness_counts"].items()):
        lines.append(f"- {key}: {value}")

    lines.extend([
        "",
        "## Priority Scopes To Rebuild First",
        "",
        "| subject | field | area | detail | readiness | rag | bm25 | safe | strict | warning | blockers |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in top_promote:
        lines.append(
            "| {subject} | {field} | {area} | {detail} | {readiness} | {total_rag_chunks} | "
            "{bm25_chunks} | {generation_safe_chunks} | {strict_packages} | {warning_packages} | {block_reasons} |".format(**row)
        )

    lines.extend([
        "",
        "## Scopes With No RAG Evidence Yet",
        "",
        "| subject | field | area | detail |",
        "|---|---|---|---|",
    ])
    for row in no_rag:
        lines.append("| {subject} | {field} | {area} | {detail} |".format(**row))

    lines.extend([
        "",
        "## Next Builder Contract",
        "",
        "1. 세부영역별 `rag_only`와 `ready_with_warnings`부터 semantic chunk를 다시 만든다.",
        "2. 큰 OCR chunk를 개념 단위 Knowledge Object로 분해한다.",
        "3. Knowledge Object마다 학습목표 Top3를 붙이고, 낮은 확신도는 자동 생성 금지로 둔다.",
        "4. `generation_safe_chunks`가 있으나 패키지가 없는 영역은 Safe Generation Package v2 승격 후보로 분리한다.",
        "5. 법규, 최신 수치, 공식, 표, 그림 기반 내용은 별도 보류한다.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_builder_contract(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "created_at": now_iso(),
        "purpose": "Semantic Chunk -> Knowledge Object -> Safe Generation Package v2 contract",
        "source_audit": str(summary["outputs"]["report_json"]),
        "knowledge_object_schema": {
            "object_id": "ko_<sha256-16>",
            "source_rag_input_ids": ["rag_input_id"],
            "source_file": "PDF name",
            "page_range": [1, 1],
            "subject": "방사선이론|방사선응용",
            "field": "출제 분야",
            "area": "출제 영역",
            "detail": "세부영역",
            "concept_name": "개념명",
            "concept_type": "definition|mechanism|comparison|procedure|quality_factor",
            "summary": "원문 재현이 아닌 새 문장 요약",
            "key_terms": ["검색 및 검증용 용어"],
            "learning_objective_candidates": [
                {"objective_id": "lo_x", "objective": "학습목표", "confidence": "high|medium|low"}
            ],
            "generation_flags": {
                "may_generate_text_question": False,
                "requires_professional_review": True,
                "holds": ["law|numeric|formula|table|figure|low_ocr|scope_uncertain"],
            },
            "evidence_policy": "answer_grounding_only_no_source_copy",
        },
        "safe_generation_package_v2_schema": {
            "package_id": "sgp2_<sha256-16>",
            "knowledge_object_ids": ["ko_id"],
            "scope": {"subject": "", "field": "", "area": "", "detail": ""},
            "learning_objective": {"objective_id": "", "objective": ""},
            "allowed_question_types": ["개념형", "비교형", "상황판단형"],
            "difficulty_candidates": ["하", "중"],
            "answerable_points": ["새 문장으로 요약한 정답 포인트"],
            "misconception_candidates": ["오답 설계용 혼동 포인트"],
            "forbidden_points": ["법규 최신성 필요", "수치 암기", "표/그림 내부 해석 미승인"],
            "minimum_evidence_objects": 1,
            "status": "strict|reviewable|hold",
        },
        "initial_gate_rules": [
            "detail and learning objective must be linked with high confidence",
            "object summary must be narrower than one textbook page",
            "no law/current numeric/formula/table/figure-only basis without approval",
            "at least one answerable point and two misconception candidates required",
            "do not generate more than five pilot questions per detail before validation",
        ],
    }
    write_json(path, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapped-rag", type=Path, default=DEFAULT_MAPPED_RAG)
    parser.add_argument("--bm25-db", type=Path, default=DEFAULT_BM25_DB)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument("--verified-scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--packages-dir", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--planning-dir", type=Path, default=DEFAULT_PLANNING_DIR)
    args = parser.parse_args()

    rows, summary = build_audit(args)
    report_json = args.report_dir / "generation_coverage_audit.json"
    report_md = args.report_dir / "generation_coverage_audit.md"
    report_csv = args.report_dir / "generation_coverage_audit.csv"
    contract_json = args.planning_dir / "knowledge_object_builder_contract.json"

    summary["outputs"] = {
        "report_json": str(report_json),
        "report_md": str(report_md),
        "report_csv": str(report_csv),
        "knowledge_object_builder_contract": str(contract_json),
    }

    fieldnames = [
        "subject",
        "field",
        "area",
        "detail",
        "scope_question_count",
        "readiness",
        "priority_score",
        "total_rag_chunks",
        "bm25_chunks",
        "generation_safe_chunks",
        "bm25_generation_safe_intersection",
        "approved_for_generation_chunks",
        "rag_evidence_chunks",
        "rag_needs_review",
        "high_mapping_chunks",
        "medium_mapping_chunks",
        "area_only_chunks",
        "learning_objective_linked_chunks",
        "hold_reason_chunks",
        "strict_packages",
        "warning_packages",
        "all_ready_packages",
        "rejected_packages",
        "excluded_packages",
        "block_reasons",
        "top_source_files",
    ]
    write_json(report_json, {"summary": summary, "rows": rows})
    write_csv(report_csv, rows, fieldnames)
    write_markdown(report_md, rows, summary)
    write_builder_contract(contract_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
