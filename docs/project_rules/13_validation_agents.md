# Validation Agents

문항 검증은 하나의 체크리스트가 아니라 5개 전문 검증 에이전트와 최종 Judge로 나눈다.

## Agent 1 - 출제범위 검증

- 입력: 문제, 출제범위, 학습목표
- 역할: 주어진 문제가 공식 출제범위와 학습목표에 포함되는지 판단한다.
- 출력: `{"pass": true, "reason": "학습목표와 일치"}`

## Agent 2 - 정답 유일성 검증

- 입력: 문제, 보기, 정답
- 역할: 정답이 하나만 존재하는지, 다른 보기가 정답으로 해석될 가능성이 있는지 판단한다.
- 실패 시: 문제 폐기

## Agent 3 - RAG 근거 검증

- 입력: 문제, 해설, RAG 근거
- 역할: 문제와 해설이 근거 문단에서 지원되는지 판단한다.
- 출력: `{"grounded": true, "confidence": 0.94}`

## Agent 4 - 저작권 검증

- 입력: 원문 chunk, 생성 문제
- 역할: 원문 문장 복사, 문제 구조 재현, 보기 재사용 여부를 판단한다.
- 출력: `{"copyright_risk": "low"}`

## Agent 5 - 국문법·문항 표현 검증

- 입력: 문제, 보기, 해설, 문항작성법, 문항평가법, 국문법 교재
- 역할: 국문법 오류, 부정문 사용, 답가지 길이, 문항줄기 오류를 검사한다.

## 최종 Judge

모든 에이전트 결과를 수집해 다음 구조로 최종 판정한다.

```json
{
  "scope": true,
  "grounding": true,
  "uniqueness": true,
  "grammar": true,
  "copyright": true,
  "final_pass": true,
  "discard": false
}
```
