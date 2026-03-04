"""
Admin handlerlari — so'rovnoma to'plamlarini yaratish (faqat native Telegram Poll orqali),
boshqarish, AI tahlil va natijalar eksporti.
"""

import os
import json
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, KeyboardButton, KeyboardButtonPollType,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters,
    ConversationHandler
)

from database import (
    list_surveys, get_survey, create_survey,
    close_survey, delete_survey, get_survey_results,
)
from ai_analyzer import analyze_survey, format_analysis

logger = logging.getLogger(__name__)

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "0")).split(",")]

# ── ConversationHandler states ──
(
    CV_TITLE,       # Sarlavha
    CV_DESC,        # Tavsif
    CV_QUESTIONS,   # Savollar qo'shish (faqat Poll)
    CV_CONFIRM,     # Yakuniy tasdiqlash
) = range(4)


# ─────────────────────────── HELPERS ───────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if not uid or uid not in ADMIN_IDS:
            if update.callback_query:
                await update.callback_query.answer("🚫 Ruxsat yo'q!", show_alert=True)
            elif update.message:
                await update.message.reply_text("🚫 Bu buyruq faqat adminlar uchun.")
            return
        return await fn(update, ctx)
    wrapper.__name__ = fn.__name__
    return wrapper


def pbar(pct: float, w: int = 10) -> str:
    filled = round(pct / 100 * w)
    return "█" * filled + "░" * (w - filled)


def questions_summary(questions: list[dict]) -> str:
    if not questions:
        return "<i>Hali savollar yo'q</i>"
    lines = []
    for i, q in enumerate(questions):
        multi = "🔲" if q.get("allow_multi") else "🔘"
        opts = ", ".join(q["options"][:3])
        if len(q["options"]) > 3:
            opts += f" (+{len(q['options'])-3})"
        lines.append(f"{i+1}. {multi} <b>{q['question']}</b>\n   <i>{opts}</i>")
    return "\n".join(lines)


# ─────────────────────────── ADMIN PANEL ───────────────────────────

@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    surveys = await list_surveys()
    active = sum(1 for s in surveys if s["is_active"])
    closed = len(surveys) - active

    text = (
        "⚙️ <b>Admin Panel</b>\n\n"
        f"🟢 Faol: <b>{active}</b>   🔴 Yopilgan: <b>{closed}</b>\n"
    )
    buttons = [
        [InlineKeyboardButton("➕ Yangi So'rovnoma", callback_data="adm_create")],
        [InlineKeyboardButton("📋 Barcha So'rovnomalar", callback_data="adm_list")],
        [InlineKeyboardButton("📈 Umumiy Statistika", callback_data="adm_stats")],
    ]
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@admin_only
async def cb_adm_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    surveys = await list_surveys()
    active = sum(1 for s in surveys if s["is_active"])
    closed = len(surveys) - active
    text = (
        "⚙️ <b>Admin Panel</b>\n\n"
        f"🟢 Faol: <b>{active}</b>   🔴 Yopilgan: <b>{closed}</b>\n"
    )
    buttons = [
        [InlineKeyboardButton("➕ Yangi So'rovnoma", callback_data="adm_create")],
        [InlineKeyboardButton("📋 Barcha So'rovnomalar", callback_data="adm_list")],
        [InlineKeyboardButton("📈 Umumiy Statistika", callback_data="adm_stats")],
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────── LIST ───────────────────────────

@admin_only
async def cb_adm_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    surveys = await list_surveys()
    if not surveys:
        await query.edit_message_text(
            "📭 Hali so'rovnomalar yo'q.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Ortga", callback_data="adm_home")
            ]])
        )
        return

    buttons = []
    for s in surveys:
        icon = "🟢" if s["is_active"] else "🔴"
        label = f"{icon} {s['title']} ({s['question_count']} savol)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"adm_sv:{s['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Ortga", callback_data="adm_home")])

    await query.edit_message_text(
        "📋 <b>Barcha So'rovnomalar:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ─────────────────────────── SURVEY PANEL ───────────────────────────

@admin_only
async def cb_adm_survey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    await _render_survey_panel(query.edit_message_text, survey_id)


async def _render_survey_panel(send_fn, survey_id: int):
    survey = await get_survey(survey_id)
    if not survey:
        await send_fn("❌ Topilmadi.")
        return

    results = await get_survey_results(survey_id)
    total_res = max((v["total"] for v in results.values()), default=0) if results else 0

    status = "🟢 Faol" if survey["is_active"] else "🔴 Yopilgan"
    text = (
        f"📊 <b>{survey['title']}</b>\n"
        f"{status} | 📅 {survey['created_at'][:16]}\n"
    )
    if survey.get("closed_at"):
        text += f"🔒 Yopildi: {survey['closed_at'][:16]}\n"
    if survey.get("description"):
        text += f"\n📝 <i>{survey['description']}</i>\n"

    text += f"\n❓ Savollar: <b>{len(survey['questions'])}</b> ta\n"
    text += f"👥 Ishtirokchilar: <b>{total_res}</b> ta\n"

    # Natijalar mini-ko'rinishi
    if results:
        text += "\n<b>Natijalar xulasasi:</b>\n"
        for qid, data in list(results.items())[:3]:  # max 3 savol
            total = data["total"]
            top_opt = max(data["counts"].items(), key=lambda x: x[1], default=(0, 0))
            if top_opt[0] != 0 or data["counts"]:
                top_label = data["options"][top_opt[0]] if data["options"] else "—"
                pct = round(top_opt[1] / total * 100) if total else 0
                text += f"  • <i>{data['question'][:40]}...</i>\n"
                text += f"    🏆 {top_label} ({pct}%)\n"

    # Ulashish havola
    text += f"\n🔗 Ulashing: /s_{survey_id}\n"

    buttons = []
    if survey["is_active"]:
        buttons.append([
            InlineKeyboardButton("🔒 Yopish", callback_data=f"adm_close:{survey_id}"),
            InlineKeyboardButton("📊 Natijalar", callback_data=f"adm_res:{survey_id}"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("🤖 AI (tez)", callback_data=f"adm_ai:{survey_id}:fast"),
            InlineKeyboardButton("🤖 AI (chuqur)", callback_data=f"adm_ai:{survey_id}:deep"),
        ])
        buttons.append([InlineKeyboardButton("📊 Natijalar", callback_data=f"adm_res:{survey_id}")])
        if survey.get("ai_analysis"):
            buttons.append([InlineKeyboardButton("📄 AI Tahlilni Ko'r", callback_data=f"adm_ai_show:{survey_id}")])

    buttons.append([
        InlineKeyboardButton("🗑️ O'chirish", callback_data=f"adm_del_confirm:{survey_id}"),
        InlineKeyboardButton("🔙 Ro'yxat", callback_data="adm_list"),
    ])

    await send_fn(text, parse_mode=ParseMode.HTML,
                  reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────── RESULTS ───────────────────────────

@admin_only
async def cb_adm_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    results = await get_survey_results(survey_id)

    if not results:
        await query.edit_message_text(
            "📭 Hali javoblar yo'q.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Ortga", callback_data=f"adm_sv:{survey_id}")
            ]])
        )
        return

    text = f"📊 <b>{survey['title']}</b> — Batafsil Natijalar\n\n"
    for qdata in results.values():
        total = qdata["total"]
        text += f"❓ <b>{qdata['question']}</b>\n"
        text += f"<i>Jami: {total} javob</i>\n"
        for i, opt in enumerate(qdata["options"]):
            cnt = qdata["counts"].get(i, 0)
            pct = round(cnt / total * 100, 1) if total else 0
            bar = pbar(pct)
            text += f"[{bar}] {pct}%  {opt}  (<b>{cnt}</b>)\n"
        text += "\n"

    buttons = [[InlineKeyboardButton("🔙 Ortga", callback_data=f"adm_sv:{survey_id}")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────── CLOSE ───────────────────────────

@admin_only
async def cb_adm_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    await close_survey(survey_id)
    await query.answer("✅ So'rovnoma yopildi.", show_alert=True)
    await _render_survey_panel(query.edit_message_text, survey_id)


# ─────────────────────────── AI ───────────────────────────

@admin_only
async def cb_adm_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🤖 AI tahlil boshlandi...")
    parts = query.data.split(":")
    survey_id = int(parts[1])
    mode = parts[2] if len(parts) > 2 else "fast"

    await query.edit_message_text(
        "⏳ <b>AI tahlil amalga oshirilmoqda...</b>\n\n"
        "Bu bir necha soniya vaqt oladi...",
        parse_mode=ParseMode.HTML
    )

    analysis = await analyze_survey(survey_id, use_secondary=(mode == "deep"))
    survey = await get_survey(survey_id)
    text = format_analysis(analysis, survey["title"] if survey else "")

    buttons = [
        [InlineKeyboardButton("🔙 So'rovnomaga", callback_data=f"adm_sv:{survey_id}")],
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


@admin_only
async def cb_adm_ai_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    analysis = survey.get("ai_analysis") if survey else None

    if not analysis:
        await query.answer("❌ AI tahlil topilmadi.", show_alert=True)
        return

    text = format_analysis(analysis, survey["title"])
    buttons = [[InlineKeyboardButton("🔙 Ortga", callback_data=f"adm_sv:{survey_id}")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────── DELETE ───────────────────────────

@admin_only
async def cb_adm_del_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    survey = await get_survey(survey_id)
    buttons = [[
        InlineKeyboardButton("✅ Ha, o'chir", callback_data=f"adm_del_yes:{survey_id}"),
        InlineKeyboardButton("❌ Bekor", callback_data=f"adm_sv:{survey_id}"),
    ]]
    await query.edit_message_text(
        f"⚠️ <b>Tasdiqlang</b>\n\n"
        f"«{survey['title']}» va barcha javoblar o'chiriladi.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@admin_only
async def cb_adm_del_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    survey_id = int(query.data.split(":")[1])
    await delete_survey(survey_id)
    await query.edit_message_text("🗑️ So'rovnoma o'chirildi.")


# ─────────────────────────── STATS ───────────────────────────

@admin_only
async def cb_adm_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    surveys = await list_surveys()
    total_resp = 0
    max_sv, max_sv_n = None, 0
    for s in surveys:
        res = await get_survey_results(s["id"])
        n = max((v["total"] for v in res.values()), default=0) if res else 0
        total_resp += n
        if n > max_sv_n:
            max_sv_n = n
            max_sv = s["title"]

    text = (
        "📈 <b>Umumiy Statistika</b>\n\n"
        f"📊 Jami so'rovnomalar: <b>{len(surveys)}</b>\n"
        f"🟢 Faol: <b>{sum(1 for s in surveys if s['is_active'])}</b>\n"
        f"🔴 Yopilgan: <b>{sum(1 for s in surveys if not s['is_active'])}</b>\n"
        f"🗳️ Jami ishtiroklar: <b>~{total_resp}</b>\n"
    )
    if max_sv:
        text += f"\n🏆 Eng faol: <b>{max_sv}</b> ({max_sv_n} ta)\n"

    buttons = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="adm_home")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons))


# ═══════════════════════════════════════════════════════════════════
# SO'ROVNOMA YARATISH — ConversationHandler (faqat Poll orqali)
# ═══════════════════════════════════════════════════════════════════

# ── Entry: callback ──

@admin_only
async def cv_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Yangi so'rovnoma yaratishni boshlash."""
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    ctx.user_data["new_sv"] = {"questions": []}

    await query.edit_message_text(
        "➕ <b>Yangi So'rovnoma</b>\n\n"
        "1️⃣ So'rovnoma <b>sarlavhasini</b> kiriting:\n\n"
        "<i>Bekor qilish: /cancel</i>",
        parse_mode=ParseMode.HTML,
    )
    return CV_TITLE


# ── CV_TITLE ──

async def cv_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if len(title) < 3:
        await update.message.reply_text("⚠️ Sarlavha juda qisqa. Qayta kiriting:")
        return CV_TITLE

    ctx.user_data["new_sv"]["title"] = title
    await update.message.reply_text(
        "2️⃣ <b>Tavsif kiriting</b> (ixtiyoriy):\n"
        "<i>O'tkazib yuborish uchun /skip</i>",
        parse_mode=ParseMode.HTML,
    )
    return CV_DESC


# ── CV_DESC ──

async def cv_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_sv"]["description"] = update.message.text.strip()
    return await _ask_for_question(update, ctx)


async def cv_skip_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_sv"]["description"] = ""
    return await _ask_for_question(update, ctx)


async def _ask_for_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qs = ctx.user_data["new_sv"]["questions"]
    n = len(qs)
    text = f"3️⃣ So'rov <b>№{n + 1}</b>\n\n"
    if n > 0:
        text += f"✅ Allaqachon qo'shildi: {n} ta savol\n"
        text += "<i>/done — yakunlash</i>\n"
        text += "<i>/undo — oxirgi savolni o'chirish</i>\n\n"

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📊 So'rovnoma yuborish", request_poll=KeyboardButtonPollType())]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await update.message.reply_text(
        f"{text}"
        "Pastdagi tugmani bosib <b>So'rovnoma</b> yuboring:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    return CV_QUESTIONS


# ── CV_QUESTIONS ──

async def cv_poll_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Native Telegram poll yuborilganda ma'lumotlarni ajratib olish."""
    poll = update.message.poll
    if not poll:
        return CV_QUESTIONS

    question = poll.question
    options = [o.text for o in poll.options]
    allow_multi = poll.allows_multiple_answers

    ctx.user_data["new_sv"]["questions"].append({
        "question": question,
        "options": options,
        "allow_multi": allow_multi
    })

    qs = ctx.user_data["new_sv"]["questions"]
    await update.message.reply_text(
        f"✅ Savol №{len(qs)} qo'shildi!\n\n"
        "Keyingi savolni yuboring yoki /done bosing.",
        parse_mode=ParseMode.HTML
    )
    return CV_QUESTIONS


async def cv_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qs = ctx.user_data["new_sv"]["questions"]
    if not qs:
        await update.message.reply_text("⚠️ O'chiriladigan savol yo'q.")
        return CV_QUESTIONS

    removed = qs.pop()
    await update.message.reply_text(
        f"↩️ <b>«{removed['question']}»</b> o'chirildi.\n\n"
        f"Hozirda <b>{len(qs)}</b> ta savol.\n\n"
        f"Yangi savol yuboring yoki /done.",
        parse_mode=ParseMode.HTML,
    )
    return CV_QUESTIONS


async def cv_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qs = ctx.user_data["new_sv"].get("questions", [])
    if not qs:
        await update.message.reply_text(
            "⚠️ Kamida 1 ta so'rovnoma yuboring!",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📊 So'rovnoma yuborish", request_poll=KeyboardButtonPollType())]],
                resize_keyboard=True
            )
        )
        return CV_QUESTIONS

    sv = ctx.user_data["new_sv"]
    text = (
        "✅ <b>So'rovnomani tasdiqlang:</b>\n\n"
        f"📌 Sarlavha: <b>{sv['title']}</b>\n"
        f"📝 Tavsif: {sv.get('description') or '—'}\n"
        f"❓ Savollar: <b>{len(qs)}</b> ta\n\n"
        f"{questions_summary(qs)}"
    )
    buttons = [[
        InlineKeyboardButton("✅ Tasdiqlash va Yaratish", callback_data="cv_confirm_yes"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data="cv_confirm_no"),
    ]]
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(
        "Tasdiqlaysizmi?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CV_CONFIRM


# ── CV_CONFIRM ──

async def cv_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cv_confirm_no":
        ctx.user_data.clear()
        await query.edit_message_text("❌ Bekor qilindi.")
        return ConversationHandler.END

    sv = ctx.user_data["new_sv"]
    survey_id = await create_survey(
        title=sv["title"],
        description=sv.get("description", ""),
        questions=sv["questions"],
        admin_id=query.from_user.id,
    )
    ctx.user_data.clear()

    await query.edit_message_text(
        f"🎉 <b>So'rovnoma yaratildi!</b>\n\n"
        f"🆔 ID: <code>{survey_id}</code>\n\n"
        f"Foydalanuvchilar bilan ulashing:\n"
        f"/s_{survey_id}\n\n"
        f"<i>Foydalanuvchilar ushbu buyruqni bosganda so'rovnoma boshlanadi.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 So'rovnomani ochish", callback_data=f"adm_sv:{survey_id}")
        ]])
    )
    return ConversationHandler.END


# ── Global Actions ──

async def cv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Bekor qilindi.")
    elif update.message:
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════
# ConversationHandler qurilmasi
# ═══════════════════════════════════════════════════════════════════

def build_create_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cv_entry, pattern="^adm_create$")],
        states={
            CV_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cv_title),
            ],
            CV_DESC: [
                CommandHandler("skip", cv_skip_desc),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cv_desc),
            ],
            CV_QUESTIONS: [
                CommandHandler("done", cv_done),
                CommandHandler("undo", cv_undo),
                MessageHandler(filters.POLL, cv_poll_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text("⚠️ Iltimos, faqat Poll yuboring (tugmani bosing) yoki /done bosing.")),
            ],
            CV_CONFIRM: [
                CallbackQueryHandler(cv_confirm, pattern="^cv_confirm_(yes|no)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cv_cancel),
            CallbackQueryHandler(cv_cancel, pattern="^adm_create_cancel$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
