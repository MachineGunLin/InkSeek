from __future__ import annotations

import hashlib
import sys
import threading
import time

from playwright.sync_api import Error, sync_playwright

from utils import DATA_DIR, STATE_PATH, ensure_runtime_dirs, fail, format_success, launch_browser_context, log_info
from weread_session import remove_state_file, session_file_usable, verify_session

QR_PATH = DATA_DIR / "login_qr.png"
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

LOGIN_ENTRY_SELECTORS = [
    "text=登录",
    "text=立即登录",
    "button:has-text('登录')",
    "a:has-text('登录')",
    "div:has-text('登录')",
]

AVATAR_SELECTORS = [
    ".wr_avatar",
    ".wr_avatar_img",
    "[class*='avatar']",
    "[class*='userPhoto']",
]

SHELF_TEXT_SELECTORS = [
    "text=我的书架",
    "text=书架",
]


def is_target_closed_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "targetclosed" in message or "has been closed" in message or "target page" in message


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
        log_info(f"二维码已保存: {QR_PATH.resolve()}")
    return digest


def safe_page_url(page) -> str:
    try:
        return page.url
    except Error:
        return ""


def ensure_login_prompt(page) -> None:
    click_login_if_possible(page)
    try:
        page.wait_for_timeout(800)
    except Error:
        pass


def report_success(message: str) -> None:
    print(format_success(message))
    sys.exit(0)


def handle_possible_manual_close() -> None:
    if session_file_usable():
        report_success("浏览器已关闭，Session 已保存。")
    fail("浏览器已关闭，尚未拿到有效 Session，请重新扫码。")


def locator_visible(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=200):
                return True
        except Error:
            continue
    return False


def detect_logged_in(page) -> bool:
    current_url = safe_page_url(page).lower().rstrip("/")
    if current_url != HOME_URL.lower().rstrip("/"):
        return False
    if locator_visible(page, AVATAR_SELECTORS):
        return True
    if locator_visible(page, SHELF_TEXT_SELECTORS):
        return True
    return False


def force_save_listener(force_save_event: threading.Event) -> None:
    try:
        sys.stdin.readline()
    except Exception:
        return
    force_save_event.set()


def persist_and_exit(context, page, prompt: str, result: str, wait_seconds: int) -> None:
    log_info(prompt)
    try:
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        context.storage_state(path=str(STATE_PATH))
    except Error as exc:
        fail(f"Session 存盘失败: {type(exc).__name__}: {exc}")

    success_url = safe_page_url(page) or HOME_URL
    report_success(f"Session 已保存。{result} 当前 URL: {success_url}")


def try_session_first(playwright) -> bool:
    if not session_file_usable():
        log_info("未发现可用 Session，需要扫码登录。")
        return False

    browser, context = launch_browser_context(playwright, headless=True, use_storage_state=True)
    try:
        page = context.new_page()
        ok, reason = verify_session(page, timeout_seconds=8)
        if ok:
            print(format_success(f"已复用现有登录态。{reason}"))
            return True

        log_info(f"已有 Session 校验结果异常：{reason}")
        remove_state_file()
        log_info("已删除损坏的 weread_state.json，准备重新扫码。")
        return False
    finally:
        browser.close()


def run_qr_login(playwright) -> None:
    browser, context = launch_browser_context(playwright, headless=False, use_storage_state=False)
    try:
        page = context.new_page()

        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            ensure_login_prompt(page)
        except Error as exc:
            if is_target_closed_error(exc):
                handle_possible_manual_close()
            fail(f"登录页打开失败: {type(exc).__name__}: {exc}")

        force_save_event = threading.Event()
        threading.Thread(target=force_save_listener, args=(force_save_event,), daemon=True).start()

        last_hash = None
        deadline = time.time() + 900

        log_info("二维码已准备，等待扫码登录。")
        log_info("如果页面已完成登录但脚本暂未响应，请在终端按回车，脚本会立即保存 Session 并退出。")

        while time.time() < deadline:
            try:
                if force_save_event.is_set():
                    persist_and_exit(context, page, "收到人工确认，正在保存 Session。", "已根据人工确认完成 Session 保存。", wait_seconds=0)

                if detect_logged_in(page):
                    persist_and_exit(context, page, "检测到可用登录态，正在保存 Session。", "已自动检测到书架或头像。", wait_seconds=5)

                ensure_login_prompt(page)

                qr_locator = find_qr_locator(page)
                if qr_locator is not None:
                    last_hash = save_qr_if_changed(qr_locator, last_hash)

                time.sleep(0.4)
            except Error as exc:
                if is_target_closed_error(exc):
                    handle_possible_manual_close()
                time.sleep(0.4)

        fail("登录超时：一直未拿到可落盘的登录态。")
    finally:
        browser.close()


def run_login() -> None:
    ensure_runtime_dirs()

    with sync_playwright() as playwright:
        if try_session_first(playwright):
            return

        log_info("现有 Session 不可用，开始生成新的登录二维码。")
        run_qr_login(playwright)


def main() -> None:
    run_login()


if __name__ == "__main__":
    main()
