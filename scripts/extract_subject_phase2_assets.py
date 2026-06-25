#!/usr/bin/env python3
"""Extract Phase 2 assets: formulas, structured tables, and visual candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "materials" / "04_subject_references"
OUTPUT_DIR = PROJECT_ROOT / "resources" / "extracted" / "subject_references_phase2"
REVIEW_DIR = PROJECT_ROOT / "resources" / "extracted" / "subject_references"


FORMULA_PATTERNS = [
    re.compile(r"[A-Za-z가-힣0-9)\]]\s*[=＝]\s*[-+*/×÷\w가-힣(\\[{\ue000-\uf8ff]"),
    re.compile(r"[∑√πΩμθλαβγ∆Δ±≤≥≠∞∝]"),
    re.compile(r"[\ue000-\uf8ff]{2,}"),
    re.compile(r"\b(?:sin|cos|tan|log|ln)\b", re.IGNORECASE),
    re.compile(r"\[[A-Za-zΩμ/%·]+\]"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(*parts: Any, length: int = 32) -> str:
    raw = "::".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value or "")


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text.strip()


def table_to_markdown(table: list[list[Any]]) -> str:
    rows = []
    for row in table or []:
        cells = [clean_text(cell) for cell in row]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def load_manual_review_rules() -> dict[str, dict[str, set[int]]]:
    rules: dict[str, dict[str, set[int]]] = {}
    for path in sorted(REVIEW_DIR.glob("manual_review_notes*.json")):
        note = json.loads(path.read_text(encoding="utf-8"))
        source = normalize(note.get("source_file", ""))
        if not source:
            continue
        actions = note.get("page_actions", {})
        entry = rules.setdefault(source, {
            "visual_pages": set(),
            "table_pages": set(),
            "excluded_pages": set(),
        })
        entry["visual_pages"].update(int(p) for p in actions.get("figure_only_or_visual_pages", []))
        entry["table_pages"].update(int(p) for p in actions.get("phase2_table_pages", []))
        entry["excluded_pages"].update(int(p) for p in actions.get("exclude_from_rag_blank_pages", []))
        entry["excluded_pages"].update(int(p) for p in actions.get("exclude_from_rag_cover_pages", []))
    return rules


def is_formula_candidate(line: str) -> bool:
    text = clean_text(line)
    if len(text) < 2 or len(text) > 220:
        return False
    if not any(pattern.search(text) for pattern in FORMULA_PATTERNS):
        return False
    # Avoid classifying ordinary prose with a single parenthesized English term as a formula.
    if "=" not in text and not re.search(r"[∑√πΩμθλαβγ∆Δ±≤≥≠∞∝\ue000-\uf8ff]", text):
        return False
    return True


def extract_formula_candidates(doc: fitz.Document, pdf_path: Path, created_at: str, max_pages: int = 0) -> list[dict[str, Any]]:
    rows = []
    page_count = min(max_pages or len(doc), len(doc))
    for page_index in range(page_count):
        page_number = page_index + 1
        text = doc[page_index].get_text("text", sort=True)
        lines = [clean_text(line) for line in text.splitlines()]
        for line_index, line in enumerate(lines):
            if not is_formula_candidate(line):
                continue
            context_lines = lines[max(0, line_index - 1): min(len(lines), line_index + 2)]
            formula_id = stable_id(pdf_path.name, page_number, line_index, line)
            rows.append({
                "formula_id": formula_id,
                "source_file": pdf_path.name,
                "source_path": str(pdf_path.relative_to(PROJECT_ROOT)),
                "page": page_number,
                "line_index": line_index,
                "formula_text": line,
                "context": "\n".join(context_lines),
                "extraction_method": "pymupdf_formula_heuristic",
                "review_status": "needs_review",
                "created_at": created_at,
            })
    return rows


def extract_tables(pdf_path: Path, created_at: str, max_pages: int = 0) -> list[dict[str, Any]]:
    rows = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = min(max_pages or len(pdf.pages), len(pdf.pages))
        for page_index in range(page_count):
            page_number = page_index + 1
            page = pdf.pages[page_index]
            for table_index, table in enumerate(page.extract_tables() or [], start=1):
                markdown = table_to_markdown(table)
                if not markdown:
                    continue
                table_id = stable_id(pdf_path.name, page_number, table_index, markdown)
                rows.append({
                    "table_id": table_id,
                    "source_file": pdf_path.name,
                    "source_path": str(pdf_path.relative_to(PROJECT_ROOT)),
                    "page": page_number,
                    "table_index": table_index,
                    "cells": table,
                    "markdown": markdown,
                    "row_count": len(table or []),
                    "column_count": max((len(row or []) for row in table or []), default=0),
                    "extraction_method": "pdfplumber_structured_table",
                    "review_status": "needs_review",
                    "created_at": created_at,
                })
    return rows


def image_ratio(page: fitz.Page, info: dict[str, Any]) -> float:
    page_area = page.rect.width * page.rect.height
    bbox = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
    return (bbox.width * bbox.height) / page_area if page_area else 0


def render_clip(page: fitz.Page, clip: fitz.Rect | None, output_path: Path, dpi: int) -> None:
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(output_path))


def extract_visual_candidates(
    doc: fitz.Document,
    pdf_path: Path,
    rules: dict[str, dict[str, set[int]]],
    output_dir: Path,
    created_at: str,
    max_pages: int = 0,
    dpi: int = 180,
    min_area_ratio: float = 0.15,
    max_area_ratio: float = 0.9,
    drawing_threshold: int = 500,
) -> list[dict[str, Any]]:
    rows = []
    source_rules = rules.get(normalize(pdf_path.name), {})
    manual_visual_pages = source_rules.get("visual_pages", set())
    manual_table_pages = source_rules.get("table_pages", set())
    excluded_pages = source_rules.get("excluded_pages", set())
    page_count = min(max_pages or len(doc), len(doc))

    for page_index in range(page_count):
        page_number = page_index + 1
        if page_number in excluded_pages and page_number not in manual_visual_pages and page_number not in manual_table_pages:
            continue

        page = doc[page_index]
        drawings = len(page.get_drawings())
        image_infos = page.get_image_info(xrefs=True)
        significant_images = []
        for image_index, info in enumerate(image_infos, start=1):
            ratio = image_ratio(page, info)
            if min_area_ratio <= ratio <= max_area_ratio:
                significant_images.append((image_index, info, ratio))

        needs_page_render = page_number in manual_visual_pages or page_number in manual_table_pages or drawings >= drawing_threshold
        for image_index, info, ratio in significant_images:
            bbox = fitz.Rect(info["bbox"])
            asset_id = stable_id(pdf_path.name, page_number, "image", image_index, bbox)
            rel_path = Path("images") / f"{asset_id}.png"
            render_clip(page, bbox, output_dir / rel_path, dpi)
            rows.append({
                "asset_id": asset_id,
                "asset_type": "image_block",
                "source_file": pdf_path.name,
                "source_path": str(pdf_path.relative_to(PROJECT_ROOT)),
                "page": page_number,
                "image_index": image_index,
                "bbox": [round(x, 2) for x in [bbox.x0, bbox.y0, bbox.x1, bbox.y1]],
                "page_area_ratio": round(ratio, 4),
                "drawing_count": drawings,
                "output_path": str(rel_path),
                "extraction_method": "pymupdf_image_clip",
                "description_status": "needs_vision_llm",
                "review_status": "needs_review",
                "created_at": created_at,
            })

        if needs_page_render and not significant_images:
            asset_id = stable_id(pdf_path.name, page_number, "page_visual", drawings)
            rel_path = Path("images") / f"{asset_id}.png"
            render_clip(page, None, output_dir / rel_path, dpi)
            rows.append({
                "asset_id": asset_id,
                "asset_type": "page_visual",
                "source_file": pdf_path.name,
                "source_path": str(pdf_path.relative_to(PROJECT_ROOT)),
                "page": page_number,
                "image_index": 0,
                "bbox": [],
                "page_area_ratio": 1.0,
                "drawing_count": drawings,
                "output_path": str(rel_path),
                "extraction_method": "pymupdf_page_render",
                "description_status": "needs_vision_llm",
                "review_status": "needs_review",
                "created_at": created_at,
            })
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract formula, table, and visual assets for Phase 2 RAG enrichment.")
    parser.add_argument("--input", type=Path, default=INPUT_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--visual-image-min-area-ratio", type=float, default=0.15)
    parser.add_argument("--visual-image-max-area-ratio", type=float, default=0.9)
    parser.add_argument("--visual-drawing-threshold", type=int, default=500)
    parser.add_argument("--skip-formulas", action="store_true")
    parser.add_argument("--skip-tables", action="store_true")
    parser.add_argument("--skip-visuals", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created_at = now_iso()
    pdf_files = sorted(args.input.resolve().glob("*.pdf"))
    rules = load_manual_review_rules()

    formulas: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    visuals: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for pdf_path in pdf_files:
        try:
            doc = fitz.open(str(pdf_path))
            if not args.skip_formulas:
                formulas.extend(extract_formula_candidates(doc, pdf_path, created_at, max_pages=args.max_pages))
            if not args.skip_visuals:
                visuals.extend(extract_visual_candidates(
                    doc,
                    pdf_path,
                    rules,
                    args.output,
                    created_at,
                    max_pages=args.max_pages,
                    dpi=args.dpi,
                    min_area_ratio=args.visual_image_min_area_ratio,
                    max_area_ratio=args.visual_image_max_area_ratio,
                    drawing_threshold=args.visual_drawing_threshold,
                ))
            doc.close()
            if not args.skip_tables:
                tables.extend(extract_tables(pdf_path, created_at, max_pages=args.max_pages))
        except Exception as exc:
            errors.append({
                "source_file": pdf_path.name,
                "error": f"{type(exc).__name__}: {exc}",
            })

    write_jsonl(args.output / "formulas.jsonl", formulas)
    write_jsonl(args.output / "tables.jsonl", tables)
    write_jsonl(args.output / "visual_assets.jsonl", visuals)

    report = {
        "version": 1,
        "created_at": created_at,
        "file_count": len(pdf_files),
        "formula_count": len(formulas),
        "table_count": len(tables),
        "visual_asset_count": len(visuals),
        "formula_review_status": dict(Counter(row["review_status"] for row in formulas)),
        "table_review_status": dict(Counter(row["review_status"] for row in tables)),
        "visual_review_status": dict(Counter(row["review_status"] for row in visuals)),
        "manual_review_files": [path.name for path in sorted(REVIEW_DIR.glob("manual_review_notes*.json"))],
        "errors": errors,
        "notes": [
            "Phase 2 산출물은 텍스트 MVP RAG와 분리한다.",
            "수식 후보는 휴리스틱 추출 결과이므로 검수 후 사용한다.",
            "표는 구조화 추출 결과이며 행·열 오류 검수가 필요하다.",
            "이미지/도식 crop은 Vision LLM 설명 생성 또는 사람 검수 후 RAG 보강에 사용한다.",
        ],
    }
    write_json(args.output / "phase2_report.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
