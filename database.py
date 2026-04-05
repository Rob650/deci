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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS virtuals_agents (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                virtuals_id           TEXT UNIQUE,
                name                  TEXT,
                ticker                TEXT,
                description           TEXT,
                market_cap            REAL,
                tvl                   REAL,
                volume_24h            REAL,
                price                 REAL,
                contract_address      TEXT,
                image_url             TEXT,
                analysis_json         TEXT,
                competitive_intel_json TEXT,
                last_scanned          DATETIME,
                created_at            DATETIME
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


# ── Virtuals agents ──────────────────────────────────────────────────────────

async def save_virtuals_agent(agent: dict) -> int:
    """Insert or update a virtuals agent. Pass analysis=dict to store analysis."""
    now = datetime.datetime.utcnow().isoformat()
    analysis = agent.get("analysis")
    competitive_intel = agent.get("competitive_intel")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM virtuals_agents WHERE virtuals_id = ?",
            (agent["virtuals_id"],),
        )
        existing = await cursor.fetchone()

        if existing:
            await db.execute(
                """
                UPDATE virtuals_agents
                SET name=?, ticker=?, description=?, market_cap=?, tvl=?,
                    volume_24h=?, price=?, contract_address=?, image_url=?,
                    analysis_json=COALESCE(?, analysis_json),
                    competitive_intel_json=COALESCE(?, competitive_intel_json),
                    last_scanned=?
                WHERE virtuals_id=?
                """,
                (
                    agent.get("name"), agent.get("ticker"), agent.get("description"),
                    agent.get("market_cap"), agent.get("tvl"), agent.get("volume_24h"),
                    agent.get("price"), agent.get("contract_address"), agent.get("image_url"),
                    json.dumps(analysis) if analysis is not None else None,
                    json.dumps(competitive_intel) if competitive_intel is not None else None,
                    now,
                    agent["virtuals_id"],
                ),
            )
            await db.commit()
            return existing[0]
        else:
            cursor = await db.execute(
                """
                INSERT INTO virtuals_agents
                    (virtuals_id, name, ticker, description, market_cap, tvl,
                     volume_24h, price, contract_address, image_url,
                     analysis_json, competitive_intel_json, last_scanned, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent["virtuals_id"], agent.get("name"), agent.get("ticker"),
                    agent.get("description"), agent.get("market_cap"), agent.get("tvl"),
                    agent.get("volume_24h"), agent.get("price"),
                    agent.get("contract_address"), agent.get("image_url"),
                    json.dumps(analysis) if analysis is not None else None,
                    json.dumps(competitive_intel) if competitive_intel is not None else None,
                    now, now,
                ),
            )
            await db.commit()
            return cursor.lastrowid


async def get_virtuals_agent(agent_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM virtuals_agents WHERE id = ?", (agent_id,)
        )
        row = await cursor.fetchone()
        return _virtuals_row_to_dict(row) if row else None


async def get_virtuals_agent_by_virtuals_id(virtuals_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM virtuals_agents WHERE virtuals_id = ?", (virtuals_id,)
        )
        row = await cursor.fetchone()
        return _virtuals_row_to_dict(row) if row else None


async def get_all_virtuals_agents() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM virtuals_agents ORDER BY volume_24h DESC NULLS LAST"
        )
        rows = await cursor.fetchall()
        return [_virtuals_row_to_dict(r) for r in rows]


# ── helpers ──────────────────────────────────────────────────────────────────

def _virtuals_row_to_dict(row) -> dict:
    d = dict(row)
    for raw_key in ("analysis_json", "competitive_intel_json"):
        clean_key = raw_key.replace("_json", "")
        try:
            d[clean_key] = json.loads(d.pop(raw_key) or "null") or {}
        except (json.JSONDecodeError, TypeError):
            d[clean_key] = {}
    return d


def _row_to_dict(row) -> dict:
    d = dict(row)
    for raw_key in ("scores_json", "analysis_json", "market_data_json"):
        clean_key = raw_key.replace("_json", "")
        try:
            d[clean_key] = json.loads(d.pop(raw_key) or "{}")
        except (json.JSONDecodeError, TypeError):
            d[clean_key] = {}
    return d
