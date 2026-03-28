from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "weread_state.json"
ERROR_SCREENSHOT_PATH = DATA_DIR / "upload_error.png"
SHELF_URL = "https://weread.qq.com/shelf"

REAL_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOGIN_MARKERS = ["登录", "立即登录", "扫码登录", "微信扫码", "手机号登录"]
UPLOAD_SUCCESS_MARKERS = ["上传成功", "导入成功", "上传完成", "导入完成"]

UPLOAD_ENTRY_SELECTORS = [
    ".shelf_upload",
    "[class*='shelf_upload']",
    "[class*='import']",
    "text=从电脑传书",
    "text=从电脑导入",
    "text=传书",
    "button:has-text('从电脑传书')",
    "button:has-text('从电脑导入')",
    "button:has-text('传书')",
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


def safe_page_url(page) -> str:
    try:
        return page.url
    except Error:
        return "<unknown-url>"


def safe_page_title(page) -> str:
    try:
        return page.title()
    except Error:
        return "<unknown-title>"


def save_error(page, reason: str, exc: Exception | None = None) -> None:
    print(reason)
    if exc is not None:
        print(f"异常详情: {type(exc).__name__}: {exc}")
    print(f"当前 URL: {safe_page_url(page)}")
    print(f"页面标题: {safe_page_title(page)}")
    try:
        page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
        print(f"错误截图: {ERROR_SCREENSHOT_PATH.resolve()}")
    except Error:
        pass


def load_state_or_exit() -> None:
    if not STATE_PATH.exists():
        raise SystemExit("未找到 Session，请运行 python3 src/login_weread.py")

    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Session 文件损坏，请运行 python3 src/login_weread.py: {exc}")

    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    if not cookies:
        raise SystemExit("Session 为空，请运行 python3 src/login_weread.py")


def resolve_file_or_exit(file_arg: str) -> Path:
    file_path = Path(file_arg).expanduser()
    if not file_path.is_absolute():
        file_path = (BASE_DIR / file_path).resolve()

    if not file_path.exists() or not file_path.is_file():
        raise SystemExit(f"文件不存在: {file_path}")

    if file_path.suffix.lower() not in {".epub", ".pdf"}:
        raise SystemExit("仅支持 .epub 或 .pdf 文件")

    return file_path


def has_login_marker(page) -> bool:
    text = body_text(page)
    return any(marker in text for marker in LOGIN_MARKERS)


def dismiss_popups(page) -> None:
    for selector in POPUP_CLOSE_SELECTORS:
        locator = page.locator(selector)
        try:
            total = locator.count()
        except Error:
            continue

        for idx in range(min(total, 4)):
            try:
                item = locator.nth(idx)
                if item.is_visible(timeout=200):
                    item.click(timeout=400)
            except Error:
                continue

    try:
        page.keyboard.press("Escape")
    except Error:
        pass


def wait_shelf_upload_area(page) -> None:
    try:
        page.goto(SHELF_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector(".shelf_upload", timeout=20000)
        dismiss_popups(page)
        return
    except TimeoutError as exc:
        dismiss_popups(page)
        if has_login_marker(page):
            save_error(page, "Session 已失效，请重新运行 login 脚本", exc)
            raise SystemExit("通行证失效，请运行 python3 src/login_weread.py")

        file_input = find_file_input(page)
        if file_input is not None:
            return

        save_error(page, "书架页加载正常，但上传动作卡住了", exc)
        raise SystemExit("书架页加载正常，但上传动作卡住了")
    except Error as exc:
        save_error(page, "访问书架页失败", exc)
        raise SystemExit("访问书架页失败")


def find_file_input(page):
    for frame in page.frames:
        for selector in UPLOAD_INPUT_SELECTORS:
            try:
                locator = frame.locator(selector).first
                if locator.count() > 0:
                    return locator
            except Error:
                continue
    return None


def click_upload_entry(page) -> bool:
    for selector in UPLOAD_ENTRY_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=300):
                locator.click(timeout=1000)
                return True
        except Error:
            continue
    return False


def wait_upload_input(page, timeout_seconds: int = 10):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)
        file_input = find_file_input(page)
        if file_input is not None:
            return file_input
        time.sleep(0.3)
    return None


def resolve_upload_input(page):
    file_input = wait_upload_input(page, timeout_seconds=2)
    if file_input is not None:
        return file_input

    if not click_upload_entry(page):
        raise SystemExit("书架页加载正常，但没找到“从电脑传书”入口")

    page.wait_for_timeout(2000)
    dismiss_popups(page)

    file_input = wait_upload_input(page, timeout_seconds=10)
    if file_input is None:
        raise SystemExit("书架页加载正常，但上传动作卡住了")

    return file_input


def wait_upload_success(page, timeout_seconds: int = 30) -> bool:
    try:
        page.wait_for_selector("text=上传成功", timeout=timeout_seconds * 1000)
        return True
    except TimeoutError:
        pass
    except Error:
        pass

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)
        text = body_text(page)
        if any(marker in text for marker in UPLOAD_SUCCESS_MARKERS):
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    args = parse_args()
    file_path = resolve_file_or_exit(args.file)
    load_state_or_exit()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH), user_agent=REAL_BROWSER_UA)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.dismiss())

        wait_shelf_upload_area(page)

        try:
            upload_input = resolve_upload_input(page)
        except SystemExit as exc:
            save_error(page, str(exc))
            browser.close()
            raise

        try:
            upload_input.set_input_files(str(file_path))
        except Error as exc:
            save_error(page, f"无法注入上传文件: {file_path}", exc)
            browser.close()
            raise SystemExit("上传失败，请检查上传入口")

        print(f"开始上传: {file_path}")
        success = wait_upload_success(page, timeout_seconds=30)
        if not success:
            save_error(page, "书架页加载正常，但上传动作卡住了")
            browser.close()
            raise SystemExit("书架页加载正常，但上传动作卡住了")

        browser.close()
        print(f"寻墨成功，《{file_path.stem}》已送达微信读书书架。")


if __name__ == "__main__":
    main()
