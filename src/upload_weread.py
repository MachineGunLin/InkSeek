from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

from utils import (
    BASE_DIR,
    archive_file_if_needed,
    ensure_runtime_dirs,
    fail,
    format_failure,
    format_success,
    launch_browser_context,
)

ERROR_SCREENSHOT_PATH = BASE_DIR / "data" / "upload_error.png"
HOME_URL = "https://weread.qq.com/"
UPLOAD_URL = "https://weread.qq.com/web/upload"

REAL_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOGIN_MARKERS = ["登录", "立即登录", "扫码登录", "微信扫码", "手机号登录"]
UPLOAD_SUCCESS_MARKERS = ["上传成功", "导入成功", "导入完成", "立即阅读"]
UPLOAD_PAGE_MARKERS = ["导入书籍", "拖拽文件到此处", "选择文件", "传书到手机"]

UPLOAD_ENTRY_SELECTORS = [
    "a[href*='/web/upload']",
    "text=传书到手机",
    "text=导入书籍",
    "text=导入文档",
    "text=从电脑传书",
    "text=从电脑导入",
    "text=传书",
    "button:has-text('选择文件')",
    "[class*='upload']",
    "[class*='import']",
]

SHELF_ENTRY_SELECTORS = [
    "text=我的书架",
    "a[href*='/web/shelf']",
    "a[href*='/shelf']",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="上传本地 Epub/PDF 到微信读书私有文档")
    parser.add_argument("file", help="本地文件路径，例如 data/test.epub")
    return parser


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
    message = reason if reason.startswith("寻墨中断：") else format_failure(reason)
    print(message)
    if exc is not None:
        print(f"异常详情: {type(exc).__name__}: {exc}")
    print(f"当前 URL: {safe_page_url(page)}")
    print(f"页面标题: {safe_page_title(page)}")
    try:
        page.screenshot(path=str(ERROR_SCREENSHOT_PATH), full_page=True)
        print(format_failure(f"错误截图已保存: {ERROR_SCREENSHOT_PATH.resolve()}"))
    except Error:
        pass


def resolve_file_or_exit(file_arg: str) -> Path:
    file_path = Path(file_arg).expanduser()
    if not file_path.is_absolute():
        file_path = (BASE_DIR / file_path).resolve()

    if not file_path.exists() or not file_path.is_file():
        fail(f"文件不存在: {file_path}")

    if file_path.suffix.lower() not in {".epub", ".pdf"}:
        fail("仅支持 .epub 或 .pdf 文件")

    return file_path


def has_login_marker(page) -> bool:
    text = body_text(page)
    return any(marker in text for marker in LOGIN_MARKERS)


def has_upload_page_marker(page) -> bool:
    text = body_text(page)
    return any(marker in text for marker in UPLOAD_PAGE_MARKERS)


def page_is_404(page) -> bool:
    title = safe_page_title(page)
    text = body_text(page)
    return "404" in title or "404 Not Found" in text


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


def click_first_visible(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=300):
                locator.click(timeout=1000)
                return True
        except Error:
            continue
    return False


def click_upload_entry(page) -> bool:
    return click_first_visible(page, UPLOAD_ENTRY_SELECTORS)


def click_shelf_entry(page) -> bool:
    return click_first_visible(page, SHELF_ENTRY_SELECTORS)


def wait_upload_input(page, timeout_seconds: int = 10):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)
        file_input = find_file_input(page)
        if file_input is not None:
            return file_input
        if has_login_marker(page):
            fail("登录态已失效，请重新运行 python3 main.py login")
        time.sleep(0.3)
    return None


def wait_upload_page_ready(page, timeout_seconds: int = 10) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)
        if page_is_404(page):
            return
        if has_login_marker(page):
            fail("登录态已失效，请重新运行 python3 main.py login")
        if find_file_input(page) is not None or has_upload_page_marker(page):
            return
        time.sleep(0.3)


def open_homepage(page) -> None:
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        dismiss_popups(page)
    except Error as exc:
        save_error(page, "访问首页失败", exc)
        fail("访问首页失败")

    if has_login_marker(page):
        fail("登录态已失效，请重新运行 python3 main.py login")


def goto_upload_page(page) -> None:
    try:
        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        wait_upload_page_ready(page, timeout_seconds=8)
    except Error as exc:
        save_error(page, "访问上传页失败", exc)
        fail("访问上传页失败")

    if page_is_404(page):
        save_error(page, "上传页返回 404")
        fail("上传页返回 404，未找到可用上传入口")


def open_upload_page(page) -> None:
    open_homepage(page)

    if wait_upload_input(page, timeout_seconds=2) is not None:
        return

    if click_upload_entry(page):
        page.wait_for_timeout(1500)
        wait_upload_page_ready(page, timeout_seconds=8)
        if page_is_404(page):
            print("检测到无效页面，正在切换到真实上传页。")
        elif find_file_input(page) is not None:
            return

    if click_shelf_entry(page):
        page.wait_for_timeout(1500)
        dismiss_popups(page)
        if page_is_404(page):
            print("检测到 /shelf 页面返回 404，正在切换到真实上传页。")
        else:
            if wait_upload_input(page, timeout_seconds=3) is not None:
                return
            if click_upload_entry(page):
                page.wait_for_timeout(1500)
                wait_upload_page_ready(page, timeout_seconds=8)
                if find_file_input(page) is not None:
                    return

    goto_upload_page(page)


def resolve_upload_input(page):
    file_input = wait_upload_input(page, timeout_seconds=2)
    if file_input is not None:
        return file_input

    if click_upload_entry(page):
        page.wait_for_timeout(1500)
        dismiss_popups(page)
        file_input = wait_upload_input(page, timeout_seconds=10)
        if file_input is not None:
            return file_input

    fail("已进入上传流程，但未找到可用的文件选择控件")


def wait_upload_success(page, file_name: str, timeout_seconds: int = 30) -> bool:
    try:
        page.wait_for_selector("text=导入完成", timeout=timeout_seconds * 1000)
        return True
    except TimeoutError:
        pass
    except Error:
        pass

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        dismiss_popups(page)
        text = body_text(page)
        if has_login_marker(page):
            fail("登录态已失效，请重新运行 python3 main.py login")
        if any(marker in text for marker in UPLOAD_SUCCESS_MARKERS):
            return True
        if file_name in text and "立即阅读" in text:
            return True
        time.sleep(0.5)
    return False


def run_upload(file_arg: str) -> None:
    ensure_runtime_dirs()
    file_path = resolve_file_or_exit(file_arg)

    with sync_playwright() as playwright:
        browser, context = launch_browser_context(
            playwright,
            headless=True,
            use_storage_state=True,
            user_agent=REAL_BROWSER_UA,
        )
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.dismiss())

        open_upload_page(page)

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
            fail("上传失败，请检查上传入口")

        print(f"开始上传文件: {file_path}")
        success = wait_upload_success(page, file_path.name, timeout_seconds=30)
        if not success:
            save_error(page, "上传页已打开，但未等到导入完成信号")
            browser.close()
            fail("上传页已打开，但未等到导入完成信号")

        browser.close()

    archived_path = archive_file_if_needed(file_path)
    if archived_path is not None:
        print(format_success(f"文件上传指令已发出，请在微信读书中确认。文件已归档至 {archived_path.relative_to(BASE_DIR)}"))
        return

    print(format_success("文件上传指令已发出，请在微信读书中确认。"))


def main() -> None:
    args = build_parser().parse_args()
    run_upload(args.file)


if __name__ == "__main__":
    main()
