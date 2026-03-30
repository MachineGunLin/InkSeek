from __future__ import annotations

from playwright.sync_api import Error, sync_playwright

from utils import BASE_DIR, ensure_runtime_dirs, fail, log_failure, log_success, launch_browser_context, save_masked_page_screenshot
from weread_session import session_file_usable, verify_session

FINAL_ERROR_PATH = BASE_DIR / "data" / "final_error.png"


def run_check() -> None:
    ensure_runtime_dirs()

    if not session_file_usable():
        fail("登录态文件不可用，请运行 python3 main.py login")

    with sync_playwright() as playwright:
        browser, context = launch_browser_context(playwright, headless=True, use_storage_state=True)
        page = context.new_page()

        ok, reason = verify_session(page, timeout_seconds=10)
        if not ok:
            try:
                save_masked_page_screenshot(page, FINAL_ERROR_PATH, full_page=True)
                log_failure(f"失败截图已保存: {FINAL_ERROR_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            fail(f"登录态校验失败，请重新运行 python3 main.py login。{reason}")

        browser.close()
        log_success(f"登录态有效。{reason}")


def main() -> None:
    run_check()


if __name__ == "__main__":
    main()
