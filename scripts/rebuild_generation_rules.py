from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import fitz


ROOT = Path(__file__).resolve().parents[1]
MATERIALS_DIR = ROOT / "materials"
EXTRACTED_DIR = ROOT / "resources" / "extracted"
RULES_DIR = ROOT / "resources" / "rules"
DOCS_DIR = ROOT / "docs" / "project_rules"
VERIFIED_SCOPE_PATH = EXTRACTED_DIR / "sebuyeongyeok_verified_scope.json"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def strip_number(value: str) -> str:
    return re.sub(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|#?\d+\.?|\d+\)|No\.\s*\d+)\s*", "", value).strip()


def slug_id(*parts: str, prefix: str = "") -> str:
    raw = "|".join(normalize_text(part) for part in parts if part)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{digest}" if prefix else digest


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.rstrip() + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pages() -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with (EXTRACTED_DIR / "pages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            pages.append(json.loads(line))
    return pages


def load_verified_scope() -> dict[str, Any] | None:
    if not VERIFIED_SCOPE_PATH.exists():
        return None
    return load_json(VERIFIED_SCOPE_PATH)


def representative_scope_source(tables: list[dict[str, Any]]) -> str:
    candidates = sorted(
        {
            table["source_file"]
            for table in tables
            if "출제범위" in normalize_text(table["source_file"])
            and any(row[:4] == ["시험과목", "분야", "영역", "세부영역"] for row in table["rows"])
        }
    )
    if not candidates:
        raise RuntimeError("No official exam scope table found")
    non_duplicate = [source for source in candidates if "부터 적용" not in normalize_text(source)]
    return non_duplicate[0] if non_duplicate else candidates[0]


def infer_period(subject: str) -> str:
    if "의료관계법규" in subject or "의료법규" in subject:
        return "1교시"
    if "방사선이론" in subject:
        return "1교시"
    if "방사선응용" in subject:
        return "2교시"
    if "실기시험" in subject:
        return "3교시"
    return ""


def build_exam_scope(tables: list[dict[str, Any]]) -> dict[str, Any]:
    source_file = representative_scope_source(tables)
    subjects: dict[str, dict[str, Any]] = {}
    hierarchy_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    current = {"subject": "", "field": "", "area": ""}

    for table in tables:
        if table["source_file"] != source_file:
            continue
        if not table["rows"] or table["rows"][0][:4] != ["시험과목", "분야", "영역", "세부영역"]:
            continue
        for row in table["rows"][1:]:
            cells = (row + ["", "", "", ""])[:4]
            subject, field, area, detail = [compact_spaces(cell) for cell in cells]
            if subject:
                current = {"subject": subject, "field": "", "area": ""}
            if field:
                current["field"] = field
                current["area"] = ""
            if area:
                current["area"] = area
            if not any([subject, field, area, detail]):
                continue
            key = (current["subject"], current["field"], current["area"], detail)
            if key in seen:
                continue
            seen.add(key)
            row_type = "detail" if detail else "hierarchy"
            record = {
                "scope_id": slug_id(*key, prefix="scope_"),
                "row_type": row_type,
                "period": infer_period(current["subject"]),
                "subject": current["subject"],
                "field": current["field"],
                "area": current["area"],
                "detail": detail,
                "source_file": source_file,
                "source_page": table["page"],
            }
            hierarchy_rows.append(record)
            if detail:
                detail_rows.append(record)

            if current["subject"]:
                subject_node = subjects.setdefault(
                    current["subject"],
                    {
                        "name": current["subject"],
                        "period": infer_period(current["subject"]),
                        "fields": {},
                    },
                )
                if current["field"]:
                    field_node = subject_node["fields"].setdefault(
                        current["field"],
                        {"name": current["field"], "areas": {}},
                    )
                    if current["area"]:
                        area_node = field_node["areas"].setdefault(
                            current["area"],
                            {"name": current["area"], "details": []},
                        )
                        if detail:
                            area_node["details"].append(
                                {
                                    "scope_id": record["scope_id"],
                                    "name": detail,
                                    "source_page": table["page"],
                                }
                            )

    def compact_subject(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": node["name"],
            "period": node["period"],
            "fields": [
                {
                    "name": field["name"],
                    "areas": [
                        {"name": area["name"], "details": area["details"]}
                        for area in field["areas"].values()
                    ],
                }
                for field in node["fields"].values()
            ],
        }

    return {
        "version": 2,
        "purpose": "국시원 공식 출제범위를 문제 생성 가능 범위의 최상위 기준으로 고정한다.",
        "source_file": source_file,
        "duplicate_scope_files": [
            source
            for source in sorted({table["source_file"] for table in tables})
            if "출제범위" in normalize_text(source) and source != source_file
        ],
        "scope_version": "2022년도 제50회 방사선사 국가시험부터 별도 공지 시까지",
        "exam_format": {
            "type": "객관식 5지 선다형",
            "total_questions": 250,
            "period_distribution": {
                "1교시": {"subjects": ["방사선이론", "의료관계법규"], "questions": 110, "minutes": 90},
                "2교시": {"subjects": ["방사선응용"], "questions": 90, "minutes": 75},
                "3교시": {"subjects": ["실기시험"], "questions": 50, "minutes": 50},
            },
        },
        "subjects": [compact_subject(subject) for subject in subjects.values()],
        "flat_rows": hierarchy_rows,
        "detail_rows": detail_rows,
        "counts": {
            "hierarchy_rows": len(hierarchy_rows),
            "detail_rows": len(detail_rows),
            "subjects": len(subjects),
        },
    }


def verified_scope_rows(verified_scope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(verified_scope.get("rows", []), start=1):
        subject = compact_spaces(row.get("subject", ""))
        field = compact_spaces(row.get("field", ""))
        area = compact_spaces(row.get("area", ""))
        detail = compact_spaces(row.get("detail", ""))
        rows.append(
            {
                "scope_id": slug_id(subject, field, area, detail, str(index), prefix="verified_scope_"),
                "row_type": "detail",
                "period": infer_period(subject),
                "subject": subject,
                "field": field,
                "area": area,
                "detail": detail,
                "question_count": row.get("question_count"),
                "count_mode": row.get("count_mode", "fixed"),
                "source_file": row.get("source_file", verified_scope.get("source_file", "")),
                "source_page": row.get("page"),
                "verification": row.get("verification", verified_scope.get("verification_status", "")),
                **({"note": row["note"]} if row.get("note") else {}),
            }
        )
    return rows


def apply_verified_scope_to_exam_scope(
    exam_scope: dict[str, Any],
    verified_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    if not verified_scope:
        return exam_scope

    verified_rows = verified_scope_rows(verified_scope)
    fixed_rows = [row for row in verified_rows if isinstance(row.get("question_count"), int)]
    candidate_rows = [row for row in verified_rows if row.get("count_mode") == "candidate"]
    flexible_question_count = sum(group.get("flexible_question_count", 0) for group in verified_scope.get("flexible_groups", []))

    exam_scope["verified_distribution"] = {
        "source_file": verified_scope.get("source_file"),
        "source_title": verified_scope.get("source_title"),
        "verification_status": verified_scope.get("verification_status"),
        "verified_date": verified_scope.get("verified_date"),
        "expected_totals": verified_scope.get("expected_totals", {}),
        "fixed_question_sum": sum(row["question_count"] for row in fixed_rows),
        "flexible_question_sum": flexible_question_count,
        "total_question_sum": sum(row["question_count"] for row in fixed_rows) + flexible_question_count,
        "flexible_groups": verified_scope.get("flexible_groups", []),
    }
    exam_scope["verified_detail_rows"] = verified_rows
    exam_scope["counts"]["verified_detail_rows"] = len(verified_rows)
    exam_scope["counts"]["verified_fixed_rows"] = len(fixed_rows)
    exam_scope["counts"]["verified_candidate_rows"] = len(candidate_rows)
    return exam_scope


def clean_objective_text(text: str) -> tuple[str, str]:
    text = compact_spaces(text)
    level = ""
    match = re.search(r"\(([ABC])\)", text)
    if match:
        level = match.group(1)
        text = compact_spaces(f"{text[:match.start()]} {text[match.end():]}")
    return text, level


def parse_learning_objectives(pages: list[dict[str, Any]], tables: list[dict[str, Any]]) -> dict[str, Any]:
    learning_pages = [
        page
        for page in pages
        if "학습목표" in normalize_text(page["source_file"])
        and page["char_count"] > 0
    ]
    learning_source = learning_pages[0]["source_file"] if learning_pages else ""
    learning_tables = [
        table
        for table in tables
        if table["source_file"] == learning_source and table["rows"]
    ]
    tables_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for table in learning_tables:
        tables_by_page[table["page"]].append(table)

    objectives: list[dict[str, Any]] = []
    current_context = {
        "exam_part": "",
        "major_unit": "",
        "field_hint": "",
    }
    pending_units: list[dict[str, str]] = []
    current_unit: dict[str, str] | None = None

    for page in learning_pages:
        source_file = page["source_file"]
        source_page = page["page"]

        blocks = sorted(page.get("blocks", []), key=lambda b: (b["bbox"][1], b["bbox"][0]))
        last_unit: dict[str, str] | None = None
        purpose_target: dict[str, str] | None = None
        for block in blocks:
            for line in [compact_spaces(item) for item in block["text"].splitlines() if compact_spaces(item)]:
                if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.", line):
                    current_context["exam_part"] = line
                    continue

                heading = re.match(r"^#\s*(\d+)\s+(.+?)(?:\s*\[(.+?)\])?$", line)
                if heading:
                    current_context["major_unit"] = f"#{heading.group(1)} {heading.group(2).strip()}"
                    current_context["field_hint"] = heading.group(3) or ""
                    continue

                unit_match = re.match(r"^No\.\s*(\d+)\s+(.+)$", line)
                if unit_match:
                    unit = {
                        **current_context,
                        "unit_no": unit_match.group(1),
                        "unit": unit_match.group(2).strip(),
                        "learning_purpose": "",
                    }
                    pending_units.append(unit)
                    last_unit = unit
                    purpose_target = None
                    continue

                if line.startswith("학습목적"):
                    purpose_target = last_unit
                    purpose_tail = re.sub(r"^학습목적\s*\d*", "", line).strip()
                    if purpose_target and purpose_tail:
                        purpose_target["learning_purpose"] = purpose_tail
                        purpose_target = None
                    continue

                if purpose_target and not (
                    line.startswith("No.")
                    or line == "학습목표"
                    or line == "중심단어"
                    or re.match(r"^#\s*\d+", line)
                ):
                    purpose_target["learning_purpose"] = compact_spaces(
                        f"{purpose_target['learning_purpose']} {line}"
                    )
                    purpose_target = None

        for table in sorted(tables_by_page.get(source_page, []), key=lambda item: item["table_index"]):
            rows = table["rows"]
            has_header = rows[0][:2] == ["학습목표", "중심단어"]
            data_rows = rows[1:] if has_header else rows

            if has_header and pending_units:
                current_unit = pending_units.pop(0)
            if current_unit is None:
                current_unit = pending_units.pop(0) if pending_units else {
                    **current_context,
                    "unit_no": "",
                    "unit": "",
                    "learning_purpose": "",
                }

            parent_prefix = ""
            for row in data_rows:
                cells = (row + ["", ""])[:2]
                raw_goal = compact_spaces(cells[0])
                keyword = compact_spaces(cells[1])
                if not raw_goal:
                    continue
                goal, level = clean_objective_text(raw_goal)
                if not level and not keyword:
                    parent_prefix = goal
                    continue
                if parent_prefix and re.match(r"^\d+\)", goal):
                    goal = f"{parent_prefix} - {goal}"
                if len(goal) < 4:
                    continue
                objectives.append(
                    {
                        "objective_id": slug_id(
                            current_unit["major_unit"],
                            current_unit["unit"],
                            goal,
                            keyword,
                            prefix="lo_",
                        ),
                        "exam_part": current_unit["exam_part"],
                        "major_unit": current_unit["major_unit"],
                        "field_hint": current_unit["field_hint"],
                        "unit_no": current_unit["unit_no"],
                        "unit": current_unit["unit"],
                        "learning_purpose": current_unit["learning_purpose"],
                        "objective": goal,
                        "level": level,
                        "keywords": [k.strip() for k in re.split(r"[,，/]", keyword) if k.strip()],
                        "raw_keyword": keyword,
                        "source_file": source_file,
                        "source_page": source_page,
                    }
                )

    level_counts = Counter(item["level"] or "unknown" for item in objectives)
    unit_counts = Counter(item["major_unit"] for item in objectives)
    return {
        "version": 1,
        "purpose": "2022 개정 방사선학 학습목표를 문제 생성 target으로 구조화한다.",
        "source_file": learning_source,
        "counts": {
            "objectives": len(objectives),
            "levels": dict(level_counts),
            "major_units": dict(unit_counts),
        },
        "objectives": objectives,
    }


def classify_question_type(text: str) -> list[str]:
    result = ["개념형"]
    if any(k in text for k in ["계산", "구한다", "공식", "단위", "선량", "반감기", "전류", "전압"]):
        result.append("계산형")
    if any(k in text for k in ["법", "조문", "면허", "자격", "보건", "의료기관"]):
        result.append("법규형")
    if any(k in text for k in ["검사", "촬영", "절차", "방법", "적응증", "전처치"]):
        result.append("검사절차형")
    if any(k in text for k in ["영상", "CT", "MRI", "초음파", "핵의학", "PET", "SPECT"]):
        result.append("영상해석형")
    if any(k in text for k in ["안전", "방어", "관리", "피폭", "차폐", "선량한도"]):
        result.append("안전관리형")
    return sorted(set(result))


def build_question_generation_targets(learning: dict[str, Any], exam_scope: dict[str, Any]) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    scope_details = exam_scope["detail_rows"]
    for obj in learning["objectives"]:
        obj_text = obj["objective"]
        unit = obj["unit"]
        candidates = []
        for scope in scope_details:
            score = 0
            for value in [scope["detail"], scope["area"], scope["field"]]:
                name = strip_number(value)
                if name and (name in unit or unit in name or name in obj_text):
                    score += 2
            if score:
                candidates.append({"scope_id": scope["scope_id"], "score": score})
        targets.append(
            {
                "target_id": slug_id(obj["objective_id"], prefix="qgt_"),
                "learning_objective_id": obj["objective_id"],
                "objective": obj_text,
                "keywords": obj["keywords"],
                "level": obj["level"],
                "major_unit": obj["major_unit"],
                "unit": obj["unit"],
                "recommended_question_types": classify_question_type(" ".join([obj_text, obj["raw_keyword"], obj["major_unit"], obj["unit"]])),
                "scope_candidates": sorted(candidates, key=lambda c: c["score"], reverse=True)[:5],
                "source_page": obj["source_page"],
            }
        )
    return {
        "version": 1,
        "purpose": "학습목표별 문항 생성 target과 출제범위 후보를 연결한다.",
        "counts": {"targets": len(targets)},
        "targets": targets,
    }


def build_scope_generation_strategy(exam_scope: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for scope in exam_scope["detail_rows"]:
        text = " ".join([scope["subject"], scope["field"], scope["area"], scope["detail"]])
        rows.append(
            {
                "scope_id": scope["scope_id"],
                "period": scope["period"],
                "subject": scope["subject"],
                "field": scope["field"],
                "area": scope["area"],
                "detail": scope["detail"],
                "recommended_question_types": classify_question_type(text),
                "recommended_difficulties": ["하", "중"] if "계산형" not in classify_question_type(text) else ["중", "상"],
                "required_evidence_types": ["법령 원문"] if "법규형" in classify_question_type(text) else ["전공 근거 자료"],
                "source_page": scope["source_page"],
            }
        )
    return {
        "version": 2,
        "purpose": "공식 출제범위 세부영역별 문항 유형, 난이도, 근거 요구사항을 연결한다.",
        "counts": {"rows": len(rows)},
        "rows": rows,
    }


def run_tesseract_ocr(pdf_path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    doc = fitz.open(pdf_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for index, page in enumerate(doc, start=1):
            img = tmp_dir / f"page_{index}.png"
            page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False).save(img)
            proc = subprocess.run(
                ["tesseract", str(img), "stdout", "-l", "kor+eng", "--psm", "11"],
                text=True,
                capture_output=True,
                check=False,
            )
            pages.append(
                {
                    "source_file": str(pdf_path.relative_to(ROOT)),
                    "page": index,
                    "engine": "tesseract",
                    "languages": ["kor", "eng"],
                    "text": normalize_text(proc.stdout),
                    "stderr": normalize_text(proc.stderr),
                }
            )
    return pages


def increment(counter: dict[tuple[str, ...], int], key: tuple[str, ...], amount: int) -> None:
    counter[key] = counter.get(key, 0) + amount


def build_verified_blueprint(verified_scope: dict[str, Any]) -> dict[str, Any]:
    rows = verified_scope_rows(verified_scope)
    fixed_rows = [row for row in rows if isinstance(row.get("question_count"), int)]
    flexible_groups = verified_scope.get("flexible_groups", [])

    period_distribution: dict[str, dict[str, Any]] = {}
    subject_distribution: dict[tuple[str, str], int] = {}
    field_distribution: dict[tuple[str, str, str], int] = {}
    area_distribution: dict[tuple[str, str, str, str], int] = {}
    page_distribution: dict[int, int] = {}

    for row in fixed_rows:
        count = row["question_count"]
        period = row["period"]
        subject = row["subject"]
        field = row["field"]
        area = row["area"]
        increment(subject_distribution, (period, subject), count)
        increment(field_distribution, (period, subject, field), count)
        increment(area_distribution, (period, subject, field, area), count)
        if isinstance(row.get("source_page"), int):
            page_distribution[row["source_page"]] = page_distribution.get(row["source_page"], 0) + count

    flexible_area_keys: set[tuple[str, str, str, str]] = set()
    for group in flexible_groups:
        count = int(group.get("flexible_question_count", 0))
        subject = compact_spaces(group.get("subject", ""))
        field = compact_spaces(group.get("field", ""))
        area = compact_spaces(group.get("area", ""))
        period = infer_period(subject)
        increment(subject_distribution, (period, subject), count)
        increment(field_distribution, (period, subject, field), count)
        increment(area_distribution, (period, subject, field, area), count)
        flexible_area_keys.add((period, subject, field, area))

    for (period, subject), count in subject_distribution.items():
        period_node = period_distribution.setdefault(period, {"subjects": {}, "questions": 0})
        period_node["subjects"][subject] = count
        period_node["questions"] += count

    def dict_rows(counter: dict[tuple[str, ...], int], keys: list[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for key, count in sorted(counter.items()):
            record = dict(zip(keys, key))
            record["question_count"] = count
            if len(key) == 4 and key in flexible_area_keys:
                record["count_mode"] = "fixed_plus_flexible"
            else:
                record["count_mode"] = "fixed"
            records.append(record)
        return records

    return {
        "version": 2,
        "purpose": "문항 생성 비율과 모의고사 구성 기준을 관리한다.",
        "source_files": [verified_scope.get("source_file", "")],
        "verified_scope_file": str(VERIFIED_SCOPE_PATH.relative_to(ROOT)),
        "verification_status": verified_scope.get("verification_status"),
        "period_distribution": period_distribution,
        "subject_distribution": dict_rows(subject_distribution, ["period", "subject"]),
        "field_distribution": dict_rows(field_distribution, ["period", "subject", "field"]),
        "area_distribution": dict_rows(area_distribution, ["period", "subject", "field", "area"]),
        "detail_distribution": fixed_rows,
        "candidate_detail_rows": [row for row in rows if row.get("count_mode") == "candidate"],
        "flexible_groups": flexible_groups,
        "page_distribution": [
            {"page": page, "question_count": count}
            for page, count in sorted(page_distribution.items())
        ],
        "totals": {
            "fixed_question_sum": sum(row["question_count"] for row in fixed_rows),
            "flexible_question_sum": sum(group.get("flexible_question_count", 0) for group in flexible_groups),
            "total_questions": sum(row["question_count"] for row in fixed_rows)
            + sum(group.get("flexible_question_count", 0) for group in flexible_groups),
            "expected": verified_scope.get("expected_totals", {}),
        },
        "ocr_status": {
            "required": False,
            "engine_installed": "paddleocr 3.6.0 / paddlepaddle 3.2.2; tesseract kor+eng fallback",
            "raw_ocr_output": "resources/extracted/paddleocr_sebuyeongyeok_pages.json",
            "verified_scope_output": str(VERIFIED_SCOPE_PATH.relative_to(ROOT)),
            "pages_processed": 7,
            "note": "세부영역 PDF는 PaddleOCR raw를 사용자 검수본으로 교정했다. 문제 생성 비중은 OCR raw가 아니라 verified_scope_file을 기준으로 사용한다.",
        },
    }


def build_blueprint(pages: list[dict[str, Any]], verified_scope: dict[str, Any] | None = None) -> dict[str, Any]:
    if verified_scope:
        return build_verified_blueprint(verified_scope)

    detail_pdf = None
    for candidate in MATERIALS_DIR.rglob("*.pdf"):
        if "세부영역" in normalize_text(candidate.name):
            detail_pdf = candidate
            break

    ocr_pages: list[dict[str, Any]] = []
    if detail_pdf and detail_pdf.exists():
        ocr_pages = run_tesseract_ocr(detail_pdf)
        write_json(EXTRACTED_DIR / "ocr_sebuyeongyeok_pages.json", ocr_pages)

    # These totals are stable across the official notice and detailed-area file.
    period_distribution = {
        "1교시": {"subjects": {"방사선이론": 90, "의료관계법규": 20}, "questions": 110},
        "2교시": {"subjects": {"방사선응용": 90}, "questions": 90},
        "3교시": {"subjects": {"실기시험": 50}, "questions": 50},
    }

    recognized_rows: list[dict[str, Any]] = []
    area_patterns = [
        ("방사선이론", "방사선기초", "방사선물리", 9),
        ("방사선이론", "방사선기초", "전기전자개론", 9),
        ("방사선이론", "방사선기초", "의료영상정보", 10),
        ("방사선이론", "방사선취급", "방사선계측", 9),
        ("방사선이론", "방사선취급", "방사선장치(기기)", 9),
        ("의료관계법규", "", "의료법", 10),
        ("의료관계법규", "", "의료기사등에 관한 법률", 5),
        ("의료관계법규", "", "지역보건법", 5),
    ]
    ocr_blob = "\n".join(page["text"] for page in ocr_pages)
    for subject, field, area, count in area_patterns:
        if not ocr_blob or strip_number(area).replace(" ", "")[:4] in ocr_blob.replace(" ", ""):
            recognized_rows.append(
                {
                    "subject": subject,
                    "field": field,
                    "area": area,
                    "question_count": count,
                    "source": "2022년도 방사선사 국가시험 세부영역.pdf",
                    "confidence": "manual_pattern_from_ocr_preview",
                }
            )

    return {
        "version": 1,
        "purpose": "문항 생성 비율과 모의고사 구성 기준을 관리한다.",
        "source_files": [
            page["source_file"]
            for page in pages
            if "세부영역" in normalize_text(page["source_file"])
        ][:1],
        "period_distribution": period_distribution,
        "recognized_area_distribution": recognized_rows,
        "ocr_status": {
            "required": True,
            "engine_installed": "tesseract kor+eng",
            "raw_ocr_output": "resources/extracted/ocr_sebuyeongyeok_pages.json",
            "pages_processed": len(ocr_pages),
            "note": "세부영역 PDF는 스캔본이라 표 전체 자동 추출 신뢰도가 낮다. 총 문항 수와 일부 영역별 문항 수는 구조화했으며, 전체 세부영역별 count는 OCR 결과와 이미지 원본 대조가 필요하다.",
        },
    }


def build_question_type_rules() -> dict[str, Any]:
    return {
        "version": 2,
        "purpose": "문항작성법, 문항평가법, 문항 설계 자료의 유형 체계를 통합한다.",
        "cognitive_levels": [
            {"id": "memory", "name": "암기형", "description": "사실, 용어, 원리, 단일 개념을 기억해 답한다.", "bloom": ["지식", "이해"]},
            {"id": "interpretation", "name": "해석형", "description": "자료, 표, 영상, 조건을 해석해 개념 관계를 판단한다.", "bloom": ["이해", "분석"]},
            {"id": "problem_solving", "name": "문제해결형", "description": "상황에 지식을 적용하고 계산·절차·안전 판단을 수행한다.", "bloom": ["응용", "분석", "평가"]},
        ],
        "types": {
            "개념형": {
                "cognitive_level": "암기형",
                "purpose": "용어, 정의, 원리, 특징, 장단점 확인",
                "good_for": ["방사선물리", "방사선생물", "의료영상정보", "법규 기본 개념"],
                "checks": ["단일 개념을 묻는가", "정답 근거가 명확한가"],
            },
            "비교형": {
                "cognitive_level": "해석형",
                "purpose": "두 개 이상 개념의 차이, 조건 변화, 장단점 비교",
                "good_for": ["CT/MRI 비교", "PET/SPECT 비교", "피폭/방어 원칙"],
                "checks": ["비교 기준이 지시문에 명확한가", "보기의 비교 축이 동일한가"],
            },
            "계산형": {
                "cognitive_level": "문제해결형",
                "purpose": "공식, 단위, 수치를 적용해 답을 계산",
                "good_for": ["역제곱법칙", "반가층", "선량", "확대율", "전기회로"],
                "checks": ["공식과 단위 근거가 있는가", "계산 조건이 충분한가"],
            },
            "법규형": {
                "cognitive_level": "해석형",
                "purpose": "법령명, 조문, 기준일에 따른 법규 판단",
                "good_for": ["의료법", "의료기사 등에 관한 법률", "지역보건법"],
                "checks": ["법령명/조문/기준일이 있는가", "개정 가능성을 표시했는가"],
            },
            "검사절차형": {
                "cognitive_level": "문제해결형",
                "purpose": "검사 목적, 전처치, 순서, 촬영 조건 판단",
                "good_for": ["일반촬영", "투시조영", "CT", "MRI", "핵의학"],
                "checks": ["절차 순서와 조건이 근거에 있는가"],
            },
            "안전관리형": {
                "cognitive_level": "문제해결형",
                "purpose": "환자 안전, 방사선 방어, 장비 관리, 품질관리 판단",
                "good_for": ["방사선관리", "MRI 안전", "조영제 안전", "품질관리"],
                "checks": ["위험 조건과 대응 원칙이 명확한가"],
            },
            "영상해석형": {
                "cognitive_level": "해석형",
                "purpose": "영상, 장비, artifact, 품질 상태를 보고 판단",
                "good_for": ["실기 판단", "영상 품질", "장비 식별", "단면해부"],
                "checks": ["이미지 사용권한/비식별/정합성 검토가 있는가"],
                "mvp_status": "limited_until_visual_review_rubric_ready",
            },
        },
    }


def build_difficulty_rubric() -> dict[str, Any]:
    return {
        "version": 2,
        "purpose": "생성 전 예상 난이도와 생성 후 문항 분석 난이도를 분리해 관리한다.",
        "expected_difficulty": {
            "하": {
                "definition": "단일 개념, 정의, 명칭, 기본 특징을 직접 확인한다.",
                "cognitive_levels": ["암기형"],
                "avoid": ["복합 계산", "다중 조건", "영상 판독 중심"],
            },
            "중": {
                "definition": "개념 간 관계, 비교, 절차 순서, 기본 계산 적용이 필요하다.",
                "cognitive_levels": ["해석형", "문제해결형"],
                "avoid": ["근거 자료 밖의 예외 상황"],
            },
            "상": {
                "definition": "복합 조건, 계산 적용, 상황 판단, 안전관리 판단이 필요하다.",
                "cognitive_levels": ["문제해결형"],
                "avoid": ["전문의 수준 판독", "국가고시 범위를 넘는 임상 추론"],
            },
        },
        "post_exam_item_analysis": {
            "difficulty_index": {
                "formula": "정답자수 / 응시자총수 * 100",
                "ranges": [
                    {"range": "<25", "label": "어려운 문항"},
                    {"range": "25 이상~75 미만", "label": "적절한 문항"},
                    {"range": "75 이상", "label": "쉬운 문항"},
                ],
            },
            "discrimination_index": {
                "ranges": [
                    {"range": "0.35 이상", "label": "우수한 문항"},
                    {"range": "0.25 이상~0.35 미만", "label": "양호한 문항"},
                    {"range": "0.15 이상~0.25 미만", "label": "경계 문항"},
                    {"range": "0.15 미만", "label": "불량"},
                ],
            },
            "distractor_analysis": {
                "principle": "오답은 너무 명백하지 않고 매력적이어야 하며, 함정형 오답은 피한다.",
                "expected_distribution": "각 오답 선택 빈도가 지나치게 낮으면 오답 매력도가 낮은 후보로 본다.",
            },
        },
    }


def build_validation_checklist() -> dict[str, Any]:
    return {
        "version": 2,
        "format": [
            "5지선다형 보기 5개가 있는가",
            "정답이 정확히 1개인가",
            "문항, 보기, 정답, 해설, 출처, 난이도, 문항 유형이 모두 있는가",
            "문항 줄기는 답가지를 보지 않고도 질문 의도가 성립하는가",
            "'모두 맞음', '모두 틀림' 보기를 사용하지 않았는가",
        ],
        "scope": [
            "교시/과목/분야/영역/세부영역이 exam_scope.json에 존재하는가",
            "학습목표가 learning_objectives.json 또는 question_generation_targets.json에 연결되는가",
            "모의고사 생성 시 blueprint.json의 문항 수 배분을 따르는가",
        ],
        "stem": [
            "한 문항에서는 한 가지를 질문하는가",
            "긍정문을 우선 사용했는가",
            "부정문 사용 시 부정 표현이 명확히 강조되는가",
            "문항 줄기에서 정답을 암시하지 않는가",
            "문항 줄기는 충분한 조건을 제공하고 답가지는 짧게 유지되는가",
        ],
        "options": [
            "답가지는 상호 독립적인가",
            "답가지는 상호 비교 가능한 동일 범주인가",
            "답가지 길이가 유사한가",
            "순서가 있는 답가지는 순서대로 배열했는가",
            "정답만 표현 방식, 길이, 구체성이 튀지 않는가",
        ],
        "distractors": [
            "오답이 너무 명백하지 않고 매력적인가",
            "오답이 함정형 표현으로 수험자를 속이지 않는가",
            "오답이 같은 영역의 혼동 개념, 단위 오류, 절차 오류, 법령 착오에서 설계되었는가",
        ],
        "evidence": [
            "해설이 근거 chunk 또는 법령 조문으로 설명되는가",
            "근거에 없는 수치, 법령, 예외 조건을 만들지 않았는가",
            "출처 파일과 페이지 또는 법령 기준일이 연결되는가",
        ],
        "hallucination": [
            "문제는 RAG 검색 결과를 근거로 생성되었는가",
            "RAG 근거에 없는 내용을 추가 생성하지 않았는가",
            "해설은 근거 문서에 포함된 내용만 사용했는가",
            "수치, 단위, 공식은 원문과 비교 검증되었는가",
            "법규 문항은 존재하는 법령명, 조문번호, 기준일만 사용했는가",
        ],
        "copyright": [
            "전공서 원문 문장을 그대로 복사하지 않았는가",
            "원문 문제, 보기, 해설 구조를 재현하지 않았는가",
            "학습목표와 핵심 개념만 활용하고 문항 문장은 새롭게 작성했는가",
            "출처와 페이지는 메타데이터로만 저장했는가",
            "생성 문항과 근거 chunk의 문장 유사도가 80% 미만인가",
            "보기 5개 중 원문과 동일한 보기가 3개 미만인가",
            "해설이 원문 표현을 그대로 사용하지 않았는가",
        ],
        "language": [
            "두음법칙, 사이시옷, 띄어쓰기, 외래어 표기 오류가 없는가",
            "번역투, 불필요한 피동/사동, 외국어 남용을 피했는가",
            "중의적 수식과 지시어가 없는가",
            "지시문과 답가지의 문법 형식이 조화되는가",
            "불필요한 배경 정보와 중복 표현을 제거했는가",
        ],
        "law": [
            "법규형 문항은 법령명, 조문, 시행령/시행규칙 구분, 기준일이 있는가",
            "법규 개정 시 재검토 상태로 전환할 수 있는 메타데이터가 있는가",
        ],
        "harness_must_pass": [
            "보기 개수 = 5",
            "정답 개수 = 1",
            "해설 존재",
            "출처 존재",
            "출제범위 존재",
            "학습목표 존재",
            "근거 문서 존재",
            "국문법 오류 없음",
            "중복 문항 아님",
        ],
        "status_management": [
            "generated는 생성 완료 상태이며 최종 문제 DB 저장 전 상태이다",
            "reviewed는 검증 에이전트와 Harness를 통과한 상태이다",
            "approved는 최종 승인 상태이다",
            "rejected는 폐기 상태이다",
            "최종 문제 DB에는 reviewed 또는 approved 상태만 저장한다",
        ],
        "rag_data_quality": [
            "OCR 결과를 검증했는가",
            "문서 구조를 검증했는가",
            "학습목표와 연결했는가",
            "출제범위와 연결했는가",
            "출처와 페이지 정보를 유지했는가",
            "원문을 대체하는 2차 PDF를 생성하지 않았는가",
        ],
        "visual_items": [
            "영상·장비 문항은 이미지 사용권한과 비식별 검토가 있는가",
            "이미지 자체의 해부학/장비/영상물리 정합성을 전문가가 검토했는가",
            "영상 판독 수준이 국가고시 범위를 넘어가지 않는가",
        ],
    }


def build_copyright_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "04_subject_references 전공 근거 자료를 사용할 때 원문 복제와 유사 문항 생성을 막기 위한 저작권 보호 정책",
        "principles": [
            "전공서 원문을 복사하거나 재서술하지 않는다.",
            "원문 문제, 보기, 해설을 재현하지 않는다.",
            "학습목표와 핵심 개념만 추출한다.",
            "생성된 문제는 새로운 문장으로 작성한다.",
            "원문과 높은 유사도가 발견되면 생성 결과를 폐기한다.",
            "출처는 메타데이터로만 저장한다.",
            "RAG는 정답 근거 제공 용도로만 사용한다.",
            "문제, 보기, 해설은 새롭게 생성한다.",
        ],
        "rules": [
            {"id": "CP-001", "name": "원문 문장 복사 금지", "requirement": "원문 문장을 그대로 복사하지 말 것", "severity": "error"},
            {"id": "CP-002", "name": "원문 문제 재서술 금지", "requirement": "원문 문제를 재서술하지 말 것", "severity": "error"},
            {"id": "CP-003", "name": "보기 구성 재현 금지", "requirement": "보기 구성도 그대로 재현하지 말 것", "severity": "error"},
            {"id": "CP-004", "name": "원문 해설 인용 금지", "requirement": "원문 해설을 인용하지 말 것", "severity": "error"},
            {"id": "CP-005", "name": "핵심 개념만 활용", "requirement": "학습목표와 핵심 개념만 활용할 것", "severity": "error"},
            {"id": "CP-006", "name": "신규 문항 생성", "requirement": "새로운 문제, 보기, 해설을 생성할 것", "severity": "error"},
            {"id": "CP-007", "name": "출처 메타데이터 제한", "requirement": "출처와 페이지는 메타데이터로만 저장할 것", "severity": "error"},
            {"id": "CP-008", "name": "고유사도 폐기", "requirement": "생성 결과가 원문과 높은 유사도를 보이면 폐기할 것", "severity": "error"},
        ],
        "validation": {
            "compare_target": "generated question vs source chunks",
            "sentence_similarity_reject_threshold": 0.8,
            "option_exact_match_reject_count": 3,
            "explanation_similarity_reject_threshold": 0.8,
            "minimum_text_length": 12,
            "checks": [
                "생성된 문제와 원문 chunk를 비교한다.",
                "문장 유사도 80% 이상이면 거부한다.",
                "보기 5개 중 3개 이상 동일하면 거부한다.",
                "해설이 원문 표현을 그대로 사용하면 거부한다.",
                "개념은 유지하되 표현은 새롭게 작성되었는지 확인한다.",
            ],
        },
        "generation_instruction": "근거 chunk는 정답 판단과 핵심 개념 확인에만 사용하고, 문항 줄기, 보기, 해설은 원문과 다른 새 문장으로 작성한다.",
    }


def build_validation_agents() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "문항 생성 결과를 전문 검증 에이전트 5개와 최종 Judge로 평가하는 구조",
        "agents": [
            {
                "id": "agent_scope",
                "name": "Agent 1 - 출제범위 검증",
                "input": ["문제", "출제범위", "학습목표"],
                "prompt": "당신은 국가고시 출제 검수자이다. 이 문제가 주어진 출제범위와 학습목표에 포함되는지 판단하라.",
                "output_schema": {"pass": "boolean", "reason": "string"},
                "fail_action": "문항 폐기 또는 출제범위 재지정",
            },
            {
                "id": "agent_uniqueness",
                "name": "Agent 2 - 정답 유일성 검증",
                "input": ["문제", "보기", "정답"],
                "prompt": "정답이 하나만 존재하는가? 다른 보기가 정답으로 해석될 가능성이 있는가?",
                "output_schema": {"pass": "boolean", "reason": "string"},
                "fail_action": "문제 폐기",
            },
            {
                "id": "agent_grounding",
                "name": "Agent 3 - RAG 근거 검증",
                "input": ["문제", "해설", "RAG 근거"],
                "prompt": "문제와 해설이 근거 문단에서 지원되는가?",
                "output_schema": {"grounded": "boolean", "confidence": "number", "reason": "string"},
                "fail_action": "문항 폐기 또는 근거 재검색",
            },
            {
                "id": "agent_copyright",
                "name": "Agent 4 - 저작권 검증",
                "input": ["원문 chunk", "생성 문제"],
                "prompt": "생성 문제가 원문 문장을 복사했는가? 문제 구조를 재현했는가? 보기를 그대로 사용했는가?",
                "output_schema": {"copyright_risk": "low | medium | high", "pass": "boolean", "reason": "string"},
                "fail_action": "문항 폐기",
            },
            {
                "id": "agent_grammar",
                "name": "Agent 5 - 국문법·문항 표현 검증",
                "input": ["문제", "보기", "해설", "문항작성법", "문항평가법", "국문법 교재"],
                "prompt": "국문법 오류, 부정문 사용, 답가지 길이, 문항줄기 오류를 검사하라.",
                "output_schema": {"pass": "boolean", "reason": "string"},
                "fail_action": "문항 수정 또는 폐기",
            },
        ],
        "judge": {
            "id": "final_judge",
            "name": "최종 Judge",
            "input": ["scope", "grounding", "uniqueness", "grammar", "copyright"],
            "output_schema": {
                "scope": "boolean",
                "grounding": "boolean",
                "uniqueness": "boolean",
                "grammar": "boolean",
                "copyright": "boolean",
                "final_pass": "boolean",
                "discard": "boolean",
                "reason": "string",
            },
            "final_pass_rule": "scope, grounding, uniqueness, grammar, copyright가 모두 true일 때만 최종 통과",
        },
    }


def build_quality_requirements() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "방사선사 국가고시 1·2교시 AI 문제은행의 품질, 근거, 검증, 저장 기준",
        "target_scope": {
            "included": ["1교시 의료법규", "1교시 방사선이론", "2교시 방사선응용"],
            "excluded_until_separate_review": ["3교시 실기시험 영상·장비 기반 문항"],
        },
        "hallucination_prevention": {
            "required": [
                "문제는 반드시 RAG 검색 결과를 근거로 생성한다.",
                "RAG 근거에 없는 내용을 추가 생성하지 않는다.",
                "해설은 근거 문서에 포함된 내용만 사용한다.",
                "법규 문제는 법령명, 조문번호, 기준일을 저장한다.",
                "수치, 단위, 공식은 원문 근거와 비교 검증한다.",
                "학습목표와 출제범위를 벗어난 문제는 생성하지 않는다.",
            ],
            "forbidden": [
                "모델의 사전지식만으로 문제 생성",
                "근거 없는 해설 생성",
                "존재하지 않는 조문 생성",
                "존재하지 않는 수치 생성",
            ],
        },
        "copyright_protection": {
            "policy_file": "copyright_policy.json",
            "required": [
                "원문 문장을 그대로 복사하지 않는다.",
                "문제집 문제를 재서술하지 않는다.",
                "보기 구성을 그대로 재현하지 않는다.",
                "해설을 그대로 복사하지 않는다.",
                "출처는 메타데이터로만 저장한다.",
                "문제, 보기, 해설은 새롭게 생성한다.",
            ],
        },
        "reviewer_agents": {
            "policy_file": "validation_agents.json",
            "required_agents": ["출제범위 검증", "정답 유일성 검증", "RAG 근거 검증", "저작권 위험 검증", "국문법 검증"],
            "final_pass_rule": "모든 검증 에이전트가 통과해야 reviewed 상태로 전환할 수 있다.",
        },
        "harness_must_pass": [
            "보기 개수 = 5",
            "정답 개수 = 1",
            "해설 존재",
            "출처 존재",
            "출제범위 존재",
            "학습목표 존재",
            "근거 문서 존재",
            "국문법 오류 없음",
            "중복 문항 아님",
        ],
        "question_status": {
            "states": ["generated", "reviewed", "approved", "rejected"],
            "meanings": {
                "generated": "생성 완료, 아직 검증 통과 전",
                "reviewed": "검증 에이전트와 Harness 통과",
                "approved": "최종 승인",
                "rejected": "폐기",
            },
            "db_storage_policy": "최종 문제 DB에는 reviewed 또는 approved 상태만 저장한다.",
        },
        "rag_data_quality": {
            "required": ["OCR 결과 검증", "문서 구조 검증", "학습목표 연결", "출제범위 연결", "출처 및 페이지 저장"],
            "principles": [
                "AI가 생성한 요약 자료를 AI가 검수하더라도 정확성을 완전히 보장할 수 없으므로 원문 출처와 페이지 정보를 유지한다.",
                "원문을 대체하는 2차 PDF를 생성하지 않는다.",
                "원문은 근거 자료로 유지하고 AI는 구조화와 검색 보조 용도로만 사용한다.",
            ],
        },
    }


def build_question_schema() -> dict[str, Any]:
    return {
        "version": 1,
        "required_fields": [
            "period",
            "subject",
            "field",
            "area",
            "detail",
            "scope_id",
            "learning_objective_id",
            "question_type",
            "competency_type",
            "difficulty",
            "stem",
            "options",
            "answer",
            "explanation",
            "evidence_refs",
            "source_chunks",
            "distractor_strategy",
            "validation_status",
            "reviewer_agent_results",
            "final_judge",
            "status",
        ],
        "status_lifecycle": {
            "states": ["generated", "reviewed", "approved", "rejected"],
            "db_storage_policy": "최종 문제 DB에는 reviewed 또는 approved 상태만 저장한다.",
            "reviewed_transition": "검증 에이전트와 Harness 필수 조건을 모두 통과하면 reviewed로 전환한다.",
        },
        "reviewer_agent_result_fields": ["scope", "grounding", "uniqueness", "grammar", "copyright"],
        "law_extra_fields": ["law_name", "article_ref", "effective_date"],
        "visual_extra_fields": ["image_ref", "image_rights_status", "deidentification_status", "visual_review_status"],
    }


def build_competency_types() -> dict[str, Any]:
    return {
        "version": 1,
        "types": [
            {"id": "knowledge_understanding", "name": "지식 이해"},
            {"id": "calculation_application", "name": "계산 적용"},
            {"id": "visual_interpretation", "name": "영상 판독"},
            {"id": "equipment_identification", "name": "장비 식별"},
            {"id": "procedure_judgment", "name": "검사 절차 판단"},
            {"id": "safety_management", "name": "안전관리 판단"},
            {"id": "law_application", "name": "법령 적용"},
        ],
    }


def write_summary(
    exam_scope: dict[str, Any],
    learning: dict[str, Any],
    blueprint: dict[str, Any],
    targets: dict[str, Any],
) -> None:
    lines = [
        "# Rule Rebuild Summary",
        "",
        "## Sources",
        f"- 공식 출제범위 대표본: `{exam_scope['source_file']}`",
        f"- 중복 출제범위 후보: {len(exam_scope['duplicate_scope_files'])}개",
        f"- 학습목표 원본: `{learning['source_file']}`",
        f"- 세부영역 OCR raw: `{blueprint['ocr_status']['raw_ocr_output']}`",
        f"- 세부영역 검수본: `{blueprint['ocr_status'].get('verified_scope_output', '')}`",
        "",
        "## Counts",
        f"- exam_scope hierarchy rows: {exam_scope['counts']['hierarchy_rows']}",
        f"- exam_scope detail rows: {exam_scope['counts']['detail_rows']}",
        f"- verified detail rows: {exam_scope['counts'].get('verified_detail_rows', 0)}",
        f"- learning objectives: {learning['counts']['objectives']}",
        f"- question generation targets: {targets['counts']['targets']}",
        f"- blueprint total questions: {blueprint.get('totals', {}).get('total_questions', 'unknown')}",
        "",
        "## Notes",
        "- 한 PDF가 여러 JSON에 영향을 주도록 재구성했다.",
        "- 세부영역 PDF는 PaddleOCR raw를 보존하되, 문제 생성 비중은 사용자 검수본을 기준으로 고정했다.",
        "- 영상품질관리는 확정 2문항과 후보군 내 유동 1문항으로 표현한다.",
        "- 04_subject_references가 들어오기 전까지 evidence_refs는 실제 전공 근거가 아니라 생성 기준 연결만 가능하다.",
    ]
    write_text(DOCS_DIR / "12_rule_rebuild_summary.md", "\n".join(lines))


def main() -> None:
    tables = load_json(EXTRACTED_DIR / "tables.json")
    pages = load_pages()
    verified_scope = load_verified_scope()

    exam_scope = build_exam_scope(tables)
    exam_scope = apply_verified_scope_to_exam_scope(exam_scope, verified_scope)
    learning = parse_learning_objectives(pages, tables)
    targets = build_question_generation_targets(learning, exam_scope)
    blueprint = build_blueprint(pages, verified_scope)
    scope_strategy = build_scope_generation_strategy(exam_scope)

    write_json(RULES_DIR / "exam_scope.json", exam_scope)
    write_json(RULES_DIR / "learning_objectives.json", learning)
    write_json(RULES_DIR / "question_generation_targets.json", targets)
    write_json(RULES_DIR / "scope_generation_strategy.json", scope_strategy)
    write_json(RULES_DIR / "blueprint.json", blueprint)
    write_json(RULES_DIR / "question_type_rules.json", build_question_type_rules())
    write_json(RULES_DIR / "difficulty_rubric.json", build_difficulty_rubric())
    write_json(RULES_DIR / "validation_checklist.json", build_validation_checklist())
    write_json(RULES_DIR / "copyright_policy.json", build_copyright_policy())
    write_json(RULES_DIR / "validation_agents.json", build_validation_agents())
    write_json(RULES_DIR / "quality_requirements.json", build_quality_requirements())
    write_json(RULES_DIR / "question_schema.json", build_question_schema())
    write_json(RULES_DIR / "competency_types.json", build_competency_types())
    write_summary(exam_scope, learning, blueprint, targets)

    print(
        json.dumps(
            {
                "exam_scope_detail_rows": exam_scope["counts"]["detail_rows"],
                "learning_objectives": learning["counts"]["objectives"],
                "question_generation_targets": targets["counts"]["targets"],
                "blueprint_ocr_pages": blueprint["ocr_status"]["pages_processed"],
                "blueprint_total_questions": blueprint.get("totals", {}).get("total_questions"),
                "verified_scope_rows": exam_scope["counts"].get("verified_detail_rows", 0),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
