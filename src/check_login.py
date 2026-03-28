from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "weread_state.json"
ERROR_SCREENSHOT_PATH = BASE_DIR / "data" / "error_state.png"
HOME_URL = "https://weread.qq.com/"

AVATAR_SELECTORS = [
    ".wr_avatar",
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

LOGIN_MARKERS = ["登录", "立即登录", "扫码登录", "微信扫码"]


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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20000)
        except TimeoutError:
            try:
                page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
                print(f"验证失败截图已保存: {ERROR_SCREENSHOT_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            raise SystemExit("访问首页超时，登录态可能失效")

        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except Error:
            body_text = ""

        current_url = page.url
        avatar_ok = has_visible_avatar(page)
        home_url_ok = current_url.rstrip("/") == HOME_URL.rstrip("/")
        has_login_marker = any(k in body_text for k in LOGIN_MARKERS)
        home_without_login = home_url_ok and not has_login_marker

        if not (avatar_ok or home_without_login):
            try:
                page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
                print(f"验证失败截图已保存: {ERROR_SCREENSHOT_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            raise SystemExit(
                f"登录态无效：URL={current_url}，avatar_ok={avatar_ok}，home_without_login={home_without_login}"
            )

        browser.close()
        print("寻墨成功，登录态有效")


if __name__ == "__main__":
    main()
