#!/usr/bin/env python3
"""Build worklists for expanding safe question-generation packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REJECT_WORKLIST = (
    PROJECT_ROOT
    / "resources/generated/review_candidates_shortage_run_20260626T030124Z/llm_secondary_validation_runs/"
    / "run_20260626T075737Z_limit9_offset0/verdict_followup/reject_reselection_worklist.jsonl"
)
DEFAULT_SCOPE_REMAP = (
    PROJECT_ROOT
    / "resources/generated/text_question_shortage_worklist/coverage_run_20260626T030124Z/scope_remap_targets.jsonl"
)
DEFAULT_OBJECTIVE_TARGETS = (
    PROJECT_ROOT
    / "resources/generated/text_question_shortage_worklist/coverage_run_20260626T030124Z/learning_objective_targets.jsonl"
)
DEFAULT_RAG = PROJECT_ROOT / "resources/extracted/rag_index_input/rag_index_input_mapped.jsonl"
DEFAULT_DB = PROJECT_ROOT / "resources/extracted/rag_search_index_text_bm25/rag_text_bm25.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/safe_package_expansion_worklists/run_20260626T081717Z"

HOLD_MARKERS = ("법령", "조문", "별표", "표 ", "표-", "그림", "수식", "공식", "영상")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def infer_period(subject: Any, period: Any = "") -> str:
    explicit = str(period or "").strip()
    if explicit:
        return explicit
    subject_text = str(subject or "").strip()
    if subject_text in {"방사선이론", "의료법규"}:
        return "1교시"
    if subject_text == "방사선응용":
        return "2교시"
    return ""


def scope_from(row: dict[str, Any]) -> dict[str, Any]:
    scope = row.get("scope") or row.get("requested_scope") or row
    subject = scope.get("subject") or row.get("subject") or ""
    return {
        "period": infer_period(subject, scope.get("period") or row.get("period")),
        "subject": subject,
        "field": scope.get("field") or row.get("field") or "",
        "area": scope.get("area") or row.get("area") or "",
        "detail": scope.get("detail") or row.get("detail") or "",
        "scope_id": scope.get("scope_id") or row.get("scope_id") or "",
    }


def scope_key(scope: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        compact(scope.get("period")),
        compact(scope.get("subject")),
        compact(scope.get("field")),
        compact(scope.get("area")),
        compact(scope.get("detail")),
    )


def text_is_hold_like(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ["source_type", "chunk_type", "excerpt", "content", "mapped_detail", "mapped_area"]
    )
    return any(marker in text for marker in HOLD_MARKERS)


def is_text_candidate(row: dict[str, Any]) -> bool:
    if text_is_hold_like(row):
        return False
    return True


def ref_for(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rag_input_id": row.get("rag_input_id"),
        "source_file": row.get("source_file"),
        "page_or_slide": row.get("page_or_slide"),
        "scope_mapping_status": row.get("scope_mapping_status"),
        "scope_mapping_confidence": row.get("scope_mapping_confidence"),
    }


def build_rag_index(rag_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in rag_rows:
        subject = row.get("mapped_subject") or ""
        scope = {
            "period": infer_period(subject, row.get("mapped_period")),
            "subject": subject,
            "field": row.get("mapped_field") or "",
            "area": row.get("mapped_area") or "",
            "detail": row.get("mapped_detail") or "",
        }
        index.setdefault(scope_key(scope), []).append(row)
    return index


def fts_query(text: str) -> str:
    tokens = [token for token in re.findall(r"[0-9A-Za-z가-힣]+", text) if token.strip()]
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:8]) or '"근거"'


def search_replacement_refs(db_path: Path, scope: dict[str, Any], used_ids: set[str], top_k: int) -> list[dict[str, Any]]:
    query = " ".join(str(scope.get(key) or "") for key in ["area", "detail"])
    where = ["rag_fts MATCH ?"]
    params: list[Any] = [fts_query(query)]
    if scope.get("subject"):
        where.append("chunks.mapped_subject = ?")
        params.append(scope["subject"])
    if scope.get("field"):
        where.append("chunks.mapped_field = ?")
        params.append(scope["field"])
    if scope.get("area"):
        where.append("chunks.mapped_area = ?")
        params.append(scope["area"])
    params.append(top_k * 4)
    sql = f"""
        SELECT
            chunks.rag_input_id,
            chunks.source_file,
            chunks.page_or_slide,
            chunks.excerpt,
            chunks.mapped_subject,
            chunks.mapped_field,
            chunks.mapped_area,
            chunks.mapped_detail,
            chunks.scope_mapping_status,
            chunks.scope_mapping_confidence,
            chunks.scope_mapping_needs_review,
            bm25(rag_fts, 1.0, 0.35) AS score
        FROM rag_fts
        JOIN chunks ON chunks.doc_id = rag_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        candidate = {
            "rag_input_id": row[0],
            "source_file": row[1],
            "page_or_slide": row[2],
            "excerpt": row[3],
            "mapped_subject": row[4],
            "mapped_field": row[5],
            "mapped_area": row[6],
            "mapped_detail": row[7],
            "scope_mapping_status": row[8],
            "scope_mapping_confidence": row[9],
            "scope_mapping_needs_review": bool(row[10]),
            "score": row[11],
        }
        if candidate["rag_input_id"] in used_ids:
            continue
        if text_is_hold_like(candidate):
            continue
        results.append(candidate)
        if len(results) >= top_k:
            break
    return results


def ranked_scope_refs(scope: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    def priority(row: dict[str, Any]) -> tuple[int, int, str]:
        confidence = str(row.get("scope_mapping_confidence") or "")
        status = str(row.get("scope_mapping_status") or "")
        conf_score = {"high": 0, "medium": 1, "area_only": 2}.get(confidence, 3)
        status_score = 1 if status == "needs_review" or row.get("scope_mapping_needs_review") else 0
        return (conf_score, status_score, str(row.get("rag_input_id") or ""))

    selected = [row for row in rows if is_text_candidate(row)]
    selected.sort(key=priority)
    return [ref_for(row) for row in selected[:limit]]


def build_expansion_packages(targets: list[dict[str, Any]], rag_index: dict[tuple[str, str, str, str, str], list[dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    packages = []
    for target in targets:
        scope = scope_from(target)
        rows = rag_index.get(scope_key(scope), [])
        refs = ranked_scope_refs(scope, rows, 12)
        if len(refs) < 2:
            status = "needs_manual_or_semantic_chunking_before_package"
        elif kind == "scope_remap":
            status = "pending_scope_confirmation_before_semantic_review"
        else:
            status = "pending_learning_objective_confirmation_before_semantic_review"
        packages.append(
            {
                "package_rebuild_id": f"expand_sgp_{short_hash({'kind': kind, 'scope': scope})}",
                "created_at": now_iso(),
                "status": status,
                "expansion_kind": kind,
                "scope": scope,
                "needed_question_count": int(target.get("needed_question_count") or 0),
                "candidate_ref_count": len(refs),
                "candidate_refs": refs,
                "review_requirements": {
                    "confirm_scope_alignment": kind == "scope_remap",
                    "confirm_learning_objective_alignment": kind == "learning_objective",
                    "confirm_not_visual_table_formula_law": True,
                    "confirm_content_can_ground_new_wording": True,
                    "minimum_refs_to_promote": 2,
                },
                "source_text_included": False,
            }
        )
    return packages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reject-worklist", type=Path, default=DEFAULT_REJECT_WORKLIST)
    parser.add_argument("--scope-remap-targets", type=Path, default=DEFAULT_SCOPE_REMAP)
    parser.add_argument("--learning-objective-targets", type=Path, default=DEFAULT_OBJECTIVE_TARGETS)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--replacement-top-k", type=int, default=8)
    args = parser.parse_args()

    reject_rows = read_jsonl(args.reject_worklist)
    scope_targets = read_jsonl(args.scope_remap_targets)
    objective_targets = read_jsonl(args.learning_objective_targets)
    rag_rows = read_jsonl(args.rag)
    rag_index = build_rag_index(rag_rows)

    rejected_discarded: list[dict[str, Any]] = []
    rejected_research: list[dict[str, Any]] = []
    for row in reject_rows:
        scope = scope_from(row)
        used_ids: set[str] = set()
        result_path = Path(row.get("llm_validation_result") or "")
        if result_path.exists():
            validation = json.loads(result_path.read_text(encoding="utf-8"))
            # IDs are not always present in result, so retain this hook for future data.
            for ref in validation.get("evidence_refs") or []:
                if isinstance(ref, dict) and ref.get("rag_input_id"):
                    used_ids.add(str(ref["rag_input_id"]))
        replacement_refs = search_replacement_refs(args.db, scope, used_ids, args.replacement_top_k)
        rejected_discarded.append(
            {
                "package_id": row.get("package_id"),
                "validation_package_id": row.get("validation_package_id"),
                "review_candidate_id": row.get("review_candidate_id"),
                "status": "discarded_after_llm_secondary_reject",
                "scope": scope,
                "discard_reason_summary": row.get("rejection_summary") or {},
            }
        )
        rejected_research.append(
            {
                "package_id": row.get("package_id"),
                "status": "needs_new_evidence_before_regeneration",
                "scope": scope,
                "replacement_candidate_count": len(replacement_refs),
                "replacement_candidate_refs": [
                    {
                        "rag_input_id": ref.get("rag_input_id"),
                        "source_file": ref.get("source_file"),
                        "page_or_slide": ref.get("page_or_slide"),
                        "scope_mapping_status": ref.get("scope_mapping_status"),
                        "scope_mapping_confidence": ref.get("scope_mapping_confidence"),
                        "search_score": ref.get("score"),
                    }
                    for ref in replacement_refs
                ],
                "next_action": "새 근거와 학습목표를 확인한 뒤 신규 생성 패키지를 만든다.",
                "source_text_included": False,
            }
        )

    scope_packages = build_expansion_packages(scope_targets, rag_index, "scope_remap")
    objective_packages = build_expansion_packages(objective_targets, rag_index, "learning_objective")
    all_packages = scope_packages + objective_packages
    ready_for_semantic = [
        package
        for package in all_packages
        if package["candidate_ref_count"] >= 2
        and package["status"] != "needs_manual_or_semantic_chunking_before_package"
    ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "rejected_discarded": args.output_dir / "rejected_draft_discarded_index.jsonl",
        "reject_research": args.output_dir / "reject_new_evidence_research_worklist.jsonl",
        "scope_remap_packages": args.output_dir / "scope_remap_safe_package_expansion_candidates.jsonl",
        "learning_objective_packages": args.output_dir / "learning_objective_safe_package_expansion_candidates.jsonl",
        "semantic_review_candidates": args.output_dir / "next_semantic_review_package_candidates.jsonl",
        "report_json": args.output_dir / "safe_package_expansion_worklist_report.json",
        "report_md": args.output_dir / "safe_package_expansion_worklist_report.md",
    }
    write_jsonl(outputs["rejected_discarded"], rejected_discarded)
    write_jsonl(outputs["reject_research"], rejected_research)
    write_jsonl(outputs["scope_remap_packages"], scope_packages)
    write_jsonl(outputs["learning_objective_packages"], objective_packages)
    write_jsonl(outputs["semantic_review_candidates"], ready_for_semantic)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "reject_worklist": str(args.reject_worklist),
            "scope_remap_targets": str(args.scope_remap_targets),
            "learning_objective_targets": str(args.learning_objective_targets),
            "rag": str(args.rag),
        },
        "outputs": {key: str(value) for key, value in outputs.items()},
        "counts": {
            "rejected_discarded": len(rejected_discarded),
            "reject_research_targets": len(rejected_research),
            "scope_remap_targets": len(scope_targets),
            "learning_objective_targets": len(objective_targets),
            "scope_remap_packages": len(scope_packages),
            "learning_objective_packages": len(objective_packages),
            "semantic_review_candidates": len(ready_for_semantic),
            "needs_manual_or_semantic_chunking": sum(1 for package in all_packages if package["candidate_ref_count"] < 2),
            "semantic_candidates_by_subject": dict(Counter((pkg.get("scope") or {}).get("subject") for pkg in ready_for_semantic)),
        },
        "policy": {
            "question_generation_performed": False,
            "source_text_included": False,
            "automatic_generation_approval_granted": False,
            "visual_formula_table_law_default_excluded": True,
        },
    }
    write_json(outputs["report_json"], report)
    write_markdown(outputs["report_md"], report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Safe Package Expansion Worklist Report",
        "",
        "반려 문항은 폐기 대상으로 분리하고, 부족 범위는 안전 패키지 확장 후보로 정리했다.",
        "",
        "## Summary",
        "",
        f"- 반려 초안 폐기: {counts['rejected_discarded']}",
        f"- 새 근거 재검색 대상: {counts['reject_research_targets']}",
        f"- 세부영역 재매핑 후보: {counts['scope_remap_packages']}",
        f"- 학습목표 보강 후보: {counts['learning_objective_packages']}",
        f"- 다음 semantic 검토 후보: {counts['semantic_review_candidates']}",
        f"- 추가 수동/의미 단위 보강 필요: {counts['needs_manual_or_semantic_chunking']}",
        "",
        "## Outputs",
    ]
    for key, value in report["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
