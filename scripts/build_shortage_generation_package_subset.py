#!/usr/bin/env python3
"""Build a question-generation package subset for current exam shortages."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERED = (
    PROJECT_ROOT
    / "resources/generated/text_question_shortage_worklist/coverage_run_20260626T030124Z/covered_shortage_by_packages.jsonl"
)
DEFAULT_PACKAGES = (
    PROJECT_ROOT
    / "resources/generated/semantic_reviewed_pilot_question_packages/run_20260626T030124Z/semantic_reviewed_pilot_question_request_packages.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/text_question_shortage_generation_packages/run_20260626T030124Z"


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
    scope = row.get("requested_scope") or row
    return (
        compact(scope.get("period")),
        compact(scope.get("subject")),
        compact(scope.get("field")),
        compact(scope.get("area")),
        compact(scope.get("detail")),
    )


def scope_label(row: dict[str, Any]) -> str:
    scope = row.get("requested_scope") or row
    return " / ".join(
        str(scope.get(key) or "")
        for key in ["period", "subject", "field", "area", "detail"]
        if scope.get(key)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--covered", type=Path, default=DEFAULT_COVERED)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    covered_rows = read_jsonl(args.covered)
    packages = read_jsonl(args.packages)

    packages_by_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for package in packages:
        packages_by_key[scope_key(package)].append(package)

    selected: list[dict[str, Any]] = []
    shortages_without_enough_packages: list[dict[str, Any]] = []
    for covered in covered_rows:
        needed = int(covered.get("needed_question_count") or 0)
        available = packages_by_key.get(scope_key(covered), [])
        chosen = available[:needed]
        if len(chosen) < needed:
            shortages_without_enough_packages.append(
                {
                    **covered,
                    "available_package_count": len(available),
                    "selected_package_count": len(chosen),
                    "still_needed_question_count": needed - len(chosen),
                }
            )
        for index, package in enumerate(chosen, start=1):
            enriched = dict(package)
            enriched["shortage_generation_target"] = {
                "target_scope": {
                    key: covered.get(key, "")
                    for key in ["period", "subject", "field", "area", "detail"]
                },
                "needed_question_count_for_scope": needed,
                "selected_order_for_scope": index,
                "source_coverage_report": str(args.covered),
            }
            selected.append(enriched)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "covered_shortage": str(args.covered),
            "packages": str(args.packages),
        },
        "outputs": {
            "generation_packages": str(args.output_dir / "shortage_question_request_packages.jsonl"),
            "shortages_without_enough_packages": str(args.output_dir / "shortages_without_enough_packages.jsonl"),
            "report_json": str(args.output_dir / "shortage_generation_package_subset_report.json"),
            "report_md": str(args.output_dir / "shortage_generation_package_subset_report.md"),
        },
        "counts": {
            "covered_targets": len(covered_rows),
            "requested_question_count": sum(int(row.get("needed_question_count") or 0) for row in covered_rows),
            "selected_generation_packages": len(selected),
            "shortages_without_enough_packages": len(shortages_without_enough_packages),
        },
        "selected_scope_labels": [scope_label(row) for row in selected],
        "policy": {
            "visual_draft_used": False,
            "law_currentness_held": True,
            "semantic_reviewed_safe_refs_only": True,
            "question_generation_performed": False,
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "shortage_question_request_packages.jsonl", selected)
    write_jsonl(args.output_dir / "shortages_without_enough_packages.jsonl", shortages_without_enough_packages)
    write_json(args.output_dir / "shortage_generation_package_subset_report.json", report)
    write_markdown(args.output_dir / "shortage_generation_package_subset_report.md", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Shortage Generation Package Subset",
        "",
        "현재 시험지 부족분 중 최신 semantic-reviewed 근거로 바로 생성 가능한 패키지만 분리했다.",
        "",
        f"- 커버된 세부영역: {counts['covered_targets']}",
        f"- 요청 문항 수: {counts['requested_question_count']}",
        f"- 선택된 생성 패키지: {counts['selected_generation_packages']}",
        f"- 패키지 수 부족 세부영역: {counts['shortages_without_enough_packages']}",
        "",
        "## Outputs",
    ]
    for key, value in report["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
