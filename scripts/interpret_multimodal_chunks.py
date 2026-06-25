#!/usr/bin/env python3
"""Interpret extracted table/formula/figure chunks for future item generation.

This script does not approve visual assets for direct use. It turns extracted
multimodal candidates into reviewable semantic records by attaching internal
RAG search results and, optionally, asking an LLM/Vision model to describe and
canonicalize the table, formula, or diagram.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from local_onnx_embedder import DEFAULT_MODEL_DIR, LocalE5Embedder


DEFAULT_INPUT = PROJECT_ROOT / "resources" / "extracted" / "device_pages_176_180_multimodal_semantic_schema" / "chunks_all.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "resources" / "interpreted" / "multimodal_chunks_interpreted.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "resources" / "interpreted" / "multimodal_interpretation_report.json"
DEFAULT_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references"
DEFAULT_TEXT_MODEL = "Qwen/Qwen3-32B"
DEFAULT_VISION_MODEL = "Qwen/Qwen2.5-VL-32B-Instruct"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_json(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response does not contain a JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON response must be an object")
    return data


def default_interpretation(chunk_type: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "semantic_description": "",
        "key_concepts": [],
        "related_rag_queries": [],
        "canonical_representation": {
            "formula_latex": "",
            "formula_plain_text": "",
            "table_json": {},
            "table_markdown": "",
            "diagram_spec": "",
            "chart_data": {},
        },
        "source_crosscheck": {
            "status": "pending",
            "supporting_chunk_ids": [],
            "supporting_source_pages": [],
            "confidence_score": 0.0,
            "notes": "",
        },
        "reconstruction_prompt": "",
        "generation_use_policy": (
            "Do not copy source visuals verbatim. Use the preserved crop, caption, "
            "nearby text, and RAG cross-check to create a new equivalent question "
            "asset only after review."
        ),
        "can_be_used_for_generation": False,
        "can_be_reconstructed": False,
        "requires_vision_llm": chunk_type in {"figure", "diagram"},
        "requires_human_review": True,
    }


def seed_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    structured = chunk.get("structured_content") or {}
    return structured.get("multimodal_seed") or {}


def build_queries(chunk: dict[str, Any], max_queries: int = 4) -> list[str]:
    structured = chunk.get("structured_content") or {}
    seed = seed_from_chunk(chunk)
    queries = []
    for query in seed.get("related_rag_query_candidates") or []:
        query = clean_text(query)
        if query and query not in queries:
            queries.append(query)
    caption = clean_text(structured.get("caption", ""))
    if caption and caption not in queries:
        queries.append(caption)
    keywords = seed.get("keyword_candidates") or []
    if keywords:
        query = clean_text(" ".join(str(item) for item in keywords[:7]))
        if query and query not in queries:
            queries.append(query)
    nearby = clean_text(structured.get("nearby_text", ""))
    if nearby:
        compact = " ".join(nearby.split()[:24])
        if compact and compact not in queries:
            queries.append(compact)
    return queries[:max_queries]


class VectorSearcher:
    def __init__(self, db_dir: Path, model_dir: Path):
        self.available = False
        self.embeddings: np.ndarray | None = None
        self.conn: sqlite3.Connection | None = None
        self.embedder: LocalE5Embedder | None = None
        if not (db_dir / "embeddings.npy").exists() or not (db_dir / "chunks.sqlite").exists():
            return
        self.embeddings = np.load(db_dir / "embeddings.npy")
        self.conn = sqlite3.connect(db_dir / "chunks.sqlite")
        self.embedder = LocalE5Embedder(model_dir)
        self.available = True

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def load_chunk(self, embedding_index: int) -> dict[str, Any] | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            """
            SELECT embedding_index, chunk_id, source_file, page_or_slide, chunk_index,
                   excerpt, metadata_json
            FROM chunks
            WHERE embedding_index = ?
            """,
            (int(embedding_index),),
        ).fetchone()
        if not row:
            return None
        metadata = json.loads(row[6])
        return {
            "embedding_index": row[0],
            "chunk_id": row[1],
            "source_file": row[2],
            "page_or_slide": row[3],
            "chunk_index": row[4],
            "excerpt": row[5],
            "metadata": metadata,
        }

    def search_many(self, queries: list[str], top_k: int) -> list[dict[str, Any]]:
        if not self.available or self.embeddings is None or self.embedder is None:
            return []
        seen = set()
        merged: list[dict[str, Any]] = []
        for query in queries:
            vector = self.embedder.embed_queries([query], batch_size=1)[0]
            scores = self.embeddings @ vector
            top_indices = np.argsort(-scores)[:top_k]
            for index in top_indices:
                chunk = self.load_chunk(int(index))
                if not chunk:
                    continue
                key = chunk["chunk_id"]
                if key in seen:
                    continue
                seen.add(key)
                metadata = chunk["metadata"]
                merged.append({
                    "score": float(scores[index]),
                    "query": query,
                    "chunk_id": chunk["chunk_id"],
                    "source_file": chunk["source_file"],
                    "page_or_slide": chunk["page_or_slide"],
                    "chunk_index": chunk["chunk_index"],
                    "exam_period": metadata.get("exam_period", ""),
                    "subject": metadata.get("subject", ""),
                    "field": metadata.get("field", ""),
                    "area": metadata.get("area", ""),
                    "sub_area": metadata.get("sub_area", ""),
                    "learning_objective": metadata.get("learning_objective", ""),
                    "excerpt": chunk["excerpt"],
                })
        merged.sort(key=lambda row: row["score"], reverse=True)
        return merged[:top_k]


def llm_config(args: argparse.Namespace) -> dict[str, str]:
    provider = (args.provider or os.environ.get("LLM_PROVIDER", "")).strip().lower()
    model = (args.model or os.environ.get("LLM_MODEL", "")).strip()
    vision_model = (args.vision_model or os.environ.get("VISION_LLM_MODEL", "")).strip()
    api_url = os.environ.get("LLM_API_URL", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    vision_api_url = os.environ.get("VISION_LLM_API_URL", "").strip()
    vision_api_key = os.environ.get("VISION_LLM_API_KEY", "").strip()
    if provider == "gemini":
        api_key = api_key or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
        model = model or "gemini-3.5-flash"
        return {
            "provider": "gemini",
            "model": model,
            "vision_model": vision_model or model,
            "api_url": api_url,
            "api_key": api_key,
            "vision_api_url": vision_api_url or api_url,
            "vision_api_key": vision_api_key or api_key,
        }
    if not provider:
        provider = "qwen_compatible"
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
    api_url = api_url or os.environ.get("OPENAI_API_BASE", "").strip()
    if api_url and not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    if vision_api_url and not vision_api_url.endswith("/chat/completions"):
        vision_api_url = vision_api_url.rstrip("/") + "/chat/completions"
    model = model or os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_TEXT_MODEL
    vision_model = vision_model or DEFAULT_VISION_MODEL
    return {
        "provider": provider,
        "model": model,
        "vision_model": vision_model,
        "api_url": api_url,
        "api_key": api_key,
        "vision_api_url": vision_api_url or api_url,
        "vision_api_key": vision_api_key or api_key,
    }


def mime_type_for(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "image/png"


def build_prompt(chunk: dict[str, Any], rag_results: list[dict[str, Any]]) -> str:
    structured = chunk.get("structured_content") or {}
    seed = seed_from_chunk(chunk)
    schema = default_interpretation(chunk.get("chunk_type", ""))
    payload = {
        "task": "방사선사 국가고시 문제 생성을 위한 표/수식/그림 의미 해석",
        "rules": [
            "원본 표, 그림, 수식, 문장을 그대로 복사하지 않는다.",
            "캡션, 주변 본문, 원본 crop 이미지, 내부 RAG 검색 결과를 함께 사용한다.",
            "추출이 잘못되었을 가능성이 있으면 source_crosscheck.notes에 명시한다.",
            "문제 생성에 쓸 수 있는 정식 표현을 canonical_representation에 작성한다.",
            "새 문제에서 사용할 수 있도록 원본을 복사하지 않는 reconstruction_prompt를 작성한다.",
            "근거가 부족하면 can_be_used_for_generation=false를 유지한다.",
        ],
        "chunk": {
            "chunk_id": chunk.get("chunk_id"),
            "chunk_type": chunk.get("chunk_type"),
            "source_file": chunk.get("source_file"),
            "page_or_slide": chunk.get("page_or_slide"),
            "content": chunk.get("content"),
            "caption": structured.get("caption", ""),
            "nearby_text": structured.get("nearby_text", ""),
            "context_before": structured.get("context_before", ""),
            "context_after": structured.get("context_after", ""),
            "multimodal_seed": seed,
        },
        "rag_results": rag_results,
        "output_schema": schema,
    }
    return (
        "아래 JSON 입력을 해석하고 output_schema와 같은 JSON object만 출력하라.\n"
        "반드시 한국어로 작성하라.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def call_gemini(prompt: str, image_path: str, config: dict[str, str], include_image: bool) -> dict[str, Any]:
    api_url = config.get("api_url") or f"https://generativelanguage.googleapis.com/v1beta/models/{config['model']}:generateContent"
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if include_image and image_path and Path(image_path).exists():
        raw = Path(image_path).read_bytes()
        parts.append({
            "inline_data": {
                "mime_type": mime_type_for(image_path),
                "data": base64.b64encode(raw).decode("ascii"),
            }
        })
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": config["api_key"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw_response = res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail[:600]}")
    data = json.loads(raw_response)
    texts = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("text"):
                texts.append(part["text"])
    return extract_json("\n".join(texts) or raw_response)


def call_openai_compatible(prompt: str, config: dict[str, str], image_path: str = "", include_image: bool = False) -> dict[str, Any]:
    use_vision = bool(include_image and image_path and Path(image_path).exists())
    api_url = config.get("vision_api_url") if use_vision else config.get("api_url")
    api_key = config.get("vision_api_key") if use_vision else config.get("api_key")
    model = config.get("vision_model") if use_vision else config.get("model")
    if not api_url:
        raise RuntimeError("OpenAI-compatible API URL is missing")

    user_content: Any = prompt
    if use_vision:
        raw = Path(image_path).read_bytes()
        data_url = (
            f"data:{mime_type_for(image_path)};base64,"
            + base64.b64encode(raw).decode("ascii")
        )
        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "JSON으로만 답하는 보건의료 국가시험 자료 해석자이다."},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw_response = res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:600]}")
    data = json.loads(raw_response)
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return extract_json(content or raw_response)


def heuristic_interpretation(chunk: dict[str, Any], rag_results: list[dict[str, Any]], queries: list[str]) -> dict[str, Any]:
    chunk_type = chunk.get("chunk_type", "")
    structured = chunk.get("structured_content") or {}
    seed = seed_from_chunk(chunk)
    interp = default_interpretation(chunk_type)
    caption = clean_text(structured.get("caption", ""))
    nearby = clean_text(structured.get("nearby_text", ""))
    keywords = seed.get("keyword_candidates") or []
    description_parts = []
    if caption:
        description_parts.append(caption)
    if seed.get("semantic_hint"):
        description_parts.append(seed["semantic_hint"])
    if nearby:
        description_parts.append("주변 본문: " + " ".join(nearby.split()[:60]))
    interp["status"] = "heuristic_draft"
    interp["semantic_description"] = clean_text(" ".join(description_parts))[:900]
    interp["key_concepts"] = keywords[:10]
    interp["related_rag_queries"] = queries
    canonical_seed = seed.get("canonical_representation_seed") or {}
    if chunk_type == "table":
        interp["canonical_representation"]["table_markdown"] = canonical_seed.get("table_markdown", "")
        interp["canonical_representation"]["table_json"] = canonical_seed.get("table_json", [])
    if chunk_type == "formula":
        interp["canonical_representation"]["formula_plain_text"] = canonical_seed.get("formula_plain_text", "")
    if chunk_type in {"figure", "diagram"}:
        interp["canonical_representation"]["diagram_spec"] = (
            "Vision LLM 검수 후 축/구성요소/관계를 새 도식으로 재작성해야 함"
        )
    interp["source_crosscheck"] = {
        "status": "rag_attached" if rag_results else "no_rag_results",
        "supporting_chunk_ids": [row["chunk_id"] for row in rag_results[:3]],
        "supporting_source_pages": [
            {"source_file": row["source_file"], "page_or_slide": row["page_or_slide"], "score": row["score"]}
            for row in rag_results[:3]
        ],
        "confidence_score": float(rag_results[0]["score"]) if rag_results else 0.0,
        "notes": "LLM 호출 전 초안이므로 사람/LLM 검수가 필요함",
    }
    interp["reconstruction_prompt"] = seed.get("reconstruction_prompt_seed", "")
    interp["can_be_used_for_generation"] = False
    interp["can_be_reconstructed"] = False
    interp["requires_human_review"] = True
    return interp


def normalize_interpretation(raw: dict[str, Any], chunk_type: str) -> dict[str, Any]:
    base = default_interpretation(chunk_type)
    for key, value in raw.items():
        if key in base:
            if isinstance(base[key], dict) and isinstance(value, dict):
                nested = base[key]
                for nkey, nvalue in value.items():
                    if nkey in nested:
                        if isinstance(nested[nkey], dict) and isinstance(nvalue, dict):
                            nested[nkey].update(nvalue)
                        else:
                            nested[nkey] = nvalue
                base[key] = nested
            else:
                base[key] = value
    base["requires_human_review"] = True
    if base.get("can_be_used_for_generation"):
        base["can_be_used_for_generation"] = False
        base["source_crosscheck"]["notes"] = clean_text(
            str(base["source_crosscheck"].get("notes", ""))
            + " 자동 해석 결과는 바로 승인하지 않고 검수 후 사용해야 함"
        )
    return base


def interpret_chunk(
    chunk: dict[str, Any],
    searcher: VectorSearcher,
    args: argparse.Namespace,
    config: dict[str, str],
) -> dict[str, Any]:
    queries = build_queries(chunk, max_queries=args.max_queries)
    rag_results = searcher.search_many(queries, top_k=args.top_k)
    prompt = build_prompt(chunk, rag_results)
    used_llm = False
    error = ""
    if args.call_llm:
        if not config.get("api_key"):
            error = "missing_api_key"
        else:
            try:
                if config["provider"] == "gemini":
                    raw = call_gemini(prompt, chunk.get("source_image_path", ""), config, include_image=args.include_images)
                else:
                    raw = call_openai_compatible(
                        prompt,
                        config,
                        image_path=chunk.get("source_image_path", ""),
                        include_image=args.include_images,
                    )
                interpretation = normalize_interpretation(raw, chunk.get("chunk_type", ""))
                interpretation["status"] = "llm_interpreted"
                used_llm = True
            except Exception as exc:
                error = str(exc)
    if not used_llm:
        interpretation = heuristic_interpretation(chunk, rag_results, queries)
        if error:
            interpretation["source_crosscheck"]["notes"] = clean_text(
                interpretation["source_crosscheck"].get("notes", "") + f" LLM 호출 실패: {error}"
            )
    updated = dict(chunk)
    updated["multimodal_interpretation"] = interpretation
    updated["multimodal_interpretation_context"] = {
        "interpreted_at": now_iso(),
        "used_llm": used_llm,
        "llm_provider": config.get("provider", "") if used_llm else "",
        "llm_model": config.get("model", "") if used_llm else "",
        "rag_query_count": len(queries),
        "rag_result_count": len(rag_results),
        "rag_results": rag_results,
        "llm_error": error,
    }
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--vector-db", type=Path, default=DEFAULT_VECTOR_DB)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--call-llm", action="store_true")
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--vision-model", default="")
    args = parser.parse_args()

    chunks = [
        row for row in read_jsonl(args.input)
        if row.get("chunk_type") in {"table", "formula", "figure", "diagram"}
    ]
    if args.limit:
        chunks = chunks[:args.limit]

    config = llm_config(args)
    searcher = VectorSearcher(args.vector_db, args.model_dir)
    interpreted = []
    errors = []
    try:
        for index, chunk in enumerate(chunks, start=1):
            try:
                interpreted.append(interpret_chunk(chunk, searcher, args, config))
            except Exception as exc:
                row = dict(chunk)
                row["multimodal_interpretation"] = heuristic_interpretation(chunk, [], build_queries(chunk, args.max_queries))
                row["multimodal_interpretation_context"] = {
                    "interpreted_at": now_iso(),
                    "used_llm": False,
                    "rag_result_count": 0,
                    "llm_error": str(exc),
                }
                interpreted.append(row)
                errors.append({"index": index, "chunk_id": chunk.get("chunk_id"), "error": str(exc)})
    finally:
        searcher.close()

    write_jsonl(args.output, interpreted)
    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for row in interpreted:
        status = (row.get("multimodal_interpretation") or {}).get("status", "")
        status_counts[status] = status_counts.get(status, 0) + 1
        chunk_type = row.get("chunk_type", "")
        type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1

    report = {
        "created_at": now_iso(),
        "input": str(args.input),
        "output": str(args.output),
        "chunk_count": len(interpreted),
        "chunk_type_counts": type_counts,
        "status_counts": status_counts,
        "vector_db_available": searcher.available,
        "call_llm": bool(args.call_llm),
        "include_images": bool(args.include_images),
        "provider": config.get("provider", ""),
        "model": config.get("model", ""),
        "errors": errors,
    }
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
