from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "weread_state.json"
MEMBER_URL = "https://weread.qq.com/member"

AVATAR_SELECTORS = [
    ".wr_avatar",
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

PROFILE_KEYWORDS = ["在读", "想读", "读完", "书架", "笔记", "阅读时长", "我的"]
INVALID_KEYWORDS = ["扫码登录", "微信扫码", "登录"]


def load_state_or_exit() -> None:
    if not STATE_PATH.exists():
        raise SystemExit(f"未找到登录态文件: {STATE_PATH.resolve()}")
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"登录态文件损坏: {exc}") from exc

    cookies = data.get("cookies", []) if isinstance(data, dict) else []
    if not cookies:
        raise SystemExit("登录态文件为空，无法验证，请重新扫码登录")


def has_visible_avatar(page) -> bool:
    for selector in AVATAR_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=600):
                return True
        except Error:
            continue
    return False


def main() -> None:
    load_state_or_exit()

    expected_keyword = os.getenv("WEREAD_USER_KEYWORD", "").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        try:
            page.goto(MEMBER_URL, wait_until="domcontentloaded", timeout=20000)
        except TimeoutError:
            browser.close()
            raise SystemExit("访问 member 页面超时，登录态可能失效")

        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except Error:
            body_text = ""

        current_url = page.url
        invalid = any(k in body_text for k in INVALID_KEYWORDS) or "login" in current_url
        avatar_ok = has_visible_avatar(page)
        keyword_ok = any(k in body_text for k in PROFILE_KEYWORDS)

        if expected_keyword and expected_keyword not in body_text:
            browser.close()
            raise SystemExit(
                f"登录态无效：member 页面未命中 WEREAD_USER_KEYWORD={expected_keyword!r}"
            )

        browser.close()

        if invalid or not avatar_ok or not keyword_ok:
            raise SystemExit("登录态无效：未识别到稳定的用户特征，请重新扫码登录")

        print("寻墨成功，登录态有效")


if __name__ == "__main__":
    main()
