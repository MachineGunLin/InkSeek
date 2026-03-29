from __future__ import annotations

from playwright.sync_api import Error, sync_playwright

from weread_session import BASE_DIR, STATE_PATH, session_file_usable, verify_session

FINAL_ERROR_PATH = BASE_DIR / "data" / "final_error.png"


def main() -> None:
    if not session_file_usable():
        raise SystemExit("登录态文件不可用，请运行 python3 src/login_weread.py")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()

        ok, reason = verify_session(page, timeout_seconds=10)
        if not ok:
            try:
                page.screenshot(path=str(FINAL_ERROR_PATH), full_page=True)
                print(f"失败截图已保存: {FINAL_ERROR_PATH.resolve()}")
            except Error:
                pass
            browser.close()
            raise SystemExit(f"登录态校验失败，请重新运行 python3 src/login_weread.py。{reason}")

        browser.close()
        print(f"登录态有效。{reason}")


if __name__ == "__main__":
    main()
