#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHORTAGE = PROJECT_ROOT / "resources/generated/text_question_shortage_worklist/text_question_shortage_worklist.jsonl"
DEFAULT_PACKAGES = (
    PROJECT_ROOT
    / "resources/generated/semantic_reviewed_pilot_question_packages/run_20260626T030124Z/semantic_reviewed_pilot_question_request_packages.jsonl"
)
DEFAULT_RAG = PROJECT_ROOT / "resources/extracted/rag_index_input/rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/text_question_shortage_worklist/coverage_run_20260626T030124Z"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def scope_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    scope = row.get("requested_scope") or row.get("scope") or row
    return (
        compact(scope.get("period")),
        compact(scope.get("subject")),
        compact(scope.get("field")),
        compact(scope.get("area")),
        compact(scope.get("detail")),
    )


def display_scope(row: dict[str, Any]) -> dict[str, Any]:
    scope = row.get("requested_scope") or row.get("scope") or row
    return {
        "period": scope.get("period") or row.get("period") or "",
        "subject": scope.get("subject") or row.get("subject") or "",
        "field": scope.get("field") or row.get("field") or "",
        "area": scope.get("area") or row.get("area") or "",
        "detail": scope.get("detail") or row.get("detail") or "",
    }


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


def build_rag_scope_index(rag_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rag_rows:
        mapped_period = infer_period(row.get("mapped_subject"), row.get("mapped_period"))
        key = (
            compact(mapped_period),
            compact(row.get("mapped_subject")),
            compact(row.get("mapped_field")),
            compact(row.get("mapped_area")),
            compact(row.get("mapped_detail")),
        )
        if not any(key):
            continue
        bucket = index.setdefault(
            key,
            {
                "mapped_period": mapped_period,
                "mapped_subject": row.get("mapped_subject") or "",
                "mapped_field": row.get("mapped_field") or "",
                "mapped_area": row.get("mapped_area") or "",
                "mapped_detail": row.get("mapped_detail") or "",
                "total": 0,
                "high": 0,
                "medium": 0,
                "area_only": 0,
                "needs_review": 0,
                "text_only_candidate": 0,
            },
        )
        bucket["total"] += 1
        confidence = row.get("scope_mapping_confidence") or ""
        status = row.get("scope_mapping_status") or ""
        if confidence == "high":
            bucket["high"] += 1
        elif confidence == "medium":
            bucket["medium"] += 1
        elif confidence == "area_only":
            bucket["area_only"] += 1
        if status == "needs_review" or row.get("scope_mapping_needs_review"):
            bucket["needs_review"] += 1
        joined = " ".join(
            str(row.get(key) or "")
            for key in ["source_type", "chunk_type", "content", "excerpt"]
        )
        if not any(token in joined for token in ["표", "그림", "수식", "법규", "조문"]):
            bucket["text_only_candidate"] += 1
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shortage", type=Path, default=DEFAULT_SHORTAGE)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    shortage_rows = read_jsonl(args.shortage)
    package_rows = read_jsonl(args.packages)
    rag_rows = read_jsonl(args.rag)
    rag_index = build_rag_scope_index(rag_rows)

    packages_by_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for package in package_rows:
        packages_by_key[scope_key(package)].append(package)

    covered: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    remap_targets: list[dict[str, Any]] = []
    objective_targets: list[dict[str, Any]] = []

    for shortage in shortage_rows:
        key = scope_key(shortage)
        packages = packages_by_key.get(key, [])
        rag_stats = rag_index.get(key, {})
        row = {
            **display_scope(shortage),
            "needed_question_count": int(shortage.get("needed_question_count") or 0),
            "available_package_count": len(packages),
            "available_package_ids": [pkg.get("package_id") for pkg in packages],
            "rag_scope_stats": rag_stats,
        }
        if packages:
            covered.append(row)
            continue
        uncovered.append(row)
        if rag_stats.get("area_only") or rag_stats.get("needs_review"):
            remap_targets.append(row)
        else:
            objective_targets.append(row)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "shortage": str(args.shortage),
            "packages": str(args.packages),
            "rag": str(args.rag),
        },
        "outputs": {
            "covered": str(args.output_dir / "covered_shortage_by_packages.jsonl"),
            "uncovered": str(args.output_dir / "uncovered_shortage_after_latest_semantic_packages.jsonl"),
            "scope_remap_targets": str(args.output_dir / "scope_remap_targets.jsonl"),
            "learning_objective_targets": str(args.output_dir / "learning_objective_targets.jsonl"),
            "report_json": str(args.output_dir / "shortage_package_coverage_report.json"),
            "report_md": str(args.output_dir / "shortage_package_coverage_report.md"),
        },
        "counts": {
            "shortage_targets": len(shortage_rows),
            "shortage_question_count": sum(int(row.get("needed_question_count") or 0) for row in shortage_rows),
            "package_count": len(package_rows),
            "covered_targets": len(covered),
            "covered_question_count": sum(row["needed_question_count"] for row in covered),
            "uncovered_targets": len(uncovered),
            "uncovered_question_count": sum(row["needed_question_count"] for row in uncovered),
            "scope_remap_target_count": len(remap_targets),
            "learning_objective_target_count": len(objective_targets),
            "covered_by_subject": dict(Counter(row.get("subject") for row in covered)),
            "uncovered_by_subject": dict(Counter(row.get("subject") for row in uncovered)),
        },
        "policy": {
            "visual_draft_used": False,
            "question_generation_performed": False,
            "next_step": "generate_questions_for_covered_targets_then_remap_uncovered_scope_chunks",
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "covered_shortage_by_packages.jsonl", covered)
    write_jsonl(args.output_dir / "uncovered_shortage_after_latest_semantic_packages.jsonl", uncovered)
    write_jsonl(args.output_dir / "scope_remap_targets.jsonl", remap_targets)
    write_jsonl(args.output_dir / "learning_objective_targets.jsonl", objective_targets)
    write_json(args.output_dir / "shortage_package_coverage_report.json", report)
    write_markdown(args.output_dir / "shortage_package_coverage_report.md", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Shortage Package Coverage Report",
        "",
        "최신 semantic review pass 결과로 만든 생성 패키지가 부족 문항 목록을 얼마나 덮는지 점검했다.",
        "",
        "## Summary",
        "",
        f"- 부족 세부영역: {counts['shortage_targets']}",
        f"- 부족 문항 수: {counts['shortage_question_count']}",
        f"- 최신 생성 패키지: {counts['package_count']}",
        f"- 즉시 생성 가능 세부영역: {counts['covered_targets']}",
        f"- 즉시 생성 가능 문항 수: {counts['covered_question_count']}",
        f"- 미커버 세부영역: {counts['uncovered_targets']}",
        f"- 미커버 문항 수: {counts['uncovered_question_count']}",
        f"- 세부영역 재매핑 우선 대상: {counts['scope_remap_target_count']}",
        f"- 학습목표/근거 보강 우선 대상: {counts['learning_objective_target_count']}",
        "",
        "## Outputs",
    ]
    for key, value in report["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
