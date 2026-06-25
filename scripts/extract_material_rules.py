from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
MATERIALS_DIR = ROOT / "materials"
EXTRACTED_DIR = ROOT / "resources" / "extracted"
RULES_DIR = ROOT / "resources" / "rules"
DOCS_DIR = ROOT / "docs" / "project_rules"


SOURCE_TYPES = {
    "01_question_guidelines": "question_guideline",
    "02_exam_scope": "exam_scope",
    "03_item_design": "item_design",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_cell(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"(.+?)\s+(\d+\.)\s+(.+)", text)
    if match and not text.startswith(match.group(2)):
        text = f"{match.group(2)} {match.group(1)} {match.group(3)}"
    return text.strip()


def slugify(path: Path) -> str:
    stem = unicodedata.normalize("NFC", path.stem)
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", stem).strip("_")
    return f"{slug[:80]}_{digest}"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


@dataclass
class PdfMaterial:
    path: Path
    source_type: str
    slug: str


def discover_materials() -> list[PdfMaterial]:
    materials: list[PdfMaterial] = []
    for pdf in sorted(MATERIALS_DIR.rglob("*.pdf")):
        parent_name = pdf.parent.name
        materials.append(
            PdfMaterial(
                path=pdf,
                source_type=SOURCE_TYPES.get(parent_name, "unknown"),
                slug=slugify(pdf),
            )
        )
    return materials


def extract_pages(materials: list[PdfMaterial]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []

    for material in materials:
        doc = fitz.open(material.path)
        page_char_counts: list[int] = []
        for index, page in enumerate(doc, start=1):
            text = normalize_text(page.get_text("text"))
            blocks = page.get_text("blocks")
            text_blocks = []
            for block in blocks:
                if len(block) >= 5:
                    block_text = normalize_text(block[4])
                    if block_text:
                        text_blocks.append(
                            {
                                "bbox": [round(float(x), 2) for x in block[:4]],
                                "text": block_text,
                            }
                        )
            page_char_counts.append(len(text))
            pages.append(
                {
                    "source_file": str(material.path.relative_to(ROOT)),
                    "source_slug": material.slug,
                    "source_type": material.source_type,
                    "page": index,
                    "char_count": len(text),
                    "needs_ocr_review": len(text) < 40,
                    "text": text,
                    "blocks": text_blocks,
                }
            )

        inventory.append(
            {
                "source_file": str(material.path.relative_to(ROOT)),
                "source_slug": material.slug,
                "source_type": material.source_type,
                "page_count": doc.page_count,
                "file_size_bytes": material.path.stat().st_size,
                "text_pages": sum(1 for count in page_char_counts if count >= 40),
                "low_text_pages": [i + 1 for i, count in enumerate(page_char_counts) if count < 40],
                "total_text_chars": sum(page_char_counts),
            }
        )

    return inventory, pages


def extract_tables(materials: list[PdfMaterial]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    csv_dir = EXTRACTED_DIR / "tables_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    for material in materials:
        with pdfplumber.open(material.path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []
                for table_index, table in enumerate(tables, start=1):
                    rows = [[clean_cell(cell) for cell in row] for row in table if row]
                    if not rows:
                        continue
                    csv_name = f"{material.slug}_p{page_index:03d}_t{table_index:02d}.csv"
                    csv_path = csv_dir / csv_name
                    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)
                    extracted.append(
                        {
                            "source_file": str(material.path.relative_to(ROOT)),
                            "source_slug": material.slug,
                            "source_type": material.source_type,
                            "page": page_index,
                            "table_index": table_index,
                            "row_count": len(rows),
                            "column_count": max(len(row) for row in rows),
                            "csv_file": str(csv_path.relative_to(ROOT)),
                            "rows": rows,
                        }
                    )

    return extracted


def build_markdown_pages(pages: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        grouped.setdefault(page["source_slug"], []).append(page)

    md_dir = EXTRACTED_DIR / "markdown"
    for source_slug, source_pages in grouped.items():
        title = source_pages[0]["source_file"]
        lines = [f"# {title}", ""]
        for page in source_pages:
            lines.append(f"## Page {page['page']}")
            lines.append("")
            lines.append(page["text"] or "[TEXT_EXTRACTION_EMPTY]")
            lines.append("")
        write_text(md_dir / f"{source_slug}.md", "\n".join(lines))


def parse_exam_scope(tables: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    subjects: dict[str, dict[str, Any]] = {}
    current = {"subject": "", "field": "", "area": ""}

    for table in tables:
        if table["source_type"] != "exam_scope":
            continue
        if not table["rows"]:
            continue
        header = (table["rows"][0] + ["", "", "", ""])[:4]
        if header != ["시험과목", "분야", "영역", "세부영역"]:
            continue
        for row in table["rows"]:
            if len(row) < 4 or row[:4] == ["시험과목", "분야", "영역", "세부영역"]:
                continue
            cells = (row + ["", "", "", ""])[:4]
            subject, field, area, detail = [clean_cell(cell) for cell in cells]
            if subject:
                current["subject"] = subject
                current["field"] = ""
                current["area"] = ""
            if field:
                current["field"] = field
                current["area"] = ""
            if area:
                current["area"] = area
            if not any([subject, field, area, detail]):
                continue
            record = {
                "subject": current["subject"],
                "field": current["field"],
                "area": current["area"],
                "detail": detail,
                "source_page": table["page"],
            }
            rows.append(record)

            if current["subject"]:
                subject_node = subjects.setdefault(
                    current["subject"],
                    {"name": current["subject"], "fields": {}},
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
                                {"name": detail, "source_page": table["page"]}
                            )

    def compact_subject(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": node["name"],
            "fields": [
                {
                    "name": field["name"],
                    "areas": [
                        {
                            "name": area["name"],
                            "details": area["details"],
                        }
                        for area in field["areas"].values()
                    ],
                }
                for field in node["fields"].values()
            ],
        }

    return {
        "source": "materials/02_exam_scope",
        "scope_version": "2022년도 제50회 방사선사 국가시험부터 별도 공지 시까지",
        "exam_format": {
            "type": "객관식 5지 선다형",
            "total_questions": 250,
            "score_per_question": 1,
            "total_minutes": 215,
            "period_distribution": {
                "1교시": {"subjects": ["방사선이론", "의료관계법규"], "questions": 110, "minutes": 90},
                "2교시": {"subjects": ["방사선응용"], "questions": 90, "minutes": 75},
                "3교시": {"subjects": ["실기시험"], "questions": 50, "minutes": 50},
            },
            "mvp_included_periods": ["1교시", "2교시"],
            "mvp_excluded_periods": ["3교시 실기시험"],
        },
        "subjects": [compact_subject(subject) for subject in subjects.values()],
        "flat_rows": rows,
    }


def text_contains(pages: list[dict[str, Any]], source_type: str, keywords: list[str]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for page in pages:
        if page["source_type"] != source_type:
            continue
        text = page["text"]
        if any(keyword in text for keyword in keywords):
            hits.append(
                {
                    "page": page["page"],
                    "keywords": [keyword for keyword in keywords if keyword in text],
                    "excerpt": text[:600],
                }
            )
    return hits


def build_rule_sets(pages: list[dict[str, Any]], exam_scope: dict[str, Any]) -> dict[str, Any]:
    difficulty_rubric = {
        "하": {
            "definition": "정의, 명칭, 기본 특성처럼 단일 개념을 확인한다.",
            "allowed_question_types": ["개념형", "법규형"],
            "avoid": ["복합 계산", "여러 조건을 동시에 판단하는 임상 사례"],
        },
        "중": {
            "definition": "개념 간 관계, 절차 순서, 조건 변화에 따른 결과를 이해해야 한다.",
            "allowed_question_types": ["개념형", "계산형", "응용형", "법규형"],
            "avoid": ["근거 자료에 없는 임상 예외 상황"],
        },
        "상": {
            "definition": "계산 적용, 임상 상황 판단, 복합 개념 비교가 필요하다.",
            "allowed_question_types": ["계산형", "응용형", "사례형"],
            "avoid": ["근거가 불충분한 고난도 추론", "영상 판독 중심 문항"],
        },
    }

    question_type_rules = {
        "개념형": {
            "purpose": "용어, 원리, 특성, 비교 관계를 확인한다.",
            "evidence_requirement": "근거 chunk에 정답 근거와 오답 배제 근거가 있어야 한다.",
            "good_for": ["방사선물리", "방사선생물", "의료영상정보", "법규 기본 개념"],
        },
        "계산형": {
            "purpose": "공식과 단위를 실제 수치에 적용한다.",
            "evidence_requirement": "공식, 변수 의미, 단위 변환 기준이 근거에 있어야 한다.",
            "good_for": ["역제곱법칙", "반가층", "선량", "확대율", "전기전자"],
        },
        "법규형": {
            "purpose": "법령명, 조문, 시행령/시행규칙 기준을 확인한다.",
            "evidence_requirement": "법령명, 조문 번호, 기준일을 함께 저장해야 한다.",
            "good_for": ["의료법", "의료기사 등에 관한 법률", "지역보건법"],
        },
        "응용형": {
            "purpose": "검사 목적, 검사 방법, 영상 결과, 안전관리 판단을 연결한다.",
            "evidence_requirement": "절차와 판단 기준이 같은 단원 또는 인접 근거에 있어야 한다.",
            "good_for": ["CT", "MRI", "초음파", "핵의학", "방사선치료", "투시조영"],
        },
    }

    question_language_rules = {
        "core_principles": [
            "국가시험 문항은 공공 언어이므로 어문 규범에 맞고 간결하며 명확해야 한다.",
            "전문 용어는 필요한 경우 한글 명칭을 앞세우고 괄호 안에 원어 또는 약어를 병기한다.",
            "수식 관계가 모호한 문장은 쉼표, 반복 명사, 어순 조정으로 의미 범위를 분명히 한다.",
            "지시문과 답지의 문법 형식이 맞아야 하며, 답지가 섞인 조합형이면 지시문도 그 구조를 반영해야 한다.",
            "고압적, 비객관적, 외국어 남용 표현을 피한다.",
        ],
        "checklist": [
            "묻는 대상이 하나로 해석되는가",
            "정답 선택 조건이 지시문에 충분히 들어 있는가",
            "부정형 문항이면 부정 표현이 눈에 띄고 오해 여지가 없는가",
            "보기의 문장 길이와 문법 구조가 지나치게 불균형하지 않은가",
            "한글 용어와 영문 약어 병기가 일관되는가",
            "불필요한 수식어와 중복 표현을 제거했는가",
        ],
        "forbidden_or_risky_patterns": [
            "근거 없이 '항상', '반드시', '절대'를 사용하는 표현",
            "무엇을 고르라는 것인지 불명확한 '관련된 것은?'",
            "둘 이상의 해석이 가능한 수식 구조",
            "정답만 지나치게 길거나 구체적인 보기",
            "한글 설명 없이 영문 약어만 제시하는 전문 용어",
        ],
        "source_evidence_pages": text_contains(
            pages,
            "question_guideline",
            ["명확", "간결", "전문 용어", "지시문", "답지", "한글"],
        )[:20],
    }

    item_design_rules = {
        "must_have_metadata": [
            "교시",
            "대과목",
            "세부 과목",
            "출제범위 코드",
            "학습목표 등급",
            "역량 유형",
            "난이도",
            "문항 유형",
            "정답 근거",
            "오답 설계 원리",
        ],
        "period_design": {
            "1교시": {
                "subjects": ["방사선이론", "의료관계법규"],
                "composition": "방사선이론 90문항 + 의료관계법규 20문항",
                "focus": "기초 이론, 안전, 계측, 생물, 관리, 법규",
            },
            "2교시": {
                "subjects": ["방사선응용"],
                "composition": "방사선응용 90문항",
                "focus": "임상 검사 절차, 영상 특성, 장비 조건, 환자 안전",
            },
        },
        "mvp_policy": [
            "3교시 영상 기반 실기 문항은 초기 범위에서 제외한다.",
            "공식 출제범위 밖의 문항은 생성하지 않는다.",
            "공식 비율이 없는 세부 문항 유형 비중은 권장 비중으로만 저장하고 공식 기준으로 표시하지 않는다.",
            "법규 문항은 법령명, 조문, 기준일을 필수로 저장한다.",
        ],
        "source_evidence_pages": text_contains(
            pages,
            "item_design",
            ["메타데이터", "1교시", "2교시", "난이도", "문항 유형", "법규", "출제기준"],
        )[:20],
    }

    validation_checklist = {
        "format": [
            "5지선다형 보기 5개가 있는가",
            "정답이 정확히 1개인가",
            "문항, 보기, 정답, 해설, 출처, 난이도, 문항 유형이 모두 있는가",
            "JSON schema를 통과하는가",
        ],
        "scope": [
            "문항의 교시/과목/분야/영역/세부영역이 출제범위 JSON에 존재하는가",
            "1·2교시 MVP 범위를 벗어나지 않는가",
            "법규 문항이면 법령 기준일과 조문 정보가 있는가",
        ],
        "evidence": [
            "해설이 근거 chunk로 설명되는가",
            "근거에 없는 수치, 법령, 예외 조건을 만들지 않았는가",
            "출처 파일과 페이지가 연결되는가",
        ],
        "hallucination": [
            "문제는 RAG 검색 결과를 근거로 생성되었는가",
            "RAG 근거에 없는 내용을 추가 생성하지 않았는가",
            "해설은 근거 문서에 포함된 내용만 사용했는가",
            "수치, 단위, 공식은 원문과 비교 검증되었는가",
            "법규 문항은 존재하는 법령명, 조문번호, 기준일만 사용했는가",
        ],
        "answer_quality": [
            "오답 중 정답으로 해석될 수 있는 보기가 없는가",
            "보기끼리 의미가 중복되지 않는가",
            "정답만 문장 길이, 구체성, 표현 방식에서 튀지 않는가",
        ],
        "language": question_language_rules["checklist"],
    }

    generation_policy = {
        "project_goal": "방사선사 국가시험 1·2교시 텍스트 기반 5지선다형 문제를 근거 중심으로 생성한다.",
        "source_priority": [
            "국시원 공식 출제범위",
            "업로드된 문항 설계 자료",
            "업로드된 문항 국문법/표현 기준",
            "저작권과 사용 권한이 확인된 전공 자료",
            "기준일이 확인된 법령 원문",
        ],
        "generation_order": [
            "출제범위 선택",
            "학습목표 또는 세부영역 선택",
            "근거 chunk 검색",
            "문항 유형과 난이도 지정",
            "문항/보기/정답/해설 생성",
            "형식/범위/근거/정답/언어/저작권 검증",
            "검증 에이전트와 Harness 통과 시 reviewed 상태로 전환",
            "reviewed 또는 approved 상태만 최종 문제 DB에 저장",
        ],
        "hallucination_prevention": [
            "문제는 반드시 RAG 검색 결과를 근거로 생성한다.",
            "RAG 근거에 없는 내용을 추가 생성하지 않는다.",
            "해설은 근거 문서에 포함된 내용만 사용한다.",
            "법규 문제는 반드시 법령명, 조문번호, 기준일을 저장한다.",
            "수치, 단위, 공식은 원문과 비교 검증한다.",
            "학습목표와 출제범위를 벗어난 문제는 생성하지 않는다.",
        ],
        "never_do": [
            "기출문제나 시중 모의고사 원문을 생성 근거로 복제하지 않는다.",
            "모델의 사전지식만으로 문제를 생성하지 않는다.",
            "근거 chunk에 없는 지식을 정답 근거로 사용하지 않는다.",
            "근거 없는 해설을 생성하지 않는다.",
            "존재하지 않는 법령 조문, 수치, 단위, 공식을 생성하지 않는다.",
            "3교시 영상 판독 문항을 MVP에 포함하지 않는다.",
            "법규 기준일이 없는 법규 문제를 승인하지 않는다.",
            "원문을 대체하는 2차 PDF를 생성하지 않는다.",
        ],
    }

    return {
        "exam_scope": exam_scope,
        "item_design_rules": item_design_rules,
        "question_type_rules": question_type_rules,
        "difficulty_rubric": difficulty_rubric,
        "question_language_rules": question_language_rules,
        "validation_checklist": validation_checklist,
        "generation_policy": generation_policy,
    }


def markdown_summary(title: str, bullets: list[str]) -> str:
    lines = [f"# {title}", ""]
    for bullet in bullets:
        lines.append(f"- {bullet}")
    return "\n".join(lines)


def write_docs(rule_sets: dict[str, Any], inventory: list[dict[str, Any]]) -> None:
    write_text(
        DOCS_DIR / "00_materials_inventory.md",
        "# Materials Inventory\n\n"
        + "\n".join(
            [
                f"- `{item['source_file']}`: {item['page_count']} pages, "
                f"{item['total_text_chars']} text chars, low-text pages {item['low_text_pages']}"
                for item in inventory
            ]
        ),
    )
    exam_scope = rule_sets["exam_scope"]
    scope_lines = [
        f"적용 범위: {exam_scope['scope_version']}",
        f"시험 형식: {exam_scope['exam_format']['type']}, 총 {exam_scope['exam_format']['total_questions']}문항",
        "MVP 포함: 1교시, 2교시",
        "MVP 제외: 3교시 실기시험",
    ]
    for subject in exam_scope["subjects"]:
        scope_lines.append(f"{subject['name']}: {len(subject['fields'])}개 분야")
    write_text(DOCS_DIR / "01_exam_scope_summary.md", markdown_summary("Exam Scope Summary", scope_lines))

    design = rule_sets["item_design_rules"]
    write_text(
        DOCS_DIR / "02_item_design_summary.md",
        markdown_summary(
            "Item Design Summary",
            [
                "문항마다 교시, 대과목, 세부 과목, 출제범위 코드, 학습목표 등급, 역량 유형, 난이도, 문항 유형, 정답 근거, 오답 설계 원리를 저장한다.",
                "1교시는 방사선이론과 의료관계법규를 분리 관리한다.",
                "2교시는 검사 목적, 검사 방법, 영상 결과, 환자 안전을 연결하는 응용형 설계를 우선한다.",
                "세부 문항 유형 비율은 공식 기준으로 취급하지 않고 권장 설계값으로만 사용한다.",
            ]
            + design["mvp_policy"],
        ),
    )

    language = rule_sets["question_language_rules"]
    write_text(
        DOCS_DIR / "03_question_language_summary.md",
        markdown_summary("Question Language Summary", language["core_principles"] + language["checklist"]),
    )

    policy = rule_sets["generation_policy"]
    write_text(
        DOCS_DIR / "04_generation_policy.md",
        markdown_summary(
            "Generation Policy",
            [policy["project_goal"]]
            + [f"Source priority: {item}" for item in policy["source_priority"]]
            + [f"Step: {item}" for item in policy["generation_order"]]
            + [f"Hallucination prevention: {item}" for item in policy.get("hallucination_prevention", [])]
            + [f"Never: {item}" for item in policy["never_do"]],
        ),
    )

    checklist = rule_sets["validation_checklist"]
    lines = ["# Validation Checklist", ""]
    for group, items in checklist.items():
        lines.append(f"## {group}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    write_text(DOCS_DIR / "05_validation_checklist.md", "\n".join(lines))


def main() -> None:
    materials = discover_materials()
    inventory, pages = extract_pages(materials)
    tables = extract_tables(materials)
    build_markdown_pages(pages)

    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    write_json(EXTRACTED_DIR / "materials_inventory.json", inventory)
    with (EXTRACTED_DIR / "pages.jsonl").open("w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")
    write_json(EXTRACTED_DIR / "tables.json", tables)

    exam_scope = parse_exam_scope(tables)
    rule_sets = build_rule_sets(pages, exam_scope)

    write_json(RULES_DIR / "exam_scope.json", rule_sets["exam_scope"])
    write_json(RULES_DIR / "item_design_rules.json", rule_sets["item_design_rules"])
    write_json(RULES_DIR / "question_type_rules.json", rule_sets["question_type_rules"])
    write_json(RULES_DIR / "difficulty_rubric.json", rule_sets["difficulty_rubric"])
    write_json(RULES_DIR / "question_language_rules.json", rule_sets["question_language_rules"])
    write_json(RULES_DIR / "validation_checklist.json", rule_sets["validation_checklist"])
    write_json(RULES_DIR / "generation_policy.json", rule_sets["generation_policy"])
    write_docs(rule_sets, inventory)

    print(json.dumps({"materials": len(materials), "pages": len(pages), "tables": len(tables)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
