"""
Database moduli — aiosqlite orqali asinxron SQLite.

Schema:
  surveys          — so'rovnoma to'plamlari
  survey_questions — har bir to'plam savollari
  survey_responses — anonim javoblar (SHA256 token asosida)
  active_polls     — foydalanuvchiga yuborilgan Telegram poll → savol xaritasi
  analysis_log     — AI tahlil logi
"""

import aiosqlite
import json
import hashlib
import os

DB_PATH = os.getenv("DB_PATH", "voting.db")
ANON_SALT = os.getenv("ANON_SALT", "xY9#mK!p2qR@survey")


# ───────────────────────── INIT ─────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS surveys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            created_by   INTEGER NOT NULL,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT (datetime('now')),
            closed_at    TEXT,
            ai_analysis  TEXT
        );

        CREATE TABLE IF NOT EXISTS survey_questions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id    INTEGER NOT NULL,
            order_num    INTEGER NOT NULL,
            question     TEXT NOT NULL,
            options      TEXT NOT NULL,   -- JSON list
            allow_multi  INTEGER DEFAULT 0,
            FOREIGN KEY (survey_id) REFERENCES surveys(id)
        );

        CREATE TABLE IF NOT EXISTS survey_responses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id    INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            anon_token   TEXT NOT NULL,
            choices      TEXT NOT NULL,   -- JSON list of int
            answered_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(question_id, anon_token),
            FOREIGN KEY (survey_id)   REFERENCES surveys(id),
            FOREIGN KEY (question_id) REFERENCES survey_questions(id)
        );

        -- Telegram poll_id → savol xaritasi (foydalanuvchi seansida)
        CREATE TABLE IF NOT EXISTS active_polls (
            tg_poll_id   TEXT PRIMARY KEY,
            survey_id    INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            sent_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS analysis_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id         INTEGER NOT NULL,
            model             TEXT,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            analysis          TEXT,
            created_at        TEXT DEFAULT (datetime('now'))
        );
        """)
        await db.commit()


# ───────────────────────── SURVEY CRUD ─────────────────────────

async def create_survey(title: str, description: str,
                        questions: list[dict], admin_id: int) -> int:
    """
    questions: [{"question": str, "options": [str, ...], "allow_multi": bool}]
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO surveys (title, description, created_by) VALUES (?,?,?)",
            (title, description, admin_id)
        )
        survey_id = cur.lastrowid
        for idx, q in enumerate(questions):
            await db.execute(
                "INSERT INTO survey_questions (survey_id, order_num, question, options, allow_multi)"
                " VALUES (?,?,?,?,?)",
                (
                    survey_id, idx,
                    q["question"],
                    json.dumps(q["options"], ensure_ascii=False),
                    int(q.get("allow_multi", False)),
                )
            )
        await db.commit()
    return survey_id


async def get_survey(survey_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM surveys WHERE id=?", (survey_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["ai_analysis"] = json.loads(d["ai_analysis"]) if d["ai_analysis"] else None
        # questions
        async with db.execute(
            "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY order_num",
            (survey_id,)
        ) as cur:
            qs = await cur.fetchall()
        d["questions"] = []
        for q in qs:
            qd = dict(q)
            qd["options"] = json.loads(qd["options"])
            d["questions"].append(qd)
        return d


async def list_surveys(active_only: bool = False) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM surveys"
        if active_only:
            q += " WHERE is_active=1"
        q += " ORDER BY id DESC"
        async with db.execute(q) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["ai_analysis"] = json.loads(d["ai_analysis"]) if d["ai_analysis"] else None
            # question count
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM survey_questions WHERE survey_id=?", (d["id"],)
            ) as cur2:
                cnt_row = await cur2.fetchone()
            d["question_count"] = cnt_row["cnt"]
            result.append(d)
        return result


async def close_survey(survey_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE surveys SET is_active=0, closed_at=datetime('now') WHERE id=?",
            (survey_id,)
        )
        await db.commit()


async def delete_survey(survey_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM survey_responses WHERE survey_id=?", (survey_id,))
        await db.execute("DELETE FROM active_polls WHERE survey_id=?", (survey_id,))
        await db.execute("DELETE FROM survey_questions WHERE survey_id=?", (survey_id,))
        await db.execute("DELETE FROM surveys WHERE id=?", (survey_id,))
        await db.commit()


async def save_ai_analysis(survey_id: int, analysis: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE surveys SET ai_analysis=? WHERE id=?",
            (json.dumps(analysis, ensure_ascii=False), survey_id)
        )
        await db.commit()


# ───────────────────────── QUESTIONS ─────────────────────────

async def get_questions(survey_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY order_num",
            (survey_id,)
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["options"] = json.loads(d["options"])
            result.append(d)
        return result


# ───────────────────────── ACTIVE POLLS ─────────────────────────

async def register_active_poll(tg_poll_id: str, survey_id: int,
                                question_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_polls (tg_poll_id, survey_id, question_id, user_id)"
            " VALUES (?,?,?,?)",
            (tg_poll_id, survey_id, question_id, user_id)
        )
        await db.commit()


async def get_active_poll(tg_poll_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_polls WHERE tg_poll_id=?", (tg_poll_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def remove_active_poll(tg_poll_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM active_polls WHERE tg_poll_id=?", (tg_poll_id,))
        await db.commit()


# ───────────────────────── RESPONSES ─────────────────────────

def make_anon_token(user_id: int, question_id: int) -> str:
    raw = f"{user_id}:{question_id}:{ANON_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def save_response(survey_id: int, question_id: int,
                        user_id: int, choices: list[int]) -> bool:
    """True = muvaffaqiyatli, False = allaqachon javob bergan."""
    token = make_anon_token(user_id, question_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO survey_responses (survey_id, question_id, anon_token, choices)"
                " VALUES (?,?,?,?)",
                (survey_id, question_id, token, json.dumps(choices))
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def has_answered(user_id: int, question_id: int) -> bool:
    token = make_anon_token(user_id, question_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM survey_responses WHERE question_id=? AND anon_token=?",
            (question_id, token)
        ) as cur:
            return await cur.fetchone() is not None


async def has_completed_survey(user_id: int, survey_id: int) -> bool:
    """Foydalanuvchi ushbu surveyni to'liq yakunlaganmi?"""
    qs = await get_questions(survey_id)
    if not qs:
        return False
    for q in qs:
        if not await has_answered(user_id, q["id"]):
            return False
    return True


async def get_survey_results(survey_id: int) -> dict:
    """
    Qaytaradi:
    {
      question_id: {
        "question": str,
        "options": [str],
        "total": int,
        "counts": {option_index: count}
      }
    }
    """
    qs = await get_questions(survey_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT question_id, choices FROM survey_responses WHERE survey_id=?",
            (survey_id,)
        ) as cur:
            responses = await cur.fetchall()

    # question_id => list of choices lists
    raw: dict[int, list] = {}
    for r in responses:
        qid = r["question_id"]
        raw.setdefault(qid, []).append(json.loads(r["choices"]))

    result = {}
    for q in qs:
        qid = q["id"]
        votes = raw.get(qid, [])
        counts: dict[int, int] = {}
        for vote in votes:
            for idx in vote:
                counts[idx] = counts.get(idx, 0) + 1
        result[qid] = {
            "question": q["question"],
            "options": q["options"],
            "total": len(votes),
            "counts": counts,
        }
    return result


# ───────────────────────── ANALYSIS LOG ─────────────────────────

async def log_analysis(survey_id: int, model: str,
                       prompt_tokens: int, completion_tokens: int, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO analysis_log (survey_id, model, prompt_tokens, completion_tokens, analysis)"
            " VALUES (?,?,?,?,?)",
            (survey_id, model, prompt_tokens, completion_tokens, text)
        )
        await db.commit()
