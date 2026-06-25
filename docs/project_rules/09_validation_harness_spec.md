# Validation Harness Spec

## VH-001 5지선다 보기 수

- Group: format
- Severity: error
- Logic: `options length must equal 5`
- Message: 보기는 정확히 5개여야 한다.

## VH-002 정답 단일성

- Group: format
- Severity: error
- Logic: `answer must identify exactly one option`
- Message: 정답은 정확히 하나여야 한다.

## VH-003 출제범위 존재

- Group: scope
- Severity: error
- Logic: `subject/field/area/detail must exist in exam_scope.json`
- Message: 문항 출제범위가 기준 DB에 존재해야 한다.

## VH-004 MVP 범위

- Group: scope
- Severity: error
- Logic: `subject must not be 실기시험 during MVP`
- Message: 현재 MVP에서는 3교시 실기시험 문항을 생성하지 않는다.

## VH-005 필수 메타데이터

- Group: metadata
- Severity: error
- Logic: `required metadata fields must be present`
- Message: 교시, 과목, 출제범위, 난이도, 문항 유형, 근거가 필요하다.

## VH-006 난이도 유효성

- Group: metadata
- Severity: error
- Logic: `difficulty in ['하','중','상']`
- Message: 난이도는 하/중/상 중 하나여야 한다.

## VH-007 문항 유형 유효성

- Group: metadata
- Severity: error
- Logic: `question_type must exist in rulebook`
- Message: 문항 유형은 등록된 유형이어야 한다.

## VH-008 근거 출처

- Group: evidence
- Severity: error
- Logic: `evidence_refs must include source file/page or law/version`
- Message: 출처 파일/페이지 또는 법령 기준 정보가 필요하다.

## VH-009 법규 최신성 메타

- Group: law
- Severity: error
- Logic: `law questions require law_name, article_ref, effective_date`
- Message: 법규형 문항은 법령명, 조문, 기준일이 필요하다.

## VH-010 문항 언어 규칙

- Group: language
- Severity: warning
- Logic: `run question_language_rulebook checks`
- Message: 문항 표현이 국문법/공공언어 기준을 만족해야 한다.

## VH-011 오답 중복

- Group: answer_quality
- Severity: error
- Logic: `options should be semantically distinct`
- Message: 보기끼리 의미가 중복되면 안 된다.

## VH-012 정답 단서 편향

- Group: answer_quality
- Severity: warning
- Logic: `answer option should not be uniquely longer/more specific without reason`
- Message: 정답만 길이·구체성·표현 방식에서 튀면 안 된다.

## VH-013 원문 문장 유사도

- Group: copyright
- Severity: error
- Logic: `generated stem/options/explanation sentence similarity against source chunk must be below 0.8`
- Message: 생성 문항 문장이 원문 chunk와 80% 이상 유사하면 안 된다.

## VH-014 보기 원문 재현

- Group: copyright
- Severity: error
- Logic: `reject when 3 or more generated options are identical or near-identical to source chunk text`
- Message: 보기 5개 중 3개 이상이 원문과 동일하면 안 된다.

## VH-015 해설 원문 인용

- Group: copyright
- Severity: error
- Logic: `generated explanation sentence similarity against source chunk must be below 0.8`
- Message: 해설이 원문 표현을 그대로 사용하면 안 된다.

## VH-016 근거 용도 제한

- Group: copyright
- Severity: warning
- Logic: `RAG source chunks are used only as evidence metadata and concept support`
- Message: RAG 근거는 정답 근거 제공 용도로만 사용하고 문항 문장은 새롭게 작성해야 한다.

## VH-017 해설 존재

- Group: evidence
- Severity: error
- Logic: `explanation or rationale must be present`
- Message: 해설은 반드시 존재해야 한다.

## VH-018 학습목표 존재

- Group: scope
- Severity: error
- Logic: `learning_objective_id or learning_objective must be present`
- Message: 학습목표가 반드시 연결되어야 한다.

## VH-019 근거 문서 chunk 존재

- Group: evidence
- Severity: error
- Logic: `strict storage validation requires source chunks or evidence chunks`
- Message: DB 저장 검증에는 원문 근거 chunk가 필요하다.

## VH-020 중복 문항

- Group: duplication
- Severity: error
- Logic: `generated question stem must not be highly similar to existing question candidates`
- Message: 기존 문항과 중복되면 안 된다.

## VH-021 근거 없는 수치·법령·공식

- Group: hallucination
- Severity: error
- Logic: `numeric values, units, formulas and law refs must be supported by source chunks or law metadata`
- Message: 근거 없는 수치, 단위, 공식, 법령 조문을 생성하면 안 된다.
