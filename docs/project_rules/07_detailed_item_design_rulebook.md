# Detailed Item Design Rulebook

## ID-001 공식 출제범위 필수 반영

- Requirement: 모든 문항은 국시원 출제범위와 법령상 시험과목 체계에 연결되어야 한다.
- Severity: error
- Source pages: 1, 22, 23

## ID-002 문항 메타데이터 필수

- Requirement: 교시, 대과목, 세부 과목, 출제범위 코드, 학습목표 등급, 역량 유형, 난이도, 문항 유형, 정답 근거, 오답 설계 원리를 저장한다.
- Severity: error
- Source pages: 2, 23

## ID-003 1교시 구성

- Requirement: 1교시는 방사선이론 90문항과 의료관계법규 20문항의 성격을 분리해 설계한다.
- Severity: error
- Source pages: 3, 4, 5, 6

## ID-004 의료관계법규 최신성

- Requirement: 법규 문항은 법령명, 조문 번호, 시행령/시행규칙 구분, 조문 기준일, 개정 이력을 관리한다.
- Severity: error
- Source pages: 6, 23

## ID-005 2교시 임상 응용 중심

- Requirement: 2교시는 검사 목적, 검사 방법, 영상 결과, 환자 안전을 연결하는 응용형 문항을 우선한다.
- Severity: warning
- Source pages: 7, 8, 9, 22, 23

## ID-006 공식 비율 오인 금지

- Requirement: 공식 공개 비율이 없는 문항 유형/영상 유형 비중은 권장 비중으로만 저장하고 공식 기준으로 표시하지 않는다.
- Severity: error
- Source pages: 5, 10

## ID-007 3교시 MVP 제외

- Requirement: 현재 MVP에서는 3교시 영상 기반 문항을 생성·승인하지 않는다.
- Severity: error
- Source pages: 10, 11, 19, 20, 21

## ID-008 정답 명확성과 오답 타당성

- Requirement: 복수정답 가능성이 없어야 하며, 오답은 너무 터무니없지 않고 흔한 혼동을 반영해야 한다.
- Severity: error
- Source pages: 22

## Required Metadata

- `period` (교시): required
- `subject` (대과목): required
- `sub_subject` (세부 과목): required
- `scope_code` (출제범위 코드): required
- `learning_objective_level` (학습목표 등급): optional
- `competency_type` (역량 유형): required
- `difficulty` (난이도): required
- `question_type` (문항 유형): required
- `evidence_refs` (정답 근거): required
- `distractor_strategy` (오답 설계 원리): required
