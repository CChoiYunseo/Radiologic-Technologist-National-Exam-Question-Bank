#!/usr/bin/env python3
"""Apply curated KO scope metadata to semantic RAG input rows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "resources/extracted/rag_index_input_semantic_ko_v2/rag_index_input_semantic_ko_v2.jsonl"
DEFAULT_CURATED_KOS = (
    PROJECT_ROOT
    / "resources/generated/knowledge_objects_v2_semantic_curated/knowledge_objects_v2_semantic_curated_ready.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/extracted/rag_index_input_semantic_ko_v2_curated"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_no}")
            rows.append(value)
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_ref_scope_map(kos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    conflicts: dict[str, list[dict[str, Any]]] = {}
    for ko in kos:
        scope = ko.get("scope") or {}
        quality = ko.get("quality_reclassification") or {}
        for ref in (ko.get("use_direct_refs") or []) + (ko.get("use_supporting_refs") or []):
            rag_input_id = ref.get("rag_input_id")
            if not rag_input_id:
                continue
            payload = {
                "scope": {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]},
                "mapped_scope_id": scope.get("scope_id") or "",
                "knowledge_object_id": ko.get("knowledge_object_id"),
                "quality_grade": quality.get("grade"),
            }
            existing = mapping.get(rag_input_id)
            if existing and existing["scope"] != payload["scope"]:
                conflicts.setdefault(rag_input_id, [existing]).append(payload)
                continue
            mapping[rag_input_id] = payload
    if conflicts:
        raise ValueError(f"Conflicting curated scopes for rag_input_id(s): {', '.join(sorted(conflicts)[:5])}")
    return mapping


def apply_scope(row: dict[str, Any], payload: dict[str, Any], created_at: str) -> tuple[dict[str, Any], bool]:
    output = dict(row)
    scope = payload["scope"]
    before = {
        "mapped_period": output.get("mapped_period"),
        "mapped_subject": output.get("mapped_subject"),
        "mapped_field": output.get("mapped_field"),
        "mapped_area": output.get("mapped_area"),
        "mapped_detail": output.get("mapped_detail"),
        "mapped_scope_id": output.get("mapped_scope_id"),
    }
    output.update(
        {
            "mapped_period": scope.get("period") or "",
            "mapped_subject": scope.get("subject") or "",
            "mapped_field": scope.get("field") or "",
            "mapped_area": scope.get("area") or "",
            "mapped_detail": scope.get("detail") or "",
            "mapped_scope_id": payload.get("mapped_scope_id") or "",
        }
    )
    after = {
        "mapped_period": output.get("mapped_period"),
        "mapped_subject": output.get("mapped_subject"),
        "mapped_field": output.get("mapped_field"),
        "mapped_area": output.get("mapped_area"),
        "mapped_detail": output.get("mapped_detail"),
        "mapped_scope_id": output.get("mapped_scope_id"),
    }
    changed = before != after
    if changed:
        output["curated_scope_applied"] = {
            "applied_at": created_at,
            "source": "knowledge_objects_v2_semantic_curated",
            "knowledge_object_id": payload.get("knowledge_object_id"),
            "quality_grade": payload.get("quality_grade"),
            "before": before,
            "after": after,
        }
        reasons = list(output.get("candidate_reasons") or [])
        reasons.append("curated_ko_scope_metadata_applied")
        output["candidate_reasons"] = sorted(dict.fromkeys(reasons))
    return output, changed


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows = read_jsonl(args.input)
    kos = read_jsonl(args.curated_kos)
    scope_by_ref = build_ref_scope_map(kos)
    row_ids = {row.get("rag_input_id") for row in rows}
    missing_refs = sorted(set(scope_by_ref) - row_ids)
    created_at = now_iso()
    output_rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    for row in rows:
        rag_input_id = row.get("rag_input_id")
        payload = scope_by_ref.get(rag_input_id)
        if not payload:
            output_rows.append(row)
            continue
        updated, changed = apply_scope(row, payload, created_at)
        output_rows.append(updated)
        if changed:
            changes.append(
                {
                    "rag_input_id": rag_input_id,
                    "knowledge_object_id": payload.get("knowledge_object_id"),
                    "before": updated["curated_scope_applied"]["before"],
                    "after": updated["curated_scope_applied"]["after"],
                }
            )

    report = {
        "created_at": created_at,
        "inputs": {
            "semantic_rag_input": str(args.input),
            "curated_kos": str(args.curated_kos),
        },
        "outputs": {
            "rag_input": str(args.output_dir / "rag_index_input_semantic_ko_v2_curated.jsonl"),
            "changes": str(args.output_dir / "curated_scope_changes.jsonl"),
            "report_json": str(args.output_dir / "curated_semantic_rag_input_report.json"),
        },
        "counts": {
            "input_rows": len(rows),
            "output_rows": len(output_rows),
            "curated_ref_ids": len(scope_by_ref),
            "changed_rows": len(changes),
            "missing_ref_ids": len(missing_refs),
            "changed_by_detail": dict(Counter(change["after"].get("mapped_detail") for change in changes)),
        },
        "missing_ref_ids": missing_refs,
        "policy": {
            "content_rewritten": False,
            "rag_input_ids_preserved": True,
            "curated_scope_metadata_only": True,
        },
    }
    return output_rows, changes, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--curated-kos", type=Path, default=DEFAULT_CURATED_KOS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    rows, changes, report = build(args)
    write_jsonl(args.output_dir / "rag_index_input_semantic_ko_v2_curated.jsonl", rows)
    write_jsonl(args.output_dir / "curated_scope_changes.jsonl", changes)
    write_json(args.output_dir / "curated_semantic_rag_input_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
