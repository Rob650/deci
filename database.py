import os
import json
import aiosqlite
import datetime
from typing import Optional

# /tmp on Railway, local otherwise
DB_PATH = "/tmp/deci.db" if os.environ.get("RAILWAY_ENVIRONMENT") else "./deci.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT UNIQUE,
                name        TEXT,
                sector      TEXT,
                tags        TEXT,
                scores_json TEXT,
                analysis_json TEXT,
                market_data_json TEXT,
                summary     TEXT,
                analyzed_at DATETIME,
                updated_at  DATETIME
            )
        """)
        await db.commit()


async def save_project(
    url: str,
    name: str,
    sector: str,
    tags: str,
    scores: dict,
    analysis: dict,
    market_data: dict,
    summary: str,
) -> int:
    now = datetime.datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM projects WHERE url = ?", (url,))
        existing = await cursor.fetchone()

        if existing:
            await db.execute(
                """
                UPDATE projects
                SET name=?, sector=?, tags=?, scores_json=?, analysis_json=?,
                    market_data_json=?, summary=?, updated_at=?
                WHERE url=?
                """,
                (
                    name, sector, tags,
                    json.dumps(scores), json.dumps(analysis),
                    json.dumps(market_data), summary, now,
                    url,
                ),
            )
            await db.commit()
            return existing[0]
        else:
            cursor = await db.execute(
                """
                INSERT INTO projects
                    (url, name, sector, tags, scores_json, analysis_json,
                     market_data_json, summary, analyzed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url, name, sector, tags,
                    json.dumps(scores), json.dumps(analysis),
                    json.dumps(market_data), summary, now, now,
                ),
            )
            await db.commit()
            return cursor.lastrowid


async def get_project(project_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None


async def get_project_by_url(url: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM projects WHERE url = ?", (url,))
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None


async def get_all_projects() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM projects ORDER BY updated_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]


async def get_similar_projects(sector: str, tags: str, exclude_url: str) -> list[dict]:
    """Return up to 5 projects in the same sector or sharing tags."""
    tag_list = {t.strip().lower() for t in tags.split(",") if t.strip()}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Same sector first
        cursor = await db.execute(
            "SELECT * FROM projects WHERE sector = ? AND url != ? ORDER BY updated_at DESC LIMIT 10",
            (sector, exclude_url),
        )
        rows = await cursor.fetchall()
        results = [_row_to_dict(r) for r in rows]
        seen = {r["id"] for r in results}

        # Fill remaining slots with tag-overlap matches
        if len(results) < 5 and tag_list:
            all_cur = await db.execute(
                "SELECT * FROM projects WHERE url != ? ORDER BY updated_at DESC",
                (exclude_url,),
            )
            for row in await all_cur.fetchall():
                if len(results) >= 5:
                    break
                d = _row_to_dict(row)
                if d["id"] in seen:
                    continue
                project_tags = {t.strip().lower() for t in (d.get("tags") or "").split(",") if t.strip()}
                if tag_list & project_tags:
                    results.append(d)
                    seen.add(d["id"])

        return results[:5]


async def search_projects(query: str) -> list[dict]:
    q = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM projects
            WHERE name LIKE ? OR sector LIKE ? OR tags LIKE ? OR summary LIKE ? OR url LIKE ?
            ORDER BY updated_at DESC
            """,
            (q, q, q, q, q),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]


# ── helpers ──────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    for raw_key in ("scores_json", "analysis_json", "market_data_json"):
        clean_key = raw_key.replace("_json", "")
        try:
            d[clean_key] = json.loads(d.pop(raw_key) or "{}")
        except (json.JSONDecodeError, TypeError):
            d[clean_key] = {}
    return d
