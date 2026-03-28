from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Error

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "weread_state.json"
HOME_URL = "https://weread.qq.com/"

NICKNAME_SELECTORS = [
    ".wr_nickname",
    ".wr_reader_name",
    ".reader_name",
    "[class*='nickname']",
    "[class*='reader-name']",
    "[class*='user-name']",
]

BOOK_LIST_SELECTORS = [
    ".shelfBook",
    ".shelf_book",
    ".reader-shelf-book",
    ".wr_bookCard",
    "[class*='shelf'] [class*='book']",
    "[class*='shelf'] li",
]

LOGIN_ENTRY_SELECTORS = [
    "text=登录",
    "text=立即登录",
    "button:has-text('登录')",
    "a:has-text('登录')",
    "div:has-text('登录')",
]


def session_file_usable() -> bool:
    if not STATE_PATH.exists():
        return False
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    return bool(cookies)


def remove_state_file() -> None:
    if STATE_PATH.exists():
        STATE_PATH.unlink()


def safe_page_url(page) -> str:
    try:
        return page.url
    except Error:
        return ""


def first_nonempty_text(page, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=200):
                continue
            text = locator.inner_text(timeout=200).strip()
            if text:
                return text
        except Error:
            continue
    return ""


def visible_count(page, selectors: list[str]) -> int:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
            if count > 0:
                return count
        except Error:
            continue
    return 0


def has_login_entry(page) -> bool:
    for selector in LOGIN_ENTRY_SELECTORS:
        try:
            if page.locator(selector).first.is_visible(timeout=200):
                return True
        except Error:
            continue
    return False


def verify_session(page, timeout_seconds: int = 10) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_reason = "未拿到书架内容"

    try:
        page.goto(HOME_URL, wait_until="commit", timeout=20000)
        page.wait_for_timeout(1000)
    except Error as exc:
        return False, f"访问首页失败: {type(exc).__name__}: {exc}"

    while time.time() < deadline:
        current_url = safe_page_url(page)
        nickname = first_nonempty_text(page, NICKNAME_SELECTORS)
        book_count = visible_count(page, BOOK_LIST_SELECTORS)
        login_entry = has_login_entry(page)

        if "shelf" in current_url.lower() and nickname:
            return True, f"命中昵称: {nickname}"

        if "shelf" in current_url.lower() and book_count > 0:
            return True, f"命中书架列表: {book_count} 项"

        if nickname:
            return True, f"命中昵称: {nickname}"

        if book_count > 0:
            return True, f"命中书架列表: {book_count} 项"

        last_reason = (
            f"URL={current_url or '<empty>'}, "
            f"nickname={'yes' if nickname else 'no'}, "
            f"book_count={book_count}, "
            f"login_entry={'yes' if login_entry else 'no'}"
        )
        time.sleep(0.4)

    return False, last_reason
