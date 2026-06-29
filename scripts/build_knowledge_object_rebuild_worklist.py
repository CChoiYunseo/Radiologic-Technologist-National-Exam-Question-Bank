#!/usr/bin/env python3
"""Build a scoped worklist for Knowledge Object rebuilding.

The worklist links scope rows to chunk identifiers, source files, and pages
without emitting source text. It is the handoff artifact for semantic chunking
and safe generation package v2 construction.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = PROJECT_ROOT / "resources" / "reports" / "generation_coverage_audit.json"
DEFAULT_MAPPED_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "knowledge_object_planning"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def scope_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("mapped_subject") or row.get("subject") or "").strip(),
        str(row.get("mapped_field") or row.get("field") or "").strip(),
        str(row.get("mapped_area") or row.get("area") or "").strip(),
        str(row.get("mapped_detail") or row.get("detail") or "").strip(),
    )


def audit_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("subject") or "").strip(),
        str(row.get("field") or "").strip(),
        str(row.get("area") or "").strip(),
        str(row.get("detail") or "").strip(),
    )


def chunk_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    confidence_order = {"high": 0, "medium": 1, "area_only": 2}
    confidence = confidence_order.get(str(row.get("scope_mapping_confidence") or ""), 3)
    has_lo = 0 if row.get("learning_objective_candidates") else 1
    page = row.get("page_or_slide")
    try:
        page_num = int(page)
    except (TypeError, ValueError):
        page_num = 999999
    return confidence, has_lo, page_num


def action_for(row: dict[str, Any]) -> str:
    readiness = row["readiness"]
    blockers = row.get("block_reasons", "")
    if "법규" in blockers or "법률" in blockers:
        return "hold_for_current_law_review"
    if readiness == "ready_with_warnings":
        return "semantic_review_then_promote_or_hold"
    if readiness == "safe_chunks_not_packaged":
        return "build_safe_generation_package_v2"
    if readiness == "rag_only":
        return "semantic_chunk_and_generation_safety_review"
    if readiness == "no_rag":
        return "source_gap_or_scope_mapping_gap_review"
    return "maintain"


def build_worklist(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit = read_json(args.audit)
    audit_rows = audit.get("rows", [])
    mapped_rows = read_jsonl(args.mapped_rag)

    mapped_by_scope: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in mapped_rows:
        mapped_by_scope[scope_key(row)].append(row)
    for rows in mapped_by_scope.values():
        rows.sort(key=chunk_rank)

    candidates = [
        row for row in audit_rows
        if row.get("readiness") in {"ready_with_warnings", "safe_chunks_not_packaged", "rag_only"}
    ]
    candidates.sort(
        key=lambda row: (
            row.get("readiness") == "rag_only",
            -int(row.get("priority_score") or 0),
            row.get("subject") or "",
            row.get("field") or "",
            row.get("area") or "",
            row.get("detail") or "",
        )
    )
    if args.limit > 0:
        candidates = candidates[: args.limit]

    worklist: list[dict[str, Any]] = []
    for order, audit_row in enumerate(candidates, start=1):
        key = audit_key(audit_row)
        chunks = mapped_by_scope.get(key, [])
        selected_chunks = []
        source_counter: Counter[str] = Counter()
        page_counter: Counter[str] = Counter()
        confidence_counter: Counter[str] = Counter()
        for chunk in chunks[: args.max_chunks_per_scope]:
            source_file = str(chunk.get("source_file") or "")
            page = str(chunk.get("page_or_slide") or "")
            confidence = str(chunk.get("scope_mapping_confidence") or "")
            source_counter[source_file] += 1
            page_counter[page] += 1
            confidence_counter[confidence] += 1
            selected_chunks.append(
                {
                    "rag_input_id": chunk.get("rag_input_id"),
                    "source_chunk_id": chunk.get("source_chunk_id"),
                    "source_file": source_file,
                    "source_path": chunk.get("source_path"),
                    "page_or_slide": chunk.get("page_or_slide"),
                    "scope_mapping_confidence": confidence,
                    "scope_mapping_status": chunk.get("scope_mapping_status"),
                    "scope_mapping_needs_review": bool(chunk.get("scope_mapping_needs_review") or chunk.get("needs_review")),
                    "has_learning_objective_candidates": bool(chunk.get("learning_objective_candidates")),
                    "learning_objective_candidate_count": len(chunk.get("learning_objective_candidates") or []),
                    "generation_hold_reasons": chunk.get("generation_hold_reasons") or [],
                    "content_sha256": chunk.get("content_sha256"),
                }
            )
        worklist.append(
            {
                "work_order": order,
                "recommended_action": action_for(audit_row),
                "scope": {
                    "subject": audit_row.get("subject"),
                    "field": audit_row.get("field"),
                    "area": audit_row.get("area"),
                    "detail": audit_row.get("detail"),
                    "scope_question_count": audit_row.get("scope_question_count"),
                },
                "audit": {
                    "readiness": audit_row.get("readiness"),
                    "priority_score": audit_row.get("priority_score"),
                    "block_reasons": audit_row.get("block_reasons"),
                    "total_rag_chunks": audit_row.get("total_rag_chunks"),
                    "bm25_chunks": audit_row.get("bm25_chunks"),
                    "generation_safe_chunks": audit_row.get("generation_safe_chunks"),
                    "strict_packages": audit_row.get("strict_packages"),
                    "warning_packages": audit_row.get("warning_packages"),
                    "high_mapping_chunks": audit_row.get("high_mapping_chunks"),
                    "medium_mapping_chunks": audit_row.get("medium_mapping_chunks"),
                    "area_only_chunks": audit_row.get("area_only_chunks"),
                    "learning_objective_linked_chunks": audit_row.get("learning_objective_linked_chunks"),
                },
                "selected_chunk_count": len(selected_chunks),
                "selected_source_files": dict(source_counter.most_common()),
                "selected_page_counts": dict(page_counter.most_common()),
                "selected_confidence_counts": dict(confidence_counter),
                "selected_chunks": selected_chunks,
                "policy": {
                    "source_text_included": False,
                    "question_generation_performed": False,
                    "next_step": "Use linked chunks to create narrow Knowledge Objects in new wording.",
                },
            }
        )

    report = {
        "created_at": now_iso(),
        "inputs": {
            "audit": str(args.audit),
            "mapped_rag": str(args.mapped_rag),
        },
        "outputs": {
            "worklist_jsonl": str(args.output_dir / "knowledge_object_rebuild_worklist.jsonl"),
            "report_json": str(args.output_dir / "knowledge_object_rebuild_worklist_report.json"),
            "report_md": str(args.output_dir / "knowledge_object_rebuild_worklist_report.md"),
        },
        "counts": {
            "worklist_rows": len(worklist),
            "selected_chunk_refs": sum(row["selected_chunk_count"] for row in worklist),
            "recommended_actions": dict(Counter(row["recommended_action"] for row in worklist)),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
        },
    }
    return worklist, report


def write_markdown(path: Path, worklist: list[dict[str, Any]], report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Knowledge Object Rebuild Worklist",
        "",
        "이 작업목록은 원문을 포함하지 않고, Knowledge Object 재구축에 필요한 위치 정보만 연결한다.",
        "",
        "## Summary",
        "",
        f"- worklist rows: {report['counts']['worklist_rows']}",
        f"- selected chunk refs: {report['counts']['selected_chunk_refs']}",
        "",
        "## Recommended Actions",
        "",
    ]
    for action, count in sorted(report["counts"]["recommended_actions"].items()):
        lines.append(f"- {action}: {count}")
    lines.extend([
        "",
        "## Top Work Items",
        "",
        "| order | action | subject | field | area | detail | readiness | chunks | blockers |",
        "|---:|---|---|---|---|---|---|---:|---|",
    ])
    for row in worklist[:30]:
        scope = row["scope"]
        audit = row["audit"]
        lines.append(
            "| {order} | {action} | {subject} | {field} | {area} | {detail} | {readiness} | {chunks} | {blockers} |".format(
                order=row["work_order"],
                action=row["recommended_action"],
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                readiness=audit.get("readiness") or "",
                chunks=row["selected_chunk_count"],
                blockers=audit.get("block_reasons") or "",
            )
        )
    lines.extend([
        "",
        "## Usage",
        "",
        "1. `semantic_review_then_promote_or_hold`: warning 패키지의 범위·학습목표·보류 사유를 재검토한다.",
        "2. `build_safe_generation_package_v2`: 이미 생성 안전 후보가 있으므로 Knowledge Object 요약 후 패키지로 승격한다.",
        "3. `semantic_chunk_and_generation_safety_review`: RAG 근거는 있으나 생성 안전 후보가 없으므로 semantic chunk와 안전성 검토를 먼저 수행한다.",
        "4. `source_gap_or_scope_mapping_gap_review`: 자료 부재인지 매핑 누락인지 확인한다.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--mapped-rag", type=Path, default=DEFAULT_MAPPED_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--max-chunks-per-scope", type=int, default=40)
    args = parser.parse_args()

    worklist, report = build_worklist(args)
    output_jsonl = args.output_dir / "knowledge_object_rebuild_worklist.jsonl"
    output_report_json = args.output_dir / "knowledge_object_rebuild_worklist_report.json"
    output_report_md = args.output_dir / "knowledge_object_rebuild_worklist_report.md"
    write_jsonl(output_jsonl, worklist)
    write_json(output_report_json, report)
    write_markdown(output_report_md, worklist, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
