# Subject Reference Upload Plan

전공 자료는 `materials/04_subject_references`에 업로드한다.

## Flow

1. 파일 업로드
2. `source_document` 등록
3. PDF 텍스트/표 추출
4. OCR 필요 페이지 표시
5. chunk 생성
6. `exam_scope` 기준 태깅
7. RAG 검색 인덱스 생성

## DB Tables

- `exam_scope`: 출제범위 기준표
- `source_document`: 업로드된 원자료
- `source_chunk`: 페이지/단락 단위 RAG chunk

## Required Metadata

- 자료명
- 파일명
- 과목/분야/영역/세부영역
- 출처 유형
- 저작권/사용권한 상태
- 법규 또는 자료 기준일

## Guardrails

- 기출문제, 복원문제, 시중 모의고사 원문은 RAG 근거 자료로 등록하지 않는다.
- 법규 자료는 기준일이 없으면 승인하지 않는다.
- chunk는 반드시 출처 파일과 페이지를 유지한다.
