from __future__ import annotations

import hashlib
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
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def click_login(page) -> None:
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
                locator.click(timeout=1000)
                return
        except Error:
            continue


def find_qr_locator(page):
    for frame in page.frames:
        for selector in QR_SELECTORS:
            locator = frame.locator(selector).first
            try:
                if not locator.is_visible(timeout=300):
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
        click_login(page)
        locator = find_qr_locator(page)
        if locator is not None:
            return locator
        time.sleep(0.4)
    raise TimeoutError("未找到微信读书登录二维码元素")


def save_qr(locator, last_hash: str | None) -> str:
    image_bytes = locator.screenshot(type="png")
    digest = hashlib.md5(image_bytes).hexdigest()
    if digest != last_hash:
        QR_PATH.write_bytes(image_bytes)
        print(f"二维码已保存: {QR_PATH.resolve()}")
    return digest


def has_login_prompt(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=1500)
    except Error:
        text = ""
    return any(k in text for k in ["扫码登录", "微信扫码", "手机号登录", "登录后"])


def has_avatar(page) -> bool:
    for selector in AVATAR_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=500):
                return True
        except Error:
            continue
    return False


def wait_login_transition(page, timeout_seconds: int = 20) -> None:
    start_url = page.url
    try:
        page.wait_for_url(lambda u: u != start_url, timeout=timeout_seconds * 1000)
    except TimeoutError:
        pass
    except Error:
        pass


def confirm_logged_in(page, timeout_seconds: int = 15) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if has_avatar(page) and not has_login_prompt(page):
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    ensure_data_dir()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        click_login(page)

        last_hash = None
        for _ in range(20):
            qr_locator = wait_qr_locator(page)
            last_hash = save_qr(qr_locator, last_hash)

            input("二维码已生成在 data/login_qr.png，请扫码登录，完成后在终端按回车继续...")

            wait_login_transition(page, timeout_seconds=20)
            if confirm_logged_in(page, timeout_seconds=15):
                context.storage_state(path=str(STATE_PATH))
                print(f"登录成功，登录态已保存: {STATE_PATH.resolve()}")
                browser.close()
                return

            print("尚未检测到登录成功，二维码可能已过期，正在刷新并重新截图...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            click_login(page)

        browser.close()
        raise SystemExit("登录失败：多次尝试后仍未检测到有效登录态")


if __name__ == "__main__":
    main()
