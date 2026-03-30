from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
ARCHIVE_DIR = DATA_DIR / "archive"
CONFIG_DIR = BASE_DIR / "config"
STATE_PATH = DATA_DIR / "weread_state.json"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SCREENSHOT_MASK_ID = "__inkseek_privacy_mask__"


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, DOWNLOADS_DIR, ARCHIVE_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_info(message: str) -> str:
    return f"[{timestamp()}] {message}"


def format_success(message: str) -> str:
    return f"[{timestamp()}] 寻墨成功：{message}"


def format_failure(message: str) -> str:
    return f"[{timestamp()}] 寻墨中断：{message}"


def log_info(message: str) -> None:
    print(format_info(message))


def log_success(message: str) -> None:
    print(format_success(message))


def log_failure(message: str) -> None:
    print(format_failure(message))


def fail(message: str) -> None:
    raise SystemExit(format_failure(message))


def load_env_file() -> dict[str, str]:
    env_path = BASE_DIR / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        os.environ.setdefault(key, value)
        loaded[key] = os.environ.get(key, value)

    return loaded


def require_env(name: str) -> str:
    load_env_file()
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"缺少环境变量：{name}")
    return value


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


def browser_context_kwargs(use_storage_state: bool = False, user_agent: str | None = None, **extra_kwargs) -> dict:
    ensure_runtime_dirs()
    kwargs: dict[str, object] = dict(extra_kwargs)

    if use_storage_state:
        load_state_payload(required=True)
        kwargs["storage_state"] = str(STATE_PATH)

    if user_agent:
        kwargs["user_agent"] = user_agent

    return kwargs


def launch_browser_context(playwright, *, headless: bool, use_storage_state: bool = False, user_agent: str | None = None, **context_kwargs):
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        **browser_context_kwargs(
            use_storage_state=use_storage_state,
            user_agent=user_agent,
            **context_kwargs,
        )
    )
    return browser, context


def sanitize_filename(name: str, default_stem: str = "download") -> str:
    cleaned = re.sub(r"[^\w\s.-]", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or default_stem


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


def save_masked_page_screenshot(page, path: Path, *, full_page: bool = True, mask_width: int = 360, mask_height: int = 140) -> None:
    page.evaluate(
        """
        ({ maskId, width, height }) => {
          let mask = document.getElementById(maskId);
          if (!mask) {
            mask = document.createElement('div');
            mask.id = maskId;
            document.body.appendChild(mask);
          }
          Object.assign(mask.style, {
            position: 'fixed',
            top: '0',
            left: '0',
            width: `${width}px`,
            height: `${height}px`,
            background: '#f5f5f5',
            opacity: '0.96',
            zIndex: '2147483647',
            pointerEvents: 'none',
          });
        }
        """,
        {
            "maskId": SCREENSHOT_MASK_ID,
            "width": mask_width,
            "height": mask_height,
        },
    )
    try:
        page.screenshot(path=str(path), full_page=full_page)
    finally:
        page.evaluate(
            """
            maskId => {
              const mask = document.getElementById(maskId);
              if (mask) {
                mask.remove();
              }
            }
            """,
            SCREENSHOT_MASK_ID,
        )


def download_binary(url: str, destination: Path, user_agent: str | None = None, timeout: int = 60) -> Path:
    ensure_runtime_dirs()
    destination.parent.mkdir(parents=True, exist_ok=True)

    request = Request(url, headers={"User-Agent": user_agent or DEFAULT_USER_AGENT})
    with urlopen(request, timeout=timeout) as response, destination.open("wb") as file_handle:
        shutil.copyfileobj(response, file_handle)
    return destination
