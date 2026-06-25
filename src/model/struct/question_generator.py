import datetime
import json
import os
import re
import urllib.error
import urllib.request


class QuestionGenerator:
    def __init__(self, core):
        self.core = core
        self.validation = core.validation

    def _now(self):
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _json_dumps(self, data):
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _extract_json(self, text):
        text = str(text or "").strip()
        if not text:
            raise Exception("LLM 응답이 비어 있습니다.")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        first_obj = text.find("{")
        first_arr = text.find("[")
        starts = [idx for idx in [first_obj, first_arr] if idx >= 0]
        if not starts:
            raise Exception("LLM 응답에서 JSON을 찾지 못했습니다.")
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            raise Exception("LLM 응답 JSON 범위를 찾지 못했습니다.")
        return json.loads(text[start:end + 1])

    def _llm_config(self):
        provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
        api_url = os.environ.get("LLM_API_URL", "").strip()
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        if provider == "gemini":
            if not api_key:
                api_key = os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not model:
                model = "gemini-3.5-flash"
            return dict(provider=provider, api_url=api_url, api_key=api_key, model=model)
        if not api_url:
            api_url = os.environ.get("OPENAI_API_BASE", "").strip()
            if api_url:
                api_url = api_url.rstrip("/") + "/chat/completions"
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not model:
            model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
        return dict(provider=provider or "openai_compatible", api_url=api_url, api_key=api_key, model=model)

    def _build_messages(self, payload):
        prompt_payload = payload.get("generation_prompt_payload") or {}
        system = "\n".join([
            "당신은 방사선사 국가고시 문항 생성자이다.",
            "반드시 제공된 RAG 근거 chunk에서 지원되는 내용만 사용한다.",
            "원문 문장, 원문 문제, 원문 보기, 원문 해설을 복사하거나 재서술하지 않는다.",
            "5지선다형 문항 1개 이상을 JSON으로만 출력한다.",
            "정답은 정확히 하나여야 하며, 모든 오답 해설을 포함한다.",
            "출처 파일, 페이지, chunk_id를 evidence_refs에 포함한다.",
        ])
        schema = {
            "questions": [
                {
                    "period": "",
                    "subject": "",
                    "field": "",
                    "area": "",
                    "detail": "",
                    "scope_id": "",
                    "learning_objective_id": "",
                    "learning_objective": "",
                    "question_type": "",
                    "competency_type": "",
                    "difficulty": "",
                    "stem": "",
                    "options": {"1": "", "2": "", "3": "", "4": "", "5": ""},
                    "answer": "1",
                    "explanation": "",
                    "wrong_option_explanations": {"1": "", "2": "", "3": "", "4": "", "5": ""},
                    "evidence_refs": [
                        {"source_file": "", "page_or_slide": "", "chunk_id": ""}
                    ],
                    "distractor_strategy": "",
                    "validation_status": "generated",
                    "status": "generated",
                }
            ]
        }
        user = "\n\n".join([
            "다음 입력을 기준으로 문제를 생성하라.",
            self._json_dumps(prompt_payload),
            "출력 JSON 형식은 다음 스키마를 따른다.",
            self._json_dumps(schema),
        ])
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def call_llm(self, payload):
        config = self._llm_config()
        if config.get("provider") == "gemini":
            return self._call_gemini_native(payload, config)
        if not config["api_url"] or not config["api_key"]:
            raise Exception("LLM 호출 설정이 없습니다. LLM_API_URL/LLM_API_KEY 또는 OPENAI_API_BASE/OPENAI_API_KEY를 설정해야 합니다.")

        body = {
            "model": config["model"],
            "messages": self._build_messages(payload),
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            config["api_url"],
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise Exception(f"LLM 호출 실패: HTTP {e.code} {detail[:500]}")
        except Exception as e:
            raise Exception(f"LLM 호출 실패: {e}")

        data = json.loads(raw)
        content = ""
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = raw
        return self._extract_json(content)

    def _call_gemini_native(self, payload, config):
        if not config["api_key"]:
            raise Exception("Gemini 호출 설정이 없습니다. LLM_API_KEY 또는 GOOGLE_API_KEY를 설정해야 합니다.")

        api_url = (
            config.get("api_url")
            or f"https://generativelanguage.googleapis.com/v1beta/models/{config['model']}:generateContent"
        )

        messages = self._build_messages(payload)
        prompt = "\n\n".join([message["content"] for message in messages if message.get("content")])
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": config["api_key"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise Exception(f"Gemini 호출 실패: HTTP {e.code} {detail[:500]}")
        except Exception as e:
            raise Exception(f"Gemini 호출 실패: {e}")

        data = json.loads(raw)
        parts = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text"):
                    parts.append(part["text"])
        return self._extract_json("\n".join(parts) or raw)

    def _options(self, value):
        if isinstance(value, dict):
            return {str(key): str(value[key]).strip() for key in sorted(value.keys(), key=lambda x: str(x))}
        if isinstance(value, list):
            return {str(index): str(option).strip() for index, option in enumerate(value, start=1)}
        return {}

    def _evidence_by_id(self, payload):
        rows = {}
        for item in payload.get("source_evidence") or []:
            chunk_id = item.get("chunk_id")
            if chunk_id:
                rows[chunk_id] = item
        return rows

    def normalize_question(self, raw, payload):
        scope = payload.get("scope") or {}
        evidence = payload.get("source_evidence") or []
        evidence_by_id = self._evidence_by_id(payload)
        if not isinstance(raw, dict):
            raise Exception("생성 문항은 JSON object여야 합니다.")

        refs = raw.get("evidence_refs") or raw.get("source_evidence") or []
        if isinstance(refs, dict):
            refs = [refs]
        normalized_refs = []
        source_chunks = []
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            chunk_id = ref.get("chunk_id")
            matched = evidence_by_id.get(chunk_id) if chunk_id else None
            if matched is None and evidence:
                matched = evidence[0]
                chunk_id = matched.get("chunk_id")
            normalized_refs.append(dict(
                source_file=ref.get("source_file") or (matched or {}).get("source_file", ""),
                page_or_slide=ref.get("page_or_slide") or (matched or {}).get("page_or_slide", ""),
                chunk_id=chunk_id or "",
            ))
            if matched and matched.get("content"):
                source_chunks.append(matched.get("content"))

        if not normalized_refs and evidence:
            for item in evidence[:3]:
                normalized_refs.append(dict(
                    source_file=item.get("source_file", ""),
                    page_or_slide=item.get("page_or_slide", ""),
                    chunk_id=item.get("chunk_id", ""),
                ))
                if item.get("content"):
                    source_chunks.append(item.get("content"))

        first_evidence = evidence[0] if evidence else {}
        question = dict(
            period=raw.get("period") or scope.get("period", ""),
            subject=raw.get("subject") or scope.get("subject", ""),
            field=raw.get("field") or scope.get("field", ""),
            area=raw.get("area") or scope.get("area", ""),
            detail=raw.get("detail") or scope.get("detail", ""),
            scope_id=raw.get("scope_id") or first_evidence.get("scope_id", ""),
            learning_objective_id=raw.get("learning_objective_id") or first_evidence.get("learning_objective_id", ""),
            learning_objective=raw.get("learning_objective") or first_evidence.get("learning_objective", ""),
            question_type=raw.get("question_type") or payload.get("question_type", ""),
            competency_type=raw.get("competency_type") or "",
            difficulty=raw.get("difficulty") or payload.get("difficulty", ""),
            stem=raw.get("stem") or raw.get("question") or raw.get("question_text") or "",
            options=self._options(raw.get("options")),
            answer=str(raw.get("answer") or "").strip(),
            explanation=raw.get("explanation") or raw.get("answer_explanation") or raw.get("rationale") or "",
            wrong_option_explanations=raw.get("wrong_option_explanations") or {},
            evidence_refs=normalized_refs,
            source_chunks=source_chunks,
            distractor_strategy=raw.get("distractor_strategy") or "",
            validation_status="generated",
            status="generated",
        )
        return question

    def _question_list(self, generated):
        if isinstance(generated, dict) and isinstance(generated.get("questions"), list):
            return generated["questions"]
        if isinstance(generated, list):
            return generated
        if isinstance(generated, dict):
            return [generated]
        raise Exception("LLM 생성 결과에서 문항 목록을 찾지 못했습니다.")

    def validate_generated(self, generated, payload):
        normalized = []
        for raw in self._question_list(generated):
            question = self.normalize_question(raw, payload)
            report = self.validation.validate(
                question,
                mvp_only=True,
                source_chunks=question.get("source_chunks"),
                require_source_chunks=True,
            )
            next_status = report.get("storage", {}).get("next_status_if_passed") or "generated"
            if not report.get("ok"):
                next_status = "rejected" if report.get("error_count", 0) > 0 else "needs_review"
            question["validation_status"] = next_status
            question["status"] = next_status
            question["reviewer_agent_results"] = report.get("agents")
            question["final_judge"] = report.get("judge")
            normalized.append(dict(question=question, validation_report=report))
        return normalized

    def run(self, payload, generated_override=None):
        generated = generated_override if generated_override is not None else self.call_llm(payload)
        validated = self.validate_generated(generated, payload)
        statuses = [item["question"]["status"] for item in validated]
        if validated and all(status == "reviewed" for status in statuses):
            final_status = "auto_reviewed"
        elif any(status == "rejected" for status in statuses):
            final_status = "rejected"
        else:
            final_status = "needs_review"
        return dict(
            generated_at=self._now(),
            raw_generation=generated,
            validated_questions=validated,
            final_status=final_status,
        )


Model = QuestionGenerator
