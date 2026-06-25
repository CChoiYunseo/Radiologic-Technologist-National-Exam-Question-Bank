#!/usr/bin/env python3
"""Build visual-question generation packages from approved visual records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APPROVED = (
    PROJECT_ROOT
    / "resources"
    / "generated"
    / "visual_question_generation_approvals"
    / "visual_question_generation_approved.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "visual_question_request_packages"
EXAM_SCOPE = PROJECT_ROOT / "resources" / "rules" / "exam_scope.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
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


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", nfc(value)).lower()


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def load_scope_rows() -> list[dict[str, Any]]:
    data = read_json(EXAM_SCOPE)
    return data.get("verified_detail_rows") or data.get("detail_rows") or []


def scope_by_detail(rows: list[dict[str, Any]], area: str, detail: str) -> dict[str, Any]:
    area_c = compact(area)
    detail_c = compact(detail)
    for row in rows:
        if compact(row.get("area")) == area_c and compact(row.get("detail")) == detail_c:
            return row
    return {}


def parse_linked_scope(value: Any, scope_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    if isinstance(value, dict):
        scope = {key: value.get(key, "") for key in ["period", "subject", "field", "area", "detail", "scope_id"]}
        if scope.get("period") and scope.get("subject") and scope.get("area"):
            return scope, "record_linked_scope_dict"
    if isinstance(value, str) and value.strip():
        parts = [part.strip() for part in value.split(">")]
        if len(parts) >= 4:
            subject, field, area, detail = parts[:4]
            for row in scope_rows:
                if (
                    compact(row.get("subject")) == compact(subject)
                    and compact(row.get("field")) == compact(field)
                    and compact(row.get("area")) == compact(area)
                    and compact(row.get("detail")) == compact(detail)
                ):
                    return {
                        "period": row.get("period", ""),
                        "subject": row.get("subject", ""),
                        "field": row.get("field", ""),
                        "area": row.get("area", ""),
                        "detail": row.get("detail", ""),
                        "scope_id": row.get("scope_id", ""),
                    }, "record_linked_scope_string"
    return {}, ""


def source_page_scope(row: dict[str, Any], scope_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    source_file = nfc(row.get("source_file"))
    try:
        page = int(row.get("page_or_slide"))
    except (TypeError, ValueError):
        page = 0

    if "전기전자개론" in source_file:
        if page <= 60:
            detail = "직류회로"
        elif page <= 120:
            detail = "전기장"
        elif page <= 180:
            detail = "자기장"
        elif page <= 230:
            detail = "교류회로"
        else:
            detail = "반도체소자"
        found = scope_by_detail(scope_rows, "전기전자개론", detail)
        if found:
            return scope_from_row(found), f"source_page_rule:{detail}"

    if "방사선장치" in source_file:
        if page <= 35:
            detail = "엑스선 고전압장치"
        elif page <= 105:
            detail = "엑스선관"
        elif page >= 379:
            detail = "엑스선 장치 성능관리"
        else:
            detail = "엑스선 발생"
        found = scope_by_detail(scope_rows, "방사선장치(기기)", detail)
        if found:
            return scope_from_row(found), f"source_page_rule:{detail}"

    if "정도관리" in source_file:
        found = scope_by_detail(scope_rows, "방사선장치(기기)", "엑스선 장치 성능관리")
        if found:
            return scope_from_row(found), "source_file_rule:엑스선 장치 성능관리"

    if "컴퓨터단층촬영" in source_file:
        found = scope_by_detail(scope_rows, "전산화단층검사", "CT 장치")
        if found:
            return scope_from_row(found), "source_file_rule:CT 장치"

    return {}, ""


def scope_from_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "period": row.get("period", ""),
        "subject": row.get("subject", ""),
        "field": row.get("field", ""),
        "area": row.get("area", ""),
        "detail": row.get("detail", ""),
        "scope_id": row.get("scope_id", ""),
    }


def question_type_for(kind: str, modes: list[str]) -> tuple[str, str]:
    if kind == "formula":
        return "계산형", "문제해결형"
    if kind in {"table", "graph", "chart"}:
        return "비교형", "해석형"
    if "component_function" in modes or "process_flow" in modes:
        return "영상해석형", "해석형"
    return "개념형", "해석형"


def generation_focus(row: dict[str, Any]) -> str:
    summary = row.get("visual_evidence_summary") or {}
    pieces = [
        summary.get("caption"),
        summary.get("nearby_text_summary"),
        summary.get("semantic_description"),
        summary.get("structure_summary"),
    ]
    if summary.get("formula_plain_text"):
        pieces.append("formula_present")
    if summary.get("table_json"):
        pieces.append("table_present")
    return " ".join(nfc(part) for part in pieces if nfc(part))[:600]


def package_for(row: dict[str, Any], scope_rows: list[dict[str, Any]]) -> dict[str, Any]:
    scope, method = parse_linked_scope(row.get("linked_scope"), scope_rows)
    if not scope:
        scope, method = source_page_scope(row, scope_rows)
    kind = row.get("visual_kind", "")
    modes = row.get("allowed_question_modes") or []
    qtype, competency = question_type_for(kind, modes)
    package_id = "vqrp_" + short_hash({"approval_id": row.get("approval_id"), "scope": scope})
    status = "ready_visual" if scope.get("period") in {"1교시", "2교시"} and scope.get("scope_id") else "needs_scope_review"
    return {
        "package_id": package_id,
        "mode": "visual_pre_generation",
        "created_at": now_iso(),
        "package_status": status,
        "source_visual_approval_id": row.get("approval_id"),
        "source_visual_chunk_id": row.get("source_visual_chunk_id"),
        "requested_scope": scope,
        "scope_mapping_method": method or "unmapped",
        "visual_evidence": {
            "visual_kind": kind,
            "source_file": row.get("source_file", ""),
            "source_path": row.get("source_path", ""),
            "page_or_slide": row.get("page_or_slide", ""),
            "allowed_question_modes": modes,
            "summary": row.get("visual_evidence_summary") or {},
        },
        "recommended_generation_settings": {
            "question_type": qtype,
            "competency_type": competency,
            "difficulty": "중",
            "allowed_question_modes": modes,
            "generation_focus": generation_focus(row),
            "learning_objective_id": "",
        },
        "generation_constraints": {
            "approved_for_visual_question_generation": True,
            "must_not_copy_source_sentences": True,
            "must_write_new_wording": True,
            "must_not_reuse_source_visual_image": True,
            "must_not_add_legal_or_current_numeric_standards": True,
            "use_structured_visual_summary_only": True,
            "final_expert_approval_required": True,
        },
        "evidence_refs": [{"rag_input_id": row.get("approval_id", "")}],
        "source_chunks": [{"rag_input_id": row.get("approval_id", "")}],
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 시각자료 문항 생성 요청 패키지 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 승인 건수: {report['counts']['input_approved']}",
        f"- 생성 패키지: {report['counts']['all_packages']}",
        f"- 생성 가능: {report['counts']['ready_visual']}",
        f"- 범위 검토 필요: {report['counts']['needs_scope_review']}",
        "",
        "## 유형별 생성 가능",
    ]
    for kind, count in report["ready_kind_counts"].items():
        lines.append(f"- {kind}: {count}")
    lines.extend(
        [
            "",
            "## 산출물",
            f"- 전체 패키지: `{report['outputs']['all_packages']}`",
            f"- 생성 가능 패키지: `{report['outputs']['ready_packages']}`",
            f"- 검토 필요 패키지: `{report['outputs']['needs_review_packages']}`",
            "",
            "## 주의",
            "- 원본 시각자료 이미지를 문제에 직접 포함하지 않습니다.",
            "- 구조화 설명만 근거로 새 문장 문항을 생성합니다.",
            "- 생성 결과는 전문가 검수 전까지 문제은행 최종 승인 상태가 아닙니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", type=Path, default=DEFAULT_APPROVED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    approved = read_jsonl(args.approved)
    scope_rows = load_scope_rows()
    packages = [package_for(row, scope_rows) for row in approved]
    ready = [pkg for pkg in packages if pkg.get("package_status") == "ready_visual"]
    needs_review = [pkg for pkg in packages if pkg.get("package_status") != "ready_visual"]

    all_path = args.output_dir / "visual_question_request_packages_all.jsonl"
    ready_path = args.output_dir / "visual_question_request_packages_ready.jsonl"
    review_path = args.output_dir / "visual_question_request_packages_needs_scope_review.jsonl"
    report_json = args.output_dir / "visual_question_request_package_report.json"
    report_md = args.output_dir / "visual_question_request_package_report.md"
    write_jsonl(all_path, packages)
    write_jsonl(ready_path, ready)
    write_jsonl(review_path, needs_review)

    report = {
        "version": "2026-06-25",
        "created_at": now_iso(),
        "inputs": {"approved_visuals": str(args.approved), "exam_scope": str(EXAM_SCOPE)},
        "outputs": {
            "all_packages": str(all_path),
            "ready_packages": str(ready_path),
            "needs_review_packages": str(review_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "input_approved": len(approved),
            "all_packages": len(packages),
            "ready_visual": len(ready),
            "needs_scope_review": len(needs_review),
        },
        "ready_kind_counts": dict(Counter(pkg["visual_evidence"]["visual_kind"] for pkg in ready)),
        "needs_review_kind_counts": dict(Counter(pkg["visual_evidence"]["visual_kind"] for pkg in needs_review)),
        "scope_mapping_method_counts": dict(Counter(pkg.get("scope_mapping_method") for pkg in packages)),
    }
    write_json(report_json, report)
    report_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"outputs": report["outputs"], "counts": report["counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
