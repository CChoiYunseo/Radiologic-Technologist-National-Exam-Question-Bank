import difflib
import re


class Validation:
    def __init__(self, core):
        self.core = core
        self.rules = core.rules
        self.exam_scope = core.exam_scope

    def _error(self, check_id, group, message, severity="error"):
        return dict(id=check_id, group=group, severity=severity, message=message)

    def _options(self, question):
        options = question.get("options", [])
        if isinstance(options, dict):
            return [options[key] for key in sorted(options.keys())]
        return options or []

    def _answer_count(self, question, options):
        answer = question.get("answer")
        if isinstance(answer, list):
            return len(answer)
        if isinstance(answer, dict):
            return len(answer)
        if answer is None or answer == "":
            return 0
        if isinstance(answer, int):
            return 1 if 1 <= answer <= len(options) else 0
        if isinstance(answer, str):
            value = answer.strip()
            if "," in value:
                return len([v for v in value.split(",") if v.strip()])
            return 1
        return 0

    def validate(self, question, mvp_only=True, source_chunks=None, duplicate_candidates=None, require_source_chunks=False):
        results = []
        options = self._options(question)
        chunks = self._source_chunks(question, source_chunks)

        if len(options) != 5:
            results.append(self._error("VH-001", "format", "보기는 정확히 5개여야 합니다."))

        if self._answer_count(question, options) != 1:
            results.append(self._error("VH-002", "format", "정답은 정확히 하나여야 합니다."))

        if not (question.get("explanation") or question.get("rationale")):
            results.append(self._error("VH-017", "evidence", "해설은 반드시 존재해야 합니다."))

        if not (question.get("learning_objective_id") or question.get("learning_objective") or question.get("target")):
            results.append(self._error("VH-018", "scope", "학습목표가 반드시 연결되어야 합니다."))

        scope_result = self.exam_scope.validate_scope(question, mvp_only=mvp_only)
        for err in scope_result.get("errors", []):
            check_id = "VH-004" if err["code"] == "SCOPE_MVP_EXCLUDED" else "VH-003"
            results.append(self._error(check_id, "scope", err["message"]))

        required = ["period", "subject", "field", "area", "difficulty", "question_type", "evidence_refs"]
        missing = [name for name in required if not question.get(name)]
        if missing:
            results.append(self._error("VH-005", "metadata", "필수 메타데이터가 누락되었습니다: " + ", ".join(missing)))

        if question.get("difficulty") and question.get("difficulty") not in ["하", "중", "상"]:
            results.append(self._error("VH-006", "metadata", "난이도는 하/중/상 중 하나여야 합니다."))

        if question.get("question_type"):
            if not self._is_valid_question_type(question.get("question_type")):
                results.append(self._error("VH-007", "metadata", "등록되지 않은 문항 유형입니다."))

        if not self._has_evidence(question):
            results.append(self._error("VH-008", "evidence", "출처 파일/페이지 또는 법령 기준 정보가 필요합니다."))

        if require_source_chunks and not chunks:
            results.append(self._error("VH-019", "evidence", "DB 저장 검증에는 원문 근거 chunk가 필요합니다."))

        if question.get("question_type") == "법규형" or "법" in str(question.get("subject", "")):
            missing_law = [name for name in ["law_name", "article_ref", "effective_date"] if not question.get(name)]
            if missing_law:
                results.append(self._error("VH-009", "law", "법규형 문항은 법령명, 조문, 기준일이 필요합니다."))

        duplicate_options = self._duplicate_options(options)
        if duplicate_options:
            results.append(self._error("VH-011", "answer_quality", "보기끼리 의미가 중복됩니다: " + ", ".join(duplicate_options)))

        duplicate_question = self._duplicate_question(question, duplicate_candidates)
        if duplicate_question:
            results.append(self._error("VH-020", "duplication", "기존 문항과 중복됩니다: " + duplicate_question))

        unsupported_facts = self._unsupported_fact_checks(question, chunks)
        results.extend(unsupported_facts)

        warnings = self._language_warnings(question, options)
        results.extend(warnings)

        copyright_results = self._copyright_checks(question, options, source_chunks=chunks)
        results.extend(copyright_results)

        agents = self._agent_report(question, results, source_chunks=chunks)
        judge = self._final_judge(agents)
        errors = [r for r in results if r.get("severity") == "error"]
        storage = self._storage_report(question, errors, judge)
        return dict(
            ok=len(errors) == 0 and judge.get("final_pass"),
            status="passed" if len(errors) == 0 and judge.get("final_pass") else "failed",
            results=results,
            agents=agents,
            judge=judge,
            storage=storage,
            error_count=len(errors),
            warning_count=len(results) - len(errors),
        )

    def _is_valid_question_type(self, value):
        rulebook = self.rules.item_design_rulebook
        types = set()
        for template in rulebook.get("period_templates", {}).values():
            for question_type in template.get("allowed_question_types", []):
                types.add(question_type)
        type_rules = self.rules.question_type_rules
        for question_type in (type_rules.get("types") or {}).keys():
            types.add(question_type)
        return value in types

    def _has_evidence(self, question):
        refs = question.get("evidence_refs") or question.get("source_refs") or []
        if isinstance(refs, str):
            return bool(refs.strip())
        if isinstance(refs, list):
            return len(refs) > 0
        if isinstance(refs, dict):
            return bool(refs)
        return False

    def _duplicate_options(self, options):
        seen = {}
        duplicates = []
        for index, option in enumerate(options, start=1):
            key = " ".join(str(option or "").split())
            if not key:
                continue
            if key in seen:
                duplicates.append(f"{seen[key]}번/{index}번")
            else:
                seen[key] = index
        return duplicates

    def _duplicate_question(self, question, duplicate_candidates=None):
        if not duplicate_candidates:
            return None
        generated = question.get("stem") or question.get("question_text") or ""
        generated = self._normalize_text(generated)
        if not generated:
            return None
        for index, candidate in enumerate(duplicate_candidates, start=1):
            if isinstance(candidate, dict):
                text = candidate.get("stem") or candidate.get("question_text") or candidate.get("text") or ""
                label = candidate.get("id") or candidate.get("question_id") or f"{index}번 후보"
            else:
                text = str(candidate or "")
                label = f"{index}번 후보"
            score = self._similarity(generated, text)
            if score >= 0.9:
                return f"{label} 유사도 {score:.2f}"
        return None

    def _unsupported_fact_checks(self, question, chunks):
        if not chunks:
            return []
        source_text = self._normalize_text(" ".join(chunks))
        generated_text = " ".join([
            str(question.get("stem") or question.get("question_text") or ""),
            str(question.get("explanation") or question.get("rationale") or ""),
        ])
        generated_norm = self._normalize_text(generated_text)
        results = []

        for token in sorted(set(re.findall(r"\d+(?:\.\d+)?\s*(?:%|mm|cm|m|kg|g|mg|kv|ma|ms|gy|sv|bq|hz|kev|mev)?", generated_norm))):
            compact = token.replace(" ", "")
            if compact and compact not in source_text.replace(" ", ""):
                results.append(self._error(
                    "VH-021",
                    "hallucination",
                    f"근거 chunk에서 확인되지 않는 수치/단위가 포함되어 있습니다: {token}",
                ))

        formula_like = re.findall(r"[a-z가-힣]\s*=\s*[^,.;\n]+", generated_text, flags=re.IGNORECASE)
        for formula in formula_like:
            if self._normalize_text(formula) not in source_text:
                results.append(self._error(
                    "VH-021",
                    "hallucination",
                    f"근거 chunk에서 확인되지 않는 공식이 포함되어 있습니다: {formula.strip()[:80]}",
                ))

        return results

    def _language_warnings(self, question, options):
        text = " ".join([str(question.get("question_text", ""))] + [str(x) for x in options])
        warnings = []
        risky_patterns = [
            ("VH-010", "language", "외국어투 또는 피동 표현 '-에 의해'가 포함되어 있습니다.", "-에 의해"),
            ("VH-010", "language", "불필요한 사동 표현 가능성이 있는 '시키'가 포함되어 있습니다.", "시키"),
            ("VH-010", "language", "근거 없는 절대 표현 가능성이 있는 '항상'이 포함되어 있습니다.", "항상"),
            ("VH-010", "language", "근거 없는 절대 표현 가능성이 있는 '절대'가 포함되어 있습니다.", "절대"),
        ]
        for check_id, group, message, pattern in risky_patterns:
            if pattern in text:
                warnings.append(self._error(check_id, group, message, severity="warning"))
        return warnings

    def _copyright_checks(self, question, options, source_chunks=None):
        chunks = self._source_chunks(question, source_chunks)
        if not chunks:
            return []

        policy = self.rules.copyright_policy
        validation = policy.get("validation", {})
        sentence_threshold = float(validation.get("sentence_similarity_reject_threshold", 0.8))
        explanation_threshold = float(validation.get("explanation_similarity_reject_threshold", 0.8))
        option_reject_count = int(validation.get("option_exact_match_reject_count", 3))
        min_len = int(validation.get("minimum_text_length", 12))

        source_sentences = []
        for chunk in chunks:
            source_sentences.extend(self._sentences(chunk))

        results = []
        generated_parts = []
        for key in ["stem", "question_text"]:
            if question.get(key):
                generated_parts.extend(self._sentences(question.get(key)))
        for option in options:
            generated_parts.extend(self._sentences(option))

        copied_sentence = self._first_high_similarity(generated_parts, source_sentences, sentence_threshold, min_len)
        if copied_sentence:
            results.append(self._error(
                "VH-013",
                "copyright",
                f"생성 문항 문장이 원문 chunk와 {int(sentence_threshold * 100)}% 이상 유사합니다: {copied_sentence}",
            ))

        matched_options = self._matched_options(options, source_sentences, min_len)
        if len(matched_options) >= option_reject_count:
            labels = ", ".join([f"{idx}번" for idx in matched_options])
            results.append(self._error(
                "VH-014",
                "copyright",
                f"보기 5개 중 {len(matched_options)}개가 원문과 동일하거나 매우 유사합니다: {labels}",
            ))

        explanation = question.get("explanation") or question.get("rationale") or ""
        copied_explanation = self._first_high_similarity(
            self._sentences(explanation),
            source_sentences,
            explanation_threshold,
            min_len,
        )
        if copied_explanation:
            results.append(self._error(
                "VH-015",
                "copyright",
                f"해설이 원문 표현과 {int(explanation_threshold * 100)}% 이상 유사합니다: {copied_explanation}",
            ))

        if not results:
            results.append(self._error(
                "VH-016",
                "copyright",
                "저작권 검증 통과: 근거 chunk는 정답 근거로만 사용하고 문항 표현은 새로 작성된 것으로 판단됩니다.",
                severity="info",
            ))
        return results

    def _source_chunks(self, question, source_chunks=None):
        candidates = source_chunks
        if candidates is None:
            for key in ["source_chunks", "evidence_chunks", "chunks"]:
                if question.get(key):
                    candidates = question.get(key)
                    break
        if candidates is None:
            refs = question.get("evidence_refs") or []
            if isinstance(refs, dict):
                refs = [refs]
            if isinstance(refs, list):
                candidates = [ref for ref in refs if isinstance(ref, dict) and (ref.get("content") or ref.get("text"))]

        if isinstance(candidates, (str, dict)):
            candidates = [candidates]
        chunks = []
        for item in candidates or []:
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("content") or item.get("text") or item.get("chunk") or ""
            else:
                text = ""
            text = str(text or "").strip()
            if text:
                chunks.append(text)
        return chunks

    def _normalize_text(self, value):
        text = str(value or "").lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w가-힣\s.%+-]", "", text)
        return text.strip()

    def _sentences(self, value):
        text = str(value or "").replace("\n", " ")
        parts = re.split(r"(?<=[.!?。])\s+|[;；]", text)
        sentences = []
        for part in parts:
            part = " ".join(part.split()).strip()
            if part:
                sentences.append(part)
        return sentences

    def _similarity(self, generated, source):
        gen = self._normalize_text(generated)
        src = self._normalize_text(source)
        if not gen or not src:
            return 0.0
        if len(gen) >= 12 and gen in src:
            return 1.0
        return difflib.SequenceMatcher(None, gen, src).ratio()

    def _first_high_similarity(self, generated_parts, source_sentences, threshold, min_len):
        for generated in generated_parts:
            if len(self._normalize_text(generated)) < min_len:
                continue
            for source in source_sentences:
                if len(self._normalize_text(source)) < min_len:
                    continue
                if self._similarity(generated, source) >= threshold:
                    return generated[:120]
        return None

    def _matched_options(self, options, source_sentences, min_len):
        matched = []
        for index, option in enumerate(options, start=1):
            text = self._normalize_text(option)
            if len(text) < min_len:
                continue
            for source in source_sentences:
                source_text = self._normalize_text(source)
                if len(source_text) < min_len:
                    continue
                if text in source_text or self._similarity(text, source_text) >= 0.9:
                    matched.append(index)
                    break
        return matched

    def _group_errors(self, results, groups):
        groups = set(groups)
        return [r for r in results if r.get("group") in groups and r.get("severity") == "error"]

    def _group_warnings(self, results, groups):
        groups = set(groups)
        return [r for r in results if r.get("group") in groups and r.get("severity") == "warning"]

    def _agent_report(self, question, results, source_chunks=None):
        scope_errors = self._group_errors(results, ["scope"])
        metadata_errors = [
            r for r in self._group_errors(results, ["metadata"])
            if "출제범위" in r.get("message", "") or "period" in r.get("message", "")
        ]
        learning_goal = (
            question.get("learning_objective")
            or question.get("learning_objective_id")
            or question.get("target")
        )
        scope_pass = not scope_errors and not metadata_errors and bool(learning_goal)
        scope_reason = "학습목표와 출제범위가 일치합니다."
        if scope_errors or metadata_errors:
            scope_reason = "; ".join([r["message"] for r in scope_errors + metadata_errors])
        elif not learning_goal:
            scope_reason = "학습목표 정보가 없어 출제범위-학습목표 일치 여부를 확정할 수 없습니다."

        uniqueness_errors = self._group_errors(results, ["format", "answer_quality"])
        uniqueness_warnings = self._group_warnings(results, ["answer_quality"])
        uniqueness_pass = not any(r.get("id") in ["VH-002", "VH-011"] for r in uniqueness_errors)
        uniqueness_reason = "정답은 하나로 판정됩니다."
        if not uniqueness_pass:
            uniqueness_reason = "; ".join([r["message"] for r in uniqueness_errors])
        elif uniqueness_warnings:
            uniqueness_reason = "; ".join([r["message"] for r in uniqueness_warnings])

        evidence_errors = self._group_errors(results, ["evidence", "law"])
        chunks = self._source_chunks(question, source_chunks)
        grounding_pass = not evidence_errors and bool(chunks or self._has_evidence(question))
        grounding_confidence = 0.94 if chunks and not evidence_errors else 0.65 if self._has_evidence(question) and not evidence_errors else 0.0
        grounding_reason = "문제와 해설이 RAG 근거로 검증될 수 있습니다." if chunks else "근거 메타데이터는 있으나 원문 chunk 기반 검증은 아직 수행되지 않았습니다."
        if evidence_errors:
            grounding_reason = "; ".join([r["message"] for r in evidence_errors])

        copyright_errors = self._group_errors(results, ["copyright"])
        copyright_pass = not copyright_errors
        copyright_risk = "high" if copyright_errors else "low" if chunks else "medium"
        copyright_reason = "원문 복제 위험이 낮습니다." if copyright_risk == "low" else "원문 chunk가 없어 저작권 유사도 검증은 보류 상태입니다."
        if copyright_errors:
            copyright_reason = "; ".join([r["message"] for r in copyright_errors])

        grammar_errors = self._group_errors(results, ["language"])
        grammar_warnings = self._group_warnings(results, ["language", "answer_quality"])
        grammar_pass = not grammar_errors
        grammar_reason = "국문법과 문항 표현 기준을 통과했습니다."
        if grammar_errors:
            grammar_reason = "; ".join([r["message"] for r in grammar_errors])
        elif grammar_warnings:
            grammar_reason = "; ".join([r["message"] for r in grammar_warnings])

        return {
            "scope": {"pass": scope_pass, "reason": scope_reason},
            "uniqueness": {"pass": uniqueness_pass, "reason": uniqueness_reason},
            "grounding": {"grounded": grounding_pass, "confidence": grounding_confidence, "reason": grounding_reason},
            "copyright": {"pass": copyright_pass, "copyright_risk": copyright_risk, "reason": copyright_reason},
            "grammar": {"pass": grammar_pass, "reason": grammar_reason},
        }

    def _final_judge(self, agents):
        summary = {
            "scope": bool(agents.get("scope", {}).get("pass")),
            "grounding": bool(agents.get("grounding", {}).get("grounded")),
            "uniqueness": bool(agents.get("uniqueness", {}).get("pass")),
            "grammar": bool(agents.get("grammar", {}).get("pass")),
            "copyright": bool(agents.get("copyright", {}).get("pass")),
        }
        failed = [name for name, passed in summary.items() if not passed]
        final_pass = len(failed) == 0
        return dict(
            **summary,
            final_pass=final_pass,
            discard=not final_pass,
            reason="모든 검증 에이전트를 통과했습니다." if final_pass else "검증 실패: " + ", ".join(failed),
        )

    def _storage_report(self, question, errors, judge):
        current_status = question.get("status") or question.get("validation_status") or "generated"
        allowed_statuses = ["reviewed", "approved"]
        can_store = len(errors) == 0 and judge.get("final_pass") and current_status in allowed_statuses
        if can_store:
            reason = "reviewed 이상이며 모든 검증을 통과했으므로 최종 문제 DB 저장 가능"
        elif current_status not in allowed_statuses:
            reason = "최종 문제 DB에는 reviewed 또는 approved 상태만 저장 가능"
        else:
            reason = "검증 실패 항목이 있어 최종 문제 DB 저장 불가"
        return {
            "allowed": can_store,
            "current_status": current_status,
            "allowed_statuses": allowed_statuses,
            "next_status_if_passed": "reviewed" if current_status == "generated" and judge.get("final_pass") and not errors else current_status,
            "reason": reason,
        }


Model = Validation
