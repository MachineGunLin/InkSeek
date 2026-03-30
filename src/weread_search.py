from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Error, TimeoutError, sync_playwright

from utils import DEFAULT_USER_AGENT, fail, launch_browser_context, log_info
from weread_session import verify_session

HOME_URL = "https://weread.qq.com/"
SEARCH_URL_TEMPLATE = "https://weread.qq.com/web/search/books?keyword={query}"

SHELF_CARD_SELECTOR = ".wr_index_mini_shelf_card"
SHELF_TITLE_SELECTOR = ".wr_index_mini_shelf_card_content_title"
SHELF_AUTHOR_SELECTOR = ".wr_index_mini_shelf_card_content_author"

RESULT_CARD_SELECTOR = ".wr_bookList_item_container"
ADD_TO_SHELF_SELECTORS = [
    "text=加入书架",
    "button:has-text('加入书架')",
    "text=放入书架",
    "button:has-text('放入书架')",
    "text=加入",
]
READER_READY_MARKERS = ["目录", "继续阅读", "已在书架", "书架", "返回书架"]

STATE_DUPLICATE_FOUND = "duplicate_found"
STATE_WAITING_FOR_SELECTION = "waiting_for_selection"
STATE_NOT_FOUND = "not_found"


@dataclass
class WeReadCandidate:
    title: str
    author: str
    translator: str
    rating: float
    reading_count: int
    href: str


@dataclass
class WeReadSeekPreparation:
    state: str
    query: str
    duplicate: WeReadCandidate | None = None
    candidates: list[WeReadCandidate] = field(default_factory=list)


def normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value or "").lower()


def query_matches_title(query: str, title: str) -> bool:
    normalized_query = normalize_lookup_text(query)
    normalized_title = normalize_lookup_text(title)
    return bool(normalized_query and (normalized_query in normalized_title or normalized_title in normalized_query))


def ensure_logged_in(page) -> None:
    last_reason = "未完成登录态校验"
    for _ in range(3):
        ok, reason = verify_session(page, timeout_seconds=8)
        if ok:
            return
        last_reason = reason
        if "访问首页失败" not in reason:
            break
        page.wait_for_timeout(1500)
    fail(f"微信读书登录态无效，请先运行 python3 main.py login。{last_reason}")


def open_home(page) -> None:
    last_error: str | None = None
    for _ in range(3):
        try:
            page.goto(HOME_URL, wait_until="commit", timeout=45000)
            page.wait_for_timeout(5000)
            return
        except Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            page.wait_for_timeout(1500)
    fail(f"微信读书首页打开失败: {last_error or '未知错误'}")


def wait_for_search_page(page, query: str) -> None:
    search_url = SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    last_error: str | None = None

    for _ in range(3):
        try:
            page.goto(search_url, wait_until="commit", timeout=45000)
            page.wait_for_timeout(6000)
        except Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        try:
            if page.locator(RESULT_CARD_SELECTOR).count() > 0:
                return
        except Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    fail(f"微信读书搜索页打开失败: {last_error or '未加载出搜索结果'}")


def fetch_search_metadata(page, query: str) -> list[dict]:
    try:
        payload = page.evaluate(
            """
            async ({ query }) => {
              const response = await fetch(
                '/web/search/global?keyword=' + encodeURIComponent(query),
                { credentials: 'include' }
              );
              const text = await response.text();
              return { ok: response.ok, status: response.status, text };
            }
            """,
            {"query": query},
        )
    except Error:
        return []

    if not payload.get("ok"):
        return []

    raw_text = (payload.get("text") or "").strip()
    if not raw_text:
        return []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    books = data.get("books", [])
    return books if isinstance(books, list) else []


def build_metadata_map(items: list[dict]) -> dict[tuple[str, str], dict]:
    metadata_map: dict[tuple[str, str], dict] = {}
    for item in items:
        book_info = item.get("bookInfo") or {}
        title = str(book_info.get("title") or "").strip()
        author = str(book_info.get("author") or "").strip()
        if not title:
            continue
        key = (normalize_lookup_text(title), normalize_lookup_text(author))
        metadata_map[key] = book_info
    return metadata_map


def parse_shelf_card(card) -> WeReadCandidate | None:
    try:
        title = card.locator(SHELF_TITLE_SELECTOR).first.inner_text(timeout=1500).strip()
    except Error:
        return None

    author = ""
    try:
        author = card.locator(SHELF_AUTHOR_SELECTOR).first.inner_text(timeout=1500).strip()
    except Error:
        author = ""

    return WeReadCandidate(
        title=title,
        author=author,
        translator="",
        rating=0.0,
        reading_count=0,
        href="",
    )


def scan_shelf_for_duplicate(page, query: str) -> WeReadCandidate | None:
    open_home(page)
    cards = page.locator(SHELF_CARD_SELECTOR)
    total = min(cards.count(), 24)
    for index in range(total):
        candidate = parse_shelf_card(cards.nth(index))
        if candidate is None:
            continue
        if query_matches_title(query, candidate.title):
            return candidate
    return None


def parse_search_card(card, metadata_map: dict[tuple[str, str], dict]) -> WeReadCandidate | None:
    try:
        text = card.inner_text(timeout=2000)
    except Error:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    title = lines[0]
    author = lines[1]
    rating_match = re.search(r"推荐值\s*([0-9.]+)%", text)
    reading_match = re.search(r"(\d+)人今日阅读", text)

    try:
        href = card.evaluate(
            """
            el => {
              const parent = el.parentElement;
              const link = parent ? parent.querySelector('a.wr_bookList_item_link') : null;
              return link ? (link.getAttribute('href') || '') : '';
            }
            """
        )
    except Error:
        href = ""

    metadata = metadata_map.get((normalize_lookup_text(title), normalize_lookup_text(author)), {})
    translator = str(metadata.get("translator") or "").strip()
    rating_raw = metadata.get("newRating")
    rating = float(rating_raw or 0) / 10.0 if rating_raw is not None else 0.0
    if rating <= 0 and rating_match:
        rating = float(rating_match.group(1))

    reading_count = int(metadata.get("readingCount") or metadata.get("reading") or 0)
    if reading_count <= 0 and reading_match:
        reading_count = int(reading_match.group(1))

    return WeReadCandidate(
        title=title,
        author=author,
        translator=translator,
        rating=rating,
        reading_count=reading_count,
        href=href,
    )


def collect_search_candidates(page, query: str, limit: int = 3) -> list[WeReadCandidate]:
    metadata_map = build_metadata_map(fetch_search_metadata(page, query))
    cards = page.locator(RESULT_CARD_SELECTOR)
    total = min(cards.count(), 20)
    candidates: list[WeReadCandidate] = []
    seen: set[tuple[str, str]] = set()

    for index in range(total):
        candidate = parse_search_card(cards.nth(index), metadata_map)
        if candidate is None:
            continue
        if not query_matches_title(query, candidate.title):
            continue
        key = (normalize_lookup_text(candidate.title), normalize_lookup_text(candidate.author))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item.rating, item.reading_count), reverse=True)
    return candidates[:limit]


def page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Error:
        return ""


def click_add_to_shelf_if_available(page) -> bool:
    for selector in ADD_TO_SHELF_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=1500):
                locator.click(timeout=3000)
                page.wait_for_timeout(2000)
                return True
        except Error:
            continue
    return False


def ensure_reader_ready(page, candidate: WeReadCandidate) -> None:
    text = page_text(page)
    if any(marker in text for marker in READER_READY_MARKERS):
        return

    if "/web/reader/" in page.url.lower():
        return

    fail(f"已打开目标书籍，但未确认进入阅读页：{candidate.title}")


def prepare_seek_selection(query: str, limit: int = 3) -> WeReadSeekPreparation:
    normalized_query = query.strip()
    if not normalized_query:
        fail("检索关键词不能为空")

    with sync_playwright() as playwright:
        browser, context = launch_browser_context(
            playwright,
            headless=True,
            use_storage_state=True,
            user_agent=DEFAULT_USER_AGENT,
        )
        page = context.new_page()
        try:
            ensure_logged_in(page)

            duplicate = scan_shelf_for_duplicate(page, normalized_query)
            if duplicate is not None:
                return WeReadSeekPreparation(
                    state=STATE_DUPLICATE_FOUND,
                    query=normalized_query,
                    duplicate=duplicate,
                )

            wait_for_search_page(page, normalized_query)
            candidates = collect_search_candidates(page, normalized_query, limit=limit)
            if not candidates:
                return WeReadSeekPreparation(
                    state=STATE_NOT_FOUND,
                    query=normalized_query,
                )

            return WeReadSeekPreparation(
                state=STATE_WAITING_FOR_SELECTION,
                query=normalized_query,
                candidates=candidates,
            )
        except TimeoutError as exc:
            fail(f"微信读书站内检索超时: {exc}")
        except Error as exc:
            fail(f"微信读书站内检索失败: {type(exc).__name__}: {exc}")
        finally:
            browser.close()


def select_highest_rated(candidates: list[WeReadCandidate]) -> WeReadCandidate:
    if not candidates:
        fail("候选列表为空，无法自动选择版本")
    return max(candidates, key=lambda item: (item.rating, item.reading_count))


def add_candidate_to_shelf(candidate: WeReadCandidate) -> None:
    if not candidate.href:
        fail(f"未拿到可用的书籍入口：{candidate.title}")

    with sync_playwright() as playwright:
        browser, context = launch_browser_context(
            playwright,
            headless=True,
            use_storage_state=True,
            user_agent=DEFAULT_USER_AGENT,
        )
        page = context.new_page()
        try:
            ensure_logged_in(page)
            page.goto(urljoin(HOME_URL, candidate.href), wait_until="commit", timeout=45000)
            page.wait_for_timeout(5000)
            click_add_to_shelf_if_available(page)
            ensure_reader_ready(page, candidate)
        except TimeoutError as exc:
            fail(f"微信读书选书入库超时: {exc}")
        except Error as exc:
            fail(f"微信读书选书入库失败: {type(exc).__name__}: {exc}")
        finally:
            browser.close()


def log_candidate_preview(candidates: list[WeReadCandidate]) -> None:
    for index, candidate in enumerate(candidates, start=1):
        translator = candidate.translator or "未标注"
        log_info(
            f"候选 {index}: {candidate.title} / 译者 {translator} / 推荐值 {candidate.rating:.1f}%"
        )
