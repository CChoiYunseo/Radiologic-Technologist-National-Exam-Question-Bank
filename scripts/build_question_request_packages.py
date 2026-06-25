#!/usr/bin/env python3
"""Build question-generation request packages from the validated RAG index.

This script does not generate questions. It prepares scope-bound evidence
packages and runs the deterministic pre-generation harness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_rule_based_generation_harness import validate_payload


DEFAULT_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"
DEFAULT_ANSWER_EVIDENCE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references"
DEFAULT_GENERATION_SAFE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references_generation_safe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "question_request_packages"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"
SCOPE_GENERATION_STRATEGY = PROJECT_ROOT / "resources" / "rules" / "scope_generation_strategy.json"
QUESTION_GENERATION_TARGETS = PROJECT_ROOT / "resources" / "rules" / "question_generation_targets.json"


LAW_SCOPE_PATTERN = re.compile(r"(법규|법률|의료법|지역보건법|조문|시행규칙|시행령|고시)")
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "area_only": 2}
MAJOR_UNIT_ALIASES = {
    "전산화단층검사": ["전산화단층검사"],
    "방사성의약품": ["핵의학검사"],
    "핵의학 기기": ["핵의학검사"],
    "심맥관 및 중재술": ["삼맥관및중재술", "심혈관및중재술"],
    "방사선생물": ["방사선생물학"],
    "초음파기술": ["초음파기술"],
}
PREFERRED_UNIT_ALIASES = {
    "CT 기초이론": ["영상의 재구성"],
    "CT 장치": ["영상의 재구성"],
    "나선형 CT 및 MDCT": ["나선형 CT 및 MDCT"],
    "영상의 재구성": ["영상의 재구성"],
    "방사성의약품의 특성 및 집적기전": ["방사성의약품"],
    "방사성의약품의 정도관리": ["방사성의약품", "핵의학기기", "체내검사"],
    "혈관조영법 및 기구": ["심장 관상동맥혈관조영술 및 중재술", "심장, 관상동맥조영술 및 중재술"],
    "흉·복부조영술 및 중재술": ["흉·복부혈관조영술 및 중재술"],
    "조직 및 장기에 대한 영향": ["조직 및 장기에 대한 방사선의 영향"],
    "진단장치": ["초음파 영상과 정도관리", "초음파 발생원리", "초음파 탐촉자"],
}
DETAIL_KEYWORD_ALIASES = {
    "CT 기초이론": ["ct", "기초", "원리", "세대", "구성", "검출기", "재구성"],
    "CT 장치": ["ct장치", "기본구성", "구성", "x선관", "검출기", "갠트리", "고전압"],
    "방사성의약품의 정도관리": ["정도관리", "품질관리", "검량계", "보관", "분배"],
    "혈관조영법 및 기구": ["혈관조영실", "운영", "검사도구", "기구", "카테터", "혈관조영검사"],
    "진단장치": ["진단장치", "구성", "영상조절", "정도관리", "탐촉자"],
}
DETAIL_NEGATIVE_KEYWORD_ALIASES = {
    "혈관조영법 및 기구": ["골관절계", "기타조영검사", "타액선", "누낭", "누공"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_vector_rag_ids(db_dir: Path) -> set[str]:
    db_path = db_dir / "chunks.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"Vector DB chunks.sqlite not found: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT chunk_id, metadata_json FROM chunks").fetchall()
    finally:
        conn.close()
    ids = set()
    for chunk_id, metadata_json in rows:
        rag_input_id = ""
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
                rag_input_id = metadata.get("rag_input_id") or ""
            except json.JSONDecodeError:
                rag_input_id = ""
        ids.add(str(rag_input_id or chunk_id))
    return {item for item in ids if item}


def install_generation_safe_filter(conn: sqlite3.Connection, safe_ids: set[str]) -> None:
    conn.execute("DROP TABLE IF EXISTS generation_safe_rag_ids")
    conn.execute("CREATE TEMP TABLE generation_safe_rag_ids (rag_input_id TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO generation_safe_rag_ids (rag_input_id) VALUES (?)",
        [(rag_input_id,) for rag_input_id in sorted(safe_ids)],
    )
    conn.commit()


def vector_policy(
    answer_evidence_vector_db: Path,
    generation_safe_vector_db: Path,
    safe_ids: set[str],
    safe_filter_enabled: bool,
) -> dict[str, Any]:
    evidence_manifest = load_json_if_exists(answer_evidence_vector_db / "manifest.json")
    safe_manifest = load_json_if_exists(generation_safe_vector_db / "manifest.json")
    return {
        "answer_evidence_vector_db": str(answer_evidence_vector_db),
        "answer_evidence_index_use": evidence_manifest.get("index_use", "rag_evidence_search"),
        "answer_evidence_chunk_count": evidence_manifest.get("chunk_count"),
        "generation_candidate_vector_db": str(generation_safe_vector_db),
        "generation_candidate_index_use": safe_manifest.get("index_use", "generation_safe_candidate_search"),
        "generation_candidate_chunk_count": safe_manifest.get("chunk_count"),
        "generation_safe_filter_enabled": safe_filter_enabled,
        "generation_safe_ids_loaded": len(safe_ids),
        "generation_policy": (
            "automatic_generation_uses_generation_safe_index_only; "
            "answer_evidence_index_is_for_post_generation_grounding_and_validation"
        ),
    }


def normalize_scope_value(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*\d+\.\s*", "", text)
    return text.strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize_scope_value(value)).lower()


def normalized_tokens(value: Any) -> set[str]:
    text = normalize_scope_value(value).lower()
    return set(re.findall(r"[0-9a-z가-힣]+", text))


def unit_matches_scope(unit: Any, detail: Any) -> bool:
    unit_compact = compact(unit)
    detail_compact = compact(detail)
    if not unit_compact or not detail_compact:
        return False
    if unit_compact == detail_compact or unit_compact in detail_compact or detail_compact in unit_compact:
        return True

    unit_tokens = normalized_tokens(unit)
    detail_tokens = normalized_tokens(detail)
    if not unit_tokens or not detail_tokens:
        return False
    return bool(unit_tokens & detail_tokens)


def package_id_for(scope: dict[str, Any], evidence_ids: list[str]) -> str:
    payload = {
        "scope": scope,
        "evidence_ids": evidence_ids,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"qrp_{digest}"


def load_scope_strategy() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    data = read_json(SCOPE_GENERATION_STRATEGY)
    rows = data.get("rows", [])
    index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            compact(row.get("subject")),
            compact(row.get("field")),
            compact(row.get("area")),
            compact(row.get("detail")),
        )
        index[key] = row
    return index


def load_generation_targets(
    strategies: dict[tuple[str, str, str, str], dict[str, Any]],
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    data = read_json(QUESTION_GENERATION_TARGETS)
    targets = data.get("targets", [])
    strategy_by_scope_id = {
        row.get("scope_id", ""): key
        for key, row in strategies.items()
        if row.get("scope_id")
    }
    by_scope_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for target in targets:
        for candidate in target.get("scope_candidates") or []:
            scope_id = candidate.get("scope_id")
            key = strategy_by_scope_id.get(scope_id)
            if key:
                by_scope_key[key].append(target)
    for rows in by_scope_key.values():
        rows.sort(key=lambda item: (item.get("level", ""), item.get("target_id", "")))
    return by_scope_key


def load_all_generation_targets() -> list[dict[str, Any]]:
    data = read_json(QUESTION_GENERATION_TARGETS)
    rows = data.get("targets", [])
    rows.sort(key=lambda item: (item.get("level", ""), item.get("target_id", "")))
    return rows


def scope_groups(conn: sqlite3.Connection, safe_filter_enabled: bool) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    safe_where = ""
    if safe_filter_enabled:
        safe_where = "WHERE rag_input_id IN (SELECT rag_input_id FROM generation_safe_rag_ids)"
    rows = conn.execute(
        f"""
        SELECT
            mapped_period,
            mapped_subject,
            mapped_field,
            mapped_area,
            mapped_detail,
            mapped_scope_id,
            COUNT(*) AS evidence_count,
            SUM(scope_mapping_confidence = 'high') AS high_count,
            SUM(scope_mapping_confidence = 'medium') AS medium_count,
            SUM(scope_mapping_confidence = 'area_only') AS area_only_count
        FROM chunks
        {safe_where}
        GROUP BY
            mapped_period, mapped_subject, mapped_field, mapped_area,
            mapped_detail, mapped_scope_id
        HAVING evidence_count >= 2
        ORDER BY evidence_count DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def select_evidence(conn: sqlite3.Connection, group: dict[str, Any], limit: int, safe_filter_enabled: bool) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    safe_where = ""
    if safe_filter_enabled:
        safe_where = "AND rag_input_id IN (SELECT rag_input_id FROM generation_safe_rag_ids)"
    rows = conn.execute(
        f"""
        SELECT
            rag_input_id,
            source_chunk_id,
            source_file,
            source_path,
            page_or_slide,
            content_sha256,
            excerpt,
            mapped_period,
            mapped_subject,
            mapped_field,
            mapped_area,
            mapped_detail,
            mapped_scope_id,
            scope_mapping_status,
            scope_mapping_confidence,
            scope_mapping_needs_review
        FROM chunks
        WHERE mapped_period = ?
          AND mapped_subject = ?
          AND mapped_field = ?
          AND mapped_area = ?
          AND mapped_detail = ?
          AND mapped_scope_id = ?
          {safe_where}
        ORDER BY
            CASE scope_mapping_confidence
                WHEN 'high' THEN 0
                WHEN 'medium' THEN 1
                ELSE 2
            END,
            scope_mapping_needs_review,
            page_or_slide
        LIMIT ?
        """,
        (
            group["mapped_period"],
            group["mapped_subject"],
            group["mapped_field"],
            group["mapped_area"],
            group["mapped_detail"],
            group["mapped_scope_id"],
            limit,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def source_scope(group: dict[str, Any]) -> dict[str, str]:
    return {
        "period": group.get("mapped_period") or "",
        "subject": group.get("mapped_subject") or "",
        "field": group.get("mapped_field") or "",
        "area": group.get("mapped_area") or "",
        "detail": group.get("mapped_detail") or "",
        "scope_id": group.get("mapped_scope_id") or "",
    }


def is_excluded_scope(scope: dict[str, str]) -> tuple[bool, str]:
    if scope.get("period") == "3교시" or compact(scope.get("subject")) == compact("실기시험"):
        return True, "third_period_excluded"
    if not scope.get("detail"):
        return True, "empty_detail_excluded"
    joined = " ".join(scope.get(key, "") for key in ["field", "area", "detail"])
    if LAW_SCOPE_PATTERN.search(joined):
        return True, "law_scope_excluded"
    return False, ""


def strategy_for(scope: dict[str, str], strategies: dict[tuple[str, str, str, str], dict[str, Any]]) -> dict[str, Any]:
    key = (compact(scope["subject"]), compact(scope["field"]), compact(scope["area"]), compact(scope["detail"]))
    strategy = strategies.get(key, {})
    return {
        "recommended_question_types": strategy.get("recommended_question_types") or ["개념형"],
        "recommended_difficulties": strategy.get("recommended_difficulties") or ["중"],
        "required_evidence_types": strategy.get("required_evidence_types") or ["전공 근거 자료"],
        "strategy_scope_id": strategy.get("scope_id", ""),
    }


def alias_compacts(values: list[str]) -> set[str]:
    return {compact(value) for value in values if compact(value)}


def scope_major_alias_compacts(scope: dict[str, str]) -> set[str]:
    aliases: list[str] = []
    for key in [scope.get("area", ""), scope.get("detail", "")]:
        aliases.extend(MAJOR_UNIT_ALIASES.get(key, []))
    return alias_compacts(aliases)


def scope_preferred_unit_compacts(scope: dict[str, str]) -> set[str]:
    aliases: list[str] = []
    for key in [scope.get("detail", ""), scope.get("area", "")]:
        aliases.extend(PREFERRED_UNIT_ALIASES.get(key, []))
    return alias_compacts(aliases)


def scope_detail_keyword_compacts(scope: dict[str, str]) -> set[str]:
    aliases: list[str] = []
    for key in [scope.get("detail", ""), scope.get("area", "")]:
        aliases.extend(DETAIL_KEYWORD_ALIASES.get(key, []))
    return alias_compacts(aliases)


def scope_detail_negative_keyword_compacts(scope: dict[str, str]) -> set[str]:
    aliases: list[str] = []
    for key in [scope.get("detail", ""), scope.get("area", "")]:
        aliases.extend(DETAIL_NEGATIVE_KEYWORD_ALIASES.get(key, []))
    return alias_compacts(aliases)


def target_search_text(target: dict[str, Any]) -> str:
    return " ".join(
        [
            str(target.get("major_unit", "")),
            str(target.get("unit", "")),
            str(target.get("objective", "")),
            " ".join(target.get("keywords") or []),
        ]
    )


def fallback_target_score(scope: dict[str, str], target: dict[str, Any]) -> int:
    major_aliases = scope_major_alias_compacts(scope)
    unit_aliases = scope_preferred_unit_compacts(scope)
    target_major = compact(target.get("major_unit"))
    target_unit = compact(target.get("unit"))
    target_text = compact(target_search_text(target))

    score = 0
    if major_aliases and any(alias in target_major for alias in major_aliases):
        score += 40
    elif major_aliases:
        return 0

    if unit_aliases and any(alias == target_unit or alias in target_unit or target_unit in alias for alias in unit_aliases):
        score += 35

    if unit_matches_scope(target.get("unit"), scope.get("detail")):
        score += 20

    detail_keywords = scope_detail_keyword_compacts(scope)
    score += sum(12 for keyword in detail_keywords if keyword and keyword in target_text)

    negative_keywords = scope_detail_negative_keyword_compacts(scope)
    score -= sum(20 for keyword in negative_keywords if keyword and keyword in target_text)

    detail_tokens = normalized_tokens(scope.get("detail"))
    area_tokens = normalized_tokens(scope.get("area"))
    target_tokens = normalized_tokens(target_search_text(target))
    score += len(detail_tokens & target_tokens) * 3
    score += len(area_tokens & target_tokens)

    if compact(scope.get("detail")) and compact(scope.get("detail")) in compact(target_search_text(target)):
        score += 10
    return score


def fallback_target_candidates(scope: dict[str, str], all_targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for target in all_targets:
        score = fallback_target_score(scope, target)
        if score >= 40:
            scored.append((score, target))
    scored.sort(key=lambda item: (-item[0], item[1].get("level", ""), item[1].get("target_id", "")))
    return [target for _, target in scored[:5]]


def format_target_candidate(item: dict[str, Any], mapping_method: str) -> dict[str, Any]:
    return {
        "target_id": item.get("target_id"),
        "learning_objective_id": item.get("learning_objective_id"),
        "major_unit": item.get("major_unit"),
        "unit": item.get("unit"),
        "objective": item.get("objective"),
        "keywords": item.get("keywords") or [],
        "level": item.get("level"),
        "recommended_question_types": item.get("recommended_question_types") or [],
        "mapping_method": mapping_method,
    }


def target_candidates(
    scope: dict[str, str],
    targets_by_scope: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    all_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    key = (compact(scope["subject"]), compact(scope["field"]), compact(scope["area"]), compact(scope["detail"]))
    candidates = targets_by_scope.get(key, [])
    unit_matched = [item for item in candidates if unit_matches_scope(item.get("unit"), scope.get("detail"))]
    selected = unit_matched if unit_matched else candidates
    if selected:
        return [format_target_candidate(item, "scope_id_unit_match" if unit_matched else "scope_id") for item in selected[:5]]
    fallback = fallback_target_candidates(scope, all_targets)
    return [format_target_candidate(item, "fallback_scope_text") for item in fallback]


def build_package(
    group: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    strategies: dict[tuple[str, str, str, str], dict[str, Any]],
    targets_by_scope: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    all_targets: list[dict[str, Any]],
    rag_policy: dict[str, Any],
) -> dict[str, Any]:
    scope = source_scope(group)
    evidence_refs = [
        {
            "rag_input_id": row["rag_input_id"],
            "source_file": row["source_file"],
            "source_path": row["source_path"],
            "page_or_slide": row["page_or_slide"],
            "content_sha256": row["content_sha256"],
            "scope_mapping_confidence": row["scope_mapping_confidence"],
            "scope_mapping_status": row["scope_mapping_status"],
            "scope_mapping_needs_review": bool(row["scope_mapping_needs_review"]),
        }
        for row in evidence_rows
    ]
    evidence_ids = [row["rag_input_id"] for row in evidence_rows]
    strategy = strategy_for(scope, strategies)
    targets = target_candidates(scope, targets_by_scope, all_targets)
    package = {
        "package_id": package_id_for(scope, evidence_ids),
        "created_at": now_iso(),
        "mode": "pre_generation",
        "requested_scope": scope,
        "min_evidence_count": 2,
        "evidence_refs": evidence_refs,
        "evidence_preview": [
            {
                "rag_input_id": row["rag_input_id"],
                "source_file": row["source_file"],
                "page_or_slide": row["page_or_slide"],
                "excerpt": row["excerpt"],
            }
            for row in evidence_rows
        ],
        "generation_constraints": {
            "do_not_generate_question_in_this_step": True,
            "rag_use": "answer_evidence_only",
            "must_write_new_wording": True,
            "must_not_copy_source_sentences": True,
            "visual_table_formula_law_materials_excluded": True,
            "approved_for_generation": False,
            "generation_candidate_index_required": True,
            "answer_evidence_index_for_validation_only": True,
        },
        "rag_index_policy": rag_policy,
        "recommended_generation_settings": {
            "question_type_candidates": strategy["recommended_question_types"],
            "difficulty_candidates": strategy["recommended_difficulties"],
            "required_evidence_types": strategy["required_evidence_types"],
            "learning_objective_candidates": targets,
        },
        "source_group_counts": {
            "total_evidence_in_index": int(group["evidence_count"]),
            "high": int(group["high_count"] or 0),
            "medium": int(group["medium_count"] or 0),
            "area_only": int(group["area_only_count"] or 0),
        },
    }
    return package


def package_payload_for_harness(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "pre_generation",
        "package": {
            "requested_scope": package["requested_scope"],
            "min_evidence_count": package["min_evidence_count"],
            "evidence_refs": [{"rag_input_id": ref["rag_input_id"]} for ref in package["evidence_refs"]],
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 문제 생성 요청 패키지 빌드 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 DB: `{report['inputs']['rag_bm25_db']}`",
        f"- 후보 범위: {report['counts']['scope_groups']}",
        f"- 제외 범위: {report['counts']['excluded_scope_groups']}",
        f"- 패키지 후보: {report['counts']['candidate_packages']}",
        f"- ready_strict: {report['counts']['ready_strict']}",
        f"- ready_with_warnings: {report['counts']['ready_with_warnings']}",
        f"- rejected: {report['counts']['rejected']}",
        "",
        "## 제외 사유",
    ]
    if report["exclude_reason_counts"]:
        for key, value in report["exclude_reason_counts"].items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- 없음")
    lines.extend(["", "## Harness 결과"])
    for key, value in report["harness_status_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## 산출물",
            f"- 전체 후보: `{report['outputs']['all_candidates_jsonl']}`",
            f"- strict 통과: `{report['outputs']['ready_strict_jsonl']}`",
            f"- warning 포함 통과: `{report['outputs']['ready_with_warnings_jsonl']}`",
            f"- 실패/제외: `{report['outputs']['rejected_jsonl']}`",
            "",
            "## 주의",
            "- 이 단계는 문제 생성이 아니라 생성 요청 패키지 구성입니다.",
            "- 패키지는 RAG 근거 위치와 생성 제약을 담으며 문제 문항·보기·해설을 만들지 않습니다.",
            "- LLM 연결 전에는 ready_strict부터 사용하는 것을 권장합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--answer-evidence-vector-db", type=Path, default=DEFAULT_ANSWER_EVIDENCE_VECTOR_DB)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument(
        "--disable-generation-safe-index-filter",
        action="store_true",
        help="Do not restrict package evidence to the generation-safe vector index. Use only for diagnostics.",
    )
    parser.add_argument("--evidence-per-package", type=int, default=4)
    parser.add_argument("--include-warning-packages", action="store_true", help="Also include warning packages in ready_all.jsonl")
    args = parser.parse_args()

    strategies = load_scope_strategy()
    targets_by_scope = load_generation_targets(strategies)
    all_targets = load_all_generation_targets()
    all_candidates: list[dict[str, Any]] = []
    ready_strict: list[dict[str, Any]] = []
    ready_with_warnings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    excluded_scope_rows: list[dict[str, Any]] = []
    exclude_reasons = Counter()
    harness_status = Counter()

    safe_filter_enabled = not args.disable_generation_safe_index_filter
    safe_ids = load_vector_rag_ids(args.generation_safe_vector_db) if safe_filter_enabled else set()
    rag_policy = vector_policy(
        args.answer_evidence_vector_db,
        args.generation_safe_vector_db,
        safe_ids,
        safe_filter_enabled,
    )

    conn = sqlite3.connect(args.db)
    try:
        if safe_filter_enabled:
            install_generation_safe_filter(conn, safe_ids)
        bm25_rows = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        safe_bm25_intersection = (
            conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE rag_input_id IN (SELECT rag_input_id FROM generation_safe_rag_ids)"
            ).fetchone()[0]
            if safe_filter_enabled
            else bm25_rows
        )
        groups = scope_groups(conn, safe_filter_enabled)
        for group in groups:
            scope = source_scope(group)
            excluded, reason = is_excluded_scope(scope)
            if excluded:
                exclude_reasons[reason] += 1
                excluded_scope_rows.append({"requested_scope": scope, "reason": reason, "evidence_count": group["evidence_count"]})
                continue

            evidence_rows = select_evidence(conn, group, args.evidence_per_package, safe_filter_enabled)
            package = build_package(group, evidence_rows, strategies, targets_by_scope, all_targets, rag_policy)
            validation = validate_payload(package_payload_for_harness(package), conn, safe_ids if safe_filter_enabled else None)
            package["harness_result"] = {
                "summary": validation["summary"],
                "findings": validation["findings"],
            }
            if validation["summary"]["error_count"] == 0 and validation["summary"]["warning_count"] == 0:
                package["package_status"] = "ready_strict"
                ready_strict.append(package)
            elif validation["summary"]["error_count"] == 0:
                package["package_status"] = "ready_with_warnings"
                ready_with_warnings.append(package)
            else:
                package["package_status"] = "rejected"
                rejected.append(package)
            harness_status[package["package_status"]] += 1
            all_candidates.append(package)
    finally:
        conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_path = args.output_dir / "question_request_packages_all_candidates.jsonl"
    strict_path = args.output_dir / "question_request_packages_ready_strict.jsonl"
    warning_path = args.output_dir / "question_request_packages_ready_with_warnings.jsonl"
    rejected_path = args.output_dir / "question_request_packages_rejected.jsonl"
    excluded_path = args.output_dir / "question_request_packages_excluded_scopes.jsonl"
    ready_all_path = args.output_dir / "question_request_packages_ready_all.jsonl"
    report_json = args.report_dir / "question_request_package_build_report.json"
    report_md = args.report_dir / "question_request_package_build_report.md"

    write_jsonl(all_path, all_candidates)
    write_jsonl(strict_path, ready_strict)
    write_jsonl(warning_path, ready_with_warnings)
    write_jsonl(rejected_path, rejected)
    write_jsonl(excluded_path, excluded_scope_rows)
    ready_all = ready_strict + (ready_with_warnings if args.include_warning_packages else [])
    write_jsonl(ready_all_path, ready_all)

    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "inputs": {
            "rag_bm25_db": str(args.db),
            "answer_evidence_vector_db": str(args.answer_evidence_vector_db),
            "generation_safe_vector_db": str(args.generation_safe_vector_db),
            "scope_generation_strategy": str(SCOPE_GENERATION_STRATEGY),
            "question_generation_targets": str(QUESTION_GENERATION_TARGETS),
        },
        "outputs": {
            "all_candidates_jsonl": str(all_path),
            "ready_strict_jsonl": str(strict_path),
            "ready_with_warnings_jsonl": str(warning_path),
            "ready_all_jsonl": str(ready_all_path),
            "rejected_jsonl": str(rejected_path),
            "excluded_scopes_jsonl": str(excluded_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "scope_groups": len(groups),
            "excluded_scope_groups": len(excluded_scope_rows),
            "candidate_packages": len(all_candidates),
            "ready_strict": len(ready_strict),
            "ready_with_warnings": len(ready_with_warnings),
            "ready_all": len(ready_all),
            "rejected": len(rejected),
            "bm25_chunks": bm25_rows,
            "generation_safe_vector_ids": len(safe_ids),
            "generation_safe_bm25_intersection": safe_bm25_intersection,
        },
        "exclude_reason_counts": dict(exclude_reasons),
        "harness_status_counts": dict(harness_status),
        "package_scope_counts": dict(Counter(pkg["requested_scope"]["area"] for pkg in all_candidates)),
        "policy": {
            "question_generation_performed": False,
            "llm_used": False,
            "recommended_next_input": str(strict_path),
            "warning_packages_require_review_before_llm": True,
            "rag_index_policy": rag_policy,
        },
    }
    write_json(report_json, report)
    report_md.write_text(markdown_report(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "scope_groups": report["counts"]["scope_groups"],
                "candidate_packages": report["counts"]["candidate_packages"],
                "ready_strict": report["counts"]["ready_strict"],
                "ready_with_warnings": report["counts"]["ready_with_warnings"],
                "rejected": report["counts"]["rejected"],
                "report": str(report_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
