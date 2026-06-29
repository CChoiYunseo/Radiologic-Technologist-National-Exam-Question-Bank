#!/usr/bin/env python3
"""Apply folder-authoritative scope mapping to RAG input rows.

The source-reference folders already encode period/subject/area. This script
uses that folder structure as the authoritative coarse scope, while keeping
detail-level mapping as a content review target unless it is already valid for
the folder area.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATERIAL_ROOT = PROJECT_ROOT / "materials/04_subject_references"
DEFAULT_INPUT = PROJECT_ROOT / "resources/extracted/rag_index_input/rag_index_input_mapped.jsonl"
DEFAULT_SCOPE = PROJECT_ROOT / "resources/extracted/sebuyeongyeok_verified_scope.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/extracted/rag_index_input_folder_authoritative"

AREA_ALIASES = {
    "심혈관 및 중재술": "심맥관 및 중재술",
    "방사선치료개요": "방사선치료 개요",
    "방사선치료 개요": "방사선치료 개요",
    "방사선장치기기": "방사선장치(기기)",
    "방사선장치(기기)": "방사선장치(기기)",
    "전기전자개론": "전기전자개론",
    "의료영상정보": "의료영상정보",
    "방사선계측": "방사선계측",
    "방사선생물": "방사선생물",
    "방사선관리": "방사선관리",
    "공중보건": "공중보건",
    "방사선영상": "방사선영상",
    "투시조영검사": "투시조영검사",
    "초음파기술": "초음파기술",
    "전산화단층검사": "전산화단층검사",
    "핵의학 기기": "핵의학 기기",
    "품질관리": "품질관리",
    "의료법": "의료법",
    "의료기사 등에 관한 법률": "의료기사 등에 관한 법률",
    "지역보건법": "지역보건법",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", nfc(value)).lower()


def strip_number_prefix(value: str) -> str:
    value = nfc(value)
    value = re.sub(r"^\d+(?:\.\d+)*\s*", "", value)
    return value.strip()


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
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
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_no}")
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


def verified_indexes(scope_data: dict[str, Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    area_index: dict[tuple[str, str], dict[str, Any]] = {}
    detail_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scope_data.get("rows") or []:
        subject = nfc(row.get("subject"))
        area = nfc(row.get("area"))
        detail = nfc(row.get("detail"))
        grouped[(subject, compact(area))].append(row)
        detail_index[(subject, compact(area), compact(detail))] = row
    for (subject, area_key), rows in grouped.items():
        first = rows[0]
        area_index[(subject, area_key)] = {
            "subject": subject,
            "field": nfc(first.get("field")),
            "area": nfc(first.get("area")),
            "details": [nfc(row.get("detail")) for row in rows],
            "detail_rows": rows,
        }
    return area_index, detail_index


def material_folder_from_path(row: dict[str, Any], material_root: Path) -> str:
    folder = nfc(row.get("material_folder"))
    if folder and folder != ".":
        return folder
    source_path = nfc(row.get("source_path"))
    if not source_path:
        return folder or "."
    try:
        path = Path(source_path)
        rel_parent = path.parent.relative_to(material_root)
        return nfc(str(rel_parent))
    except ValueError:
        return folder or "."


def period_from_top_folder(top: str) -> str:
    top = nfc(top)
    if top.startswith("1교시"):
        return "1교시"
    if top.startswith("2교시"):
        return "2교시"
    if top.startswith("3교시"):
        return "3교시"
    return ""


def subject_from_folder(period: str, area: str) -> str:
    if area in {"의료법", "의료기사 등에 관한 법률", "지역보건법"}:
        return "의료법규"
    if period == "1교시":
        return "방사선이론"
    if period == "2교시":
        return "방사선응용"
    if period == "3교시":
        return "실기시험"
    return ""


def canonical_area(raw_leaf: str) -> str:
    area = strip_number_prefix(raw_leaf)
    if area in AREA_ALIASES:
        return AREA_ALIASES[area]
    compact_area = compact(area)
    for key, value in AREA_ALIASES.items():
        if compact(key) == compact_area:
            return value
    return area


def folder_scope(row: dict[str, Any], material_root: Path, area_index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    folder = material_folder_from_path(row, material_root)
    parts = [nfc(part) for part in Path(folder).parts if nfc(part) and nfc(part) != "."]
    if len(parts) < 2:
        return {
            "material_folder": folder,
            "period": "",
            "subject": "",
            "field": "",
            "area": "",
            "details": [],
            "status": "missing_folder_scope",
        }
    top, leaf = parts[0], parts[-1]
    period = period_from_top_folder(top)
    area = canonical_area(leaf)
    subject = subject_from_folder(period, area)
    area_info = area_index.get((subject, compact(area)))
    if not area_info:
        return {
            "material_folder": folder,
            "period": period,
            "subject": subject,
            "field": "",
            "area": area,
            "details": [],
            "status": "folder_area_not_in_verified_scope",
        }
    return {
        "material_folder": folder,
        "period": period,
        "subject": area_info["subject"],
        "field": area_info["field"],
        "area": area_info["area"],
        "details": area_info["details"],
        "status": "folder_scope_resolved",
    }


def rewrite_scope_candidates(row: dict[str, Any], scope: dict[str, Any], detail_index: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    original_candidates = row.get("scope_candidates") or []
    by_detail = {compact((candidate or {}).get("detail")): candidate for candidate in original_candidates if isinstance(candidate, dict)}
    for detail in scope.get("details") or []:
        key = (scope["subject"], compact(scope["area"]), compact(detail))
        verified = detail_index.get(key, {})
        original = by_detail.get(compact(detail), {})
        candidates.append(
            {
                "period": scope["period"],
                "subject": scope["subject"],
                "field": scope["field"],
                "area": scope["area"],
                "detail": detail,
                "scope_id": verified.get("scope_id") or original.get("scope_id") or "",
                "score": original.get("score", 0),
                "reasons": list(original.get("reasons") or []) + ["folder_authoritative_area"],
            }
        )
    return candidates


def apply_mapping(row: dict[str, Any], scope: dict[str, Any], detail_index: dict[tuple[str, str, str], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    new = dict(row)
    old = {
        "period": row.get("mapped_period") or "",
        "subject": row.get("mapped_subject") or "",
        "field": row.get("mapped_field") or "",
        "area": row.get("mapped_area") or "",
        "detail": row.get("mapped_detail") or "",
        "scope_id": row.get("mapped_scope_id") or "",
    }
    audit = {
        "rag_input_id": row.get("rag_input_id"),
        "source_file": row.get("source_file"),
        "page_or_slide": row.get("page_or_slide"),
        "source_path": row.get("source_path"),
        "material_folder_before": row.get("material_folder") or "",
        "material_folder_after": scope.get("material_folder"),
        "folder_scope_status": scope.get("status"),
        "old_scope": old,
        "folder_scope": {
            "period": scope.get("period", ""),
            "subject": scope.get("subject", ""),
            "field": scope.get("field", ""),
            "area": scope.get("area", ""),
            "details": scope.get("details") or [],
        },
        "action": "unchanged",
        "detail_action": "unchanged",
    }
    new["material_folder"] = scope.get("material_folder") or row.get("material_folder") or ""
    new["folder_authoritative_scope"] = audit["folder_scope"]
    new["folder_scope_status"] = scope.get("status")

    if scope.get("status") != "folder_scope_resolved":
        new["scope_mapping_status"] = "needs_folder_scope_review"
        new["scope_mapping_confidence"] = "none"
        new["scope_mapping_needs_review"] = True
        audit["action"] = "folder_scope_unresolved"
        return new, audit

    folder_mismatch = (
        compact(old["subject"]) != compact(scope["subject"])
        or compact(old["field"]) != compact(scope["field"])
        or compact(old["area"]) != compact(scope["area"])
    )
    if folder_mismatch:
        new["mapped_period"] = scope["period"]
        new["mapped_subject"] = scope["subject"]
        new["mapped_field"] = scope["field"]
        new["mapped_area"] = scope["area"]
        audit["action"] = "coarse_scope_replaced_by_folder"
    else:
        new["mapped_period"] = old["period"] or scope["period"]
        audit["action"] = "coarse_scope_confirmed_by_folder"

    valid_detail = old["detail"] in set(scope.get("details") or [])
    if valid_detail:
        new["mapped_detail"] = old["detail"]
        key = (scope["subject"], compact(scope["area"]), compact(old["detail"]))
        verified = detail_index.get(key, {})
        if verified.get("scope_id"):
            new["mapped_scope_id"] = verified["scope_id"]
        audit["detail_action"] = "detail_kept_within_folder_area"
    elif len(scope.get("details") or []) == 1:
        detail = scope["details"][0]
        new["mapped_detail"] = detail
        key = (scope["subject"], compact(scope["area"]), compact(detail))
        verified = detail_index.get(key, {})
        new["mapped_scope_id"] = verified.get("scope_id") or ""
        audit["detail_action"] = "single_detail_applied_from_folder_area"
    else:
        new["mapped_detail"] = ""
        new["mapped_scope_id"] = ""
        new["scope_mapping_needs_review"] = True
        audit["detail_action"] = "detail_cleared_for_content_review"

    new["scope_candidates"] = rewrite_scope_candidates(new, scope, detail_index)
    if new.get("mapped_detail"):
        new["scope_mapping_status"] = "folder_authoritative_detail_mapped"
        new["scope_mapping_confidence"] = "folder_confirmed"
        new["scope_mapping_needs_review"] = False
    else:
        new["scope_mapping_status"] = "folder_authoritative_area_only"
        new["scope_mapping_confidence"] = "folder_area_only"
        new["scope_mapping_needs_review"] = True
    return new, audit


def build_markdown(report: dict[str, Any], audit_rows: list[dict[str, Any]]) -> str:
    counts = report["counts"]
    lines = [
        "# Folder-Authoritative Scope Mapping Report",
        "",
        "전공 자료 폴더 구조를 교시·과목·분야·영역의 기준값으로 적용했다.",
        "",
        "## Summary",
        "",
        f"- input_rows: {counts['input_rows']}",
        f"- output_rows: {counts['output_rows']}",
        f"- coarse_scope_confirmed_by_folder: {counts['actions'].get('coarse_scope_confirmed_by_folder', 0)}",
        f"- coarse_scope_replaced_by_folder: {counts['actions'].get('coarse_scope_replaced_by_folder', 0)}",
        f"- folder_scope_unresolved: {counts['actions'].get('folder_scope_unresolved', 0)}",
        f"- detail_kept_within_folder_area: {counts['detail_actions'].get('detail_kept_within_folder_area', 0)}",
        f"- single_detail_applied_from_folder_area: {counts['detail_actions'].get('single_detail_applied_from_folder_area', 0)}",
        f"- detail_cleared_for_content_review: {counts['detail_actions'].get('detail_cleared_for_content_review', 0)}",
        "",
        "## Outputs",
    ]
    for key, value in report["outputs"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Sample Replacements", ""])
    replaced = [row for row in audit_rows if row["action"] == "coarse_scope_replaced_by_folder"][:20]
    if not replaced:
        lines.append("- coarse scope replacements 없음")
    for row in replaced:
        old = row["old_scope"]
        folder = row["folder_scope"]
        lines.append(
            "- {rag} p.{page}: {old_subject}/{old_area}/{old_detail} -> {subject}/{area} ({detail_action})".format(
                rag=row.get("rag_input_id"),
                page=row.get("page_or_slide"),
                old_subject=old.get("subject"),
                old_area=old.get("area"),
                old_detail=old.get("detail"),
                subject=folder.get("subject"),
                area=folder.get("area"),
                detail_action=row.get("detail_action"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--material-root", type=Path, default=MATERIAL_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    input_rows = read_jsonl(args.input)
    scope_data = read_json(args.scope)
    area_index, detail_index = verified_indexes(scope_data)

    output_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for row in input_rows:
        scope = folder_scope(row, args.material_root, area_index)
        mapped, audit = apply_mapping(row, scope, detail_index)
        output_rows.append(mapped)
        audit_rows.append(audit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "rag_index_input_folder_authoritative.jsonl"
    audit_path = args.output_dir / "folder_scope_mapping_audit.jsonl"
    report_json = args.output_dir / "folder_scope_mapping_report.json"
    report_md = args.output_dir / "folder_scope_mapping_report.md"
    write_jsonl(output_path, output_rows)
    write_jsonl(audit_path, audit_rows)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "rag_input": str(args.input),
            "verified_scope": str(args.scope),
            "material_root": str(args.material_root),
        },
        "outputs": {
            "folder_authoritative_rag_input": str(output_path),
            "audit": str(audit_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "input_rows": len(input_rows),
            "output_rows": len(output_rows),
            "actions": dict(Counter(row["action"] for row in audit_rows)),
            "detail_actions": dict(Counter(row["detail_action"] for row in audit_rows)),
            "folder_scope_status": dict(Counter(row["folder_scope_status"] for row in audit_rows)),
            "mapping_status": dict(Counter(row.get("scope_mapping_status") for row in output_rows)),
            "by_folder_area": dict(Counter((row.get("folder_authoritative_scope") or {}).get("area", "") for row in output_rows)),
        },
        "policy": {
            "source_text_modified": False,
            "question_generation_performed": False,
            "folder_scope_overrides_content_guess": True,
            "detail_requires_content_review_when_multiple_details": True,
        },
    }
    write_json(report_json, report)
    report_md.write_text(build_markdown(report, audit_rows), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
