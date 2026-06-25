#!/usr/bin/env python3
"""Build local vector DB for subject-reference RAG chunks."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from local_onnx_embedder import DEFAULT_MODEL_DIR, LocalE5Embedder


DEFAULT_INPUT = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "vector_db" / "subject_references"


METADATA_FIELDS = [
    "rag_input_id",
    "source_chunk_id",
    "candidate_record_id",
    "chunk_id",
    "document_id",
    "source_file",
    "source_path",
    "source_path_exists",
    "source_kind",
    "source_type",
    "chunk_type",
    "page_or_slide",
    "page",
    "chunk_index",
    "content_hash",
    "content_sha256",
    "token_estimate",
    "content_chars",
    "extraction_method",
    "extraction_quality",
    "ocr_confidence",
    "exam_period",
    "subject",
    "mapped_subject",
    "field",
    "mapped_field",
    "area",
    "mapped_area",
    "sub_area",
    "mapped_detail",
    "scope_id",
    "mapped_scope_id",
    "learning_objective_id",
    "learning_objective",
    "learning_objective_candidates",
    "question_generation_target_id",
    "recommended_question_types",
    "scope_mapping_confidence",
    "scope_mapping_status",
    "scope_mapping_needs_review",
    "learning_objective_mapping_confidence",
    "scope_objective_mapping_status",
    "scope_objective_mapping_needs_review",
    "human_review",
    "needs_review",
    "approved_for_rag_evidence",
    "approved_for_generation",
    "candidate_rag_status",
    "rag_use_policy",
    "copyright_use_policy",
    "generation_hold_reasons",
    "candidate_reasons",
    "review_reason",
    "created_at",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def metadata_for(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in METADATA_FIELDS if field in row}


def row_chunk_id(row: dict[str, Any]) -> str:
    return str(row.get("rag_input_id") or row.get("chunk_id") or row.get("source_chunk_id") or "")


def row_page(row: dict[str, Any]) -> int:
    value = row.get("page_or_slide", row.get("page", 0))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def row_chunk_index(row: dict[str, Any], fallback: int) -> int:
    value = row.get("chunk_index")
    try:
        return int(value if value is not None else fallback)
    except (TypeError, ValueError):
        return fallback


def row_mapping_status(row: dict[str, Any]) -> str:
    return str(row.get("scope_mapping_status") or row.get("scope_objective_mapping_status") or "")


def row_scope_name(row: dict[str, Any]) -> str:
    return str(row.get("mapped_detail") or row.get("sub_area") or row.get("mapped_area") or row.get("area") or "none")


def build_excerpt(content: str, limit: int = 320) -> str:
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def recreate_sqlite(db_path: Path, chunks: list[dict[str, Any]]) -> None:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE chunks (
                embedding_index INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL,
                page_or_slide INTEGER,
                chunk_index INTEGER,
                content TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX idx_chunks_chunk_id ON chunks(chunk_id)")
        conn.execute("CREATE INDEX idx_chunks_source_page ON chunks(source_file, page_or_slide)")
    except sqlite3.OperationalError:
        # Keep DB creation tolerant across SQLite versions.
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manifest (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for index, row in enumerate(chunks):
        metadata = metadata_for(row)
        chunk_id = row_chunk_id(row)
        if not chunk_id:
            chunk_id = f"embedding_row_{index:08d}"
        conn.execute(
            """
            INSERT INTO chunks (
                embedding_index, chunk_id, source_file, page_or_slide, chunk_index,
                content, excerpt, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                index,
                chunk_id,
                row.get("source_file", ""),
                row_page(row),
                row_chunk_index(row, index),
                row.get("content", ""),
                build_excerpt(row.get("content", "")),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()


def write_manifest(
    output_dir: Path,
    input_path: Path,
    model_dir: Path,
    chunks: list[dict[str, Any]],
    embeddings: np.ndarray,
    batch_size: int,
    include_excluded: bool,
    include_needs_review: bool,
    exclude_generation_hold: bool,
) -> dict[str, Any]:
    manifest = {
        "version": 1,
        "purpose": "local_subject_reference_vector_db",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_chunks": str(input_path),
        "model_dir": str(model_dir),
        "embedding_model": "Xenova/multilingual-e5-small",
        "embedding_runtime": "onnxruntime",
        "embedding_file": "embeddings.npy",
        "sqlite_file": "chunks.sqlite",
        "chunk_count": len(chunks),
        "embedding_shape": list(embeddings.shape),
        "embedding_dtype": str(embeddings.dtype),
        "batch_size": batch_size,
        "include_excluded": include_excluded,
        "include_needs_review": include_needs_review,
        "exclude_generation_hold": exclude_generation_hold,
        "index_use": "generation_safe_candidate_search" if exclude_generation_hold else "rag_evidence_search",
        "mapping_status_counts": dict(Counter(row_mapping_status(row) for row in chunks)),
        "scope_counts": dict(Counter(row_scope_name(row) for row in chunks)),
        "source_file_counts": dict(Counter(row.get("source_file", "") for row in chunks)),
        "metadata_fields": METADATA_FIELDS,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument(
        "--include-excluded",
        action="store_true",
        help="Include chunks marked excluded_from_auto_mapping. Default excludes front matter/contents.",
    )
    parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="Include chunks still marked needs_review. Default excludes them.",
    )
    parser.add_argument(
        "--exclude-generation-hold",
        action="store_true",
        help="Exclude chunks with generation_hold_reasons. Use for automatic question-generation candidate search.",
    )
    args = parser.parse_args()

    chunks = read_jsonl(args.input)
    chunks = [
        row
        for row in chunks
        if row.get("content")
        and row.get("mvp_text_rag_ready", True)
        and row.get("approved_for_rag_evidence", True)
    ]
    if not args.include_excluded:
        chunks = [
            row
            for row in chunks
            if row_mapping_status(row) not in {"excluded_from_auto_mapping", "excluded"}
        ]
    if not args.include_needs_review:
        chunks = [row for row in chunks if row_mapping_status(row) != "needs_review" and not row.get("needs_review")]
    if args.exclude_generation_hold:
        chunks = [row for row in chunks if not row.get("generation_hold_reasons")]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    embedder = LocalE5Embedder(args.model_dir)
    embeddings = embedder.embed_passages([row["content"] for row in chunks], batch_size=args.batch_size)

    np.save(args.output_dir / "embeddings.npy", embeddings)
    recreate_sqlite(args.output_dir / "chunks.sqlite", chunks)
    manifest = write_manifest(
        args.output_dir,
        args.input,
        args.model_dir,
        chunks,
        embeddings,
        args.batch_size,
        args.include_excluded,
        args.include_needs_review,
        args.exclude_generation_hold,
    )

    print(json.dumps({
        "chunk_count": manifest["chunk_count"],
        "embedding_shape": manifest["embedding_shape"],
        "output_dir": str(args.output_dir),
        "mapping_status_counts": manifest["mapping_status_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
