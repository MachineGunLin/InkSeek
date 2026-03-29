from __future__ import annotations

import json
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
ARCHIVE_DIR = DATA_DIR / "archive"
CONFIG_DIR = BASE_DIR / "config"
STATE_PATH = DATA_DIR / "weread_state.json"


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, DOWNLOADS_DIR, ARCHIVE_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def format_success(message: str) -> str:
    return f"寻墨成功：{message}"


def format_failure(message: str) -> str:
    return f"寻墨中断：{message}"


def fail(message: str) -> None:
    raise SystemExit(format_failure(message))


def load_state_payload(required: bool = True) -> dict:
    ensure_runtime_dirs()

    if not STATE_PATH.exists():
        if required:
            fail("未找到 Session，请运行 python3 main.py login")
        return {}

    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if required:
            fail(f"Session 文件损坏，请重新登录。{exc}")
        return {}

    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    if required and not cookies:
        fail("Session 为空，请运行 python3 main.py login")

    return payload if isinstance(payload, dict) else {}


def browser_context_kwargs(use_storage_state: bool = False, user_agent: str | None = None) -> dict:
    ensure_runtime_dirs()
    kwargs: dict[str, str] = {}

    if use_storage_state:
        load_state_payload(required=True)
        kwargs["storage_state"] = str(STATE_PATH)

    if user_agent:
        kwargs["user_agent"] = user_agent

    return kwargs


def launch_browser_context(playwright, *, headless: bool, use_storage_state: bool = False, user_agent: str | None = None):
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(**browser_context_kwargs(use_storage_state=use_storage_state, user_agent=user_agent))
    return browser, context


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def archive_file_if_needed(file_path: Path) -> Path | None:
    ensure_runtime_dirs()
    resolved = file_path.resolve()

    try:
        relative = resolved.relative_to(DATA_DIR.resolve())
    except ValueError:
        return None

    if relative.parts and relative.parts[0] == ARCHIVE_DIR.name:
        return resolved

    target = unique_path(ARCHIVE_DIR / resolved.name)
    shutil.move(str(resolved), str(target))
    return target
