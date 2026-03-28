from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "weread_state.json"
ERROR_SCREENSHOT_PATH = BASE_DIR / "data" / "error_state.png"
SHELF_URL = "https://weread.qq.com/shelf"

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
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"登录态文件损坏: {exc}") from exc

    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    if not cookies:
        raise SystemExit("登录态文件为空，请重新运行 login_weread.py")



def has_avatar(page) -> bool:
    for selector in AVATAR_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=300):
                return True
        except Error:
            continue
    return False



def wait_avatar(page, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if has_avatar(page):
            return True
        time.sleep(0.2)
    return False



def has_login_marker(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except Error:
        return False
    return any(marker in text for marker in LOGIN_MARKERS)



def main() -> None:
    load_state_or_exit()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        try:
            page.goto(SHELF_URL, wait_until="commit", timeout=20000)
            page.wait_for_timeout(1000)
        except (TimeoutError, Error) as exc:
            try:
                page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
                print(f"验证失败截图已保存: {ERROR_SCREENSHOT_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            raise SystemExit(f"访问书架失败: {type(exc).__name__}: {exc}")

        avatar_ok = wait_avatar(page, timeout_seconds=5)
        invalid = has_login_marker(page)

        if not avatar_ok or invalid:
            try:
                page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
                print(f"验证失败截图已保存: {ERROR_SCREENSHOT_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            raise SystemExit("登录态无效，请运行 python3 src/login_weread.py")

        browser.close()
        print("寻墨成功，登录态有效")



if __name__ == "__main__":
    main()
