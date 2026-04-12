"""
Foydalanuvchi handlerlari.
Native Telegram polllar ketma-ket yuboriladi (quiz-bot uslubida).
PollAnswerHandler anonim javoblarni qayd etadi va keyingi savolni yuboradi.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from database import (
    list_surveys, get_survey, get_questions, get_survey_results,
    save_response, has_answered, has_completed_survey,
    register_active_poll, get_active_poll, remove_active_poll,
    save_user, get_user, check_access
)

logger = logging.getLogger(__name__)

BAR_WIDTH = 10

# ───────────────────────── REGISTER STATES ─────────────────────────

FACULTY, COURSE, GENDER = range(3)


def pbar(pct: float) -> str:
    filled = round(pct / 100 * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


# ───────────────────────── REGISTER FLOW ─────────────────────────

async def start_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)

    if user:
        return ConversationHandler.END

    await update.message.reply_text("📚 Fakul'tetingizni kiriting:")
    return FACULTY


async def get_faculty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["faculty"] = update.message.text.strip()
    await update.message.reply_text("🎓 Kursingiz (1-4):")
    return COURSE


async def get_course(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["course"] = int(update.message.text.strip())
    await update.message.reply_text("⚧ Jinsingiz (erkak/ayol):")
    return GENDER


async def get_gender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await save_user(
        update.effective_user.id,
        ctx.user_data["faculty"],
        ctx.user_data["course"],
        update.message.text.strip().lower()
    )

    await update.message.reply_text("✅ Ro‘yxatdan o‘tdingiz!")
    return ConversationHandler.END


# ───────────────────────── COMMANDS ─────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "📊 /surveys — Faol so'rovnomalar\n"
        "ℹ️ /about — Bot haqida\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔒 Anonim so'rovnoma bot\n\n"
        "• Javoblar SHA256 orqali anonimlashtiriladi\n"
        "• Admin user identityni ko‘ra olmaydi",
        parse_mode=ParseMode.HTML
    )


# ───────────────────────── SURVEYS LIST ─────────────────────────

async def cmd_surveys(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    surveys = await list_surveys(active_only=True)

    if not surveys:
        await update.message.reply_text("📭 Faol so'rovnoma yo‘q.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"📋 {s['title']} ({s['question_count']} savol)",
            callback_data=f"sv_info:{s['id']}"
        )]
        for s in surveys
    ]

    await update.message.reply_text(
        "📊 <b>Faol so‘rovnomalar:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ───────────────────────── SURVEY INFO ─────────────────────────

async def cb_sv_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)

    if not survey:
        await query.edit_message_text("❌ Topilmadi.")
        return

    user_id = query.from_user.id
    completed = await has_completed_survey(user_id, survey_id)

    text = f"📊 <b>{survey['title']}</b>\n\n"

    if survey.get("description"):
        text += f"📝 {survey['description']}\n\n"

    text += f"❓ Savollar: {len(survey['questions'])}\n"

    if completed:
        text += "\n✅ Siz allaqachon qatnashgansiz"

    buttons = []

    if survey["is_active"]:
        buttons.append([
            InlineKeyboardButton(
                "▶️ Boshlash" if not completed else "🔁 Qayta",
                callback_data=f"sv_start:{survey_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("📈 Natijalar", callback_data=f"sv_results:{survey_id}")
    ])

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ───────────────────────── START SURVEY ─────────────────────────

async def cb_sv_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)

    if not survey:
        return

    ctx.user_data["survey_session"] = {
        "survey_id": survey_id,
        "questions": survey["questions"],
        "current_idx": 0,
        "total": len(survey["questions"]),
    }

    await query.message.reply_text("🚀 So‘rovnoma boshlandi!")
    await _send_next_question(query.from_user.id, ctx, query.message.chat_id)


# ───────────────────────── NEXT QUESTION ─────────────────────────

async def _send_next_question(user_id: int, ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    session = ctx.user_data.get("survey_session")
    if not session:
        return

    idx = session["current_idx"]
    questions = session["questions"]

    if idx >= len(questions):
        await _finish_survey(user_id, ctx, chat_id)
        return

    q = questions[idx]

    msg = await ctx.bot.send_poll(
        chat_id=chat_id,
        question=q["question"],
        options=q["options"],
        is_anonymous=False,
        allows_multiple_answers=bool(q.get("allow_multi", False)),
    )

    await register_active_poll(
        msg.poll.id,
        session["survey_id"],
        q["id"],
        user_id
    )


# ───────────────────────── POLL ANSWER ─────────────────────────

async def handle_poll_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    user = answer.user
    chosen = list(answer.option_ids or [])

    if not chosen:
        return

    active = await get_active_poll(answer.poll_id)
    if not active:
        return

    if user.id != active["user_id"]:
        return

    await save_response(
        active["survey_id"],
        active["question_id"],
        user.id,
        chosen
    )

    await remove_active_poll(answer.poll_id)

    session = ctx.user_data.get("survey_session") or {}

    if session.get("survey_id") == active["survey_id"]:
        session["current_idx"] += 1
        await _send_next_question(user.id, ctx, update.effective_chat.id)
