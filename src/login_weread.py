from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
QR_PATH = DATA_DIR / "login_qr.png"
STATE_PATH = DATA_DIR / "weread_state.json"
HOME_URL = "https://weread.qq.com/"

QR_SELECTORS = [
    ".wr_login_canvas",
    "canvas.wr_login_canvas",
    "[class*='login'] canvas",
    "[class*='qrcode'] canvas",
    "[class*='qr'] canvas",
    "img[src*='qrcode']",
    "img[src*='qr']",
]

AVATAR_SELECTORS = [
    ".wr_avatar",
    ".wr_avatar_img",
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

UPLOAD_BUTTON_SELECTORS = [
    ".shelf_upload",
    "text=传书",
    "text=从电脑导入",
    "button:has-text('传书')",
    "button:has-text('从电脑导入')",
    "a:has-text('传书')",
]

LOGIN_ENTRY_SELECTORS = [
    "text=登录",
    "text=立即登录",
    "button:has-text('登录')",
    "a:has-text('登录')",
    "div:has-text('登录')",
]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def session_file_usable() -> bool:
    if not STATE_PATH.exists():
        return False
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    return bool(cookies)


def is_target_closed_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "targetclosed" in message or "has been closed" in message or "target page" in message


def safe_page_url(page) -> str:
    try:
        return page.url
    except Error:
        return ""


def any_selector_visible(page, selectors: list[str], timeout_ms: int = 200) -> bool:
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=timeout_ms):
                return True
        except Error:
            continue
    return False


def click_login_if_possible(page) -> None:
    for selector in LOGIN_ENTRY_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=200):
                locator.click(timeout=800)
                return
        except Error:
            continue


def find_qr_locator(page):
    for frame in page.frames:
        for selector in QR_SELECTORS:
            try:
                locator = frame.locator(selector).first
                if not locator.is_visible(timeout=200):
                    continue
                box = locator.bounding_box()
                if box and box["width"] >= 120 and box["height"] >= 120:
                    return locator
            except Error:
                continue
    return None


def save_qr_if_changed(locator, last_hash: str | None) -> str:
    image_bytes = locator.screenshot(type="png")
    digest = hashlib.md5(image_bytes).hexdigest()
    if digest != last_hash:
        QR_PATH.write_bytes(image_bytes)
        print(f"二维码已保存: {QR_PATH.resolve()}")
    return digest


def success_signal(page) -> tuple[bool, str]:
    current_url = safe_page_url(page)
    if any_selector_visible(page, AVATAR_SELECTORS):
        return True, "检测到头像"
    if "shelf" in current_url.lower():
        return True, f"检测到书架 URL: {current_url}"
    if any_selector_visible(page, UPLOAD_BUTTON_SELECTORS):
        return True, "检测到传书按钮"
    return False, ""


def persist_storage_state(context) -> None:
    temp_state = STATE_PATH.with_suffix(".tmp.json")
    context.storage_state(path=str(temp_state))
    temp_state.replace(STATE_PATH)


def report_success(message: str) -> None:
    print(message)
    sys.exit(0)


def handle_possible_manual_close() -> None:
    if session_file_usable():
        report_success("检测到浏览器已关闭，Session 已在此前成功入库")
    raise SystemExit("浏览器已关闭，且未拿到有效 Session")


def try_session_first(playwright) -> bool:
    if not session_file_usable():
        print("未发现可用 Session，需要扫码登录。")
        return False

    browser = playwright.chromium.launch(headless=True)
    try:
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()
        try:
            page.goto(HOME_URL, wait_until="commit", timeout=15000)
            page.wait_for_timeout(1000)
        except Error:
            print("已有 Session 启动失败，需要扫码登录。")
            return False

        ok, _ = success_signal(page)
        if ok:
            print("Session 有效，跳过扫码")
            return True

        print("Session 已失效，需要扫码登录。")
        return False
    finally:
        browser.close()


def run_qr_login(playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            click_login_if_possible(page)
        except Error as exc:
            if is_target_closed_error(exc):
                handle_possible_manual_close()
            raise

        last_hash = None
        qr_refresh_deadline = time.time() + 600

        print("二维码已准备，脚本会主动探测头像、书架 URL、传书按钮。")
        print("一旦命中任一成功信号，立即持久化 Session。")

        while time.time() < qr_refresh_deadline:
            try:
                ok, reason = success_signal(page)
                if ok:
                    persist_storage_state(context)
                    report_success(f"寻墨成功，Session 已持久化 ({reason})")

                click_login_if_possible(page)
                qr_locator = find_qr_locator(page)
                if qr_locator is not None:
                    last_hash = save_qr_if_changed(qr_locator, last_hash)
                else:
                    print("正在等待二维码出现或刷新...")

                time.sleep(0.3)
            except Error as exc:
                if is_target_closed_error(exc):
                    handle_possible_manual_close()
                time.sleep(0.3)

        raise SystemExit("登录超时：长时间未探测到有效登录信号")
    finally:
        browser.close()


def main() -> None:
    ensure_data_dir()

    with sync_playwright() as p:
        if try_session_first(p):
            return

        print("Session 无效，进入极简稳健扫码流程。")
        run_qr_login(p)


if __name__ == "__main__":
    main()
