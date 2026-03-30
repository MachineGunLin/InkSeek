from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from utils import format_failure, load_env_file, log_failure, log_info, require_env

ROOT_DIR = Path(__file__).resolve().parent.parent
MAIN_PATH = ROOT_DIR / "main.py"


def load_bot_config() -> tuple[str, int]:
    load_env_file()
    token = require_env("TELEGRAM_BOT_TOKEN")
    allowed_user_id_raw = require_env("ALLOWED_USER_ID")

    try:
        allowed_user_id = int(allowed_user_id_raw)
    except ValueError as exc:
        raise SystemExit(format_failure(f"ALLOWED_USER_ID 不是有效整数: {exc}"))

    return token, allowed_user_id


async def run_seek_subprocess(query: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(MAIN_PATH),
        "seek",
        query,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(ROOT_DIR),
    )
    stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    return process.returncode or 0, output


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    allowed_user_id = context.application.bot_data["allowed_user_id"]
    if user.id != allowed_user_id:
        log_info("已忽略未授权请求。")
        return

    query = (message.text or "").strip()
    if not query:
        return

    await message.reply_text(f"正在为您寻墨：{query}...")
    log_info(f"已接收远程检索任务：{query}")

    return_code, output = await run_seek_subprocess(query)
    if output:
        log_info(f"任务输出:\n{output}")

    if return_code == 0:
        await message.reply_text("寻墨成功！书已送达微信读书。")
        return

    log_failure("远程检索任务执行失败。")
    await message.reply_text("寻墨失败，请查看日志。")


async def post_init(application) -> None:
    me = await application.bot.get_me()
    log_info(f"Telegram 遥控器已启动: @{me.username}")


def main() -> None:
    token, allowed_user_id = load_bot_config()
    application = ApplicationBuilder().token(token).post_init(post_init).build()
    application.bot_data["allowed_user_id"] = allowed_user_id
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log_info("Telegram 遥控器开始监听消息。")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
