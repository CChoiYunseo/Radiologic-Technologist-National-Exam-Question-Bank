from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "resources" / "rules"
DOCS_DIR = ROOT / "docs" / "project_rules"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def evidence(source_type: str, pages: list[int], note: str) -> dict[str, Any]:
    return {"source_type": source_type, "pages": pages, "note": note}


def rule(
    rule_id: str,
    category: str,
    name: str,
    requirement: str,
    check: str,
    severity: str,
    source_pages: list[int],
    examples: list[str] | None = None,
    auto_check_hint: str | None = None,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "category": category,
        "name": name,
        "requirement": requirement,
        "check": check,
        "severity": severity,
        "examples": examples or [],
        "auto_check_hint": auto_check_hint or "",
        "evidence": evidence("question_guideline", source_pages, name),
    }


def build_question_language_rulebook() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "보건의료인 국가시험 문항의 표기, 표현, 명료성, 품위 기준을 문제 생성 및 검증에 반영한다.",
        "source": "materials/01_question_guidelines",
        "categories": [
            {
                "id": "legal_notation",
                "name": "법에 맞게",
                "scope": "공공 언어, 한글 우선 표기, 단위 표기, 전문어 병기",
                "source_pages": [5, 7, 8, 9, 10, 11, 12, 13],
            },
            {
                "id": "accuracy",
                "name": "정확하게",
                "scope": "어문 규범, 어휘 선택, 조사, 피동, 사동, 시제, 병렬, 호응, 어순",
                "source_pages": list(range(14, 61)),
            },
            {
                "id": "clarity",
                "name": "명료하게",
                "scope": "중의적 표현, 부적절한 수식, 지시문과 답지의 조화",
                "source_pages": list(range(61, 79)),
            },
            {
                "id": "sufficiency",
                "name": "충분하게",
                "scope": "문항 이해에 필요한 주어, 목적어, 행위자, 조건, 대상 보충",
                "source_pages": list(range(79, 84)),
            },
            {
                "id": "naturalness",
                "name": "자연스럽게",
                "scope": "국어 기본 문형, 외국어투, 명사문 회피",
                "source_pages": list(range(84, 95)),
            },
            {
                "id": "plain_language",
                "name": "쉽게",
                "scope": "어려운 전문어, 일반어, 약어를 이해하기 쉽게 표현",
                "source_pages": list(range(95, 100)),
            },
            {
                "id": "conciseness",
                "name": "간결하게",
                "scope": "문제 해결에 불필요한 어구와 중복 표현 제거",
                "source_pages": list(range(100, 107)),
            },
            {
                "id": "dignity_consistency",
                "name": "그 밖에",
                "scope": "고압적, 비격식, 비객관적, 외국어 남용, 비일관적 표현 방지",
                "source_pages": list(range(107, 118)),
            },
        ],
        "rules": [
            rule(
                "QL-001",
                "legal_notation",
                "한글 우선 표기와 원어 병기",
                "문항은 한글을 우선하고, 뜻 전달에 필요한 경우에만 괄호 안에 한자·영문·약어를 병기한다.",
                "전문 용어 또는 약어가 처음 나올 때 한글 설명 없이 외국 문자만 제시하지 않았는지 확인한다.",
                "error",
                [5, 7, 8, 9, 10, 13],
                ["자기공명(MR, magnetic resonance)", "리보핵산(RNA, ribonucleic acid)"],
                "영문 대문자 약어가 있고 괄호/한글 설명이 없으면 경고한다.",
            ),
            rule(
                "QL-002",
                "legal_notation",
                "단위 표기",
                "수치와 단위 기호 사이의 띄어쓰기 원칙을 지키되, 한글 단위 기호, ℃, % 등은 국시원 표기 관례에 맞춘다.",
                "수치+단위 표기가 일관적인지 확인한다.",
                "warning",
                [11, 12, 13],
                ["6 kg", "37℃", "10%"],
                "정규식으로 숫자와 단위 사이 공백 및 예외 단위를 검사한다.",
            ),
            rule(
                "QL-003",
                "accuracy",
                "어문 규범 준수",
                "한글 맞춤법, 표준어, 외래어 표기, 로마자 표기, 문장 부호 규정을 따른다.",
                "비표준어, 틀린 두음법칙, 사이시옷, 띄어쓰기, 문장부호 오류가 없는지 확인한다.",
                "error",
                [14, 15, 16, 17, 18, 19, 23, 24, 25, 26, 30],
                ["한랭손상", "젖양", "부서 간", "「혈액관리법」상"],
            ),
            rule(
                "QL-004",
                "accuracy",
                "정확한 어휘 선택",
                "출제 의도에 맞는 단어를 선택하고, 유사어를 혼동하지 않는다.",
                "완전/완료, 전부/전체, 시행/실시/실행 등 뜻이 다른 단어를 구분했는지 확인한다.",
                "warning",
                [31, 32, 33, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128],
                ["작성을 완료해야 하는 때", "의치상 전체"],
            ),
            rule(
                "QL-005",
                "accuracy",
                "조사 선택",
                "조사는 앞말과 서술어의 관계를 정확히 드러내도록 선택한다.",
                "사람/동물 대상에는 '에게', 장소/사물에는 '에'처럼 대상과 의미에 맞는 조사를 썼는지 확인한다.",
                "warning",
                [35, 36, 37, 38, 39, 40, 128, 129],
                ["환자에게", "혀에서 맛을 담당하는 신경"],
            ),
            rule(
                "QL-006",
                "accuracy",
                "피동 표현 절제",
                "불필요한 피동, '-에 의하여', '-어지다', '-되다' 중첩을 피하고 능동문을 우선한다.",
                "행위 주체와 목적어가 분명한데 피동으로 돌려 쓴 표현이 없는지 확인한다.",
                "warning",
                [41, 42, 43],
                ["펩신이 분해하는 영양소"],
                "'에 의해', '의해서', '되어지' 패턴을 경고한다.",
            ),
            rule(
                "QL-007",
                "accuracy",
                "사동 표현 절제",
                "'-시키다'는 실제로 누군가에게 하게 만드는 경우에만 쓰고, 자체로 사동 의미가 있는 단어에는 붙이지 않는다.",
                "향상시키다, 개선시키다, 감소시키다처럼 불필요한 사동이 없는지 확인한다.",
                "warning",
                [44, 45],
                ["향상하다", "개선하다", "낮추다"],
                "'시키' 포함 단어를 검토 대상으로 표시한다.",
            ),
            rule(
                "QL-008",
                "accuracy",
                "시제 일관성",
                "사례형 문항에서 과거 병력, 현재 증상, 앞으로의 처치 판단 시제가 일관되어야 한다.",
                "문항이 현재 판단을 요구하는데 과거형만 나열해 의미가 어색하지 않은지 확인한다.",
                "warning",
                [46, 47],
            ),
            rule(
                "QL-009",
                "accuracy",
                "병렬 구조 일치",
                "나열된 보기와 지시문의 품사, 문장 구조, 의미 층위가 서로 맞아야 한다.",
                "보기 일부는 명사구, 일부는 문장처럼 섞여 있지 않은지 확인한다.",
                "warning",
                [48, 49, 50],
            ),
            rule(
                "QL-010",
                "accuracy",
                "호응",
                "주어-서술어, 목적어-서술어, 부사어-서술어가 문법적으로 호응해야 한다.",
                "생략된 주어/목적어 때문에 누가 무엇을 하는지 불명확하지 않은지 확인한다.",
                "error",
                [51, 52, 53, 54, 55, 56],
            ),
            rule(
                "QL-011",
                "accuracy",
                "어순",
                "긴 수식어나 부사구 때문에 의미가 흐려지지 않도록 가까이 있어야 할 성분은 가까이 둔다.",
                "수식어가 어떤 단어를 꾸미는지 명확한지 확인한다.",
                "warning",
                [57, 58, 59, 60],
            ),
            rule(
                "QL-012",
                "clarity",
                "중의적 표현 금지",
                "하나의 문장이나 어구가 둘 이상으로 해석되면 안 된다.",
                "행위 주체, 대상, 수식 범위, 시간/조건이 한 가지로 해석되는지 확인한다.",
                "error",
                [61, 62, 63, 64],
                ["정관절제술을 받은 남편과 그 부인"],
            ),
            rule(
                "QL-013",
                "clarity",
                "수식 관계 명확화",
                "수식어와 피수식어의 관계가 구조상 조화해야 한다.",
                "형용사나 관형어가 엉뚱한 명사나 결과를 꾸미지 않는지 확인한다.",
                "warning",
                [65, 66, 67],
            ),
            rule(
                "QL-014",
                "clarity",
                "지시문 내용과 표현 일치",
                "지시문은 실제로 묻는 대상과 선택 기준을 분명히 표현해야 한다.",
                "'설명으로 옳은 것은?'이 실제로 설명형 보기를 요구하는지 확인한다.",
                "error",
                [68, 69, 70, 71],
            ),
            rule(
                "QL-015",
                "clarity",
                "지시문과 답지 조화",
                "답지에 둘 이상의 항목을 묶거나 짝지어 제시하면 지시문도 그 구조를 반영해야 한다.",
                "보기 형식이 조합형/연결형이면 '옳게 짝지은 것은' 등으로 묻는지 확인한다.",
                "error",
                [72, 73, 74, 75, 76, 77, 78],
                ["임신 금기약으로만 묶인 것은", "옳게 연결한 것은"],
            ),
            rule(
                "QL-016",
                "sufficiency",
                "필수 정보 보충",
                "수험생이 지시문과 답지를 이해하는 데 필요한 행위자, 대상, 조건, 기준이 빠지면 안 된다.",
                "누가, 무엇을, 누구에게, 어떤 조건에서, 무엇으로 판단하는지 확인한다.",
                "error",
                [79, 80, 81, 82, 83],
            ),
            rule(
                "QL-017",
                "naturalness",
                "국어 기본 문형",
                "주어, 목적어, 부사어, 서술어를 서술어 성질에 맞춰 배치한다.",
                "명사 나열이나 조사 생략 때문에 국어 문장성이 떨어지지 않는지 확인한다.",
                "warning",
                [84, 85, 86, 87, 88, 89],
            ),
            rule(
                "QL-018",
                "naturalness",
                "외국어투 회피",
                "영어식 수동태, '가지다', 명사문식 표현을 자연스러운 국어 표현으로 바꾼다.",
                "가지다, -에 의해, -에 대한, 명사+목적 등 외국어투가 불필요하게 쓰이지 않았는지 확인한다.",
                "warning",
                [90, 91, 92, 93, 94],
            ),
            rule(
                "QL-019",
                "plain_language",
                "쉬운 말 우선",
                "일반화되지 않은 약어와 어려운 전문어는 가능한 쉬운 말 또는 한글 설명으로 바꾼다.",
                "전문어를 써야 하면 한글 설명, 원어, 약어를 일관되게 제시한다.",
                "warning",
                [95, 96, 97, 98, 99, 118, 119, 120, 121, 122],
            ),
            rule(
                "QL-020",
                "conciseness",
                "불필요한 정보 제거",
                "문제 해결에 필요하지 않은 배경, 중복 수식, 장황한 표현은 제거한다.",
                "삭제해도 정답 판단에 영향이 없는 문구가 없는지 확인한다.",
                "warning",
                [100, 101, 102, 103, 104, 105, 106, 110],
            ),
            rule(
                "QL-021",
                "dignity_consistency",
                "고압적 표현 금지",
                "수험자나 환자에게 감정적, 위압적, 명령조로 읽히는 표현을 피한다.",
                "지시하였다, 명령하였다 등 고압적 표현이 필요한 맥락인지 확인한다.",
                "warning",
                [107, 111, 117],
            ),
            rule(
                "QL-022",
                "dignity_consistency",
                "비격식 표현 금지",
                "공식 시험 문항에서는 일상어보다 공식 명칭을 우선한다.",
                "해썹 등 비격식 명칭이 공식 명칭과 병기되거나 대체되었는지 확인한다.",
                "warning",
                [108, 112, 117],
            ),
            rule(
                "QL-023",
                "dignity_consistency",
                "비객관적 표현 금지",
                "부정적 사건이나 지역·집단을 불필요하게 특정해 편견을 만들지 않는다.",
                "특정 지역, 집단, 개인을 근거 없이 부정적으로 드러내지 않았는지 확인한다.",
                "error",
                [109, 113, 117],
            ),
            rule(
                "QL-024",
                "dignity_consistency",
                "외국어 남용 금지",
                "대체 가능한 우리말이 있으면 외국어만 단독으로 쓰지 않는다.",
                "외국어/외래어가 일반 전문어인지, 대체어가 있는지 확인한다.",
                "warning",
                [109, 110, 113, 114, 115, 117],
            ),
            rule(
                "QL-025",
                "dignity_consistency",
                "보기 표현 일관성",
                "한 문항 안에서 보기의 용어, 문체, 문장 형식, 표기 방식이 일관되어야 한다.",
                "정답 보기만 표현 형식이 다르거나 더 구체적이지 않은지 확인한다.",
                "warning",
                [116, 117],
            ),
        ],
    }


def build_item_design_rulebook() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "방사선사 국가고시 문제은행의 문항 메타데이터, 교시별 설계, 유형, 검증 원칙을 고정한다.",
        "source": "materials/03_item_design",
        "rules": [
            {
                "id": "ID-001",
                "name": "공식 출제범위 필수 반영",
                "requirement": "모든 문항은 국시원 출제범위와 법령상 시험과목 체계에 연결되어야 한다.",
                "severity": "error",
                "evidence": evidence("item_design", [1, 22, 23], "공식 출제범위 기반"),
            },
            {
                "id": "ID-002",
                "name": "문항 메타데이터 필수",
                "requirement": "교시, 대과목, 세부 과목, 출제범위 코드, 학습목표 등급, 역량 유형, 난이도, 문항 유형, 정답 근거, 오답 설계 원리를 저장한다.",
                "severity": "error",
                "evidence": evidence("item_design", [2, 23], "문항 메타데이터화"),
            },
            {
                "id": "ID-003",
                "name": "1교시 구성",
                "requirement": "1교시는 방사선이론 90문항과 의료관계법규 20문항의 성격을 분리해 설계한다.",
                "severity": "error",
                "evidence": evidence("item_design", [3, 4, 5, 6], "1교시 구성"),
            },
            {
                "id": "ID-004",
                "name": "의료관계법규 최신성",
                "requirement": "법규 문항은 법령명, 조문 번호, 시행령/시행규칙 구분, 조문 기준일, 개정 이력을 관리한다.",
                "severity": "error",
                "evidence": evidence("item_design", [6, 23], "법규 최신성 관리"),
            },
            {
                "id": "ID-005",
                "name": "2교시 임상 응용 중심",
                "requirement": "2교시는 검사 목적, 검사 방법, 영상 결과, 환자 안전을 연결하는 응용형 문항을 우선한다.",
                "severity": "warning",
                "evidence": evidence("item_design", [7, 8, 9, 22, 23], "2교시 임상응용"),
            },
            {
                "id": "ID-006",
                "name": "공식 비율 오인 금지",
                "requirement": "공식 공개 비율이 없는 문항 유형/영상 유형 비중은 권장 비중으로만 저장하고 공식 기준으로 표시하지 않는다.",
                "severity": "error",
                "evidence": evidence("item_design", [5, 10], "권장 비중과 공식 비율 구분"),
            },
            {
                "id": "ID-007",
                "name": "3교시 MVP 제외",
                "requirement": "현재 MVP에서는 3교시 영상 기반 문항을 생성·승인하지 않는다.",
                "severity": "error",
                "evidence": evidence("item_design", [10, 11, 19, 20, 21], "3교시 영상 문항은 이후 확장"),
            },
            {
                "id": "ID-008",
                "name": "정답 명확성과 오답 타당성",
                "requirement": "복수정답 가능성이 없어야 하며, 오답은 너무 터무니없지 않고 흔한 혼동을 반영해야 한다.",
                "severity": "error",
                "evidence": evidence("item_design", [22], "문항 검증 기준"),
            },
        ],
        "required_metadata": [
            {"name": "period", "label": "교시", "required": True},
            {"name": "subject", "label": "대과목", "required": True},
            {"name": "sub_subject", "label": "세부 과목", "required": True},
            {"name": "scope_code", "label": "출제범위 코드", "required": True},
            {"name": "learning_objective_level", "label": "학습목표 등급", "required": False},
            {"name": "competency_type", "label": "역량 유형", "required": True},
            {"name": "difficulty", "label": "난이도", "required": True},
            {"name": "question_type", "label": "문항 유형", "required": True},
            {"name": "evidence_refs", "label": "정답 근거", "required": True},
            {"name": "distractor_strategy", "label": "오답 설계 원리", "required": True},
        ],
        "period_templates": {
            "1교시": {
                "subjects": ["방사선이론", "의료관계법규"],
                "allowed_question_types": ["개념형", "계산형", "비교형", "법규형"],
                "design_focus": ["기초 개념", "선량/계측 계산", "방사선 안전", "법령 조문"],
                "recommended_mix_note": "개념 이해형 중심, 계산 적용형과 사례 판단형 보완. 공식 비율로 취급하지 않는다.",
            },
            "2교시": {
                "subjects": ["방사선응용"],
                "allowed_question_types": ["임상검사 절차형", "검사조건 판단형", "안전관리형", "응용형"],
                "design_focus": ["검사 목적", "검사 방법", "영상 결과", "환자 안전"],
                "recommended_mix_note": "단순 암기보다 임상 응용 흐름을 우선한다.",
            },
            "3교시": {
                "subjects": ["실기시험"],
                "allowed_question_types": ["영상판독형", "장비식별형", "영상품질평가형", "artifact 판단형"],
                "design_focus": ["영상", "장비", "품질관리"],
                "mvp_status": "excluded",
            },
        },
    }


def infer_strategy(subject: str, field: str, area: str, detail: str) -> dict[str, Any]:
    text = " ".join([subject, field, area, detail])
    question_types: list[str] = ["개념형"]
    competencies: list[str] = ["지식 이해"]
    difficulty = ["하", "중"]
    evidence = ["전공 자료", "출제범위"]

    if any(k in text for k in ["계측", "선량", "반가층", "감쇠", "전기", "회로", "변압", "확대", "해상도"]):
        question_types += ["계산형", "비교형"]
        competencies += ["계산 적용"]
        difficulty = ["중", "상"]
    if any(k in text for k in ["의료법", "의료기사", "지역보건법", "법률", "면허", "벌칙", "자격정지"]):
        question_types = ["법규형", "개념형"]
        competencies = ["법령 이해", "조문 적용"]
        evidence = ["법령 원문", "출제범위"]
    if any(k in text for k in ["영상", "검사", "CT", "전산화단층", "자기공명", "MRI", "초음파", "핵의학", "치료", "조영", "중재"]):
        question_types += ["응용형", "절차형", "안전관리형"]
        competencies += ["임상 절차 판단", "검사 조건 판단", "안전관리 판단"]
        difficulty = ["중", "상"]
    if any(k in text for k in ["생물", "관리", "방어", "차폐", "피폭", "선량한도"]):
        question_types += ["사례형", "안전관리형"]
        competencies += ["방사선 안전 판단"]

    return {
        "recommended_question_types": sorted(set(question_types)),
        "recommended_competency_types": sorted(set(competencies)),
        "recommended_difficulties": difficulty,
        "required_evidence_types": evidence,
        "generation_notes": [
            "출제범위에 직접 연결되는 근거 chunk를 우선 사용한다.",
            "오답은 같은 영역 내 혼동 개념, 단위 오류, 절차 오류, 법령 착오에서 설계한다.",
        ],
    }


def build_scope_generation_strategy(exam_scope: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for subject in exam_scope["subjects"]:
        for field in subject["fields"]:
            for area in field["areas"]:
                details = area["details"] or [{"name": "", "source_page": None}]
                for detail in details:
                    strategy = infer_strategy(subject["name"], field["name"], area["name"], detail["name"])
                    rows.append(
                        {
                            "subject": subject["name"],
                            "field": field["name"],
                            "area": area["name"],
                            "detail": detail["name"],
                            "source_page": detail.get("source_page"),
                            **strategy,
                        }
                    )
    return {
        "version": 1,
        "purpose": "출제범위의 각 단위에 문항 유형, 역량, 난이도, 근거 요구사항을 연결한다.",
        "mvp_filter": {
            "included_subjects": ["1. 방사선이론", "2. 의료관계법규", "3. 방사선응용"],
            "excluded_subjects": ["4. 실기시험"],
        },
        "rows": rows,
    }


def build_validation_harness_spec() -> dict[str, Any]:
    return {
        "version": 1,
        "purpose": "생성 문항을 저장/검수 전에 자동 평가하는 Harness 명세",
        "checks": [
            {
                "id": "VH-001",
                "group": "format",
                "name": "5지선다 보기 수",
                "severity": "error",
                "logic": "options length must equal 5",
                "message": "보기는 정확히 5개여야 한다.",
            },
            {
                "id": "VH-002",
                "group": "format",
                "name": "정답 단일성",
                "severity": "error",
                "logic": "answer must identify exactly one option",
                "message": "정답은 정확히 하나여야 한다.",
            },
            {
                "id": "VH-003",
                "group": "scope",
                "name": "출제범위 존재",
                "severity": "error",
                "logic": "subject/field/area/detail must exist in exam_scope.json",
                "message": "문항 출제범위가 기준 DB에 존재해야 한다.",
            },
            {
                "id": "VH-004",
                "group": "scope",
                "name": "MVP 범위",
                "severity": "error",
                "logic": "subject must not be 실기시험 during MVP",
                "message": "현재 MVP에서는 3교시 실기시험 문항을 생성하지 않는다.",
            },
            {
                "id": "VH-005",
                "group": "metadata",
                "name": "필수 메타데이터",
                "severity": "error",
                "logic": "required metadata fields must be present",
                "message": "교시, 과목, 출제범위, 난이도, 문항 유형, 근거가 필요하다.",
            },
            {
                "id": "VH-006",
                "group": "metadata",
                "name": "난이도 유효성",
                "severity": "error",
                "logic": "difficulty in ['하','중','상']",
                "message": "난이도는 하/중/상 중 하나여야 한다.",
            },
            {
                "id": "VH-007",
                "group": "metadata",
                "name": "문항 유형 유효성",
                "severity": "error",
                "logic": "question_type must exist in rulebook",
                "message": "문항 유형은 등록된 유형이어야 한다.",
            },
            {
                "id": "VH-008",
                "group": "evidence",
                "name": "근거 출처",
                "severity": "error",
                "logic": "evidence_refs must include source file/page or law/version",
                "message": "출처 파일/페이지 또는 법령 기준 정보가 필요하다.",
            },
            {
                "id": "VH-009",
                "group": "law",
                "name": "법규 최신성 메타",
                "severity": "error",
                "logic": "law questions require law_name, article_ref, effective_date",
                "message": "법규형 문항은 법령명, 조문, 기준일이 필요하다.",
            },
            {
                "id": "VH-010",
                "group": "language",
                "name": "문항 언어 규칙",
                "severity": "warning",
                "logic": "run question_language_rulebook checks",
                "message": "문항 표현이 국문법/공공언어 기준을 만족해야 한다.",
            },
            {
                "id": "VH-011",
                "group": "answer_quality",
                "name": "오답 중복",
                "severity": "error",
                "logic": "options should be semantically distinct",
                "message": "보기끼리 의미가 중복되면 안 된다.",
            },
            {
                "id": "VH-012",
                "group": "answer_quality",
                "name": "정답 단서 편향",
                "severity": "warning",
                "logic": "answer option should not be uniquely longer/more specific without reason",
                "message": "정답만 길이·구체성·표현 방식에서 튀면 안 된다.",
            },
            {
                "id": "VH-013",
                "group": "copyright",
                "name": "원문 문장 유사도",
                "severity": "error",
                "logic": "generated stem/options/explanation sentence similarity against source chunk must be below 0.8",
                "message": "생성 문항 문장이 원문 chunk와 80% 이상 유사하면 안 된다.",
            },
            {
                "id": "VH-014",
                "group": "copyright",
                "name": "보기 원문 재현",
                "severity": "error",
                "logic": "reject when 3 or more generated options are identical or near-identical to source chunk text",
                "message": "보기 5개 중 3개 이상이 원문과 동일하면 안 된다.",
            },
            {
                "id": "VH-015",
                "group": "copyright",
                "name": "해설 원문 인용",
                "severity": "error",
                "logic": "generated explanation sentence similarity against source chunk must be below 0.8",
                "message": "해설이 원문 표현을 그대로 사용하면 안 된다.",
            },
            {
                "id": "VH-016",
                "group": "copyright",
                "name": "근거 용도 제한",
                "severity": "warning",
                "logic": "RAG source chunks are used only as evidence metadata and concept support",
                "message": "RAG 근거는 정답 근거 제공 용도로만 사용하고 문항 문장은 새롭게 작성해야 한다.",
            },
            {
                "id": "VH-017",
                "group": "evidence",
                "name": "해설 존재",
                "severity": "error",
                "logic": "explanation or rationale must be present",
                "message": "해설은 반드시 존재해야 한다.",
            },
            {
                "id": "VH-018",
                "group": "scope",
                "name": "학습목표 존재",
                "severity": "error",
                "logic": "learning_objective_id or learning_objective must be present",
                "message": "학습목표가 반드시 연결되어야 한다.",
            },
            {
                "id": "VH-019",
                "group": "evidence",
                "name": "근거 문서 chunk 존재",
                "severity": "error",
                "logic": "strict storage validation requires source chunks or evidence chunks",
                "message": "DB 저장 검증에는 원문 근거 chunk가 필요하다.",
            },
            {
                "id": "VH-020",
                "group": "duplication",
                "name": "중복 문항",
                "severity": "error",
                "logic": "generated question stem must not be highly similar to existing question candidates",
                "message": "기존 문항과 중복되면 안 된다.",
            },
            {
                "id": "VH-021",
                "group": "hallucination",
                "name": "근거 없는 수치·법령·공식",
                "severity": "error",
                "logic": "numeric values, units, formulas and law refs must be supported by source chunks or law metadata",
                "message": "근거 없는 수치, 단위, 공식, 법령 조문을 생성하면 안 된다.",
            },
        ],
    }


def write_rulebook_docs(
    language: dict[str, Any],
    design: dict[str, Any],
    strategy: dict[str, Any],
    harness: dict[str, Any],
) -> None:
    language_lines = ["# Detailed Question Language Rulebook", ""]
    for category in language["categories"]:
        language_lines.append(f"## {category['name']}")
        language_lines.append("")
        language_lines.append(f"- Scope: {category['scope']}")
        language_lines.append(f"- Source pages: {', '.join(map(str, category['source_pages'][:8]))}")
        language_lines.append("")
        for item in [r for r in language["rules"] if r["category"] == category["id"]]:
            language_lines.append(f"### {item['id']} {item['name']}")
            language_lines.append("")
            language_lines.append(f"- Requirement: {item['requirement']}")
            language_lines.append(f"- Check: {item['check']}")
            language_lines.append(f"- Severity: {item['severity']}")
            language_lines.append("")
    write_text(DOCS_DIR / "06_detailed_question_language_rulebook.md", "\n".join(language_lines))

    design_lines = ["# Detailed Item Design Rulebook", ""]
    for item in design["rules"]:
        design_lines.append(f"## {item['id']} {item['name']}")
        design_lines.append("")
        design_lines.append(f"- Requirement: {item['requirement']}")
        design_lines.append(f"- Severity: {item['severity']}")
        design_lines.append(f"- Source pages: {', '.join(map(str, item['evidence']['pages']))}")
        design_lines.append("")
    design_lines.append("## Required Metadata")
    design_lines.append("")
    for item in design["required_metadata"]:
        required = "required" if item["required"] else "optional"
        design_lines.append(f"- `{item['name']}` ({item['label']}): {required}")
    write_text(DOCS_DIR / "07_detailed_item_design_rulebook.md", "\n".join(design_lines))

    rows = strategy["rows"]
    included = [row for row in rows if row["subject"] != "4. 실기시험"]
    strategy_lines = ["# Scope Generation Strategy", ""]
    strategy_lines.append(f"- Total scope rows: {len(rows)}")
    strategy_lines.append(f"- MVP included rows: {len(included)}")
    strategy_lines.append("")
    for row in included[:80]:
        label = " > ".join([row["subject"], row["field"], row["area"], row["detail"]]).strip(" > ")
        strategy_lines.append(f"## {label}")
        strategy_lines.append("")
        strategy_lines.append(f"- Question types: {', '.join(row['recommended_question_types'])}")
        strategy_lines.append(f"- Competencies: {', '.join(row['recommended_competency_types'])}")
        strategy_lines.append(f"- Difficulties: {', '.join(row['recommended_difficulties'])}")
        strategy_lines.append("")
    write_text(DOCS_DIR / "08_scope_generation_strategy.md", "\n".join(strategy_lines))

    harness_lines = ["# Validation Harness Spec", ""]
    for item in harness["checks"]:
        harness_lines.append(f"## {item['id']} {item['name']}")
        harness_lines.append("")
        harness_lines.append(f"- Group: {item['group']}")
        harness_lines.append(f"- Severity: {item['severity']}")
        harness_lines.append(f"- Logic: `{item['logic']}`")
        harness_lines.append(f"- Message: {item['message']}")
        harness_lines.append("")
    write_text(DOCS_DIR / "09_validation_harness_spec.md", "\n".join(harness_lines))


def main() -> None:
    exam_scope = load_json(RULES_DIR / "exam_scope.json")
    language = build_question_language_rulebook()
    design = build_item_design_rulebook()
    strategy = build_scope_generation_strategy(exam_scope)
    harness = build_validation_harness_spec()

    write_json(RULES_DIR / "question_language_rulebook.json", language)
    write_json(RULES_DIR / "item_design_rulebook.json", design)
    write_json(RULES_DIR / "scope_generation_strategy.json", strategy)
    write_json(RULES_DIR / "validation_harness_spec.json", harness)
    write_rulebook_docs(language, design, strategy, harness)

    print(
        json.dumps(
            {
                "language_rules": len(language["rules"]),
                "item_design_rules": len(design["rules"]),
                "scope_strategy_rows": len(strategy["rows"]),
                "harness_checks": len(harness["checks"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
