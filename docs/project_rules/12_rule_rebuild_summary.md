# Rule Rebuild Summary

## Sources
- 공식 출제범위 대표본: `materials/02_exam_scope/붙임. 방사선사 국가시험 출제범위(2022년 제 50회 방사선사 국가시험).pdf`
- 중복 출제범위 후보: 1개
- 학습목표 원본: `materials/03_item_design/2022 개정 방사선학 학습목표.pdf`
- 세부영역 OCR raw: `resources/extracted/paddleocr_sebuyeongyeok_pages.json`
- 세부영역 검수본: `resources/extracted/sebuyeongyeok_verified_scope.json`

## Counts
- exam_scope hierarchy rows: 233
- exam_scope detail rows: 125
- verified detail rows: 180
- learning objectives: 1946
- question generation targets: 1946
- blueprint total questions: 250

## Notes
- 한 PDF가 여러 JSON에 영향을 주도록 재구성했다.
- 세부영역 PDF는 PaddleOCR raw를 보존하되, 문제 생성 비중은 사용자 검수본을 기준으로 고정했다.
- 영상품질관리는 확정 2문항과 후보군 내 유동 1문항으로 표현한다.
- 04_subject_references가 들어오기 전까지 evidence_refs는 실제 전공 근거가 아니라 생성 기준 연결만 가능하다.
