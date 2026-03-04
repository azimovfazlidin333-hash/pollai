"""
Asosiy bot fayli — barcha handlerlarni birlashtiradi.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

from telegram import BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PollAnswerHandler, filters,
)

from database import init_db
from user_handlers import (
    cmd_start, cmd_surveys, cmd_about, cmd_survey_link,
    cb_sv_info, cb_sv_start, cb_sv_results, cb_sv_ai, cb_sv_list,
    handle_poll_answer,
)
from admin_handlers import (
    cmd_admin,
    cb_adm_home, cb_adm_list, cb_adm_survey,
    cb_adm_results, cb_adm_close,
    cb_adm_ai, cb_adm_ai_show,
    cb_adm_del_confirm, cb_adm_del_yes,
    cb_adm_stats,
    build_create_conv,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def post_init(app: Application):
    """DB ni ishga tushirish va bot buyruqlarini ro'yxatga olish."""
    await init_db()
    logger.info("✅ Database tayyor.")
    await app.bot.set_my_commands([
        BotCommand("start",   "Botni boshlash"),
        BotCommand("surveys", "Faol so'rovnomalar"),
        BotCommand("about",   "Bot haqida"),
        BotCommand("admin",   "Admin panel"),
        BotCommand("done",    "So'rovnoma yaratishni yakunlash"),
        BotCommand("undo",    "Oxirgi savolni o'chirish"),
        BotCommand("cancel",  "Bekor qilish"),
        BotCommand("skip",    "O'tkazib yuborish"),
    ])
    logger.info("✅ Bot buyruqlari sozlandi.")


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN .env faylida topilmadi!")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # ── 1. ConversationHandler (eng yuqori ustuvorlik) ──
    app.add_handler(build_create_conv())

    # ── 2. Foydalanuvchi komandalar ──
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("surveys", cmd_surveys))
    app.add_handler(CommandHandler("about",   cmd_about))

    # ── 3. Admin komandalar ──
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("cancel", lambda u, c: None))  # global fallback

    # ── 4. /s_N direct link ──
    app.add_handler(MessageHandler(filters.Regex(r"^/s_\d+"), cmd_survey_link))

    # ── 5. PollAnswer — anonim javoblarni qayd etish ──
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    # ── 6. Foydalanuvchi callback-lar ──
    app.add_handler(CallbackQueryHandler(cb_sv_info,     pattern=r"^sv_info:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sv_start,    pattern=r"^sv_start:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sv_results,  pattern=r"^sv_results:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sv_ai,       pattern=r"^sv_ai:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sv_list,     pattern=r"^sv_list$"))

    # ── 7. Admin callback-lar ──
    app.add_handler(CallbackQueryHandler(cb_adm_home,        pattern=r"^adm_home$"))
    app.add_handler(CallbackQueryHandler(cb_adm_list,        pattern=r"^adm_list$"))
    app.add_handler(CallbackQueryHandler(cb_adm_survey,      pattern=r"^adm_sv:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_results,     pattern=r"^adm_res:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_close,       pattern=r"^adm_close:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_ai,          pattern=r"^adm_ai:\d+:(fast|deep)$"))
    app.add_handler(CallbackQueryHandler(cb_adm_ai_show,     pattern=r"^adm_ai_show:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_del_confirm, pattern=r"^adm_del_confirm:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_del_yes,     pattern=r"^adm_del_yes:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_stats,       pattern=r"^adm_stats$"))

    logger.info("🚀 Bot ishga tushmoqda...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
