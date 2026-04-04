import os
import json
import aiosqlite
from datetime import datetime
from typing import Optional, List, Dict

DB_PATH = os.environ.get("DB_PATH", "./deci.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                name TEXT,
                analyzed_at TEXT,
                scores_json TEXT,
                categories_json TEXT,
                market_data_json TEXT,
                sources_json TEXT,
                tags TEXT,
                sector TEXT,
                summary TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_ids TEXT NOT NULL,
                comparison_json TEXT,
                created_at TEXT
            )
        """)
        await db.commit()


async def save_project(
    url: str,
    name: str,
    scores: dict,
    categories: dict,
    market_data: dict,
    sources: dict,
    tags: list,
    sector: str,
    summary: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        tags_str = ",".join(tags) if isinstance(tags, list) else (tags or "")
        now = datetime.utcnow().isoformat()

        await db.execute(
            """
            INSERT INTO projects
                (url, name, analyzed_at, scores_json, categories_json,
                 market_data_json, sources_json, tags, sector, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                name=excluded.name,
                analyzed_at=excluded.analyzed_at,
                scores_json=excluded.scores_json,
                categories_json=excluded.categories_json,
                market_data_json=excluded.market_data_json,
                sources_json=excluded.sources_json,
                tags=excluded.tags,
                sector=excluded.sector,
                summary=excluded.summary
            """,
            (
                url, name, now,
                json.dumps(scores),
                json.dumps(categories),
                json.dumps(market_data),
                json.dumps(sources),
                tags_str, sector, summary,
            ),
        )
        await db.commit()

        cursor = await db.execute("SELECT id FROM projects WHERE url = ?", (url,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_project(url: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM projects WHERE url = ?", (url,))
        row = await cursor.fetchone()
        if row:
            return _row_to_dict(dict(row))
    return None


async def get_project_by_id(project_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if row:
            return _row_to_dict(dict(row))
    return None


async def get_all_projects() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM projects ORDER BY analyzed_at DESC"
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(dict(r)) for r in rows]


async def get_similar_projects(
    tags: list, sector: str, exclude_url: str = None
) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM projects ORDER BY analyzed_at DESC"
        )
        rows = await cursor.fetchall()

    tags_set = set(t.strip() for t in tags) if tags else set()
    results = []

    for row in rows:
        d = dict(row)
        if exclude_url and d["url"] == exclude_url:
            continue
        row_tags = (
            set(t.strip() for t in d.get("tags", "").split(",") if t.strip())
            if d.get("tags")
            else set()
        )
        row_sector = d.get("sector", "")

        if row_sector == sector or (tags_set & row_tags):
            results.append(_row_to_dict(d))

    return results[:5]


async def search_projects(query: str) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = f"%{query.lower()}%"
        cursor = await db.execute(
            """
            SELECT * FROM projects
            WHERE LOWER(name) LIKE ? OR LOWER(summary) LIKE ?
               OR LOWER(tags) LIKE ? OR LOWER(sector) LIKE ?
            ORDER BY analyzed_at DESC
            """,
            (q, q, q, q),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(dict(r)) for r in rows]


async def save_comparison(project_ids: List[int], comparison_data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        ids_str = ",".join(str(i) for i in project_ids)
        cursor = await db.execute(
            "INSERT INTO comparisons (project_ids, comparison_json, created_at) VALUES (?, ?, ?)",
            (ids_str, json.dumps(comparison_data), now),
        )
        await db.commit()
        return cursor.lastrowid


def _row_to_dict(row: dict) -> dict:
    """Parse JSON blob fields and normalise tags."""
    for field in ["scores_json", "categories_json", "market_data_json", "sources_json"]:
        key = field.replace("_json", "")
        if row.get(field):
            try:
                row[key] = json.loads(row[field])
            except Exception:
                row[key] = {}
        else:
            row[key] = {}

    if row.get("tags"):
        row["tags_list"] = [t.strip() for t in row["tags"].split(",") if t.strip()]
    else:
        row["tags_list"] = []

    return row
