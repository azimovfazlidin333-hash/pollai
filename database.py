import aiosqlite
import json
import hashlib
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "voting.db")
ANON_SALT = os.getenv("ANON_SALT", "xY9#mK!p2qR@survey")


# ───────────────────────── INIT ─────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            faculty TEXT,
            course INTEGER,
            gender TEXT
        );

        CREATE TABLE IF NOT EXISTS surveys (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            created_by   INTEGER NOT NULL,
            faculty      TEXT,
            course       INTEGER,
            gender       TEXT,
            deadline     TEXT,
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
            options      TEXT NOT NULL,
            allow_multi  INTEGER DEFAULT 0,
            FOREIGN KEY (survey_id) REFERENCES surveys(id)
        );

        CREATE TABLE IF NOT EXISTS survey_responses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id    INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            anon_token   TEXT NOT NULL,
            choices      TEXT NOT NULL,
            answered_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(question_id, anon_token)
        );

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


# ───────────────────────── USERS ─────────────────────────

async def save_user(user_id: int, faculty: str, course: int, gender: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, faculty, course, gender)
            VALUES (?, ?, ?, ?)
        """, (user_id, faculty, course, gender))
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()

        return dict(row) if row else None


# ───────────────────────── SURVEY CRUD ─────────────────────────

async def create_survey(title: str, description: str,
                        questions: list[dict], admin_id: int,
                        faculty=None, course=None, gender=None,
                        deadline=None) -> int:

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO surveys
            (title, description, created_by, faculty, course, gender, deadline)
            VALUES (?,?,?,?,?,?,?)""",
            (title, description, admin_id, faculty, course, gender, deadline)
        )

        survey_id = cur.lastrowid

        for idx, q in enumerate(questions):
            await db.execute(
                """INSERT INTO survey_questions
                (survey_id, order_num, question, options, allow_multi)
                VALUES (?,?,?,?,?)""",
                (
                    survey_id,
                    idx,
                    q["question"],
                    json.dumps(q["options"], ensure_ascii=False),
                    int(q.get("allow_multi", False))
                )
            )

        await db.commit()

    return survey_id


async def get_survey(survey_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT * FROM surveys WHERE id=?",
            (survey_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return None

        d = dict(row)
        d["ai_analysis"] = json.loads(d["ai_analysis"]) if d["ai_analysis"] else None

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


# ───────────────────────── FILTER HELPERS ─────────────────────────

async def check_access(user_id: int, survey: dict) -> bool:
    user = await get_user(user_id)
    if not user:
        return False

    if survey.get("deadline"):
        if datetime.now() > datetime.fromisoformat(survey["deadline"]):
            return False

    if survey.get("faculty") and survey["faculty"] != user["faculty"]:
        return False

    if survey.get("course") and str(survey["course"]) != str(user["course"]):
        return False

    if survey.get("gender") and survey["gender"] != user["gender"]:
        return False

    return True


# ───────────────────────── ANON TOKEN ─────────────────────────

def make_anon_token(user_id: int, question_id: int) -> str:
    raw = f"{user_id}:{question_id}:{ANON_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()
