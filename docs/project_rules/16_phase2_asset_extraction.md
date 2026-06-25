# Phase 2 Asset Extraction

텍스트 MVP RAG와 별도로 수식, 표, 그림/도식/장비 구조도 후보를 추출한다. 이 산출물은 바로 문제 생성 근거로 사용하지 않고, 검수와 설명 보강 후 RAG를 확장하는 데 사용한다.

## Outputs

```text
resources/extracted/subject_references_phase2/
├── formulas.jsonl
├── tables.jsonl
├── visual_assets.jsonl
├── phase2_report.json
└── images/
```

## Rules

- 수식 후보는 휴리스틱 결과이므로 검수 후 사용한다.
- 표는 행·열 구조를 보존하되, 병합 셀과 줄바꿈 오류를 검수한다.
- 그림과 도식 이미지는 Vision LLM 또는 사람 검수 설명문이 붙기 전까지 정답 근거로 단독 사용하지 않는다.
- 원문 PDF는 수정하지 않는다.
- 2차 PDF를 생성하지 않는다.

## Command

```bash
python3 scripts/extract_subject_phase2_assets.py
```

스모크 테스트:

```bash
python3 scripts/extract_subject_phase2_assets.py --max-pages 10 --output resources/extracted/subject_references_phase2_smoke
```
