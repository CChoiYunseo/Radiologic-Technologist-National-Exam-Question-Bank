#!/usr/bin/env python3
"""Build MVP text-only RAG chunks from extracted chunks and manual review notes."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACTED_DIR = PROJECT_ROOT / "resources" / "extracted" / "subject_references"
SOURCE_CHUNKS = EXTRACTED_DIR / "chunks.jsonl"
OUTPUT_CHUNKS = EXTRACTED_DIR / "chunks_mvp_text_ready.jsonl"
OUTPUT_REPORT = EXTRACTED_DIR / "mvp_text_ready_report.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_chunks() -> list[dict[str, Any]]:
    rows = []
    with SOURCE_CHUNKS.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value or "")


def load_manual_review_rules() -> dict[str, dict[int, list[str]]]:
    rules: dict[str, dict[int, list[str]]] = {}
    for path in sorted(EXTRACTED_DIR.glob("manual_review_notes*.json")):
        note = read_json(path)
        source = normalize(note.get("source_file", ""))
        if not source:
            continue
        pages = rules.setdefault(source, {})
        actions = note.get("page_actions", {})
        for page in actions.get("exclude_from_rag_cover_pages", []):
            pages.setdefault(int(page), []).append("manual_cover_page")
        for page in actions.get("exclude_from_rag_blank_pages", []):
            pages.setdefault(int(page), []).append("manual_blank_page")
        for page in actions.get("phase2_table_pages", []):
            pages.setdefault(int(page), []).append("phase2_table_page")
        for page in actions.get("figure_only_or_visual_pages", []):
            pages.setdefault(int(page), []).append("phase2_visual_page")
    return rules


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    chunks = load_chunks()
    rules = load_manual_review_rules()
    ready = []
    excluded = []

    for chunk in chunks:
        source = normalize(chunk.get("source_file", ""))
        page = int(chunk.get("page_or_slide") or 0)
        reasons = []

        if chunk.get("extraction_quality") == "low":
            reasons.append("low_quality")
        reasons.extend(rules.get(source, {}).get(page, []))

        if reasons:
            excluded.append({
                "chunk_id": chunk.get("chunk_id"),
                "source_file": chunk.get("source_file"),
                "page_or_slide": page,
                "extraction_quality": chunk.get("extraction_quality"),
                "exclude_reasons": sorted(set(reasons)),
            })
            continue

        row = dict(chunk)
        row["mvp_text_rag_ready"] = True
        ready.append(row)

    report = {
        "version": 1,
        "source_chunks": len(chunks),
        "mvp_text_ready_chunks": len(ready),
        "excluded_chunks": len(excluded),
        "exclude_reason_counts": dict(Counter(reason for row in excluded for reason in row["exclude_reasons"])),
        "ready_quality_counts": dict(Counter(row.get("extraction_quality") for row in ready)),
        "manual_review_files": [path.name for path in sorted(EXTRACTED_DIR.glob("manual_review_notes*.json"))],
        "excluded": excluded,
    }

    write_jsonl(OUTPUT_CHUNKS, ready)
    OUTPUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "excluded"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
