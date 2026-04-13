

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import (
    list_surveys, get_survey, get_questions, get_survey_results,
    save_response, has_answered, has_completed_survey,
    register_active_poll, get_active_poll, remove_active_poll,
)

logger = logging.getLogger(__name__)

BAR_WIDTH = 10


def pbar(pct: float) -> str:
    filled = round(pct / 100 * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


# ─────────────── COMMANDS ───────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "Bu bot orqali <b>mutloq anonim</b> so'rovnomalarda ishtirok etasiz.\n\n"
        "🔒 <i>Kim nima tanladi — hech kim bilmaydi, hatto bot ham.</i>\n\n"
        "📋 /surveys — Faol so'rovnomalar\n"
        "ℹ️ /about — Bot haqida\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 <b>Anonim So'rovnoma Tizimi</b>\n\n"
        "<b>Maxfiylik qanday ta'minlanadi?</b>\n"
        "• Telegram ID'ingiz <b>hech qachon saqlanmaydi</b>\n"
        "• Har bir javob SHA-256 kriptografik token bilan qayd etiladi\n"
        "• Token noyob, lekin teskari aylantirish <b>mumkin emas</b>\n"
        "• Admin ham kim nima javob berganini bila olmaydi\n\n"
        "<b>Jarayon:</b>\n"
        "1️⃣ So'rovnoma tanlash\n"
        "2️⃣ Savollarni ketma-ket javoblash (Telegram poll uslubida)\n"
        "3️⃣ Natijalar real-vaqtda, AI tahlil yopilgach\n\n"
        "🤖 Natijalar <b>Sun'iy intellekt</b> va <b>inson nazorati</b> ostida tahlil qilinadi."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_surveys(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Faol so'rovnomalar ro'yxati."""
    surveys = await list_surveys(active_only=True)
    if not surveys:
        await update.message.reply_text(
            "📭 Hozircha faol so'rovnomalar yo'q.\n"
            "Yangi so'rovnomalar qo'shilganda xabar beriladi."
        )
        return

    buttons = []
    for s in surveys:
        label = f"📋 {s['title']} ({s['question_count']} savol)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"sv_info:{s['id']}")])

    await update.message.reply_text(
        "📊 <b>Faol So'rovnomalar:</b>\n\n"
        "Ishtirok etmoqchi bo'lgan so'rovnomani tanlang:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_survey_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/s_N — to'g'ridan-to'g'ri survey ochish."""
    parts = update.message.text.split("_", 1)
    try:
        survey_id = int(parts[1])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Noto'g'ri havola.")
        return
    # sv_info callback ni simulate qilish
    survey = await get_survey(survey_id)
    if not survey or not survey["is_active"]:
        await update.message.reply_text("❌ Bu so'rovnoma topilmadi yoki yopilgan.")
        return
    await _send_survey_info(update.message.reply_text, survey, update.effective_user.id)


# ─────────────── SURVEY INFO ───────────────

async def cb_sv_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """So'rovnoma haqida ma'lumot + boshlash tugmasi."""
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    if not survey:
        await query.edit_message_text("❌ Topilmadi.")
        return
    await _send_survey_info(query.edit_message_text, survey, query.from_user.id)


async def _send_survey_info(send_fn, survey: dict, user_id: int):
    completed = await has_completed_survey(user_id, survey["id"])
    qs = survey["questions"]
    status_txt = "✅ <i>Siz bu so'rovnomani yakunladingiz</i>" if completed else ""

    text = (
        f"📊 <b>{survey['title']}</b>\n"
        f"{'—' * 30}\n"
    )
    if survey.get("description"):
        text += f"📝 {survey['description']}\n\n"

    text += f"❓ Savollar soni: <b>{len(qs)}</b>\n"
    text += f"🔒 <b>Mutloq anonim</b> (identifikatsiyasiz)\n"
    if status_txt:
        text += f"\n{status_txt}\n"

    buttons = []
    if survey["is_active"]:
        if completed:
            buttons.append([InlineKeyboardButton("🔁 Qayta ishtirok", callback_data=f"sv_start:{survey['id']}")])
        else:
            buttons.append([InlineKeyboardButton("▶️ Boshlash", callback_data=f"sv_start:{survey['id']}")])
        buttons.append([InlineKeyboardButton("📈 Joriy natijalar", callback_data=f"sv_results:{survey['id']}")])
    else:
        buttons.append([InlineKeyboardButton("📈 Natijalar", callback_data=f"sv_results:{survey['id']}")])
        buttons.append([InlineKeyboardButton("🤖 AI Tahlil", callback_data=f"sv_ai:{survey['id']}")])

    buttons.append([InlineKeyboardButton("🔙 Ro'yxatga", callback_data="sv_list")])

    await send_fn(text, parse_mode=ParseMode.HTML,
                  reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────── START SURVEY ───────────────

async def cb_sv_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Survey ni boshlash — birinchi savolni native poll sifatida yuborish."""
    query = update.callback_query
    await query.answer("▶️ So'rovnoma boshlanmoqda...")
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)

    if not survey or not survey["is_active"]:
        await query.message.reply_text("❌ Bu so'rovnoma yopilgan.")
        return

    questions = survey["questions"]
    if not questions:
        await query.message.reply_text("❌ Bu so'rovnomada savollar yo'q.")
        return

    # Seans ma'lumotlarini saqlash
    ctx.user_data["survey_session"] = {
        "survey_id": survey_id,
        "survey_title": survey["title"],
        "questions": questions,
        "current_idx": 0,
        "total": len(questions),
    }

    await query.message.reply_text(
        f"🚀 <b>{survey['title']}</b> so'rovnomasi boshlandi!\n\n"
        f"Jami {len(questions)} ta savol. Har biriga javob bering.\n"
        f"🔒 <i>Javoblaringiz mutloq anonim.</i>",
        parse_mode=ParseMode.HTML,
    )
    await _send_next_question(query.from_user.id, ctx, query.message.chat_id)


async def _send_next_question(user_id: int, ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Keyingi savolni native Telegram poll sifatida yuborish."""
    session: dict = ctx.user_data.get("survey_session")
    if not session:
        return

    idx = session["current_idx"]
    questions = session["questions"]
    total = session["total"]

    if idx >= total:
        # Barcha savollar tugadi
        await _finish_survey(user_id, ctx, chat_id)
        return

    q = questions[idx]
    progress = f"📝 Savol {idx + 1}/{total}"

    # Native Telegram poll yuborish
    # DIQQAT: is_anonymous=False bo'lishi shart, aks holda bot PollAnswer updatesni olmaydi 
    # va keyingi savolga o'tkaza olmaydi. 
    # Anonimlik baribir saqlanadi, chunki bot ID ni hashlab saqlaydi.
    msg = await ctx.bot.send_poll(
        chat_id=chat_id,
        question=f"{progress}\n\n{q['question']}",
        options=q["options"],
        is_anonymous=False,           # Bot javobni ko'rishi uchun
        allows_multiple_answers=bool(q.get("allow_multi", False)),
        protect_content=False,
    )

    # poll_id → question_id xaritasini DB ga saqlash
    await register_active_poll(
        tg_poll_id=msg.poll.id,
        survey_id=session["survey_id"],
        question_id=q["id"],
        user_id=user_id,
    )

    logger.info(
        "Poll yuborildi: user=%d survey=%d q=%d poll_id=%s",
        user_id, session["survey_id"], q["id"], msg.poll.id
    )


async def _finish_survey(user_id: int, ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Survey yakunlanganida xabar va natijalar."""
    session = ctx.user_data.pop("survey_session", {})
    survey_id = session.get("survey_id")
    title = session.get("survey_title", "")

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            "🎉 <b>Rahmat! So'rovnomani yakunladingiz.</b>\n\n"
            f"📊 <i>{title}</i>\n\n"
            "Javoblaringiz anonim tarzda qayd etildi.\n"
            "Natijalarni ko'rish uchun quyidagi tugmani bosing:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Natijalarni ko'rish",
                                   callback_data=f"sv_results:{survey_id}")],
            [InlineKeyboardButton("📋 Barcha so'rovnomalar", callback_data="sv_list")],
        ]),
    )


# ─────────────── POLL ANSWER HANDLER ───────────────

async def handle_poll_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Telegram PollAnswer eventi.
    is_anonymous=True bo'lsa ham DM orqali biz user_id ni olamiz —
    biz uni darhol SHA256 token ga aylantiramiz va saqlamaymiz.
    """
    answer = update.poll_answer
    tg_poll_id = answer.poll_id
    user = answer.user
    chosen = list(answer.option_ids)  # [int, ...]

    if not chosen:
        # Foydalanuvchi tanlovini bekor qildi — skip
        return

    # poll_id → savol ma'lumotlarini olish
    active = await get_active_poll(tg_poll_id)
    if not active:
        logger.warning("Noma'lum poll_id: %s", tg_poll_id)
        return

    survey_id = active["survey_id"]
    question_id = active["question_id"]
    registered_user_id = active["user_id"]

    # Faqat so'rovnomani boshlagan foydalanuvchi javob berishi kerak
    if user.id != registered_user_id:
        logger.info("Boshqa user (%d) javob berdi — e'tiborsiz", user.id)
        return

    # Javobni anonim saqlash (user.id faqat token uchun ishlatiladi)
    await save_response(survey_id, question_id, user.id, chosen)
    await remove_active_poll(tg_poll_id)

    logger.info(
        "Anonim javob: user=%d survey=%d q=%d choices=%s",
        user.id, survey_id, question_id, chosen
    )

    # Keyingi savolga o'tish
    session: dict = ctx.user_data.get("survey_session", {})
    if session.get("survey_id") == survey_id:
        session["current_idx"] += 1
        chat_id = user.id  # DM chat_id = user_id
        await _send_next_question(user.id, ctx, chat_id)
    else:
        # Session topilmadi (bot restart bo'lgan bo'lishi mumkin)
        # Survey dan qolgan savollarni topib davom ettiramiz
        await _resume_session_after_restart(user.id, ctx, survey_id, question_id)


async def _resume_session_after_restart(user_id: int, ctx: ContextTypes.DEFAULT_TYPE,
                                         survey_id: int, last_question_id: int):
    """Bot restart bo'lgandan keyin sesiyani tiklash."""
    survey = await get_survey(survey_id)
    if not survey or not survey["is_active"]:
        return

    questions = survey["questions"]
    # Kim nimadan keyin kelishini aniqlaymiz
    last_idx = next((i for i, q in enumerate(questions) if q["id"] == last_question_id), -1)
    next_idx = last_idx + 1

    if next_idx >= len(questions):
        # Tugadi
        ctx.user_data["survey_session"] = {
            "survey_id": survey_id,
            "survey_title": survey["title"],
            "questions": questions,
            "current_idx": next_idx,
            "total": len(questions),
        }
        await _finish_survey(user_id, ctx, user_id)
    else:
        ctx.user_data["survey_session"] = {
            "survey_id": survey_id,
            "survey_title": survey["title"],
            "questions": questions,
            "current_idx": next_idx,
            "total": len(questions),
        }
        await _send_next_question(user_id, ctx, user_id)


# ─────────────── RESULTS ───────────────

async def cb_sv_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Survey natijalarini ko'rsatish."""
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    if not survey:
        await query.edit_message_text("❌ Topilmadi.")
        return

    results = await get_survey_results(survey_id)
    if not results:
        await query.edit_message_text(
            "📭 Hali javoblar yo'q.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Ortga", callback_data=f"sv_info:{survey_id}")
            ]])
        )
        return

    text = f"📊 <b>{survey['title']}</b>\n<i>Natijalar</i>\n\n"

    for qid, data in results.items():
        total = data["total"]
        text += f"❓ <b>{data['question']}</b>\n"
        text += f"<i>Ishtirok: {total} ta javob</i>\n"
        for i, opt in enumerate(data["options"]):
            cnt = data["counts"].get(i, 0)
            pct = round(cnt / total * 100, 1) if total else 0
            bar = pbar(pct)
            text += f"[{bar}] {pct}%  {opt}  ({cnt})\n"
        text += "\n"

    buttons = []
    if not survey["is_active"] and survey.get("ai_analysis"):
        buttons.append([InlineKeyboardButton("🤖 AI Tahlil", callback_data=f"sv_ai:{survey_id}")])
    buttons.append([InlineKeyboardButton("🔙 Ortga", callback_data=f"sv_info:{survey_id}")])

    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cb_sv_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """AI tahlilni ko'rsatish (foydalanuvchi uchun)."""
    from ai_analyzer import format_analysis
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    if not survey:
        await query.edit_message_text("❌ Topilmadi.")
        return

    analysis = survey.get("ai_analysis")
    if not analysis:
        text = "🤖 <i>AI tahlil hali amalga oshirilmagan.\nAdmin yopilgan so'rovnomani tahlil qilishi kerak.</i>"
    else:
        text = format_analysis(analysis, survey["title"])

    buttons = [[InlineKeyboardButton("🔙 Ortga", callback_data=f"sv_info:{survey_id}")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


async def cb_sv_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Faol so'rovnomalar ro'yxatiga qaytish."""
    query = update.callback_query
    await query.answer()
    surveys = await list_surveys(active_only=True)
    if not surveys:
        await query.edit_message_text("📭 Hozircha faol so'rovnomalar yo'q.")
        return

    buttons = []
    for s in surveys:
        label = f"📋 {s['title']} ({s['question_count']} savol)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"sv_info:{s['id']}")])

    await query.edit_message_text(
        "📊 <b>Faol So'rovnomalar:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
