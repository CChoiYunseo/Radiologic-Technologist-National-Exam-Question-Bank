#!/usr/bin/env python3
"""Create draft mappings from subject-reference chunks to scope/objectives.

This script produces a reviewable first pass. It intentionally keeps low
confidence mappings as needs_review instead of treating them as final labels.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "resources" / "rules"
EXTRACTED_DIR = PROJECT_ROOT / "resources" / "extracted" / "subject_references"

INPUT_CHUNKS = EXTRACTED_DIR / "chunks_mvp_text_ready.jsonl"
OUTPUT_MAPPING = EXTRACTED_DIR / "chunk_scope_objective_mapping.jsonl"
OUTPUT_CHUNKS = EXTRACTED_DIR / "chunks_mvp_text_mapped.jsonl"
OUTPUT_REPORT = EXTRACTED_DIR / "chunk_scope_objective_mapping_report.json"
OVERRIDES_FILE = EXTRACTED_DIR / "chunk_scope_objective_overrides.json"


PROFILE_RULES: dict[str, dict[str, Any]] = {
    "radiation_device": {
        "source_contains": ["방사선장치", "방사선장치(기기)"],
        "area": "방사선장치(기기)",
        "major_unit": "#5 방사선장치(기기)",
        "front_matter_pages": set(range(1, 9)),
        "page_scope_ranges": [
            ("엑스선 발생", 9, 40),
            ("엑스선관", 41, 107),
            ("엑스선 고전압장치", 108, 185),
            ("엑스선 제어장치", 186, 236),
            ("엑스선 영상장치", 237, 354),
            ("엑스선 장치 성능관리", 379, 488),
        ],
        "scope_aliases": {
            "엑스선 발생": ["의료용 엑스선 발생", "음극선 발생", "엑스선 발생장치"],
            "엑스선관": ["진단용 엑스선 관", "엑스선 관 장치", "엑스선 관"],
            "엑스선 고전압장치": ["고전압 발생장치", "고전압 정류 회로", "고전압 변압기", "정류 회로"],
            "엑스선 제어장치": ["엑스선 제어장치", "자동노출제어", "관전압조정기", "관전류 조정기"],
            "엑스선 영상장치": ["영상증배관", "엑스선 텔레비전", "디지털 엑스선", "DR", "CR"],
            "엑스선 장치 성능관리": ["성능관리", "관전압 측정", "관전류 측정", "조사시간 측정", "조사선량 측정"],
        },
    },
    "electronics": {
        "source_contains": ["전기전자개론", "전기전자"],
        "area": "전기전자개론",
        "major_unit": "#2 전기전자개론",
        "front_matter_pages": set(range(1, 11)),
        "page_scope_ranges": [
            ("직류회로", 11, 64),
            ("자기장", 65, 96),
            ("전기장", 97, 116),
            ("교류회로", 117, 152),
            ("전기회로의 과도현상·전기계측", 153, 162),
            ("반도체소자", 163, 279),
        ],
        "scope_aliases": {
            "직류회로": ["직류", "전류 전압 저항", "오옴의 법칙", "키르히호프"],
            "자기장": ["자계", "자속", "전자유도", "변압기", "앙페르"],
            "전기장": ["정전기", "전계", "전기력선", "콘덴서", "유전체"],
            "교류회로": ["교류", "위상", "임피던스", "공진", "3상교류"],
            "전기회로의 과도현상·전기계측": ["과도현상", "시정수", "R-L", "R-C", "계측기", "VOM"],
            "반도체소자": ["반도체", "다이오드", "트랜지스터", "사이리스터", "SCR"],
        },
    },
}


FRONT_MATTER_PATTERNS = [
    "머리말",
    "preface",
    "contents",
    "차례",
    "목차",
    "일러두기",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def compact(value: Any) -> str:
    text = nfc(value).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def tokenize(value: Any) -> set[str]:
    text = nfc(value).lower()
    raw = re.findall(r"[0-9a-zA-Z가-힣]+", text)
    tokens = set()
    for token in raw:
        if len(token) < 2:
            continue
        if token.isdigit():
            continue
        tokens.add(token)
    return tokens


def objective_text(value: str) -> str:
    return re.sub(r"^\s*\d+\.\s*", "", nfc(value)).strip()


def get_profile(source_file: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    source = nfc(source_file)
    for profile_name, profile in PROFILE_RULES.items():
        if any(marker in source for marker in profile["source_contains"]):
            return profile_name, profile
    return None, None


def is_front_matter(chunk: dict[str, Any], profile: dict[str, Any] | None) -> bool:
    page = int(chunk.get("page_or_slide") or 0)
    content = nfc(chunk.get("content", ""))
    lower = content.lower()
    if profile and page in profile.get("front_matter_pages", set()):
        return True
    return any(pattern in lower for pattern in FRONT_MATTER_PATTERNS)


def page_range_detail(page: int, profile: dict[str, Any]) -> str:
    for detail, start, end in profile.get("page_scope_ranges", []):
        if start <= page <= end:
            return detail
    return ""


def scope_aliases(detail: str, profile: dict[str, Any]) -> list[str]:
    return [detail, *profile.get("scope_aliases", {}).get(detail, [])]


def normalized_unit(value: str) -> str:
    return compact(value).replace("전기회로의과도현상전기계측", "전기회로의과도현상전기계측")


def score_scope(
    chunk: dict[str, Any],
    scope: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[float, list[str]]:
    page = int(chunk.get("page_or_slide") or 0)
    content = nfc(chunk.get("content", ""))
    compact_content = compact(content)
    content_tokens = tokenize(content)
    detail = nfc(scope.get("detail", ""))
    aliases = scope_aliases(detail, profile)
    reasons: list[str] = []
    score = 1.0

    ranged_detail = page_range_detail(page, profile)
    if ranged_detail and compact(ranged_detail) == compact(detail):
        score += 14.0
        reasons.append(f"page_range:{ranged_detail}")

    if compact(detail) and compact(detail) in compact_content:
        score += 4.0
        reasons.append("detail_exact_match")

    alias_hits = []
    for alias in aliases:
        alias_compact = compact(alias)
        if alias_compact and alias_compact in compact_content:
            alias_hits.append(alias)
    if alias_hits:
        score += min(4.0, 1.6 * len(set(alias_hits)))
        reasons.append("alias_match:" + ",".join(sorted(set(alias_hits))[:3]))

    detail_tokens = tokenize(" ".join(aliases))
    matched = sorted(detail_tokens & content_tokens)
    if matched:
        score += min(4.0, 0.8 * len(matched))
        reasons.append("token_match:" + ",".join(matched[:5]))

    return score, reasons


def score_objective(
    chunk: dict[str, Any],
    objective: dict[str, Any],
    selected_scope_detail: str,
    profile: dict[str, Any],
) -> tuple[float, list[str]]:
    page = int(chunk.get("page_or_slide") or 0)
    content = nfc(chunk.get("content", ""))
    compact_content = compact(content)
    content_tokens = tokenize(content)
    unit = nfc(objective.get("unit", ""))
    objective_body = objective_text(objective.get("objective", ""))
    reasons: list[str] = []
    score = 0.0

    if compact(unit) == compact(selected_scope_detail):
        score += 3.0
        reasons.append("unit_matches_selected_scope")
    elif normalized_unit(unit) == normalized_unit(selected_scope_detail):
        score += 3.0
        reasons.append("unit_equivalent_to_selected_scope")

    ranged_detail = page_range_detail(page, profile)
    if ranged_detail and (compact(unit) == compact(ranged_detail) or normalized_unit(unit) == normalized_unit(ranged_detail)):
        score += 3.0
        reasons.append(f"page_range_unit:{ranged_detail}")

    keyword_hits = []
    for keyword in objective.get("keywords") or []:
        key = nfc(keyword).strip()
        if not key:
            continue
        key_compact = compact(key)
        if key_compact and key_compact in compact_content:
            keyword_hits.append(key)
    if keyword_hits:
        score += min(10.0, 4.0 * len(set(keyword_hits)))
        reasons.append("keyword_match:" + ",".join(sorted(set(keyword_hits))[:5]))

    objective_tokens = tokenize(objective_body)
    matched_objective_tokens = sorted(objective_tokens & content_tokens)
    if matched_objective_tokens:
        score += min(5.0, 0.6 * len(matched_objective_tokens))
        reasons.append("objective_token_match:" + ",".join(matched_objective_tokens[:5]))

    purpose_tokens = tokenize(objective.get("learning_purpose", ""))
    matched_purpose_tokens = sorted(purpose_tokens & content_tokens)
    if matched_purpose_tokens:
        score += min(2.0, 0.4 * len(matched_purpose_tokens))
        reasons.append("purpose_token_match:" + ",".join(matched_purpose_tokens[:5]))

    return score, reasons


def confidence(score: float, high: float, medium: float, low: float) -> str:
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    if score >= low:
        return "low"
    return "none"


def public_scope(scope: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    return {
        "scope_id": scope.get("scope_id"),
        "score": round(score, 2),
        "confidence": confidence(score, high=12.0, medium=7.0, low=3.0),
        "period": scope.get("period", ""),
        "subject": scope.get("subject", ""),
        "field": scope.get("field", ""),
        "area": scope.get("area", ""),
        "detail": scope.get("detail", ""),
        "question_count": scope.get("question_count"),
        "count_mode": scope.get("count_mode", ""),
        "reasons": reasons,
    }


def public_objective(
    objective: dict[str, Any],
    target_by_objective_id: dict[str, dict[str, Any]],
    score: float,
    reasons: list[str],
) -> dict[str, Any]:
    target = target_by_objective_id.get(objective.get("objective_id", ""), {})
    return {
        "objective_id": objective.get("objective_id"),
        "target_id": target.get("target_id", ""),
        "score": round(score, 2),
        "confidence": confidence(score, high=11.0, medium=6.0, low=3.0),
        "major_unit": objective.get("major_unit", ""),
        "unit": objective.get("unit", ""),
        "objective": objective.get("objective", ""),
        "level": objective.get("level", ""),
        "keywords": objective.get("keywords", []),
        "recommended_question_types": target.get("recommended_question_types", []),
        "reasons": reasons,
    }


def top_scopes(
    chunk: dict[str, Any],
    scopes: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = []
    for scope in scopes:
        score, reasons = score_scope(chunk, scope, profile)
        candidates.append(public_scope(scope, score, reasons))
    candidates.sort(key=lambda row: row["score"], reverse=True)
    return candidates[:3]


def top_objectives(
    chunk: dict[str, Any],
    objectives: list[dict[str, Any]],
    selected_scope_detail: str,
    profile: dict[str, Any],
    target_by_objective_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = []
    for objective in objectives:
        score, reasons = score_objective(chunk, objective, selected_scope_detail, profile)
        if score <= 0:
            continue
        candidates.append(public_objective(objective, target_by_objective_id, score, reasons))
    candidates.sort(key=lambda row: row["score"], reverse=True)
    return candidates[:5]


def load_overrides() -> list[dict[str, Any]]:
    if not OVERRIDES_FILE.exists():
        return []
    data = read_json(OVERRIDES_FILE)
    return data.get("overrides", [])


def find_override(chunk: dict[str, Any], overrides: list[dict[str, Any]]) -> dict[str, Any] | None:
    source_file = nfc(chunk.get("source_file", ""))
    page = int(chunk.get("page_or_slide") or 0)
    chunk_index = int(chunk.get("chunk_index") or 0)
    for override in overrides:
        marker = nfc(override.get("source_file_contains", ""))
        if marker and marker not in source_file:
            continue
        if int(override.get("page_or_slide") or -1) != page:
            continue
        if "chunk_index" in override and int(override.get("chunk_index") or 0) != chunk_index:
            continue
        return override
    return None


def build_override_mapping(
    chunk: dict[str, Any],
    override: dict[str, Any],
    scope_by_id: dict[str, dict[str, Any]],
    objective_by_id: dict[str, dict[str, Any]],
    target_by_objective_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scope = scope_by_id.get(override.get("scope_id", ""))
    objective = objective_by_id.get(override.get("learning_objective_id", ""))
    selected_scope = public_scope(scope, 99.0, ["human_verified_override"]) if scope else None
    selected_objective = (
        public_objective(objective, target_by_objective_id, 99.0, ["human_verified_override"])
        if objective
        else None
    )
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", ""),
        "source_file": chunk.get("source_file", ""),
        "page_or_slide": int(chunk.get("page_or_slide") or 0),
        "chunk_index": chunk.get("chunk_index", 0),
        "content_hash": chunk.get("content_hash", ""),
        "extraction_quality": chunk.get("extraction_quality", ""),
        "profile": "human_override",
        "content_role": "body",
        "mapped_at": datetime.now(timezone.utc).isoformat(),
        "mapping_status": "human_verified",
        "needs_review": False,
        "review_reasons": [],
        "human_review": {
            "reviewed_classification": override.get("reviewed_classification", ""),
            "classification_basis": override.get("classification_basis", ""),
            "criteria_files": override.get("criteria_files", []),
            "criteria_note": override.get("criteria_note", ""),
            "controlled_tags": override.get("controlled_tags", []),
            "secondary_learning_objective_ids": override.get("secondary_learning_objective_ids", []),
            "reason": override.get("reason", ""),
        },
        "selected_scope": selected_scope,
        "selected_learning_objective": selected_objective,
        "scope_candidates": [selected_scope] if selected_scope else [],
        "learning_objective_candidates": [selected_objective] if selected_objective else [],
    }


def selected_or_none(candidates: list[dict[str, Any]], minimum: str = "low") -> dict[str, Any] | None:
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    if not candidates:
        return None
    top = candidates[0]
    if rank.get(top.get("confidence", "none"), 0) >= rank[minimum]:
        return top
    return None


def build_mapping(
    chunk: dict[str, Any],
    scopes_by_area: dict[str, list[dict[str, Any]]],
    objectives_by_major_unit: dict[str, list[dict[str, Any]]],
    target_by_objective_id: dict[str, dict[str, Any]],
    overrides: list[dict[str, Any]],
    scope_by_id: dict[str, dict[str, Any]],
    objective_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    override = find_override(chunk, overrides)
    if override:
        return build_override_mapping(chunk, override, scope_by_id, objective_by_id, target_by_objective_id)

    source_file = nfc(chunk.get("source_file", ""))
    page = int(chunk.get("page_or_slide") or 0)
    profile_name, profile = get_profile(source_file)
    content_role = "front_matter" if is_front_matter(chunk, profile) else "body"

    base = {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", ""),
        "source_file": chunk.get("source_file", ""),
        "page_or_slide": page,
        "chunk_index": chunk.get("chunk_index", 0),
        "content_hash": chunk.get("content_hash", ""),
        "extraction_quality": chunk.get("extraction_quality", ""),
        "profile": profile_name or "",
        "content_role": content_role,
        "mapped_at": datetime.now(timezone.utc).isoformat(),
    }

    if not profile:
        return {
            **base,
            "mapping_status": "needs_review",
            "needs_review": True,
            "review_reasons": ["unknown_source_profile"],
            "selected_scope": None,
            "selected_learning_objective": None,
            "scope_candidates": [],
            "learning_objective_candidates": [],
        }

    if content_role == "front_matter":
        return {
            **base,
            "mapping_status": "excluded_from_auto_mapping",
            "needs_review": True,
            "review_reasons": ["front_matter_or_contents_page"],
            "selected_scope": None,
            "selected_learning_objective": None,
            "scope_candidates": [],
            "learning_objective_candidates": [],
        }

    scope_candidates = top_scopes(chunk, scopes_by_area.get(profile["area"], []), profile)
    selected_scope = selected_or_none(scope_candidates, minimum="low")
    selected_detail = selected_scope.get("detail", "") if selected_scope else ""

    objective_candidates = top_objectives(
        chunk,
        objectives_by_major_unit.get(profile["major_unit"], []),
        selected_detail,
        profile,
        target_by_objective_id,
    )
    selected_objective = selected_or_none(objective_candidates, minimum="low")

    review_reasons = []
    if not selected_scope:
        review_reasons.append("no_scope_candidate_above_threshold")
    elif selected_scope["confidence"] == "low":
        review_reasons.append("low_scope_confidence")

    if not selected_objective:
        review_reasons.append("no_learning_objective_candidate_above_threshold")
    elif selected_objective["confidence"] == "low":
        review_reasons.append("low_learning_objective_confidence")

    selected_confidences = [
        selected_scope["confidence"] if selected_scope else "none",
        selected_objective["confidence"] if selected_objective else "none",
    ]
    if "none" in selected_confidences or "low" in selected_confidences:
        mapping_status = "needs_review"
    elif "medium" in selected_confidences:
        mapping_status = "draft_mapped"
    else:
        mapping_status = "auto_mapped_high"

    return {
        **base,
        "mapping_status": mapping_status,
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "selected_scope": selected_scope,
        "selected_learning_objective": selected_objective,
        "scope_candidates": scope_candidates,
        "learning_objective_candidates": objective_candidates,
    }


def apply_mapping(chunk: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    row = dict(chunk)
    scope = mapping.get("selected_scope") or {}
    objective = mapping.get("selected_learning_objective") or {}

    row["exam_period"] = scope.get("period", "")
    row["subject"] = scope.get("subject", "")
    row["field"] = scope.get("field", "")
    row["area"] = scope.get("area", "")
    row["sub_area"] = scope.get("detail", "")
    row["learning_objective"] = objective.get("objective", "")
    row["keywords"] = objective.get("keywords", [])
    row["scope_id"] = scope.get("scope_id", "")
    row["learning_objective_id"] = objective.get("objective_id", "")
    row["question_generation_target_id"] = objective.get("target_id", "")
    row["recommended_question_types"] = objective.get("recommended_question_types", [])
    row["scope_mapping_confidence"] = scope.get("confidence", "none")
    row["learning_objective_mapping_confidence"] = objective.get("confidence", "none")
    row["scope_objective_mapping_status"] = mapping.get("mapping_status", "")
    row["scope_objective_mapping_needs_review"] = mapping.get("needs_review", True)
    row["scope_objective_mapping_reasons"] = mapping.get("review_reasons", [])
    row["human_review"] = mapping.get("human_review")
    row["scope_candidates"] = mapping.get("scope_candidates", [])
    row["learning_objective_candidates"] = mapping.get("learning_objective_candidates", [])
    return row


def main() -> None:
    chunks = read_jsonl(INPUT_CHUNKS)
    exam_scope = read_json(RULES_DIR / "exam_scope.json")
    learning_objectives = read_json(RULES_DIR / "learning_objectives.json").get("objectives", [])
    targets = read_json(RULES_DIR / "question_generation_targets.json").get("targets", [])

    scopes = exam_scope.get("verified_detail_rows") or exam_scope.get("detail_rows") or []
    scopes_by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scope in scopes:
        scopes_by_area[nfc(scope.get("area", ""))].append(scope)

    objectives_by_major_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for objective in learning_objectives:
        objectives_by_major_unit[nfc(objective.get("major_unit", ""))].append(objective)

    target_by_objective_id = {
        nfc(target.get("learning_objective_id", "")): target
        for target in targets
        if target.get("learning_objective_id")
    }
    scope_by_id = {nfc(scope.get("scope_id", "")): scope for scope in scopes}
    objective_by_id = {nfc(objective.get("objective_id", "")): objective for objective in learning_objectives}
    overrides = load_overrides()

    mappings = [
        build_mapping(
            chunk,
            scopes_by_area,
            objectives_by_major_unit,
            target_by_objective_id,
            overrides,
            scope_by_id,
            objective_by_id,
        )
        for chunk in chunks
    ]
    mapped_chunks = [apply_mapping(chunk, mapping) for chunk, mapping in zip(chunks, mappings, strict=True)]

    report = {
        "version": 1,
        "purpose": "subject_reference_chunk_to_exam_scope_and_learning_objective_draft_mapping",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_chunks": str(INPUT_CHUNKS.relative_to(PROJECT_ROOT)),
        "mapping_output": str(OUTPUT_MAPPING.relative_to(PROJECT_ROOT)),
        "mapped_chunks_output": str(OUTPUT_CHUNKS.relative_to(PROJECT_ROOT)),
        "overrides_file": str(OVERRIDES_FILE.relative_to(PROJECT_ROOT)) if OVERRIDES_FILE.exists() else "",
        "override_count": len(overrides),
        "total_chunks": len(chunks),
        "mapping_status_counts": dict(Counter(row["mapping_status"] for row in mappings)),
        "scope_confidence_counts": dict(Counter((row.get("selected_scope") or {}).get("confidence", "none") for row in mappings)),
        "learning_objective_confidence_counts": dict(Counter((row.get("selected_learning_objective") or {}).get("confidence", "none") for row in mappings)),
        "content_role_counts": dict(Counter(row["content_role"] for row in mappings)),
        "profile_counts": dict(Counter(row["profile"] for row in mappings)),
        "selected_scope_counts": dict(Counter((row.get("selected_scope") or {}).get("detail", "none") for row in mappings)),
        "review_reason_counts": dict(Counter(reason for row in mappings for reason in row.get("review_reasons", []))),
        "by_source_file": {},
    }

    for source_file in sorted({row.get("source_file", "") for row in mappings}):
        source_rows = [row for row in mappings if row.get("source_file") == source_file]
        report["by_source_file"][source_file] = {
            "total_chunks": len(source_rows),
            "mapping_status_counts": dict(Counter(row["mapping_status"] for row in source_rows)),
            "selected_scope_counts": dict(Counter((row.get("selected_scope") or {}).get("detail", "none") for row in source_rows)),
            "review_reason_counts": dict(Counter(reason for row in source_rows for reason in row.get("review_reasons", []))),
        }

    write_jsonl(OUTPUT_MAPPING, mappings)
    write_jsonl(OUTPUT_CHUNKS, mapped_chunks)
    OUTPUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "total_chunks": report["total_chunks"],
        "mapping_status_counts": report["mapping_status_counts"],
        "scope_confidence_counts": report["scope_confidence_counts"],
        "learning_objective_confidence_counts": report["learning_objective_confidence_counts"],
        "selected_scope_counts": report["selected_scope_counts"],
        "review_reason_counts": report["review_reason_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
