#!/usr/bin/env python3
"""Search the text-only RAG BM25 index."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"


def query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[0-9A-Za-z가-힣]+", query) if token.strip()]


def fts_query(query: str) -> str:
    tokens = query_tokens(query)
    if not tokens:
        safe = query.replace('"', '""')
        return f'"{safe}"'
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def search(
    db_path: Path,
    query: str,
    top_k: int,
    period: str = "",
    subject: str = "",
    field: str = "",
    area: str = "",
    min_confidence: str = "",
) -> list[dict[str, Any]]:
    where = ["rag_fts MATCH ?"]
    params: list[Any] = [fts_query(query)]
    if period:
        where.append("chunks.mapped_period = ?")
        params.append(period)
    if subject:
        where.append("chunks.mapped_subject = ?")
        params.append(subject)
    if field:
        where.append("chunks.mapped_field = ?")
        params.append(field)
    if area:
        where.append("chunks.mapped_area = ?")
        params.append(area)
    if min_confidence:
        allowed = {
            "high": ["high"],
            "medium": ["high", "medium"],
            "area_only": ["high", "medium", "area_only"],
        }.get(min_confidence, [])
        if allowed:
            placeholders = ",".join("?" for _ in allowed)
            where.append(f"chunks.scope_mapping_confidence IN ({placeholders})")
            params.extend(allowed)

    params.append(top_k)
    sql = f"""
        SELECT
            chunks.rag_input_id,
            chunks.source_file,
            chunks.page_or_slide,
            chunks.excerpt,
            chunks.mapped_period,
            chunks.mapped_subject,
            chunks.mapped_field,
            chunks.mapped_area,
            chunks.mapped_detail,
            chunks.mapped_scope_id,
            chunks.scope_mapping_status,
            chunks.scope_mapping_confidence,
            chunks.scope_mapping_needs_review,
            bm25(rag_fts, 1.0, 0.35) AS score
        FROM rag_fts
        JOIN chunks ON chunks.doc_id = rag_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    results = []
    for rank, row in enumerate(rows, start=1):
        results.append(
            {
                "rank": rank,
                "score": row[13],
                "rag_input_id": row[0],
                "source_file": row[1],
                "page_or_slide": row[2],
                "excerpt": row[3],
                "period": row[4],
                "subject": row[5],
                "field": row[6],
                "area": row[7],
                "detail": row[8],
                "scope_id": row[9],
                "scope_mapping_status": row[10],
                "scope_mapping_confidence": row[11],
                "scope_mapping_needs_review": bool(row[12]),
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--period", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--field", default="")
    parser.add_argument("--area", default="")
    parser.add_argument("--min-confidence", choices=["high", "medium", "area_only"], default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = search(
        args.db,
        args.query,
        args.top_k,
        period=args.period,
        subject=args.subject,
        field=args.field,
        area=args.area,
        min_confidence=args.min_confidence,
    )

    if args.json:
        print(json.dumps({"query": args.query, "results": results}, ensure_ascii=False, indent=2))
        return

    print(f"query: {args.query}")
    for result in results:
        print()
        print(f"[{result['rank']}] score={result['score']:.6f}")
        print(f"source={result['source_file']} page={result['page_or_slide']} id={result['rag_input_id']}")
        print(
            "scope="
            f"{result['period']} / {result['subject']} / {result['field']} / "
            f"{result['area']} / {result['detail']}"
        )
        print(
            "mapping="
            f"{result['scope_mapping_status']} / {result['scope_mapping_confidence']} / "
            f"needs_review={result['scope_mapping_needs_review']}"
        )
        print(f"excerpt={result['excerpt']}")


if __name__ == "__main__":
    main()
