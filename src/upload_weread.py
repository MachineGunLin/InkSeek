from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "weread_state.json"
HOME_URL = "https://weread.qq.com/"

LOGIN_MARKERS = ["登录", "立即登录", "扫码登录", "微信扫码", "手机号登录"]
UPLOAD_SUCCESS_MARKERS = [
    "上传成功",
    "导入成功",
    "上传完成",
    "导入完成",
    "已加入书架",
    "已添加到书架",
]
UPLOAD_PROGRESS_MARKERS = ["上传中", "处理中", "导入中", "解析中"]

AVATAR_SELECTORS = [
    ".wr_avatar",
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

UPLOAD_ENTRY_SELECTORS = [
    "text=传书",
    "text=从电脑导入",
    "text=导入书籍",
    "text=上传",
    "button:has-text('传书')",
    "button:has-text('从电脑导入')",
    "a:has-text('传书')",
]

UPLOAD_INPUT_SELECTORS = [
    "input[type='file'][accept*='epub']",
    "input[type='file'][accept*='pdf']",
    "input[type='file']",
]

POPUP_CLOSE_SELECTORS = [
    "button:has-text('知道了')",
    "button:has-text('我知道了')",
    "button:has-text('关闭')",
    "button:has-text('取消')",
    "[class*='close']",
    "[aria-label*='关闭']",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="上传本地 Epub/PDF 到微信读书私有文档")
    parser.add_argument("file", help="本地文件路径，例如 data/test.epub")
    return parser.parse_args()


def body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Error:
        return ""


def has_avatar(page) -> bool:
    for selector in AVATAR_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=500):
                return True
        except Error:
            continue
    return False


def is_session_expired(page) -> bool:
    text = body_text(page)
    has_login_marker = any(marker in text for marker in LOGIN_MARKERS)
    on_login_url = "login" in page.url
    return (has_login_marker or on_login_url) and not has_avatar(page)


def dismiss_popups(page) -> None:
    for selector in POPUP_CLOSE_SELECTORS:
        locator = page.locator(selector)
        try:
            total = locator.count()
        except Error:
            continue

        for idx in range(min(total, 3)):
            item = locator.nth(idx)
            try:
                if item.is_visible(timeout=200):
                    item.click(timeout=400)
            except Error:
                continue

    try:
        page.keyboard.press("Escape")
    except Error:
        pass


def click_upload_entry(page) -> bool:
    for selector in UPLOAD_ENTRY_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=400):
                locator.click(timeout=1000)
                return True
        except Error:
            continue
    return False


def find_file_input(page):
    for frame in page.frames:
        for selector in UPLOAD_INPUT_SELECTORS:
            locator = frame.locator(selector).first
            try:
                if locator.count() > 0:
                    return locator
            except Error:
                continue
    return None


def wait_upload_input(page, timeout_seconds: int = 30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)

        file_input = find_file_input(page)
        if file_input is not None:
            return file_input

        click_upload_entry(page)
        time.sleep(0.5)

    raise TimeoutError("未找到传书文件输入框")


def wait_upload_finished(page, book_name: str, timeout_seconds: int = 240) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)

        if is_session_expired(page):
            raise SystemExit("通行证失效，请重新运行 login 脚本")

        text = body_text(page)
        if any(marker in text for marker in UPLOAD_SUCCESS_MARKERS):
            return True

        if book_name and book_name in text:
            in_progress = any(marker in text for marker in UPLOAD_PROGRESS_MARKERS)
            if not in_progress:
                return True

        time.sleep(1)

    return False


def load_state_or_exit() -> None:
    if not STATE_PATH.exists():
        raise SystemExit("通行证失效，请重新运行 login 脚本")

    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit("通行证失效，请重新运行 login 脚本")

    cookies = data.get("cookies", []) if isinstance(data, dict) else []
    if not cookies:
        raise SystemExit("通行证失效，请重新运行 login 脚本")


def resolve_file_or_exit(file_arg: str) -> Path:
    file_path = Path(file_arg).expanduser()
    if not file_path.is_absolute():
        file_path = (BASE_DIR / file_path).resolve()

    if not file_path.exists() or not file_path.is_file():
        raise SystemExit(f"文件不存在: {file_path}")

    if file_path.suffix.lower() not in {".epub", ".pdf"}:
        raise SystemExit("仅支持 .epub 或 .pdf 文件")

    return file_path


def main() -> None:
    args = parse_args()
    file_path = resolve_file_or_exit(args.file)
    load_state_or_exit()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.dismiss())

        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except TimeoutError:
            browser.close()
            raise SystemExit("无法访问微信读书首页")

        dismiss_popups(page)
        if is_session_expired(page):
            browser.close()
            raise SystemExit("通行证失效，请重新运行 login 脚本")

        try:
            upload_input = wait_upload_input(page, timeout_seconds=30)
        except TimeoutError:
            browser.close()
            raise SystemExit("未找到传书入口，请检查页面是否改版")

        try:
            upload_input.set_input_files(str(file_path))
        except Error as exc:
            browser.close()
            raise SystemExit(f"上传失败，无法选择文件: {exc}")

        print(f"开始上传: {file_path}")
        finished = wait_upload_finished(page, file_path.stem, timeout_seconds=240)
        browser.close()

        if not finished:
            raise SystemExit("上传超时，请重试")

        print(f"《{file_path.stem}》已成功送达书架")


if __name__ == "__main__":
    main()
