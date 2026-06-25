#!/usr/bin/env python3
"""Map RAG input chunks to verified exam scope rows.

The output is a reviewable first pass. It never marks chunks as approved for
question generation; it only adds subject/scope candidates for RAG retrieval.
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
DEFAULT_INPUT = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"
VERIFIED_SCOPE = PROJECT_ROOT / "resources" / "extracted" / "sebuyeongyeok_verified_scope.json"
EXAM_SCOPE = PROJECT_ROOT / "resources" / "rules" / "exam_scope.json"
LEARNING_OBJECTIVES = PROJECT_ROOT / "resources" / "rules" / "learning_objectives.json"


SOURCE_SCOPE_PROFILES: dict[str, dict[str, Any]] = {
    "공중보건학": {
        "subject": "방사선이론",
        "field": "기초의학",
        "area": "공중보건",
        "major_units": ["#8 공중보건학", "#8 공중보건"],
        "aliases": ["공중보건", "보건", "역학", "질병관리", "환경보건"],
    },
    "관리학": {
        "subject": "방사선이론",
        "field": "방사선 장해방어",
        "area": "방사선관리",
        "major_units": ["#7 방사선관리학", "#7 방사선관리"],
        "aliases": ["방사선관리", "방사선안전", "방사선방어", "모니터링", "관계법규"],
        "generation_hold_reason": "방사선 안전관리·법규성 내용은 최신 기준 확인 전 자동 생성 금지",
    },
    "방사선계측학": {
        "subject": "방사선이론",
        "field": "방사선취급",
        "area": "방사선계측",
        "major_units": ["#6 방사선계측학", "#6 방사선계측"],
        "aliases": ["방사선계측", "검출기", "선량", "교정", "통계"],
        "generation_hold_reason": "계측 수치·단위·공식은 검토 전 자동 생성 금지",
    },
    "방사선생물학": {
        "subject": "방사선이론",
        "field": "방사선 장해방어",
        "area": "방사선생물",
        "major_units": ["#9 방사선생물학", "#9 방사선생물"],
        "aliases": ["방사선생물", "세포", "장해", "유전", "태아", "급성효과"],
    },
    "의료영상정보학": {
        "subject": "방사선이론",
        "field": "방사선기초",
        "area": "의료영상정보",
        "major_units": ["#3 의료영상정보학", "#3 의료영상정보"],
        "aliases": ["의료영상정보", "디지털", "PACS", "영상평가", "기록계"],
    },
    "방사선영상학1": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "방사선영상",
        "major_units": ["#10 방사선영상학", "#10 방사선영상검사"],
        "aliases": ["방사선영상", "일반촬영", "두개부", "흉부", "복부", "척추", "골반"],
    },
    "방사선영상학2": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "방사선영상",
        "major_units": ["#10 방사선영상학", "#10 방사선영상검사"],
        "aliases": ["방사선영상", "일반촬영", "유방", "소아", "골밀도", "영상치의학"],
    },
    "투시조영": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "투시조영검사",
        "major_units": ["#11 투시조영검사", "#11 투시조영"],
        "aliases": ["투시", "조영", "위장관", "비뇨", "담도", "관절", "신경계"],
    },
    "혈관조영": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "심맥관 및 중재술",
        "major_units": ["#12 심맥관 및 중재술", "#12 혈관조영"],
        "aliases": ["혈관조영", "중재", "관상동맥", "뇌혈관", "사지", "카테터"],
    },
    "초음파영상학실습": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "초음파기술",
        "major_units": ["#13 초음파기술", "#13 초음파영상검사"],
        "aliases": ["초음파", "탐촉자", "도플러", "상복부", "심장", "혈관", "산부인과"],
    },
    "컴퓨터단층촬영학": {
        "subject": "방사선응용",
        "field": "영상진단",
        "area": "전산화단층검사",
        "major_units": ["#14 전산화단층검사", "#14 CT"],
        "aliases": ["CT", "전산화단층", "나선형", "MDCT", "재구성", "선량"],
    },
    "핵의학": {
        "subject": "방사선응용",
        "field": "핵의학 검사",
        "area": "",
        "major_units": ["#15 핵의학검사", "#15 핵의학"],
        "aliases": ["핵의학", "방사성의약품", "SPECT", "PET", "감마카메라", "체내검사"],
    },
    "방사선 치료학": {
        "subject": "방사선응용",
        "field": "방사선 치료",
        "area": "",
        "major_units": ["#16 방사선치료", "#16 방사선 치료"],
        "aliases": ["방사선치료", "선형가속기", "치료계획", "선량분포", "원격치료", "근접치료"],
        "generation_hold_reason": "치료 선량·장치 수치·공식은 검토 전 자동 생성 금지",
    },
}


DETAIL_ALIASES: dict[str, list[str]] = {
    "공중보건총론": ["공중보건", "건강", "보건행정", "보건지표", "지역사회"],
    "역학 및 질병관리": ["역학", "감염병", "질병관리", "예방접종", "유병률", "발생률"],
    "환경보건": ["환경", "수질", "대기", "폐기물", "식품위생", "산업보건"],
    "방사선관계법규": ["법규", "의료법", "원자력", "규칙", "고시", "허가", "신고"],
    "방사선관리의 개념": ["관리", "방어", "최적화", "정당화", "ALARA"],
    "방사선모니터링": ["모니터링", "감시", "측정", "오염", "개인선량"],
    "방사선안전관리": ["안전관리", "차폐", "방호", "구역", "선량한도"],
    "각종 방사선 검출기의 원리 및 특성": ["검출기", "전리함", "GM", "섬광", "반도체"],
    "조사선량과 흡수선량 측정": ["조사선량", "흡수선량", "선량", "커마", "측정"],
    "방사선 계측기의 교정 및 계측치의 통계": ["교정", "오차", "통계", "표준편차", "불확도"],
    "방사선생물학의 개념": ["생물학", "LET", "RBE", "직접작용", "간접작용"],
    "방사선에 의한 세포영향": ["세포", "DNA", "염색체", "세포주기", "생존곡선"],
    "방사선효과의 영향인자": ["산소효과", "선량률", "분할", "감수성", "회복"],
    "배아 및 태아·유전적 영향": ["배아", "태아", "유전", "기형", "생식"],
    "전신조사의 급성효과 및 만발성장해": ["급성", "만발", "전신", "백내장", "발암"],
    "조직 및 장기에 대한 영향": ["조직", "장기", "피부", "조혈", "소화관"],
    "기록계": ["기록계", "필름", "증감지", "카세트"],
    "디지털 엑스선 영상": ["디지털", "CR", "DR", "검출기", "픽셀"],
    "엑스선 영상의 성립": ["영상", "감약", "대조도", "선예도", "흐림"],
    "의료영상의 평가": ["평가", "화질", "MTF", "노이즈", "DQE"],
    "방사선 검사에 관련된 용어": ["용어", "자세", "방향", "투사", "체위"],
    "혈관조영법 및 기구": ["혈관조영", "기구", "카테터", "가이드와이어", "조영제"],
    "뇌혈관조영술 및 중재술": ["뇌혈관", "경동맥", "척추동맥", "중재"],
    "심장·관상동맥혈관조영술 및 중재술": ["심장", "관상동맥", "PCI", "스텐트"],
    "흉·복부조영술 및 중재술": ["흉부", "복부", "간", "신장", "색전"],
    "사지조영술 및 중재술": ["사지", "말초혈관", "하지", "상지"],
    "비혈관계 중재술": ["비혈관", "배액", "생검", "스텐트"],
    "CT 기초이론": ["CT", "감약", "CT number", "HU", "투영"],
    "CT 장치": ["갠트리", "검출기", "콜리메이터", "테이블", "관전압"],
    "영상의 재구성": ["재구성", "필터", "역투영", "알고리즘"],
    "나선형 CT 및 MDCT": ["나선형", "helical", "MDCT", "pitch", "다중검출기"],
    "화질 및 선량": ["화질", "노이즈", "선량", "CTDI", "DLP"],
    "초음파 물리": ["음파", "주파수", "파장", "음향임피던스", "감쇠"],
    "진단장치": ["장치", "탐촉자", "빔포머", "스캔"],
    "탐촉자": ["탐촉자", "프로브", "압전", "배열"],
    "도플러 초음파 검사": ["도플러", "혈류", "속도", "파형"],
    "방사성의약품의 특성 및 집적기전": ["방사성의약품", "집적", "표지", "섭취"],
    "방사성의약품의 정도관리": ["정도관리", "순도", "표지효율", "품질"],
    "섬광카메라 및 핵의학검사 관련기기": ["섬광카메라", "감마카메라", "콜리메이터"],
    "SPECT 및 PET 시스템": ["SPECT", "PET", "동시계수", "소멸방사선"],
    "핵의학 기기의 성능평가": ["성능평가", "균일도", "공간분해능", "감도"],
    "방사선치료 개념 및 기술": ["치료", "분할", "표적", "정상조직"],
    "선량의 기본개념": ["선량", "흡수선량", "등선량", "선량률"],
    "선량측정 및 선량분포": ["선량측정", "분포", "PDD", "TAR", "TPR"],
    "모의치료 및 치료계획": ["모의치료", "치료계획", "CT simulator", "계획"],
    "선형가속기": ["선형가속기", "LINAC", "가속관", "MLC"],
    "광자선 및 전자선 치료기술": ["광자선", "전자선", "SSD", "SAD"],
    "특수치료기술": ["IMRT", "VMAT", "SRS", "SBRT", "IGRT"],
    "근접치료기술": ["근접치료", "brachytherapy", "선원", "강내"],
    "치료장치 및 모의치료기기의 정도관리": ["품질관리", "정도관리", "QA", "QC"],
}


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def strip_numbering(value: Any) -> str:
    text = nfc(value).strip()
    return re.sub(r"^\s*\d+\.\s*", "", text).strip()


def compact(value: Any) -> str:
    text = strip_numbering(value).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def tokenize(value: Any) -> set[str]:
    text = strip_numbering(value).lower()
    tokens = set()
    for token in re.findall(r"[0-9a-zA-Z가-힣]+", text):
        token = token.lower()
        if len(token) < 2 or token.isdigit():
            continue
        tokens.add(token)
    return tokens


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_verified_scope_rows() -> list[dict[str, Any]]:
    data = read_json(VERIFIED_SCOPE)
    rows = data.get("rows", data if isinstance(data, list) else [])
    output: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["subject"] = strip_numbering(row.get("subject"))
        normalized["field"] = strip_numbering(row.get("field"))
        normalized["area"] = strip_numbering(row.get("area"))
        normalized["detail"] = strip_numbering(row.get("detail"))
        output.append(normalized)
    return output


def load_scope_ids() -> dict[tuple[str, str, str, str], str]:
    data = read_json(EXAM_SCOPE)
    rows = data.get("verified_detail_rows") or data.get("detail_rows") or []
    ids: dict[tuple[str, str, str, str], str] = {}
    for row in rows:
        key = (
            strip_numbering(row.get("subject")),
            strip_numbering(row.get("field")),
            strip_numbering(row.get("area")),
            strip_numbering(row.get("detail")),
        )
        ids[key] = row.get("scope_id", "")
    return ids


def load_learning_objectives() -> list[dict[str, Any]]:
    rows = read_json(LEARNING_OBJECTIVES).get("objectives", [])
    output: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        normalized["field_hint"] = strip_numbering(row.get("field_hint"))
        normalized["unit"] = strip_numbering(row.get("unit"))
        normalized["major_unit"] = strip_numbering(row.get("major_unit"))
        output.append(normalized)
    return output


def source_profile(source_file: str) -> tuple[str, dict[str, Any]] | tuple[str, None]:
    source = nfc(source_file)
    source_compact = compact(source)
    for marker, profile in SOURCE_SCOPE_PROFILES.items():
        if compact(marker) in source_compact:
            return marker, profile
    if "컴퓨터단층촬영학" in source or "컴퓨터단층촬영학" in source:
        return "컴퓨터단층촬영학", SOURCE_SCOPE_PROFILES["컴퓨터단층촬영학"]
    return "", None


def scope_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        strip_numbering(row.get("subject")),
        strip_numbering(row.get("field")),
        strip_numbering(row.get("area")),
        strip_numbering(row.get("detail")),
    )


def inferred_period(subject: str) -> str:
    subject_compact = compact(subject)
    if subject_compact in {compact("방사선이론"), compact("의료법규")}:
        return "1교시"
    if subject_compact == compact("방사선응용"):
        return "2교시"
    if subject_compact == compact("실기시험"):
        return "3교시"
    return ""


def candidate_scopes(
    scope_rows: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = compact(profile.get("subject"))
    field = compact(profile.get("field"))
    area = compact(profile.get("area"))
    candidates = []
    for row in scope_rows:
        if compact(row.get("subject")) != subject:
            continue
        if field and compact(row.get("field")) != field:
            continue
        if area and compact(row.get("area")) != area:
            continue
        candidates.append(row)
    if candidates:
        return candidates

    for row in scope_rows:
        if compact(row.get("subject")) == subject and field and compact(row.get("field")) == field:
            candidates.append(row)
    return candidates


def score_scope(row: dict[str, Any], content: str, profile: dict[str, Any]) -> tuple[float, list[str]]:
    content_compact = compact(content)
    content_tokens = tokenize(content)
    detail = strip_numbering(row.get("detail"))
    area = strip_numbering(row.get("area"))
    aliases = [detail, area, *(profile.get("aliases") or []), *DETAIL_ALIASES.get(detail, [])]
    score = 0.0
    reasons: list[str] = []

    if detail and compact(detail) in content_compact:
        score += 8.0
        reasons.append("detail_exact")
    if area and compact(area) in content_compact:
        score += 3.0
        reasons.append("area_exact")

    alias_hits = []
    for alias in aliases:
        alias_compact = compact(alias)
        if alias_compact and alias_compact in content_compact:
            alias_hits.append(strip_numbering(alias))
    if alias_hits:
        unique_hits = sorted(set(alias_hits))
        score += min(8.0, 1.25 * len(unique_hits))
        reasons.append("alias:" + ",".join(unique_hits[:5]))

    alias_tokens = tokenize(" ".join(aliases))
    token_hits = sorted(alias_tokens & content_tokens)
    if token_hits:
        score += min(6.0, 0.75 * len(token_hits))
        reasons.append("token:" + ",".join(token_hits[:6]))

    return score, reasons


def score_learning_objective(
    objective: dict[str, Any],
    content: str,
    selected_scope: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[float, list[str]]:
    content_compact = compact(content)
    content_tokens = tokenize(content)
    reasons: list[str] = []
    score = 0.0
    unit = strip_numbering(objective.get("unit"))
    detail = strip_numbering(selected_scope.get("detail"))

    if detail and compact(unit) == compact(detail):
        score += 5.0
        reasons.append("unit_matches_scope_detail")
    elif unit and detail and compact(unit) in compact(detail) or detail and unit and compact(detail) in compact(unit):
        score += 2.5
        reasons.append("unit_near_scope_detail")

    major_unit = strip_numbering(objective.get("major_unit"))
    if any(compact(x) and compact(x) == compact(major_unit) for x in profile.get("major_units", [])):
        score += 2.0
        reasons.append("major_unit_profile")

    keyword_hits = []
    for keyword in objective.get("keywords") or []:
        if compact(keyword) and compact(keyword) in content_compact:
            keyword_hits.append(strip_numbering(keyword))
    if keyword_hits:
        score += min(6.0, 1.2 * len(set(keyword_hits)))
        reasons.append("keyword:" + ",".join(sorted(set(keyword_hits))[:5]))

    objective_tokens = tokenize(objective.get("objective", ""))
    token_hits = sorted(objective_tokens & content_tokens)
    if token_hits:
        score += min(3.0, 0.5 * len(token_hits))
        reasons.append("objective_token:" + ",".join(token_hits[:5]))

    return score, reasons


def select_learning_objectives(
    objectives: list[dict[str, Any]],
    content: str,
    selected_scope: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    field = compact(profile.get("field"))
    for objective in objectives:
        if field and compact(objective.get("field_hint")) != field:
            continue
        if profile.get("major_units"):
            objective_major = compact(objective.get("major_unit"))
            if objective_major and not any(compact(x) == objective_major for x in profile.get("major_units", [])):
                if compact(objective.get("unit")) != compact(selected_scope.get("detail")):
                    continue
        score, reasons = score_learning_objective(objective, content, selected_scope, profile)
        if score <= 0:
            continue
        selected.append(
            {
                "objective_id": objective.get("objective_id"),
                "major_unit": objective.get("major_unit"),
                "unit": objective.get("unit"),
                "objective": objective.get("objective"),
                "level": objective.get("level"),
                "score": round(score, 3),
                "reasons": reasons,
            }
        )
    selected.sort(key=lambda item: item["score"], reverse=True)
    return selected[:5]


def mapping_confidence(scope_score: float, selected_scope: dict[str, Any] | None, profile: dict[str, Any] | None) -> tuple[str, str, bool]:
    if not profile:
        return "unmapped", "needs_review", True
    if not selected_scope:
        return "area_only", "needs_review", True
    if scope_score >= 10:
        return "high", "auto_mapped_high", False
    if scope_score >= 5:
        return "medium", "draft_mapped", False
    return "area_only", "needs_review", True


def build_mapping(
    row: dict[str, Any],
    scope_rows: list[dict[str, Any]],
    scope_ids: dict[tuple[str, str, str, str], str],
    objectives: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_marker, profile = source_profile(row.get("source_file", ""))
    content = nfc(row.get("content", ""))
    selected_scope: dict[str, Any] | None = None
    scope_score = 0.0
    scope_reasons: list[str] = []
    candidates_out: list[dict[str, Any]] = []

    if profile:
        candidates = candidate_scopes(scope_rows, profile)
        scored = []
        for candidate in candidates:
            score, reasons = score_scope(candidate, content, profile)
            scored.append((score, candidate, reasons))
        scored.sort(key=lambda item: item[0], reverse=True)
        for score, candidate, reasons in scored[:5]:
            key = scope_key(candidate)
            candidates_out.append(
                {
                    "scope_id": scope_ids.get(key, candidate.get("scope_id", "")),
                    "period": candidate.get("period", ""),
                    "subject": candidate.get("subject", ""),
                    "field": candidate.get("field", ""),
                    "area": candidate.get("area", ""),
                    "detail": candidate.get("detail", ""),
                    "score": round(score, 3),
                    "reasons": reasons,
                }
            )
        if scored and scored[0][0] > 0:
            scope_score, selected_scope, scope_reasons = scored[0]

    confidence, status, needs_review = mapping_confidence(scope_score, selected_scope, profile)
    if selected_scope:
        selected_key = scope_key(selected_scope)
        selected = {
            "scope_id": scope_ids.get(selected_key, selected_scope.get("scope_id", "")),
            "period": selected_scope.get("period", "") or inferred_period(selected_scope.get("subject", "")),
            "subject": selected_scope.get("subject", ""),
            "field": selected_scope.get("field", ""),
            "area": selected_scope.get("area", ""),
            "detail": selected_scope.get("detail", ""),
        }
    elif profile:
        selected = {
            "scope_id": "",
            "period": inferred_period(profile.get("subject", "")),
            "subject": profile.get("subject", ""),
            "field": profile.get("field", ""),
            "area": profile.get("area", ""),
            "detail": "",
        }
        if not candidates_out:
            needs_review = True
            status = "needs_review"
    else:
        selected = {"scope_id": "", "period": "", "subject": "", "field": "", "area": "", "detail": ""}

    objective_candidates = []
    if selected_scope and profile and confidence in {"high", "medium"}:
        objective_candidates = select_learning_objectives(objectives, content, selected_scope, profile)

    hold_reasons = []
    if profile and profile.get("generation_hold_reason"):
        hold_reasons.append(profile["generation_hold_reason"])
    if re.search(r"(법|규칙|고시|허가|신고|선량한도|kV|mA|Gy|Sv|Bq|공식|식|계산)", content, re.IGNORECASE):
        hold_reasons.append("법규·수치·단위·공식 후보 포함 가능성으로 검토 전 자동 생성 금지")

    mapping = {
        "rag_input_id": row.get("rag_input_id", ""),
        "source_chunk_id": row.get("source_chunk_id", ""),
        "source_file": row.get("source_file", ""),
        "source_path": row.get("source_path", ""),
        "page_or_slide": row.get("page_or_slide"),
        "content_sha256": row.get("content_sha256", ""),
        "source_profile": source_marker,
        "mapping_status": status,
        "mapping_confidence": confidence,
        "scope_mapping_needs_review": needs_review,
        "selected_scope": selected,
        "selected_scope_score": round(scope_score, 3),
        "selected_scope_reasons": scope_reasons,
        "scope_candidates": candidates_out,
        "learning_objective_candidates": objective_candidates,
        "approved_for_rag_evidence": bool(row.get("approved_for_rag_evidence")),
        "approved_for_generation": False,
        "generation_hold_reasons": sorted(set(hold_reasons)),
    }

    mapped_row = dict(row)
    mapped_row.update(
        {
            "scope_mapping_status": status,
            "scope_mapping_confidence": confidence,
            "scope_mapping_needs_review": needs_review,
            "mapped_subject": selected["subject"],
            "mapped_field": selected["field"],
            "mapped_area": selected["area"],
            "mapped_detail": selected["detail"],
            "mapped_scope_id": selected["scope_id"],
            "scope_candidates": candidates_out,
            "learning_objective_candidates": objective_candidates,
            "approved_for_generation": False,
            "generation_hold_reasons": sorted(set(hold_reasons)),
        }
    )
    return mapped_row, mapping


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# RAG 입력 과목·출제범위 매핑 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 chunk: {report['counts']['input_rows']}",
        f"- 매핑 완료: {report['counts']['mapped_rows']}",
        f"- 검토 필요: {report['counts']['needs_review_rows']}",
        f"- 자동 생성 승인: {report['counts']['approved_for_generation_rows']} (항상 0 유지)",
        "",
        "## 매핑 상태",
    ]
    for key, value in report["mapping_status_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 신뢰도"])
    for key, value in report["mapping_confidence_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 문서별 매핑 요약"])
    for source_file, item in report["source_file_summary"].items():
        lines.append(
            f"- {source_file}: 전체 {item['total']}, high {item.get('high', 0)}, "
            f"medium {item.get('medium', 0)}, area_only {item.get('area_only', 0)}, "
            f"needs_review {item.get('needs_review', 0)}"
        )
    lines.extend(
        [
            "",
            "## 산출물",
            f"- 매핑된 RAG 입력: `{report['outputs']['mapped_input']}`",
            f"- 매핑 인덱스: `{report['outputs']['mapping_index']}`",
            f"- 검토 대기: `{report['outputs']['review_queue']}`",
            "",
            "## 주의",
            "- 이 산출물은 RAG 근거 검색용 1차 매핑입니다.",
            "- 문제 생성 승인값은 모두 false로 유지했습니다.",
            "- 법규·수치·단위·공식 후보는 검토 전 자동 생성 금지 사유를 유지했습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Map RAG index input rows to verified exam scopes.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    input_rows = read_jsonl(args.input)
    scope_rows = load_verified_scope_rows()
    scope_ids = load_scope_ids()
    objectives = load_learning_objectives()

    mapped_rows: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []

    for row in input_rows:
        mapped_row, mapping = build_mapping(row, scope_rows, scope_ids, objectives)
        mapped_rows.append(mapped_row)
        mappings.append(mapping)
        if mapping["scope_mapping_needs_review"]:
            review_rows.append(mapping)

    mapped_input = args.output_dir / "rag_index_input_mapped.jsonl"
    mapping_index = args.output_dir / "rag_scope_mapping.jsonl"
    review_queue = args.output_dir / "rag_scope_mapping_review_queue.jsonl"
    report_json = args.report_dir / "rag_scope_mapping_report.json"
    report_md = args.report_dir / "rag_scope_mapping_report.md"

    write_jsonl(mapped_input, mapped_rows)
    write_jsonl(mapping_index, mappings)
    write_jsonl(review_queue, review_rows)

    status_counts = Counter(m["mapping_status"] for m in mappings)
    confidence_counts = Counter(m["mapping_confidence"] for m in mappings)
    source_summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    selected_scope_counts = Counter()
    for mapping in mappings:
        source = mapping["source_file"]
        source_summary[source]["total"] += 1
        source_summary[source][mapping["mapping_confidence"]] += 1
        if mapping["scope_mapping_needs_review"]:
            source_summary[source]["needs_review"] += 1
        selected = mapping["selected_scope"]
        selected_scope_counts[
            " | ".join(
                [
                    selected.get("subject", ""),
                    selected.get("field", ""),
                    selected.get("area", ""),
                    selected.get("detail", ""),
                ]
            )
        ] += 1

    report = {
        "version": "2026-06-24",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "rag_index_input": str(args.input),
            "verified_scope": str(VERIFIED_SCOPE),
            "exam_scope": str(EXAM_SCOPE),
            "learning_objectives": str(LEARNING_OBJECTIVES),
        },
        "outputs": {
            "mapped_input": str(mapped_input),
            "mapping_index": str(mapping_index),
            "review_queue": str(review_queue),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "counts": {
            "input_rows": len(input_rows),
            "mapped_rows": len(mapped_rows),
            "needs_review_rows": len(review_rows),
            "approved_for_generation_rows": sum(1 for row in mapped_rows if row.get("approved_for_generation")),
        },
        "mapping_status_counts": dict(status_counts),
        "mapping_confidence_counts": dict(confidence_counts),
        "source_file_summary": {source: dict(counts) for source, counts in sorted(source_summary.items())},
        "top_selected_scope_counts": dict(selected_scope_counts.most_common(50)),
        "policy": {
            "rag_use": "answer_evidence_only",
            "question_generation_approval": "not_granted_by_this_mapping",
            "needs_review_rule": "medium and area-only mappings remain reviewable",
        },
    }
    write_json(report_json, report)
    report_md.write_text(markdown_report(report), encoding="utf-8")

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print("status", dict(status_counts))
    print("confidence", dict(confidence_counts))


if __name__ == "__main__":
    main()
