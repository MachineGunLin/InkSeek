from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_DIR / "weread_state.json"
SHELF_URL = "https://weread.qq.com/web/shelf"


def main() -> None:
    if not STATE_PATH.exists():
        raise SystemExit(f"未找到登录态文件: {STATE_PATH}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        try:
            page.goto(SHELF_URL, wait_until="domcontentloaded", timeout=20000)
        except TimeoutError:
            browser.close()
            raise SystemExit("访问书架超时，登录态可能已失效")

        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Error:
            text = ""

        invalid_markers = ["扫码登录", "微信扫码", "登录"]
        valid_markers = ["书架", "最近阅读", "继续阅读", "我的书架"]

        valid_by_text = any(k in text for k in valid_markers) and not any(
            k in text for k in invalid_markers
        )

        try:
            valid_by_books = page.locator("[class*='shelf'] [class*='book']").count() > 0
        except Error:
            valid_by_books = False

        browser.close()

        if valid_by_text or valid_by_books:
            print("寻墨成功，登录态有效")
            return

        raise SystemExit("登录态无效，请重新执行 src/login_weread.py")


if __name__ == "__main__":
    main()
