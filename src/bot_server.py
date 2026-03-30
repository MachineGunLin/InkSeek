from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from telegram import Update
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from seek_pipeline import (
    STATE_DUPLICATE_FOUND,
    STATE_NOT_FOUND,
    STATE_WAITING_FOR_SELECTION,
    WeReadSeekPreparation,
    execute_selection,
    format_candidate_options,
    prepare_seek_request,
    run_public_fallback,
)
from utils import format_failure, load_env_file, log_failure, log_info, require_env

SELECTION_TIMEOUT_SECONDS = 60
REPLY_RETRY_ATTEMPTS = 3


@dataclass
class PendingSelection:
    query: str
    chat_id: int
    user_id: int
    preparation: WeReadSeekPreparation
    timeout_task: asyncio.Task | None = None


def load_bot_config() -> tuple[str, int]:
    load_env_file()
    token = require_env("TELEGRAM_BOT_TOKEN")
    allowed_user_id_raw = require_env("ALLOWED_USER_ID")

    try:
        allowed_user_id = int(allowed_user_id_raw)
    except ValueError as exc:
        raise SystemExit(format_failure(f"ALLOWED_USER_ID 不是有效整数: {exc}"))

    return token, allowed_user_id


def build_request() -> HTTPXRequest:
    return HTTPXRequest(
        connection_pool_size=32,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(retries=3),
        },
    )


async def reply_with_retry(message, text: str) -> None:
    for attempt in range(1, REPLY_RETRY_ATTEMPTS + 1):
        try:
            await message.reply_text(text)
            return
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after))
        except (TimedOut, NetworkError):
            if attempt >= REPLY_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(float(attempt * 2))


async def send_text_with_retry(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    bot = context.application.bot
    for attempt in range(1, REPLY_RETRY_ATTEMPTS + 1):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            return
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after))
        except (TimedOut, NetworkError):
            if attempt >= REPLY_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(float(attempt * 2))


def get_pending_selection(context: ContextTypes.DEFAULT_TYPE) -> PendingSelection | None:
    return context.application.bot_data.get("pending_selection")


def set_pending_selection(context: ContextTypes.DEFAULT_TYPE, pending: PendingSelection | None) -> None:
    context.application.bot_data["pending_selection"] = pending


async def clear_pending_selection(context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = get_pending_selection(context)
    if pending is None:
        return

    task = pending.timeout_task
    current_task = asyncio.current_task()
    if task is not None and task is not current_task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    set_pending_selection(context, None)


async def run_public_seek_flow(context: ContextTypes.DEFAULT_TYPE, message, query: str) -> None:
    await reply_with_retry(message, "站内无果，正在启动公开书源寻墨...")
    try:
        await asyncio.to_thread(run_public_fallback, query)
    except SystemExit as exc:
        log_failure("公开书源寻墨失败。")
        reason = str(exc)
        if "未找到与" in reason:
            await reply_with_retry(message, "对不起，寻墨未果。我会继续留意这本书的信息。")
            return
        await reply_with_retry(message, "寻墨失败，请查看日志。")
        return

    await reply_with_retry(message, "寻墨成功！书已送达。")


async def execute_pending_selection(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    selection_index: int | None,
    *,
    prefix_message: str | None = None,
) -> None:
    pending = get_pending_selection(context)
    if pending is None:
        return

    preparation = pending.preparation
    await clear_pending_selection(context)

    if prefix_message:
        await send_text_with_retry(context, chat_id, prefix_message)

    try:
        result = await asyncio.to_thread(
            execute_selection,
            preparation,
            selection_index=selection_index,
        )
    except SystemExit as exc:
        log_failure("站内选书入库失败。")
        await send_text_with_retry(context, chat_id, "寻墨失败，请查看日志。")
        log_info(f"失败原因: {exc}")
        return

    await send_text_with_retry(context, chat_id, result)


async def auto_select_after_timeout(context: ContextTypes.DEFAULT_TYPE, chat_id: int, query: str) -> None:
    try:
        await asyncio.sleep(SELECTION_TIMEOUT_SECONDS)
        pending = get_pending_selection(context)
        if pending is None or pending.chat_id != chat_id or pending.query != query:
            return
        await execute_pending_selection(
            context,
            chat_id,
            None,
            prefix_message="60 秒未收到选择，已自动为您选择推荐值最高的版本。",
        )
    except asyncio.CancelledError:
        return


async def start_selection_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    await reply_with_retry(message, "正在为您翻阅微信读书...")
    log_info(f"已接收远程检索任务：{query}")

    try:
        preparation = await asyncio.to_thread(prepare_seek_request, query)
    except SystemExit as exc:
        reason = str(exc)
        if "书架已存在《" in reason:
            await reply_with_retry(message, reason.split("寻墨成功：", 1)[-1])
            return
        await reply_with_retry(message, "寻墨失败，请查看日志。")
        log_failure(f"检索任务准备失败：{reason}")
        return

    if preparation.state == STATE_DUPLICATE_FOUND and preparation.duplicate is not None:
        await reply_with_retry(message, f"书架已存在《{preparation.duplicate.title}》，无需重复入库。")
        return

    if preparation.state == STATE_NOT_FOUND:
        await run_public_seek_flow(context, message, preparation.query)
        return

    if preparation.state != STATE_WAITING_FOR_SELECTION:
        await reply_with_retry(message, "寻墨失败，请查看日志。")
        return

    pending = PendingSelection(
        query=preparation.query,
        chat_id=message.chat_id,
        user_id=user.id,
        preparation=preparation,
    )
    set_pending_selection(context, pending)
    pending.timeout_task = asyncio.create_task(auto_select_after_timeout(context, message.chat_id, preparation.query))
    await reply_with_retry(message, format_candidate_options(preparation.candidates))


async def handle_selection_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pending: PendingSelection,
    text: str,
) -> None:
    message = update.effective_message
    if message is None:
        return

    if not text.isdigit():
        await reply_with_retry(message, "当前正在等待选书，请回复 1-3。60 秒未回复时我会自动选择推荐值最高的版本。")
        return

    selection = int(text)
    if selection < 1 or selection > len(pending.preparation.candidates):
        await reply_with_retry(message, f"可选范围是 1-{len(pending.preparation.candidates)}，请重新回复数字。")
        return

    await execute_pending_selection(
        context,
        pending.chat_id,
        selection - 1,
        prefix_message="已收到您的选择，正在为您入库...",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    allowed_user_id = context.application.bot_data["allowed_user_id"]
    if user.id != allowed_user_id:
        log_info("已忽略未授权请求。")
        return

    text = (message.text or "").strip()
    if not text:
        return

    pending = get_pending_selection(context)
    if pending is not None and pending.user_id == user.id and pending.chat_id == message.chat_id:
        await handle_selection_input(update, context, pending, text)
        return

    await start_selection_flow(update, context, text)


async def post_init(application) -> None:
    me = await application.bot.get_me()
    log_info(f"Telegram 遥控器已启动: @{me.username}")


def main() -> None:
    token, allowed_user_id = load_bot_config()
    request = build_request()
    updates_request = build_request()
    application = (
        ApplicationBuilder()
        .token(token)
        .request(request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .build()
    )
    application.bot_data["allowed_user_id"] = allowed_user_id
    application.bot_data["pending_selection"] = None
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log_info("Telegram 遥控器开始监听消息。")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
