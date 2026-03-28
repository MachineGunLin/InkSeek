from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

from weread_session import HOME_URL, verify_session

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "weread_state.json"
DEBUG_SCREENSHOT_PATH = DATA_DIR / "upload_fail_debug.png"

REAL_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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
UPLOAD_DIALOG_MARKERS = ["从电脑导入", "导入书籍", "导入本地图书", "拖拽", "上传"]

AVATAR_SELECTORS = [
    ".wr_avatar",
    "img.wr_avatar",
    "[class*='avatar'] img",
    "[class*='avatar']",
]

UPLOAD_ENTRY_SELECTORS = [
    ".shelf_upload",
    "text=传书",
    "text=从电脑导入",
    "text=导入书籍",
    "button:has-text('传书')",
    "button:has-text('从电脑导入')",
    "a:has-text('传书')",
]

UPLOAD_INPUT_SELECTORS = [
    "input[type='file'][accept*='epub']",
    "input[type='file'][accept*='pdf']",
    "input[type='file']",
]

UPLOAD_DIALOG_SELECTORS = [
    "[role='dialog']",
    "[class*='dialog']",
    "[class*='upload']",
    "[class*='import']",
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



def save_debug(page, reason: str, exc: Exception | None = None) -> None:
    print(f"[上传失败] {reason}")
    if exc is not None:
        print(f"[上传失败] 异常详情: {type(exc).__name__}: {exc}")
    print(f"[上传失败] 当前 URL: {safe_page_url(page)}")
    print(f"[上传失败] 页面标题: {safe_page_title(page)}")
    try:
        page.screenshot(path=str(DEBUG_SCREENSHOT_PATH), full_page=True)
        print(f"[上传失败] 调试截图: {DEBUG_SCREENSHOT_PATH.resolve()}")
    except Error as screenshot_err:
        print(f"[上传失败] 截图失败: {screenshot_err}")



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



def ensure_session_alive_or_exit(page) -> None:
    try:
        page.goto(HOME_URL, wait_until="commit", timeout=20000)
        page.wait_for_timeout(1000)
        dismiss_popups(page)
    except (TimeoutError, Error) as exc:
        save_debug(page, "访问首页失败", exc)
        raise SystemExit("无法访问微信读书首页，请先运行 python3 src/login_weread.py")

    ok, reason = verify_session(page, timeout_seconds=8)
    if ok:
        print(f"Session 有效，直接进入上传流程。{reason}")
        return

    save_debug(page, "Session 无效或已过期")
    raise SystemExit("通行证失效，请运行 python3 src/login_weread.py")



def click_upload_entry(page) -> bool:
    for selector in UPLOAD_ENTRY_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=400):
                locator.click(timeout=1200)
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



def upload_dialog_open(page) -> bool:
    text = body_text(page)
    if any(marker in text for marker in UPLOAD_DIALOG_MARKERS):
        return True

    for selector in UPLOAD_DIALOG_SELECTORS:
        locator = page.locator(selector)
        try:
            total = locator.count()
        except Error:
            continue

        for idx in range(min(total, 5)):
            item = locator.nth(idx)
            try:
                if item.is_visible(timeout=200):
                    return True
            except Error:
                continue

    return False



def wait_upload_input(page, timeout_seconds: int = 40):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)

        file_input = find_file_input(page)
        if file_input is not None:
            return file_input

        if click_upload_entry(page):
            page.wait_for_timeout(2000)
            if upload_dialog_open(page):
                file_input = find_file_input(page)
                if file_input is not None:
                    return file_input

        time.sleep(0.4)

    raise TimeoutError("未找到传书输入框")



def wait_upload_finished(page, book_name: str, timeout_seconds: int = 300) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)

        if has_login_marker(page) and not has_avatar(page):
            raise SystemExit("通行证失效，请运行 python3 src/login_weread.py")

        text = body_text(page)
        if any(marker in text for marker in UPLOAD_SUCCESS_MARKERS):
            return True

        if book_name and book_name in text:
            in_progress = any(marker in text for marker in UPLOAD_PROGRESS_MARKERS)
            if not in_progress:
                return True

        time.sleep(1)

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

        ensure_session_alive_or_exit(page)

        try:
            upload_input = wait_upload_input(page, timeout_seconds=40)
        except TimeoutError as exc:
            save_debug(page, "未找到传书入口或文件输入框", exc)
            browser.close()
            raise SystemExit("传书入口定位失败，请查看 data/upload_fail_debug.png")

        try:
            upload_input.set_input_files(str(file_path))
        except Error as exc:
            save_debug(page, f"无法注入上传文件: {file_path}", exc)
            browser.close()
            raise SystemExit("上传失败，请查看 data/upload_fail_debug.png")

        print(f"开始上传: {file_path}")
        try:
            finished = wait_upload_finished(page, file_path.stem, timeout_seconds=300)
        except SystemExit:
            save_debug(page, "上传过程中登录态失效")
            browser.close()
            raise

        if not finished:
            save_debug(page, "上传超时，300 秒未完成")
            browser.close()
            raise SystemExit("上传超时，请查看 data/upload_fail_debug.png")

        print(f"《{file_path.stem}》已成功送达书架")
        browser.close()



if __name__ == "__main__":
    main()
