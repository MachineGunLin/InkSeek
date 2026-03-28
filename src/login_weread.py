from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
QR_PATH = DATA_DIR / "login_qr.png"
STATE_PATH = DATA_DIR / "weread_state.json"
HOME_URL = "https://weread.qq.com/"
SHELF_URL = "https://weread.qq.com/shelf"

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
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

LOGIN_TEXT_MARKERS = ["登录", "扫码登录", "微信扫码", "手机号登录"]



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



def has_login_prompt(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=1500)
    except Error:
        return False
    return any(marker in text for marker in LOGIN_TEXT_MARKERS)



def try_session_first(playwright) -> bool:
    if not session_file_usable():
        print("未发现可用 Session，需要扫码登录。")
        return False

    browser = playwright.chromium.launch(headless=True)
    try:
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        try:
            page.goto(SHELF_URL, wait_until="commit", timeout=20000)
            page.wait_for_timeout(1000)
        except (TimeoutError, Error):
            print("已有 Session 访问书架失败，需要扫码登录。")
            return False

        if wait_avatar(page, timeout_seconds=5) and not has_login_prompt(page):
            print("Session 有效，跳过扫码")
            return True

        print("Session 已失效，需要扫码登录。")
        return False
    finally:
        browser.close()



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
            if locator.is_visible(timeout=400):
                locator.click(timeout=1000)
                return
        except Error:
            continue



def find_qr_locator(page):
    for frame in page.frames:
        for selector in QR_SELECTORS:
            locator = frame.locator(selector).first
            try:
                if not locator.is_visible(timeout=200):
                    continue
                box = locator.bounding_box()
                if box and box["width"] >= 120 and box["height"] >= 120:
                    return locator
            except Error:
                continue
    return None



def wait_qr_locator(page, timeout_seconds: int = 30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        click_login_if_possible(page)
        locator = find_qr_locator(page)
        if locator is not None:
            return locator
        time.sleep(0.4)
    raise TimeoutError("未找到二维码元素")



def save_qr_if_changed(locator, last_hash: str | None) -> str:
    image_bytes = locator.screenshot(type="png")
    digest = hashlib.md5(image_bytes).hexdigest()
    if digest != last_hash:
        QR_PATH.write_bytes(image_bytes)
        print(f"二维码已保存: {QR_PATH.resolve()}")
    return digest



def ensure_shelf_ready(page) -> bool:
    try:
        page.goto(SHELF_URL, wait_until="commit", timeout=25000)
        page.wait_for_timeout(1500)
    except (TimeoutError, Error):
        return False
    return wait_avatar(page, timeout_seconds=12) and not has_login_prompt(page)



def persist_storage_state(context) -> None:
    temp_state = STATE_PATH.with_suffix(".tmp.json")
    context.storage_state(path=str(temp_state))
    temp_state.replace(STATE_PATH)



def run_qr_login(playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context()
        page = context.new_page()

        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        click_login_if_possible(page)

        last_hash = None
        for attempt in range(1, 21):
            qr_locator = wait_qr_locator(page)
            last_hash = save_qr_if_changed(qr_locator, last_hash)

            print(f"需要扫码登录（第 {attempt}/20 次）")
            input("二维码已生成在 data/login_qr.png，请扫码后按回车继续...")

            if ensure_shelf_ready(page):
                page.wait_for_timeout(2000)
                persist_storage_state(context)
                print(f"扫码登录成功，Session 已更新: {STATE_PATH.resolve()}")
                return

            print("尚未确认登录成功，二维码可能过期，继续重试。")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            click_login_if_possible(page)

        raise SystemExit("登录失败：多次扫码后仍未进入书架")
    finally:
        browser.close()



def main() -> None:
    ensure_data_dir()

    with sync_playwright() as p:
        if try_session_first(p):
            return

        print("Session 无效，进入扫码流程。")
        run_qr_login(p)



if __name__ == "__main__":
    main()
