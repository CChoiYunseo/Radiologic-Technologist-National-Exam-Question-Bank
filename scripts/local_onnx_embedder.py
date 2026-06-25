#!/usr/bin/env python3
"""Local ONNX embedding helper for subject-reference RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


DEFAULT_MODEL_DIR = Path("/opt/app/models/xenova-multilingual-e5-small")
DEFAULT_ONNX_FILE = "onnx/model_quantized.onnx"
DEFAULT_MAX_LENGTH = 512


class LocalE5Embedder:
    """Minimal multilingual-e5 ONNX embedder.

    E5 models expect a task prefix:
    - query: ...
    - passage: ...
    """

    def __init__(
        self,
        model_dir: Path | str = DEFAULT_MODEL_DIR,
        onnx_file: str = DEFAULT_ONNX_FILE,
        max_length: int = DEFAULT_MAX_LENGTH,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / onnx_file
        self.tokenizer_path = self.model_dir / "tokenizer.json"
        self.max_length = max_length

        if not self.model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {self.model_path}")
        if not self.tokenizer_path.exists():
            raise FileNotFoundError(f"Tokenizer not found: {self.tokenizer_path}")

        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(max_length=self.max_length)
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.input_names = {item.name for item in self.session.get_inputs()}

    def embed_passages(self, texts: Iterable[str], batch_size: int = 16) -> np.ndarray:
        return self._embed([self._prefixed("passage", text) for text in texts], batch_size=batch_size)

    def embed_queries(self, texts: Iterable[str], batch_size: int = 16) -> np.ndarray:
        return self._embed([self._prefixed("query", text) for text in texts], batch_size=batch_size)

    def _embed(self, texts: list[str], batch_size: int) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)

        vectors = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.append(self._embed_batch(batch))
        return np.vstack(vectors).astype(np.float32)

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        encoded = self.tokenizer.encode_batch(texts)
        max_len = min(self.max_length, max(len(item.ids) for item in encoded))

        input_ids = np.zeros((len(encoded), max_len), dtype=np.int64)
        attention_mask = np.zeros((len(encoded), max_len), dtype=np.int64)
        for row_index, item in enumerate(encoded):
            ids = item.ids[:max_len]
            mask = item.attention_mask[:max_len]
            input_ids[row_index, : len(ids)] = ids
            attention_mask[row_index, : len(mask)] = mask

        feed = {}
        if "input_ids" in self.input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self.input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

        last_hidden_state = self.session.run(None, feed)[0].astype(np.float32)
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (last_hidden_state * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        return self._l2_normalize(pooled)

    @staticmethod
    def _prefixed(kind: str, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("query:") or text.startswith("passage:"):
            return text
        return f"{kind}: {text}"

    @staticmethod
    def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
        return vectors / np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12, None)
