from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DATA_DIR = Path("/opt/app/data")
DB_PATH = APP_DATA_DIR / "base.db"
RULES_DIR = ROOT / "resources" / "rules"


def load_json(name: str):
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def period_for(subject: str) -> str:
    if subject in ["1. 방사선이론", "2. 의료관계법규", "방사선이론", "의료법규", "의료관계법규"]:
        return "1교시"
    if subject in ["3. 방사선응용", "방사선응용"]:
        return "2교시"
    if subject in ["4. 실기시험", "실기시험"]:
        return "3교시"
    return ""


def normalize(value) -> str:
    return str(value or "").strip()


def strategy_lookup():
    strategy = load_json("scope_generation_strategy.json")
    rows = {}
    for row in strategy.get("rows", []):
        key = (
            normalize(row.get("subject")),
            normalize(row.get("field")),
            normalize(row.get("area")),
            normalize(row.get("detail")),
        )
        rows[key] = row
    return rows


def seed_rows():
    scope = load_json("exam_scope.json")
    strategies = strategy_lookup()
    rows = []
    verified_rows = scope.get("verified_detail_rows") or []

    if verified_rows:
        for index, row in enumerate(verified_rows, start=1):
            subject_name = normalize(row.get("subject"))
            field_name = normalize(row.get("field"))
            area_name = normalize(row.get("area"))
            detail_name = normalize(row.get("detail"))
            key = (subject_name, field_name, area_name, detail_name)
            strategy = strategies.get(key, {})
            rows.append(
                {
                    "id": f"scope-{index:04d}",
                    "period": row.get("period") or period_for(subject_name),
                    "subject": subject_name,
                    "field": field_name,
                    "area": area_name,
                    "detail": detail_name,
                    "question_count": row.get("question_count") if isinstance(row.get("question_count"), int) else 0,
                    "count_mode": row.get("count_mode") or "fixed",
                    "source_page": row.get("source_page") or 0,
                    "is_mvp": 0 if subject_name in ["4. 실기시험", "실기시험"] else 1,
                    "recommended_question_types": ",".join(strategy.get("recommended_question_types", [])),
                    "recommended_competency_types": ",".join(strategy.get("recommended_competency_types", [])),
                    "recommended_difficulties": ",".join(strategy.get("recommended_difficulties", [])),
                }
            )
        return rows

    index = 1
    for subject in scope.get("subjects", []):
        subject_name = normalize(subject.get("name"))
        for field in subject.get("fields", []):
            field_name = normalize(field.get("name"))
            for area in field.get("areas", []):
                area_name = normalize(area.get("name"))
                details = area.get("details") or [{"name": "", "source_page": 0}]
                for detail in details:
                    detail_name = normalize(detail.get("name"))
                    key = (subject_name, field_name, area_name, detail_name)
                    strategy = strategies.get(key, {})
                    rows.append(
                        {
                            "id": f"scope-{index:04d}",
                            "period": period_for(subject_name),
                            "subject": subject_name,
                            "field": field_name,
                            "area": area_name,
                            "detail": detail_name,
                            "question_count": 0,
                            "count_mode": "fixed",
                            "source_page": detail.get("source_page") or 0,
                            "is_mvp": 0 if subject_name == "4. 실기시험" else 1,
                            "recommended_question_types": ",".join(strategy.get("recommended_question_types", [])),
                            "recommended_competency_types": ",".join(strategy.get("recommended_competency_types", [])),
                            "recommended_difficulties": ",".join(strategy.get("recommended_difficulties", [])),
                        }
                    )
                    index += 1
    return rows


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_scope (
            id TEXT PRIMARY KEY,
            period TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            field TEXT DEFAULT '',
            area TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            question_count INTEGER DEFAULT 0,
            count_mode TEXT DEFAULT 'fixed',
            source_page INTEGER DEFAULT 0,
            is_mvp INTEGER DEFAULT 1,
            recommended_question_types TEXT DEFAULT '',
            recommended_competency_types TEXT DEFAULT '',
            recommended_difficulties TEXT DEFAULT ''
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(exam_scope)").fetchall()}
    if "question_count" not in existing:
        conn.execute("ALTER TABLE exam_scope ADD COLUMN question_count INTEGER DEFAULT 0")
    if "count_mode" not in existing:
        conn.execute("ALTER TABLE exam_scope ADD COLUMN count_mode TEXT DEFAULT 'fixed'")

    for column in ["period", "subject", "field", "area", "detail", "question_count", "count_mode", "is_mvp"]:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_exam_scope_{column} ON exam_scope ({column})")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS question_generation_request (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'draft',
            period TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            field TEXT DEFAULT '',
            area TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            question_count INTEGER DEFAULT 1,
            difficulty TEXT DEFAULT '',
            question_type TEXT DEFAULT '',
            request_payload TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created DATETIME,
            updated DATETIME
        )
        """
    )
    for column in ["status", "period", "subject", "field", "area", "detail", "created_by"]:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_qgr_{column} ON question_generation_request ({column})")


def main() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = seed_rows()

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        conn.executemany(
            """
            INSERT INTO exam_scope (
                id, period, subject, field, area, detail, question_count, count_mode, source_page, is_mvp,
                recommended_question_types, recommended_competency_types, recommended_difficulties
            )
            VALUES (
                :id, :period, :subject, :field, :area, :detail, :question_count, :count_mode, :source_page, :is_mvp,
                :recommended_question_types, :recommended_competency_types, :recommended_difficulties
            )
            ON CONFLICT(id) DO UPDATE SET
                period = excluded.period,
                subject = excluded.subject,
                field = excluded.field,
                area = excluded.area,
                detail = excluded.detail,
                question_count = excluded.question_count,
                count_mode = excluded.count_mode,
                source_page = excluded.source_page,
                is_mvp = excluded.is_mvp,
                recommended_question_types = excluded.recommended_question_types,
                recommended_competency_types = excluded.recommended_competency_types,
                recommended_difficulties = excluded.recommended_difficulties
            """,
            rows,
        )
        keep_ids = [row["id"] for row in rows]
        placeholders = ",".join(["?"] * len(keep_ids))
        conn.execute(f"DELETE FROM exam_scope WHERE id NOT IN ({placeholders})", keep_ids)
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM exam_scope").fetchone()[0]
        mvp = conn.execute("SELECT COUNT(*) FROM exam_scope WHERE is_mvp = 1").fetchone()[0]
        print(json.dumps({"db": str(DB_PATH), "seeded": len(rows), "total": total, "mvp": mvp}, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
