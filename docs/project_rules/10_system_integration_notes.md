# System Integration Notes

이번 기준 자료는 WIZ 백엔드에서 다음 Struct로 접근한다.

```python
struct = wiz.model("struct")
struct.rules.list()
struct.exam_scope.rows(include_practical=False)
struct.validation.validate(question)
```

## Rules Loader

- `struct.rules`는 `resources/rules/*.json` 파일을 읽는다.
- 생성 정책, 문항 설계 규칙, 국문법 규칙, 검증 Harness 명세를 코드에서 재사용한다.

## Exam Scope

- `struct.exam_scope`는 `exam_scope.json`과 `scope_generation_strategy.json`을 합쳐 출제범위 인덱스를 만든다.
- `sync_seed()`를 호출하면 `exam_scope` DB 테이블에 기준 데이터를 upsert한다.
- 현재 MVP 필터는 `4. 실기시험`을 제외한다.

## Validation Harness

- `struct.validation.validate(question)`은 문항 초안의 형식, 출제범위, 필수 메타데이터, 근거, 법규 메타데이터, 보기 중복, 일부 언어 위험 패턴을 검사한다.
- 현재 Harness는 1차 규칙 기반 검증이며, RAG 근거 일치성과 의미 중복 검증은 이후 단계에서 확장한다.
