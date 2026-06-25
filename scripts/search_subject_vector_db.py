#!/usr/bin/env python3
"""Search the local subject-reference vector DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from local_onnx_embedder import DEFAULT_MODEL_DIR, LocalE5Embedder


DEFAULT_DB_DIR = PROJECT_ROOT / "resources" / "vector_db" / "subject_references"


def load_chunk(conn: sqlite3.Connection, embedding_index: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT embedding_index, chunk_id, source_file, page_or_slide, chunk_index,
               excerpt, metadata_json
        FROM chunks
        WHERE embedding_index = ?
        """,
        (int(embedding_index),),
    ).fetchone()
    if not row:
        raise KeyError(f"Missing chunk row for embedding index {embedding_index}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a readable report")
    args = parser.parse_args()

    embeddings = np.load(args.db_dir / "embeddings.npy")
    embedder = LocalE5Embedder(args.model_dir)
    query_vector = embedder.embed_queries([args.query], batch_size=1)[0]
    scores = embeddings @ query_vector
    top_indices = np.argsort(-scores)[: args.top_k]

    conn = sqlite3.connect(args.db_dir / "chunks.sqlite")
    try:
        results = []
        for rank, index in enumerate(top_indices, start=1):
            chunk = load_chunk(conn, int(index))
            metadata = chunk["metadata"]
            results.append({
                "rank": rank,
                "score": float(scores[index]),
                "chunk_id": chunk["chunk_id"],
                "source_file": chunk["source_file"],
                "page_or_slide": chunk["page_or_slide"],
                "chunk_index": chunk["chunk_index"],
                "exam_period": metadata.get("exam_period", ""),
                "subject": metadata.get("mapped_subject") or metadata.get("subject", ""),
                "field": metadata.get("mapped_field") or metadata.get("field", ""),
                "area": metadata.get("mapped_area") or metadata.get("area", ""),
                "sub_area": metadata.get("mapped_detail") or metadata.get("sub_area", ""),
                "scope_id": metadata.get("mapped_scope_id") or metadata.get("scope_id", ""),
                "learning_objective_id": metadata.get("learning_objective_id", ""),
                "learning_objective": metadata.get("learning_objective", ""),
                "question_generation_target_id": metadata.get("question_generation_target_id", ""),
                "candidate_rag_status": metadata.get("candidate_rag_status", ""),
                "rag_use_policy": metadata.get("rag_use_policy", ""),
                "excerpt": chunk["excerpt"],
            })
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"query": args.query, "results": results}, ensure_ascii=False, indent=2))
        return

    print(f"query: {args.query}")
    for result in results:
        print()
        print(f"[{result['rank']}] score={result['score']:.4f}")
        print(f"source={result['source_file']} page={result['page_or_slide']} chunk={result['chunk_id']}")
        print(
            "scope="
            f"{result['exam_period']} / {result['subject']} / {result['field']} / "
            f"{result['area']} / {result['sub_area']}"
        )
        print(f"rag_status={result['candidate_rag_status']} policy={result['rag_use_policy']}")
        print(f"objective={result['learning_objective']}")
        print(f"excerpt={result['excerpt']}")


if __name__ == "__main__":
    main()
