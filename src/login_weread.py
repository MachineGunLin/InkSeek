from __future__ import annotations

import hashlib
import sys
import time

from playwright.sync_api import Error, sync_playwright

from weread_session import DATA_DIR, STATE_PATH, remove_state_file, session_file_usable, verify_session

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

def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


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
        print(f"二维码已保存: {QR_PATH.resolve()}")
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
    raise SystemExit("浏览器已关闭，还没拿到有效 Session，请重新扫码")


def try_session_first(playwright) -> bool:
    if not session_file_usable():
        print("未发现可用 Session，需要扫码登录。")
        return False

    browser = playwright.chromium.launch(headless=True)
    try:
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()
        ok, reason = verify_session(page, timeout_seconds=8)
        if ok:
            print(f"Session 有效，直接进入书架。{reason}")
            return True

        print(f"旧 Session 校验失败：{reason}")
        remove_state_file()
        print("已删除损坏的 weread_state.json，准备重新扫码。")
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
            ensure_login_prompt(page)
        except Error as exc:
            if is_target_closed_error(exc):
                handle_possible_manual_close()
            raise SystemExit(f"登录页打开失败: {type(exc).__name__}: {exc}")

        last_hash = None
        deadline = time.time() + 900
        qr_seen_once = False

        print("二维码已准备。要么直接进书架，要么就继续等，不会假装成功。")

        while time.time() < deadline:
            try:
                ensure_login_prompt(page)

                current_url = safe_page_url(page).lower()
                qr_locator = find_qr_locator(page)
                if qr_locator is not None:
                    qr_seen_once = True
                    last_hash = save_qr_if_changed(qr_locator, last_hash)
                else:
                    print("首页已打开，正在等待二维码出现或刷新...")

                root_url = HOME_URL.rstrip("/")
                current_url_no_slash = current_url.rstrip("/")
                suspected_success = False

                if "shelf" in current_url:
                    suspected_success = True
                elif qr_seen_once and current_url_no_slash == root_url and qr_locator is None:
                    suspected_success = True

                if suspected_success:
                    print("健哥，我看你进去了，我先等 3 秒让子弹飞一会儿，别急...")
                    time.sleep(3)
                    persist_storage_state(context)

                    success_url = safe_page_url(page) or HOME_URL
                    report_success(f"寻墨成功，Session 已持久化。当前 URL: {success_url}")

                time.sleep(0.4)
            except Error as exc:
                if is_target_closed_error(exc):
                    handle_possible_manual_close()
                time.sleep(0.4)

        raise SystemExit("登录超时：一直没验证出真实书架内容")
    finally:
        browser.close()


def main() -> None:
    ensure_data_dir()

    with sync_playwright() as p:
        if try_session_first(p):
            return

        print("Session 无效，老老实实出二维码重新扫码。")
        run_qr_login(p)


if __name__ == "__main__":
    main()
