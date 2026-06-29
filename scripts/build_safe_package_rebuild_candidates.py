#!/usr/bin/env python3
"""Build metadata-only safe package rebuild candidates from review worklist."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE_WORKLIST = (
    PROJECT_ROOT
    / "resources/generated/generation_safety_review/generation_safety_scope_worklist.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/safe_generation_package_rebuild_candidates"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


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


def package_id(scope: dict[str, Any]) -> str:
    key = "|".join(str(scope.get(field) or "") for field in ["period", "subject", "field", "area", "detail"])
    return f"rebuild_sgp_{short_hash(key)}"


def build(args: argparse.Namespace) -> dict[str, Any]:
    scope_rows = read_jsonl(args.scope_worklist)
    selected = [
        row
        for row in scope_rows
        if row.get("package_rebuild_priority") in set(args.priorities)
        and int(row.get("candidate_chunks") or 0) >= args.min_candidate_refs
    ]
    packages: list[dict[str, Any]] = []
    for row in selected:
        scope = row.get("scope") or {}
        refs = row.get("candidate_refs") or []
        packages.append(
            {
                "package_rebuild_id": package_id(scope),
                "created_at": now_iso(),
                "status": "pending_semantic_generation_safety_review",
                "scope": scope,
                "candidate_ref_count": row.get("candidate_chunks"),
                "hold_ref_count": row.get("hold_chunks"),
                "candidate_refs": refs,
                "review_requirements": {
                    "confirm_scope_alignment": True,
                    "confirm_not_visual_table_formula_law": True,
                    "confirm_not_numeric_standard_dependent": True,
                    "confirm_content_can_ground_new_wording": True,
                    "minimum_refs_to_promote": args.min_candidate_refs,
                },
                "post_review_action": {
                    "if_pass": "create_safe_generation_package_v3_or_promote_refs",
                    "if_fail": "return_to_semantic_chunking_or_manual_review",
                },
                "source_text_included": False,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    packages_path = args.output_dir / "safe_package_rebuild_candidates.jsonl"
    report_json_path = args.output_dir / "safe_package_rebuild_candidates_report.json"
    report_md_path = args.output_dir / "safe_package_rebuild_candidates_report.md"
    write_jsonl(packages_path, packages)

    report = {
        "created_at": now_iso(),
        "inputs": {"scope_worklist": str(args.scope_worklist)},
        "outputs": {
            "rebuild_candidates": str(packages_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": {
            "input_scopes": len(scope_rows),
            "rebuild_candidates": len(packages),
            "by_subject": dict(Counter((pkg.get("scope") or {}).get("subject") for pkg in packages)),
            "by_priority": dict(Counter(row.get("package_rebuild_priority") for row in selected)),
        },
        "policy": {
            "source_text_included": False,
            "automatic_generation_approval_granted": False,
            "question_generation_performed": False,
        },
    }
    write_json(report_json_path, report)
    write_markdown(report_md_path, report, packages)
    return report


def write_markdown(path: Path, report: dict[str, Any], packages: list[dict[str, Any]]) -> None:
    lines = [
        "# Safe Package Rebuild Candidates",
        "",
        "이 파일은 자동 문항 생성을 실행하지 않고, 검수 후 Safe Generation Package로 승격할 후보 범위를 정리한다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Candidates",
            "",
            "| package_rebuild_id | subject | field | area | detail | candidate refs | hold refs |",
            "|---|---|---|---|---|---:|---:|",
        ]
    )
    for pkg in packages[:80]:
        scope = pkg.get("scope") or {}
        lines.append(
            "| {pid} | {subject} | {field} | {area} | {detail} | {candidates} | {holds} |".format(
                pid=pkg.get("package_rebuild_id"),
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                candidates=pkg.get("candidate_ref_count"),
                holds=pkg.get("hold_ref_count"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope-worklist", type=Path, default=DEFAULT_SCOPE_WORKLIST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--priorities", nargs="+", default=["high"])
    parser.add_argument("--min-candidate-refs", type=int, default=2)
    args = parser.parse_args()
    report = build(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
