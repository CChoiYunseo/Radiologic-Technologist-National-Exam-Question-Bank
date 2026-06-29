#!/usr/bin/env python3
"""Build detail/objective review packages after folder-authoritative mapping.

The folder path is treated as the authoritative period/subject/field/area.
This script only prepares review worklists for choosing the correct detail and
learning objective inside that area. It does not approve automatic generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAG = (
    PROJECT_ROOT
    / "resources"
    / "extracted"
    / "rag_index_input_folder_authoritative"
    / "rag_index_input_folder_authoritative.jsonl"
)
DEFAULT_VERIFIED_SCOPE = PROJECT_ROOT / "resources" / "extracted" / "sebuyeongyeok_verified_scope.json"
DEFAULT_OBJECTIVES = PROJECT_ROOT / "resources" / "rules" / "learning_objectives.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "folder_authoritative_detail_review"

TEXT_TYPES = {"text", "ocr_text", "body_text"}
READY_STATUS = "ready_for_rag_evidence"
HOLD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(제\s*\d+\s*조|제\d+조|별표|시행규칙|시행령|"
        r"의료법|모자보건법|지역보건법|원자력안전법|"
        r"의료기사\s*등에\s*관한\s*법률|"
        r"진단용\s*방사선\s*발생장치.*안전관리|"
        r"질병관리청|식품의약품안전처|보건복지부)",
        re.IGNORECASE,
    ),
    re.compile(r"(\[?\s*(그림|표)\s*[0-9]+(\s*[-–—]\s*[0-9]+)?|\b(fig|table)\.?\s*[0-9]+)", re.IGNORECASE),
    re.compile(
        r"(수식|공식|방정식|"
        r"[A-Za-zηλμρσθ]\s*=|"
        r"\d+(?:\.\d+)?\s*[×x]\s*10\s*[-−^]?\s*\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+(?:\.\d+)?\s*"
        r"(kV|mA|mAs|Gy|Sv|Bq|keV|MeV|MHz|mmHg|mGy|mSv|MBq|GBq)\b",
        re.IGNORECASE,
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize(value)).lower()


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def token_set(text: Any) -> set[str]:
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", normalize(text).lower())
    return {token for token in tokens if len(token) >= 2}


def exam_part_subject(exam_part: str) -> str:
    if "의료관계법규" in exam_part:
        return "의료법규"
    if "방사선 응용" in exam_part:
        return "방사선응용"
    if "방사선 이론" in exam_part:
        return "방사선이론"
    return ""


def objective_scope_hint(objective: dict[str, Any]) -> dict[str, str]:
    major = normalize(objective.get("major_unit"))
    major_key = compact(major)
    area_hint = ""
    for candidate in [
        "방사선물리",
        "전기전자개론",
        "의료영상정보",
        "방사선계측",
        "방사선장치(기기)",
        "방사선생물",
        "방사선관리",
        "인체해부학",
        "인체생리학",
        "공중보건",
        "방사선영상",
        "투시조영검사",
        "심맥관 및 중재술",
        "심혈관 및 중재술",
        "초음파기술",
        "초음파영상검사",
        "전산화단층검사",
        "전산화 단층 검사",
        "핵의학검사",
        "핵의학기술",
        "방사선치료",
        "영상품질관리",
        "의료법",
        "의료기사 등에 관한 법률",
        "지역보건법",
    ]:
        if compact(candidate) in major_key:
            area_hint = candidate
            break
    if area_hint == "심혈관 및 중재술":
        area_hint = "심맥관 및 중재술"
    if area_hint in {"전산화 단층 검사"}:
        area_hint = "전산화단층검사"
    if area_hint in {"초음파영상검사"}:
        area_hint = "초음파기술"
    if area_hint in {"핵의학검사", "핵의학기술"}:
        area_hint = "핵의학 기기"
    if area_hint == "방사선치료":
        area_hint = "방사선치료 개요"
    return {
        "subject": exam_part_subject(normalize(objective.get("exam_part"))),
        "field_hint": normalize(objective.get("field_hint")),
        "area_hint": area_hint,
    }


def build_verified_scope_index(path: Path) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    data = read_json(path)
    rows = data.get("rows", []) if isinstance(data, dict) else data
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        subject = normalize(row.get("subject"))
        field = normalize(row.get("field"))
        area = normalize(row.get("area"))
        if subject == "방사선응용" and area == "심혈관 및 중재술":
            area = "심맥관 및 중재술"
        index[(compact(subject), compact(field), compact(area))].append(
            {
                "subject": subject,
                "field": field,
                "area": area,
                "detail": normalize(row.get("detail")),
                "question_count": row.get("question_count"),
                "count_mode": row.get("count_mode"),
                "verification": row.get("verification"),
            }
        )
    return index


def build_objective_index(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    objectives = data.get("objectives", []) if isinstance(data, dict) else data
    rows: list[dict[str, Any]] = []
    for objective in objectives:
        hint = objective_scope_hint(objective)
        text = " ".join(
            normalize(objective.get(key))
            for key in ["major_unit", "unit", "learning_purpose", "objective", "raw_keyword"]
        )
        text += " " + " ".join(normalize(item) for item in objective.get("keywords", []) or [])
        rows.append(
            {
                "objective_id": objective.get("objective_id"),
                "exam_part": normalize(objective.get("exam_part")),
                "major_unit": normalize(objective.get("major_unit")),
                "unit": normalize(objective.get("unit")),
                "learning_purpose": normalize(objective.get("learning_purpose")),
                "objective": normalize(objective.get("objective")),
                "level": normalize(objective.get("level")),
                "keywords": objective.get("keywords", []) or [],
                "source_page": objective.get("source_page"),
                "subject_hint": hint["subject"],
                "field_hint": hint["field_hint"],
                "area_hint": hint["area_hint"],
                "_tokens": token_set(text),
            }
        )
    return rows


def is_safe_text_row(row: dict[str, Any]) -> bool:
    content = normalize(row.get("content"))
    return (
        row.get("approved_for_rag_evidence") is True
        and row.get("candidate_rag_status") == READY_STATUS
        and row.get("approved_for_generation") is not True
        and row.get("chunk_type") in TEXT_TYPES
        and row.get("extraction_quality") == "high"
        and row.get("source_path_exists") is True
        and not any(pattern.search(content) for pattern in HOLD_PATTERNS)
    )


def candidate_objectives(
    objectives: list[dict[str, Any]],
    row: dict[str, Any],
    detail: str,
    limit: int,
) -> list[dict[str, Any]]:
    subject = normalize(row.get("mapped_subject"))
    field = normalize(row.get("mapped_field"))
    area = normalize(row.get("mapped_area"))
    row_tokens = token_set(" ".join([area, detail, row.get("content", "")]))
    scored: list[tuple[float, dict[str, Any]]] = []
    for objective in objectives:
        score = 0.0
        if objective["subject_hint"] and objective["subject_hint"] == subject:
            score += 3.0
        if objective["field_hint"] and compact(objective["field_hint"]) == compact(field):
            score += 2.0
        if objective["area_hint"] and compact(objective["area_hint"]) == compact(area):
            score += 4.0
        if compact(detail) and compact(detail) in compact(" ".join([objective["unit"], objective["objective"], objective["learning_purpose"]])):
            score += 2.0
        overlap = row_tokens & objective["_tokens"]
        score += min(len(overlap), 6) * 0.35
        if score <= 0:
            continue
        scored.append((score, objective))
    scored.sort(key=lambda item: (-item[0], item[1].get("objective_id") or ""))
    return [
        {
            "objective_id": objective["objective_id"],
            "major_unit": objective["major_unit"],
            "unit": objective["unit"],
            "objective": objective["objective"],
            "level": objective["level"],
            "score": round(score, 3),
            "match_basis": {
                "subject_hint": objective["subject_hint"],
                "field_hint": objective["field_hint"],
                "area_hint": objective["area_hint"],
            },
        }
        for score, objective in scored[:limit]
    ]


def excerpt(text: Any, limit: int) -> str:
    compacted = " ".join(normalize(text).split())
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit].rstrip() + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--verified-scope", type=Path, default=DEFAULT_VERIFIED_SCOPE)
    parser.add_argument("--learning-objectives", type=Path, default=DEFAULT_OBJECTIVES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-objectives-per-detail", type=int, default=5)
    parser.add_argument("--excerpt-chars", type=int, default=320)
    args = parser.parse_args()

    rag_rows = read_jsonl(args.rag)
    scope_index = build_verified_scope_index(args.verified_scope)
    objectives = build_objective_index(args.learning_objectives)

    review_chunks: list[dict[str, Any]] = []
    safe_priority_chunks: list[dict[str, Any]] = []
    scope_summary: dict[tuple[str, str, str], dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()

    for row in rag_rows:
        if row.get("scope_mapping_status") != "folder_authoritative_area_only":
            continue
        subject = normalize(row.get("mapped_subject"))
        field = normalize(row.get("mapped_field"))
        area = normalize(row.get("mapped_area"))
        key = (compact(subject), compact(field), compact(area))
        details = scope_index.get(key, [])
        detail_candidates = []
        for detail_row in details:
            detail = detail_row["detail"]
            detail_candidates.append(
                {
                    **detail_row,
                    "learning_objective_candidates": candidate_objectives(
                        objectives, row, detail, args.max_objectives_per_detail
                    ),
                }
            )
        safe_text = is_safe_text_row(row)
        status = "safe_text_detail_review_priority" if safe_text else "detail_review_after_source_policy_check"
        status_counts[status] += 1
        record = {
            "review_package_id": f"folder_detail_review_{short_hash(row.get('rag_input_id'))}",
            "created_at": now_iso(),
            "status": status,
            "rag_input_id": row.get("rag_input_id"),
            "source_chunk_id": row.get("source_chunk_id"),
            "source_file": normalize(row.get("source_file")),
            "source_path": normalize(row.get("source_path")),
            "page_or_slide": row.get("page_or_slide"),
            "content_sha256": row.get("content_sha256"),
            "folder_authoritative_scope": {
                "period": normalize(row.get("mapped_period")),
                "subject": subject,
                "field": field,
                "area": area,
            },
            "previous_detail": normalize(row.get("mapped_detail")),
            "candidate_detail_count": len(detail_candidates),
            "detail_candidates": detail_candidates,
            "safety": {
                "safe_text_for_rag_index": safe_text,
                "approved_for_generation": False,
                "requires_human_or_llm_detail_confirmation": True,
                "requires_learning_objective_confirmation": True,
            },
            "source_excerpt_for_review": excerpt(row.get("content", ""), args.excerpt_chars),
            "source_text_policy": "review excerpt only; do not copy source wording into generated questions/options/explanations",
        }
        review_chunks.append(record)
        if safe_text:
            safe_priority_chunks.append(record)
        summary = scope_summary.setdefault(
            key,
            {
                "scope": {
                    "period": normalize(row.get("mapped_period")),
                    "subject": subject,
                    "field": field,
                    "area": area,
                },
                "area_only_chunks": 0,
                "safe_text_priority_chunks": 0,
                "candidate_details": [
                    {
                        "detail": detail["detail"],
                        "question_count": detail.get("question_count"),
                        "count_mode": detail.get("count_mode"),
                    }
                    for detail in details
                ],
                "sample_refs": [],
            },
        )
        summary["area_only_chunks"] += 1
        if safe_text:
            summary["safe_text_priority_chunks"] += 1
        if len(summary["sample_refs"]) < 8:
            summary["sample_refs"].append(
                {
                    "rag_input_id": row.get("rag_input_id"),
                    "source_file": normalize(row.get("source_file")),
                    "page_or_slide": row.get("page_or_slide"),
                    "safe_text_for_rag_index": safe_text,
                }
            )

    review_chunks.sort(key=lambda item: (item["source_file"], item["page_or_slide"] or 0, item["rag_input_id"] or ""))
    safe_priority_chunks.sort(
        key=lambda item: (item["folder_authoritative_scope"]["area"], item["source_file"], item["page_or_slide"] or 0)
    )
    scope_rows = sorted(
        scope_summary.values(),
        key=lambda item: (
            -item["safe_text_priority_chunks"],
            -item["area_only_chunks"],
            item["scope"]["subject"],
            item["scope"]["field"],
            item["scope"]["area"],
        ),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_path = args.output_dir / "folder_detail_review_chunks.jsonl"
    safe_path = args.output_dir / "folder_detail_review_safe_text_priority.jsonl"
    scope_path = args.output_dir / "folder_detail_review_scope_summary.jsonl"
    report_json_path = args.output_dir / "folder_detail_review_report.json"
    report_md_path = args.output_dir / "folder_detail_review_report.md"
    write_jsonl(all_path, review_chunks)
    write_jsonl(safe_path, safe_priority_chunks)
    write_jsonl(scope_path, scope_rows)

    report = {
        "created_at": now_iso(),
        "inputs": {
            "rag": str(args.rag),
            "verified_scope": str(args.verified_scope),
            "learning_objectives": str(args.learning_objectives),
        },
        "outputs": {
            "review_chunks": str(all_path),
            "safe_text_priority_chunks": str(safe_path),
            "scope_summary": str(scope_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": {
            "input_rows": len(rag_rows),
            "area_only_review_chunks": len(review_chunks),
            "safe_text_priority_chunks": len(safe_priority_chunks),
            "scope_summary_rows": len(scope_rows),
            "status_counts": dict(status_counts),
        },
        "policy": {
            "folder_scope_overrides_content_guess": True,
            "automatic_generation_approval_granted": False,
            "review_purpose": "choose_detail_and_learning_objective_inside_folder_area",
        },
    }
    write_json(report_json_path, report)
    lines = [
        "# 폴더 기준 세부항목·학습목표 검수 패키지 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 row: {report['counts']['input_rows']}",
        f"- 세부항목 검수 대상: {report['counts']['area_only_review_chunks']}",
        f"- 우선 검수 안전 텍스트: {report['counts']['safe_text_priority_chunks']}",
        f"- 영역 요약: {report['counts']['scope_summary_rows']}",
        "",
        "## 출력",
        f"- 전체 검수 목록: `{all_path}`",
        f"- 우선 검수 목록: `{safe_path}`",
        f"- 영역별 요약: `{scope_path}`",
        "",
        "## 정책",
        "- 폴더로 확정된 영역 밖의 세부항목 후보는 제시하지 않는다.",
        "- 이 산출물은 자동 문제 생성 승인이 아니라 세부항목·학습목표 확인용이다.",
        "- 원문 표현은 문제·보기·해설에 복사하지 않는다.",
    ]
    report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "area_only_review_chunks": len(review_chunks),
                "safe_text_priority_chunks": len(safe_priority_chunks),
                "scope_summary_rows": len(scope_rows),
                "report_json": str(report_json_path),
                "report_md": str(report_md_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
