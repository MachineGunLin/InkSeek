from __future__ import annotations

import hashlib
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
QR_PATH = DATA_DIR / "login_qr.png"
STATE_PATH = DATA_DIR / "weread_state.json"
HOME_URL = "https://weread.qq.com/"
SHELF_URL = "https://weread.qq.com/web/shelf"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def click_login_if_possible(page) -> None:
    selectors = [
        "text=登录",
        "text=立即登录",
        "button:has-text('登录')",
        "a:has-text('登录')",
        "div:has-text('登录')",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=500):
                locator.click(timeout=800)
                return
        except Error:
            continue


def find_qr_locator(page):
    selectors = [
        "img[src*='qrcode']",
        "img[src*='qr']",
        "img[alt*='二维码']",
        "canvas",
        "[class*='qrcode'] img",
        "[class*='qr'] img",
    ]

    for frame in page.frames:
        for selector in selectors:
            locator = frame.locator(selector).first
            try:
                if locator.is_visible(timeout=300):
                    box = locator.bounding_box()
                    if box and box["width"] >= 100 and box["height"] >= 100:
                        return locator
            except Error:
                continue
    return None


def save_qr_if_changed(locator, last_digest: str | None) -> str | None:
    try:
        image_bytes = locator.screenshot(type="png")
    except Error:
        return last_digest

    digest = hashlib.md5(image_bytes).hexdigest()
    if digest != last_digest:
        QR_PATH.write_bytes(image_bytes)
        print(f"二维码已更新: {QR_PATH}")
    return digest


def page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=1500)
    except Error:
        return ""


def is_logged_in(page) -> bool:
    try:
        page.goto(SHELF_URL, wait_until="domcontentloaded", timeout=15000)
    except TimeoutError:
        return False

    text = page_text(page)
    if any(k in text for k in ["扫码登录", "微信扫码", "手机号登录"]):
        return False

    if any(k in text for k in ["书架", "最近阅读", "继续阅读", "我的书架"]):
        return True

    try:
        has_books = page.locator("[class*='shelf'] [class*='book']").count() > 0
    except Error:
        has_books = False

    if has_books:
        return True

    cookie_names = {cookie["name"] for cookie in page.context.cookies()}
    return any(name.startswith("wr_") for name in cookie_names)


def main() -> None:
    ensure_dirs()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")

        print("已打开微信读书首页，准备扫码登录。")
        click_login_if_possible(page)

        timeout_seconds = 300
        deadline = time.time() + timeout_seconds
        last_qr_digest = None
        last_login_check = 0.0

        while time.time() < deadline:
            click_login_if_possible(page)

            qr_locator = find_qr_locator(page)
            if qr_locator is not None:
                last_qr_digest = save_qr_if_changed(qr_locator, last_qr_digest)

            now = time.time()
            if now - last_login_check >= 3:
                last_login_check = now
                if is_logged_in(page):
                    context.storage_state(path=str(STATE_PATH))
                    print(f"登录成功，已保存会话: {STATE_PATH}")
                    browser.close()
                    return
                page.goto(HOME_URL, wait_until="domcontentloaded")

            time.sleep(1)

        browser.close()
        raise SystemExit("登录超时：未在 300 秒内检测到成功登录")


if __name__ == "__main__":
    main()
