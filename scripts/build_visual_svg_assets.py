#!/usr/bin/env python3
"""Build newly authored SVG assets from approved structured visual packages.

The output SVGs are not copies of textbook figures. They are simplified
educational diagrams generated from structured captions, nearby summaries, and
embedded text candidates. Source images are not read or embedded.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGES = (
    PROJECT_ROOT
    / "resources/generated/visual_question_request_packages/visual_question_request_packages_ready.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/visual_assets_svg"
SVG_READY_KINDS = {"diagram", "graph", "chart"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_slug(text: str, max_len: int = 70) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", text).strip("_")
    return value[:max_len] or "visual_asset"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def compact(value: Any, max_chars: int = 90) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def split_labels(labels: list[Any], max_count: int = 7) -> list[str]:
    cleaned: list[str] = []
    seen = set()
    for label in labels:
        text = compact(label, 28)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= max_count:
            break
    return cleaned


def visual_summary(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    visual = package.get("visual_evidence") or {}
    summary = visual.get("summary") or {}
    return visual, summary


def classify_diagram(labels: list[str], caption: str, nearby: str) -> str:
    text = " ".join([caption, nearby, *labels]).lower()
    if any(token.lower() in text for token in ["scr", "t1", "t2", "회로", "변압기", "필라멘트", "다이오드", "전원", "관전압"]):
        return "circuit"
    if any(token.lower() in text for token in ["필터", "collimator", "collimation", "x선빔", "bowtie", "aperture", "피사체", "초점"]):
        return "beamline"
    if any(token.lower() in text for token in ["흐름", "단계", "process", "system", "장치", "구성"]):
        return "flow"
    return "block"


def svg_header(width: int, height: int, title: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{e(title)}">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#334155"/>
    </marker>
    <style>
      .bg {{ fill: #ffffff; }}
      .frame {{ fill: none; stroke: #cbd5e1; stroke-width: 2; }}
      .title {{ font: 700 18px sans-serif; fill: #0f172a; }}
      .note {{ font: 500 12px sans-serif; fill: #64748b; }}
      .label {{ font: 700 13px sans-serif; fill: #0f172a; }}
      .small {{ font: 600 11px sans-serif; fill: #334155; }}
      .node {{ fill: #e0f2fe; stroke: #0284c7; stroke-width: 2; }}
      .node2 {{ fill: #f1f5f9; stroke: #64748b; stroke-width: 1.6; }}
      .line {{ stroke: #334155; stroke-width: 2; fill: none; marker-end: url(#arrow); }}
      .soft {{ stroke: #94a3b8; stroke-width: 1.4; fill: none; }}
      .accent {{ stroke: #0ea5e9; stroke-width: 3; fill: none; }}
      .curve {{ stroke: #0284c7; stroke-width: 4; fill: none; }}
    </style>
  </defs>
  <rect class="bg" x="0" y="0" width="{width}" height="{height}"/>
  <rect class="frame" x="16" y="16" width="{width - 32}" height="{height - 32}" rx="8"/>
  <text class="title" x="32" y="46">{e(title)}</text>
'''


def wrap_text(text: str, width: int = 42) -> list[str]:
    text = compact(text, 180)
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for part in text.split():
        if len(current) + len(part) + 1 > width:
            lines.append(current)
            current = part
        else:
            current = (current + " " + part).strip()
    if current:
        lines.append(current)
    return lines[:3]


def draw_text_lines(lines: list[str], x: int, y: int, klass: str = "note", dy: int = 16) -> str:
    return "\n".join(
        f'  <text class="{klass}" x="{x}" y="{y + index * dy}">{e(line)}</text>'
        for index, line in enumerate(lines)
    )


def render_beamline(title: str, labels: list[str], nearby: str) -> str:
    width, height = 860, 360
    labels = labels or ["발생부", "조절부", "필터", "콜리메이터", "대상"]
    nodes = labels[:5]
    x_positions = [90, 230, 380, 540, 700]
    y = 170
    parts = [svg_header(width, height, title)]
    parts.append('  <text class="note" x="32" y="72">구조화 설명을 바탕으로 새로 만든 선속 조절 모식도</text>')
    for idx, (x, label) in enumerate(zip(x_positions, nodes)):
        if idx == 0:
            parts.append(f'  <circle class="node" cx="{x}" cy="{y}" r="34"/>')
            parts.append(f'  <text class="label" x="{x}" y="{y + 5}" text-anchor="middle">{e(label)}</text>')
        else:
            parts.append(f'  <rect class="node2" x="{x - 58}" y="{y - 30}" width="116" height="60" rx="8"/>')
            parts.append(f'  <text class="label" x="{x}" y="{y + 5}" text-anchor="middle">{e(label)}</text>')
        if idx < len(nodes) - 1:
            parts.append(f'  <path class="line" d="M {x + 48} {y} L {x_positions[idx + 1] - 72} {y}"/>')
    parts.append('  <path class="accent" d="M 70 250 C 210 220, 430 220, 760 250"/>')
    parts.append(draw_text_lines(wrap_text(nearby), 32, 300))
    parts.append("</svg>\n")
    return "\n".join(parts)


def render_circuit(title: str, labels: list[str], nearby: str) -> str:
    width, height = 860, 420
    labels = labels or ["전원", "제어부", "변압기", "정류부", "부하"]
    parts = [svg_header(width, height, title)]
    parts.append('  <text class="note" x="32" y="72">원본 회로를 복제하지 않은 교육용 단순 회로도</text>')
    parts.append('  <path class="soft" d="M 110 210 H 220 M 640 210 H 750 M 750 210 V 310 H 110 V 210"/>')
    xs = [110, 250, 390, 530, 670]
    for i, label in enumerate(labels[:5]):
        x = xs[i]
        if i == 0:
            parts.append(f'  <circle class="node" cx="{x}" cy="210" r="34"/>')
        elif i == 2:
            parts.append(f'  <rect class="node" x="{x - 54}" y="170" width="108" height="80" rx="8"/>')
            parts.append(f'  <path class="soft" d="M {x - 22} 182 q 18 14 0 28 q 18 14 0 28 M {x + 22} 182 q -18 14 0 28 q -18 14 0 28"/>')
        elif i == 3:
            parts.append(f'  <path class="node2" d="M {x - 48} 186 H {x + 48} V 234 H {x - 48} Z"/>')
            parts.append(f'  <path class="soft" d="M {x - 30} 224 L {x - 8} 196 L {x + 14} 224 L {x + 36} 196"/>')
        else:
            parts.append(f'  <rect class="node2" x="{x - 52}" y="180" width="104" height="60" rx="8"/>')
        parts.append(f'  <text class="label" x="{x}" y="270" text-anchor="middle">{e(label)}</text>')
        if i < min(len(labels[:5]), 5) - 1:
            parts.append(f'  <path class="line" d="M {x + 55} 210 H {xs[i + 1] - 62}"/>')
    parts.append(draw_text_lines(wrap_text(nearby), 32, 350))
    parts.append("</svg>\n")
    return "\n".join(parts)


def render_flow(title: str, labels: list[str], nearby: str) -> str:
    width, height = 860, 390
    labels = labels or ["입력", "처리", "변환", "출력"]
    labels = labels[:6]
    gap = 720 // max(1, len(labels) - 1) if len(labels) > 1 else 0
    parts = [svg_header(width, height, title)]
    parts.append('  <text class="note" x="32" y="72">핵심 구성요소의 관계를 단순화한 블록도</text>')
    for idx, label in enumerate(labels):
        x = 70 + idx * gap
        parts.append(f'  <rect class="node" x="{x}" y="150" width="110" height="64" rx="10"/>')
        parts.append(f'  <text class="label" x="{x + 55}" y="187" text-anchor="middle">{e(label)}</text>')
        if idx < len(labels) - 1:
            parts.append(f'  <path class="line" d="M {x + 118} 182 H {70 + (idx + 1) * gap - 12}"/>')
    parts.append(draw_text_lines(wrap_text(nearby), 32, 290))
    parts.append("</svg>\n")
    return "\n".join(parts)


def render_graph(title: str, labels: list[str], nearby: str, kind: str) -> str:
    width, height = 760, 430
    parts = [svg_header(width, height, title)]
    parts.append('  <text class="note" x="32" y="72">정량값 복제가 아닌 경향 이해용 새 그래프</text>')
    x0, y0, x1, y1 = 90, 330, 680, 110
    parts.append(f'  <path class="soft" d="M {x0} {y0} H {x1} M {x0} {y0} V {y1}"/>')
    x_label = labels[0] if labels else "독립 변수"
    y_label = labels[1] if len(labels) > 1 else "상대 값"
    parts.append(f'  <text class="small" x="{x1 - 70}" y="{y0 + 34}">{e(x_label)}</text>')
    parts.append(f'  <text class="small" x="{x0 - 52}" y="{y1 + 8}" transform="rotate(-90 {x0 - 52} {y1 + 8})">{e(y_label)}</text>')
    if kind == "chart":
        for i, h in enumerate([70, 120, 165, 100]):
            x = 160 + i * 105
            parts.append(f'  <rect class="node" x="{x}" y="{y0 - h}" width="58" height="{h}" rx="3"/>')
            parts.append(f'  <text class="small" x="{x + 29}" y="{y0 + 20}" text-anchor="middle">항목 {i + 1}</text>')
    else:
        parts.append(f'  <path class="curve" d="M {x0 + 20} {y0 - 8} C 210 315, 250 170, 360 160 S 510 250, 650 132"/>')
        parts.append(f'  <path class="soft" d="M 360 {y0} V 160 M 650 {y0} V 132"/>')
        parts.append('  <text class="small" x="370" y="154">변화 구간</text>')
        parts.append('  <text class="small" x="585" y="126">특징점</text>')
    parts.append(draw_text_lines(wrap_text(nearby), 32, 382))
    parts.append("</svg>\n")
    return "\n".join(parts)


def build_svg(package: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    visual, summary = visual_summary(package)
    kind = visual.get("visual_kind") or "diagram"
    caption = compact(summary.get("caption") or visual.get("caption") or package.get("package_id"), 72)
    nearby = summary.get("nearby_text_summary") or summary.get("semantic_description") or summary.get("structure_summary") or ""
    labels = split_labels(summary.get("embedded_text_candidates") or [], 7)

    spec = {
        "asset_kind": "generated_svg",
        "source_visual_kind": kind,
        "caption_basis": caption,
        "labels_used": labels,
        "nearby_text_basis": compact(nearby, 260),
        "policy": {
            "source_image_embedded": False,
            "source_image_traced": False,
            "new_educational_diagram": True,
            "requires_expert_review_before_student_release": True,
        },
    }
    if kind in {"graph", "chart"}:
        return render_graph(caption, labels, nearby, kind), {**spec, "template": kind}
    diagram_type = classify_diagram(labels, caption, nearby)
    spec["template"] = diagram_type
    if diagram_type == "circuit":
        return render_circuit(caption, labels, nearby), spec
    if diagram_type == "beamline":
        return render_beamline(caption, labels, nearby), spec
    return render_flow(caption, labels, nearby), spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    packages = read_jsonl(args.packages)
    selected = [
        package for package in packages
        if ((package.get("visual_evidence") or {}).get("visual_kind") or "") in SVG_READY_KINDS
    ]
    if args.limit > 0:
        selected = selected[: args.limit]

    asset_dir = args.output_dir / "assets"
    spec_dir = args.output_dir / "specs"
    index_rows: list[dict[str, Any]] = []
    kind_counts = Counter()
    template_counts = Counter()
    for package in selected:
        visual, summary = visual_summary(package)
        package_id = package.get("package_id") or "package"
        approval_id = package.get("source_visual_approval_id") or ""
        source_kind = visual.get("visual_kind") or ""
        filename = f"{short_slug(package_id)}_{short_slug(approval_id, 24)}.svg"
        svg_path = asset_dir / source_kind / filename
        spec_path = spec_dir / source_kind / filename.replace(".svg", ".json")
        svg, spec = build_svg(package)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(svg, encoding="utf-8")
        write_json(spec_path, {**spec, "package_id": package_id, "source_visual_approval_id": approval_id})
        kind_counts[source_kind] += 1
        template_counts[spec["template"]] += 1
        scope = package.get("requested_scope") or {}
        index_rows.append(
            {
                "asset_id": f"svg_{approval_id or package_id}",
                "package_id": package_id,
                "source_visual_approval_id": approval_id,
                "source_visual_chunk_id": package.get("source_visual_chunk_id") or "",
                "source_visual_kind": source_kind,
                "template": spec["template"],
                "svg_path": str(svg_path),
                "spec_path": str(spec_path),
                "source_file": visual.get("source_file") or "",
                "page_or_slide": visual.get("page_or_slide") or "",
                "caption": summary.get("caption") or "",
                "scope": scope,
                "status": "pending_expert_review",
                "created_at": now_iso(),
                "policy": spec["policy"],
            }
        )

    index_path = args.output_dir / "visual_svg_asset_index.jsonl"
    report_path = args.output_dir / "visual_svg_asset_report.json"
    manifest_path = args.output_dir / "manifest.json"
    write_jsonl(index_path, index_rows)
    report = {
        "created_at": now_iso(),
        "input_packages": str(args.packages),
        "output_dir": str(args.output_dir),
        "counts": {
            "input_packages": len(packages),
            "svg_ready_selected": len(selected),
            "assets_written": len(index_rows),
            "by_source_visual_kind": dict(sorted(kind_counts.items())),
            "by_template": dict(sorted(template_counts.items())),
        },
        "outputs": {
            "index_jsonl": str(index_path),
            "report_json": str(report_path),
            "manifest_json": str(manifest_path),
            "assets_dir": str(asset_dir),
            "specs_dir": str(spec_dir),
        },
        "policy": {
            "source_images_read": False,
            "source_images_embedded": False,
            "copyright_safe_method": "structured_summary_to_new_schematic_svg",
            "student_release_status": "pending_expert_review",
        },
    }
    write_json(report_path, report)
    write_json(manifest_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
