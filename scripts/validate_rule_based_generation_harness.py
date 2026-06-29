#!/usr/bin/env python3
"""Rule-based validation harness for pre-generation evidence and draft items.

This is intentionally deterministic. It does not call an LLM and does not
approve questions for final storage.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"
DEFAULT_GENERATION_SAFE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references_generation_safe"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources" / "reports"
RULES_DIR = PROJECT_ROOT / "resources" / "rules"
QUESTION_SCHEMA = RULES_DIR / "question_schema.json"
QUESTION_TYPE_RULES = RULES_DIR / "question_type_rules.json"
VALIDATION_SPEC = RULES_DIR / "validation_harness_spec.json"
EXAM_SCOPE = RULES_DIR / "exam_scope.json"


DIFFICULTIES = {"하", "중", "상"}
ANSWER_LABELS = {"1", "2", "3", "4", "5", 1, 2, 3, 4, 5, "A", "B", "C", "D", "E", "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"}
TEXT_FIELDS_FOR_COPYRIGHT = ["stem", "explanation"]
LLM_FIRST_CHECK_KEYS = [
    "scope_alignment",
    "learning_objective_alignment",
    "evidence_grounding",
    "answer_uniqueness",
    "option_quality",
    "explanation_quality",
    "copyright_safety",
    "text_only_policy",
]
HOLD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "hold_legal_or_statutory",
        re.compile(
            r"(제\s*\d+\s*조|제\d+조|별표|시행규칙|시행령|"
            r"의료법|모자보건법|지역보건법|원자력안전법|"
            r"의료기사\s*등에\s*관한\s*법률|"
            r"진단용\s*방사선\s*발생장치.*안전관리|"
            r"질병관리청|식품의약품안전처|보건복지부)",
            re.IGNORECASE,
        ),
    ),
    (
        "hold_visual_caption",
        re.compile(r"(\[?\s*(그림|표)\s*[0-9]+(\s*[-–—]\s*[0-9]+)?|\b(fig|table)\.?\s*[0-9]+)", re.IGNORECASE),
    ),
    (
        "hold_formula_or_equation",
        re.compile(
            r"(수식|공식|방정식|"
            r"[A-Za-zηλμρσθ]\s*=|"
            r"\d+(?:\.\d+)?\s*[×x]\s*10\s*[-−^]?\s*\d+)",
            re.IGNORECASE,
        ),
    ),
    (
        "hold_numeric_unit",
        re.compile(
            r"\b\d+(?:\.\d+)?\s*"
            r"(kV|mA|mAs|Gy|Sv|Bq|keV|MeV|MHz|mmHg|mGy|mSv|MBq|GBq)\b",
            re.IGNORECASE,
        ),
    ),
]
VISUAL_QUESTION_PATTERN = re.compile(
    r"(그림|도표|이미지|사진|스캔|"
    r"표\s*\d|표를|표에서|표의|표와|"
    r"아래\s*(?:의)?\s*(?:자료|그림|도표|표)|"
    r"다음\s*(?:그림|도표|표|영상)|"
    r"(?:제시된|보이는)\s*영상|"
    r"영상\s*(?:자료|제시|판독형|사진|이미지)|"
    r"영상(?:을|를)?\s*(?:보고|판독하|해석하)|"
    r"영상(?:에서|에)\s*(?:보이는|나타난|관찰되는))",
    re.IGNORECASE,
)
OPTION_REFERENCE_PATTERN = re.compile(r"(?<!\d)([1-5])\s*번|[①②③④⑤]")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def normalize_scope_value(value: Any) -> str:
    text = nfc(value).strip()
    text = re.sub(r"^\s*\d+\.\s*", "", text)
    return text.strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize_scope_value(value)).lower()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def message(check_id: str, severity: str, status: str, text: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "severity": severity,
        "status": status,
        "message": text,
        "details": details or {},
    }


def load_scope_keys() -> set[tuple[str, str, str, str]]:
    data = read_json(EXAM_SCOPE)
    rows = data.get("verified_detail_rows") or data.get("detail_rows") or []
    keys = set()
    for row in rows:
        keys.add(
            (
                compact(row.get("subject")),
                compact(row.get("field")),
                compact(row.get("area")),
                compact(row.get("detail")),
            )
        )
    return keys


def load_question_types() -> set[str]:
    data = read_json(QUESTION_TYPE_RULES)
    types = data.get("types", [])
    names = set()
    for item in types:
        if isinstance(item, dict):
            for key in ["id", "name", "label"]:
                if item.get(key):
                    names.add(str(item[key]))
        elif item:
            names.add(str(item))
    return names


def db_chunk(conn: sqlite3.Connection, rag_input_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT *
        FROM chunks
        WHERE rag_input_id = ?
        """,
        (rag_input_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    return item


def db_search(
    conn: sqlite3.Connection,
    period: str,
    subject: str,
    field: str,
    area: str,
    detail: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    where = [
        "mapped_period = ?",
        "mapped_subject = ?",
        "mapped_field = ?",
        "mapped_area = ?",
    ]
    params: list[Any] = [period, subject, field, area]
    if detail:
        where.append("mapped_detail = ?")
        params.append(detail)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT *
        FROM chunks
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE scope_mapping_confidence
                WHEN 'high' THEN 0
                WHEN 'medium' THEN 1
                ELSE 2
            END,
            page_or_slide
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def hold_hits(content: str) -> list[str]:
    return [reason for reason, pattern in HOLD_PATTERNS if pattern.search(content or "")]


def validate_evidence_package(
    package: dict[str, Any],
    conn: sqlite3.Connection,
    scope_keys: set[tuple[str, str, str, str]],
    generation_safe_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    requested = package.get("requested_scope") or {}
    required_scope_fields = ["period", "subject", "field", "area"]
    missing = [field for field in required_scope_fields if not requested.get(field)]
    if missing:
        findings.append(message("PG-001", "error", "fail", "요청 출제범위 필수 필드가 부족합니다.", {"missing": missing}))

    if compact(requested.get("subject")) == compact("실기시험") or requested.get("period") == "3교시":
        findings.append(message("PG-002", "error", "fail", "현재 단계에서는 3교시 실기시험 문항 생성을 허용하지 않습니다."))

    if requested.get("detail"):
        key = (
            compact(requested.get("subject")),
            compact(requested.get("field")),
            compact(requested.get("area")),
            compact(requested.get("detail")),
        )
        if key not in scope_keys:
            findings.append(message("PG-003", "error", "fail", "요청 세부영역이 검수된 출제범위 기준에 없습니다.", {"requested_scope": requested}))
    else:
        findings.append(message("PG-004", "warning", "warn", "세부영역 detail이 비어 있어 영역 단위 검증만 수행합니다."))

    refs = package.get("evidence_refs") or []
    if not refs:
        auto_rows = db_search(
            conn,
            requested.get("period", ""),
            requested.get("subject", ""),
            requested.get("field", ""),
            requested.get("area", ""),
            requested.get("detail", ""),
            int(package.get("min_evidence_count") or 2),
        )
        refs = [{"rag_input_id": row["rag_input_id"]} for row in auto_rows]
        package["evidence_refs"] = refs

    min_evidence_count = int(package.get("min_evidence_count") or 2)
    if len(refs) < min_evidence_count:
        findings.append(
            message(
                "PG-005",
                "error",
                "fail",
                "근거 chunk 수가 최소 기준보다 적습니다.",
                {"evidence_count": len(refs), "minimum": min_evidence_count},
            )
        )

    seen_sources = set()
    mismatch_refs = []
    policy_refs = []
    hold_refs = []
    low_confidence_refs = []
    unsafe_generation_refs = []
    found_count = 0
    for ref in refs:
        rag_input_id = ref.get("rag_input_id") if isinstance(ref, dict) else str(ref)
        if generation_safe_ids is not None and rag_input_id not in generation_safe_ids:
            unsafe_generation_refs.append(rag_input_id)
        row = db_chunk(conn, rag_input_id)
        if not row:
            findings.append(message("PG-006", "error", "fail", "근거 chunk를 인덱스에서 찾을 수 없습니다.", {"rag_input_id": rag_input_id}))
            continue
        found_count += 1
        seen_sources.add((row.get("source_file"), row.get("page_or_slide")))

        if row.get("approved_for_rag_evidence") != 1 or row.get("approved_for_generation") != 0:
            policy_refs.append(rag_input_id)
        if row.get("extraction_quality") != "high" or row.get("candidate_rag_status") != "ready_for_rag_evidence":
            policy_refs.append(rag_input_id)
        hits = hold_hits(row.get("content", ""))
        if hits:
            hold_refs.append({"rag_input_id": rag_input_id, "reasons": hits})

        expected_pairs = {
            "period": "mapped_period",
            "subject": "mapped_subject",
            "field": "mapped_field",
            "area": "mapped_area",
        }
        mismatched = {
            expected: {"expected": requested.get(expected), "actual": row.get(actual)}
            for expected, actual in expected_pairs.items()
            if requested.get(expected) and row.get(actual) != requested.get(expected)
        }
        if requested.get("detail") and row.get("mapped_detail") != requested.get("detail"):
            mismatched["detail"] = {"expected": requested.get("detail"), "actual": row.get("mapped_detail")}
        if mismatched:
            mismatch_refs.append({"rag_input_id": rag_input_id, "mismatches": mismatched})

        if row.get("scope_mapping_confidence") == "area_only" or row.get("scope_mapping_needs_review"):
            low_confidence_refs.append(rag_input_id)

    if found_count >= min_evidence_count:
        findings.append(message("PG-007", "info", "pass", "최소 근거 chunk 수 기준을 충족했습니다.", {"evidence_count": found_count}))
    if len(seen_sources) < min_evidence_count:
        findings.append(
            message(
                "PG-008",
                "warning",
                "warn",
                "근거가 서로 다른 파일/페이지에서 충분히 분산되지 않았습니다.",
                {"distinct_source_pages": len(seen_sources), "minimum": min_evidence_count},
            )
        )
    if mismatch_refs:
        findings.append(message("PG-009", "error", "fail", "근거 chunk의 출제범위가 요청 범위와 다릅니다.", {"mismatches": mismatch_refs}))
    if policy_refs:
        findings.append(message("PG-010", "error", "fail", "근거 chunk 정책 플래그가 생성 전 기준과 맞지 않습니다.", {"rag_input_ids": sorted(set(policy_refs))}))
    if hold_refs:
        findings.append(message("PG-011", "error", "fail", "근거 chunk에 법규·표·그림·수식·수치 보류 패턴이 남아 있습니다.", {"hits": hold_refs}))
    if low_confidence_refs:
        findings.append(
            message(
                "PG-012",
                "warning",
                "warn",
                "일부 근거 chunk는 세부영역 매핑 신뢰도가 낮아 검토가 필요합니다.",
                {"rag_input_ids": low_confidence_refs[:20], "count": len(low_confidence_refs)},
            )
        )
    if unsafe_generation_refs:
        findings.append(
            message(
                "PG-013",
                "error",
                "fail",
                "자동 문제 생성 근거가 generation-safe 벡터 인덱스에 포함되어 있지 않습니다.",
                {"rag_input_ids": sorted(set(unsafe_generation_refs))[:50], "count": len(set(unsafe_generation_refs))},
            )
        )
    return findings


def option_index(answer: Any) -> int | None:
    if answer in {1, 2, 3, 4, 5}:
        return int(answer) - 1
    text = str(answer).strip()
    if text in {"1", "2", "3", "4", "5"}:
        return int(text) - 1
    if text in {"A", "B", "C", "D", "E"}:
        return ord(text) - ord("A")
    return None


def sentence_similarity(a: str, b: str) -> float:
    a_norm = " ".join(nfc(a).split())
    b_norm = " ".join(nfc(b).split())
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def max_source_similarity(generated_text: str, source_texts: list[str]) -> float:
    generated_sentences = [s.strip() for s in re.split(r"(?<=[.!?。])|\n", generated_text) if s.strip()]
    if not generated_sentences:
        generated_sentences = [generated_text]
    max_ratio = 0.0
    for generated in generated_sentences:
        if len(generated) < 12:
            continue
        for source in source_texts:
            source_windows = [source[i : i + max(80, len(generated) * 2)] for i in range(0, max(1, len(source) - 40), 80)]
            for window in source_windows[:80]:
                max_ratio = max(max_ratio, sentence_similarity(generated, window))
    return max_ratio


def validate_generated_item(
    item: dict[str, Any],
    conn: sqlite3.Connection,
    scope_keys: set[tuple[str, str, str, str]],
    question_types: set[str],
    generation_safe_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    schema = read_json(QUESTION_SCHEMA)
    required_fields = schema.get("required_fields", [])
    missing = [field for field in required_fields if field not in item or item.get(field) in (None, "", [])]
    if missing:
        findings.append(message("VH-005", "error", "fail", "필수 문항 필드가 부족합니다.", {"missing": missing}))

    options = item.get("options") or []
    if not isinstance(options, list) or len(options) != 5:
        findings.append(message("VH-001", "error", "fail", "보기는 정확히 5개여야 합니다.", {"option_count": len(options) if isinstance(options, list) else None}))

    if item.get("answer") not in ANSWER_LABELS and option_index(item.get("answer")) is None:
        findings.append(message("VH-002", "error", "fail", "정답은 5개 보기 중 정확히 하나를 가리켜야 합니다.", {"answer": item.get("answer")}))

    if item.get("difficulty") and item.get("difficulty") not in DIFFICULTIES:
        findings.append(message("VH-006", "error", "fail", "난이도는 하/중/상 중 하나여야 합니다.", {"difficulty": item.get("difficulty")}))

    if item.get("question_type") and question_types and item.get("question_type") not in question_types:
        findings.append(message("VH-007", "warning", "warn", "문항 유형이 등록 유형 목록과 직접 일치하지 않습니다.", {"question_type": item.get("question_type")}))

    if item.get("question_type") == "영상해석형":
        findings.append(message("VH-021", "error", "fail", "1·2교시 텍스트 시험지에는 시각자료 기반 문항 유형을 사용하지 않습니다."))

    if item.get("period") == "3교시" or compact(item.get("subject")) == compact("실기시험"):
        findings.append(message("VH-004", "error", "fail", "현재 단계에서는 3교시 실기시험 문항을 저장하지 않습니다."))

    if item.get("detail"):
        key = (compact(item.get("subject")), compact(item.get("field")), compact(item.get("area")), compact(item.get("detail")))
        if key not in scope_keys:
            findings.append(message("VH-003", "error", "fail", "문항 출제범위가 기준표에 없습니다.", {"scope": {k: item.get(k) for k in ["subject", "field", "area", "detail"]}}))

    if not item.get("explanation"):
        findings.append(message("VH-017", "error", "fail", "해설이 없습니다."))
    elif OPTION_REFERENCE_PATTERN.search(str(item.get("explanation") or "")):
        findings.append(
            message(
                "VH-023",
                "error",
                "fail",
                "정답 보기 위치 랜덤 섞기와 충돌하지 않도록 해설에서 보기 번호를 직접 참조하지 않아야 합니다.",
            )
        )
    else:
        explanation = str(item.get("explanation") or "")
        exclusion_markers = re.findall(
            r"(맞지 않|아니|혼동|배제|설명하지 못|구별|부적절|적절하지 않|어긋|정답 조건과 달리|와 달리|과 달리|와 다르|과 다르)",
            explanation,
        )
        if len(explanation) < 120:
            findings.append(
                message(
                    "VH-024",
                    "warning",
                    "warn",
                    "해설이 짧아 정답 근거와 오답별 배제 이유가 충분하지 않을 수 있습니다.",
                    {"explanation_length": len(explanation)},
                )
            )
        if len(exclusion_markers) < 3:
            findings.append(
                message(
                    "VH-025",
                    "warning",
                    "warn",
                    "해설에 오답 배제 이유를 드러내는 표현이 부족할 수 있습니다.",
                    {"exclusion_marker_count": len(exclusion_markers)},
                )
            )

    visual_question_text = "\n".join(
        [
            str(item.get("stem") or ""),
            str(item.get("distractor_strategy") or ""),
            str(item.get("explanation") or ""),
        ]
    )
    if VISUAL_QUESTION_PATTERN.search(visual_question_text):
        findings.append(
            message(
                "VH-022",
                "error",
                "fail",
                "1·2교시 텍스트 문항에는 그림·표·도표·영상 제시를 전제로 한 표현을 사용할 수 없습니다.",
            )
        )

    llm_first_check = item.get("llm_first_check") or {}
    if not isinstance(llm_first_check, dict):
        findings.append(message("VH-018", "error", "fail", "LLM 1차 검증 필드 형식이 올바르지 않습니다."))
    else:
        if llm_first_check.get("overall_verdict") != "pass":
            findings.append(
                message(
                    "VH-018",
                    "error",
                    "fail",
                    "LLM 1차 검증 overall_verdict가 pass가 아닙니다.",
                    {"overall_verdict": llm_first_check.get("overall_verdict")},
                )
            )
        checks = llm_first_check.get("checks") or {}
        missing_checks = [key for key in LLM_FIRST_CHECK_KEYS if key not in checks]
        if missing_checks:
            findings.append(
                message(
                    "VH-019",
                    "error",
                    "fail",
                    "LLM 1차 검증 필수 check가 부족합니다.",
                    {"missing": missing_checks},
                )
            )
        non_pass_checks = {
            key: value.get("verdict") if isinstance(value, dict) else None
            for key, value in checks.items()
            if key in LLM_FIRST_CHECK_KEYS and (not isinstance(value, dict) or value.get("verdict") != "pass")
        }
        if non_pass_checks:
            findings.append(
                message(
                    "VH-020",
                    "error",
                    "fail",
                    "LLM 1차 검증 check 중 pass가 아닌 항목이 있습니다.",
                    {"checks": non_pass_checks},
                )
            )

    evidence_package = {
        "requested_scope": {
            "period": item.get("period"),
            "subject": item.get("subject"),
            "field": item.get("field"),
            "area": item.get("area"),
            "detail": item.get("detail"),
        },
        "evidence_refs": item.get("source_chunks") or item.get("evidence_refs") or [],
        "min_evidence_count": 1,
    }
    findings.extend(validate_evidence_package(evidence_package, conn, scope_keys, generation_safe_ids))

    source_texts = []
    for ref in evidence_package.get("evidence_refs") or []:
        rag_input_id = ref.get("rag_input_id") if isinstance(ref, dict) else str(ref)
        row = db_chunk(conn, rag_input_id)
        if row:
            source_texts.append(row.get("content", ""))
    generated_text = "\n".join(str(item.get(field, "")) for field in TEXT_FIELDS_FOR_COPYRIGHT)
    generated_text += "\n" + "\n".join(str(opt) for opt in options)
    max_ratio = max_source_similarity(generated_text, source_texts)
    if max_ratio >= 0.8:
        findings.append(message("VH-013", "error", "fail", "생성 문항 문장이 근거 원문과 과도하게 유사합니다.", {"max_similarity": round(max_ratio, 4)}))
    elif max_ratio >= 0.65:
        findings.append(message("VH-016", "warning", "warn", "일부 생성 문장이 근거 원문과 유사합니다.", {"max_similarity": round(max_ratio, 4)}))

    duplicate_options = [opt for opt, count in Counter(str(opt).strip() for opt in options).items() if opt and count > 1]
    if duplicate_options:
        findings.append(message("VH-011", "error", "fail", "중복 보기가 있습니다.", {"duplicates": duplicate_options}))

    if options:
        lengths = [len(str(opt)) for opt in options]
        answer_i = option_index(item.get("answer"))
        if answer_i is not None and 0 <= answer_i < len(lengths):
            mean_other = sum(length for idx, length in enumerate(lengths) if idx != answer_i) / max(1, len(lengths) - 1)
            if lengths[answer_i] > mean_other * 1.8 and lengths[answer_i] > 28:
                findings.append(message("VH-012", "warning", "warn", "정답 보기만 길이가 두드러질 수 있습니다.", {"answer_length": lengths[answer_i], "other_mean": round(mean_other, 2)}))

    return findings


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(finding["severity"] for finding in findings if finding["status"] != "pass")
    return {
        "overall_pass": counts.get("error", 0) == 0,
        "error_count": counts.get("error", 0),
        "warning_count": counts.get("warning", 0),
        "info_count": sum(1 for finding in findings if finding["severity"] == "info"),
        "finding_count": len(findings),
    }


def validate_payload(payload: dict[str, Any], conn: sqlite3.Connection, generation_safe_ids: set[str] | None = None) -> dict[str, Any]:
    scope_keys = load_scope_keys()
    question_types = load_question_types()
    mode = payload.get("mode") or "pre_generation"
    if mode == "generated_item":
        findings = validate_generated_item(payload.get("item") or payload, conn, scope_keys, question_types, generation_safe_ids)
    else:
        package = payload.get("package") or payload
        findings = validate_evidence_package(package, conn, scope_keys, generation_safe_ids)
    return {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "mode": mode,
        "summary": summarize(findings),
        "findings": findings,
        "policy": {
            "llm_used": False,
            "generation_approval_granted": False,
            "rag_use": "evidence_validation_only",
            "generation_safe_index_enforced": generation_safe_ids is not None,
            "generation_safe_index_id_count": len(generation_safe_ids or []),
        },
    }


def self_test_passing_package(conn: sqlite3.Connection, generation_safe_ids: set[str] | None = None) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    if generation_safe_ids:
        placeholders = ",".join("?" for _ in generation_safe_ids)
        rows = conn.execute(
            f"""
            SELECT mapped_period, mapped_subject, mapped_field, mapped_area, mapped_detail, rag_input_id
            FROM chunks
            WHERE rag_input_id IN ({placeholders})
              AND mapped_period = '2교시'
              AND mapped_subject = '방사선응용'
              AND mapped_detail != ''
            ORDER BY mapped_area, mapped_detail, page_or_slide
            """,
            sorted(generation_safe_ids),
        ).fetchall()
        grouped: dict[tuple[str, str, str, str, str], list[str]] = {}
        for row in rows:
            key = (
                row["mapped_period"],
                row["mapped_subject"],
                row["mapped_field"],
                row["mapped_area"],
                row["mapped_detail"],
            )
            grouped.setdefault(key, []).append(row["rag_input_id"])
        for key, ids in grouped.items():
            if len(ids) >= 2:
                period, subject, field, area, detail = key
                return {
                    "mode": "pre_generation",
                    "package": {
                        "requested_scope": {
                            "period": period,
                            "subject": subject,
                            "field": field,
                            "area": area,
                            "detail": detail,
                        },
                        "evidence_refs": [{"rag_input_id": rag_input_id} for rag_input_id in ids[:2]],
                        "min_evidence_count": 2,
                    },
                }
    return {
        "mode": "pre_generation",
        "package": {
            "requested_scope": {
                "period": "2교시",
                "subject": "방사선응용",
                "field": "영상진단",
                "area": "초음파기술",
                "detail": "",
            },
            "min_evidence_count": 2,
        },
    }


def run_self_test(conn: sqlite3.Connection, generation_safe_ids: set[str] | None = None) -> dict[str, Any]:
    passing_package = self_test_passing_package(conn, generation_safe_ids)
    failing_package = {
        "mode": "pre_generation",
        "package": {
            "requested_scope": {
                "period": "3교시",
                "subject": "실기시험",
                "field": "초음파영상검사",
                "area": "초음파영상검사",
                "detail": "초음파진단기",
            },
            "evidence_refs": [{"rag_input_id": "missing_chunk"}],
            "min_evidence_count": 2,
        },
    }
    return {
        "passing_package": validate_payload(passing_package, conn, generation_safe_ids),
        "failing_package": validate_payload(failing_package, conn, generation_safe_ids),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 규칙 기반 문제 생성 전 검증 Harness 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- LLM 사용: {report['policy']['llm_used']}",
        f"- 문제 생성 승인 부여: {report['policy']['generation_approval_granted']}",
        "",
        "## 요약",
        f"- overall_pass: {report['summary']['overall_pass']}",
        f"- errors: {report['summary']['error_count']}",
        f"- warnings: {report['summary']['warning_count']}",
        f"- findings: {report['summary']['finding_count']}",
        "",
        "## 주요 finding",
    ]
    for finding in report["findings"][:20]:
        lines.append(
            f"- {finding['check_id']} [{finding['severity']}/{finding['status']}]: {finding['message']}"
        )
    if len(report["findings"]) > 20:
        lines.append(f"- ... {len(report['findings']) - 20}개 추가 finding은 JSON 보고서 참조")
    lines.extend(
        [
            "",
            "## 적용 범위",
            "- 생성 전 RAG 근거 묶음 검증",
            "- 생성 문항 JSON의 형식·메타데이터·근거·보류 정책 1차 검증",
            "- 저작권 위험은 문자열 유사도 기반 1차 탐지이며 최종 판단은 LLM/전문 검수 필요",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON payload to validate")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument("--disable-generation-safe-index-check", action="store_true")
    parser.add_argument("--output", type=Path, help="JSON output path")
    parser.add_argument("--self-test", action="store_true", help="Run built-in passing/failing pre-generation examples")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        generation_safe_ids = None if args.disable_generation_safe_index_check else load_vector_rag_ids(args.generation_safe_vector_db)
        if args.self_test:
            result = {
                "version": "2026-06-24",
                "created_at": now_iso(),
                "db": str(args.db),
                "generation_safe_vector_db": str(args.generation_safe_vector_db),
                "generation_safe_index_enforced": generation_safe_ids is not None,
                "self_test": run_self_test(conn, generation_safe_ids),
            }
            output = args.output or DEFAULT_REPORT_DIR / "rule_based_generation_harness_self_test.json"
            write_json(output, result)
            md_output = output.with_suffix(".md")
            md_output.write_text(
                "# 규칙 기반 검증 Harness Self-Test\n\n"
                f"- 생성 시각: {result['created_at']}\n"
                f"- passing_package overall_pass: {result['self_test']['passing_package']['summary']['overall_pass']}\n"
                f"- failing_package overall_pass: {result['self_test']['failing_package']['summary']['overall_pass']}\n",
                encoding="utf-8",
            )
            print(json.dumps({"output": str(output), "markdown": str(md_output)}, ensure_ascii=False, indent=2))
            return

        if not args.input:
            raise SystemExit("--input or --self-test is required")
        payload = read_json(args.input)
        report = validate_payload(payload, conn, generation_safe_ids)
    finally:
        conn.close()

    output = args.output or DEFAULT_REPORT_DIR / "rule_based_generation_harness_report.json"
    write_json(output, report)
    output.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
