from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus
from urllib.parse import urljoin

from playwright.sync_api import Error, TimeoutError, sync_playwright

from utils import DEFAULT_USER_AGENT, fail, launch_browser_context, log_info
from weread_session import verify_session

HOME_URL = "https://weread.qq.com/"
SEARCH_URL_TEMPLATE = "https://weread.qq.com/web/search/books?keyword={query}"

RESULT_CARD_SELECTOR = ".wr_bookList_item_container"
RESULT_LINK_SELECTOR = ".wr_bookList_item_link"
ADD_TO_SHELF_SELECTORS = [
    "text=加入书架",
    "button:has-text('加入书架')",
    "text=放入书架",
    "button:has-text('放入书架')",
    "text=加入",
]
READER_READY_MARKERS = ["目录", "继续阅读", "已在书架", "书架", "返回书架"]


@dataclass
class WeReadMatch:
    book_id: str
    title: str
    author: str
    rating: float
    reading_count: int
    href: str


@dataclass
class WeReadSeekResult:
    found: bool
    title: str = ""
    author: str = ""
    rating: float = 0.0


def normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value or "").lower()


def ensure_logged_in(page) -> None:
    ok, reason = verify_session(page, timeout_seconds=8)
    if not ok:
        fail(f"微信读书登录态无效，请先运行 python3 main.py login。{reason}")


def select_best_match(query: str, matches: list[WeReadMatch]) -> WeReadMatch | None:
    if not matches:
        return None

    normalized_query = normalize_lookup_text(query)
    strict = [
        match
        for match in matches
        if normalized_query and normalized_query in normalize_lookup_text(match.title)
    ]
    ranked = strict or matches
    ranked.sort(key=lambda item: (item.rating, item.reading_count), reverse=True)
    return ranked[0]


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


def parse_card_match(card) -> WeReadMatch | None:
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

    book_id_match = re.search(r"/web/reader/([^/?#]+)", href)
    return WeReadMatch(
        book_id=book_id_match.group(1) if book_id_match else "",
        title=title,
        author=author,
        rating=float(rating_match.group(1)) if rating_match else 0.0,
        reading_count=int(reading_match.group(1)) if reading_match else 0,
        href=href,
    )


def collect_visible_matches(page) -> list[WeReadMatch]:
    cards = page.locator(RESULT_CARD_SELECTOR)
    total = min(cards.count(), 20)
    matches: list[WeReadMatch] = []

    for index in range(total):
        card = cards.nth(index)
        match = parse_card_match(card)
        if match is None:
            continue
        matches.append(match)
    return matches


def open_best_match(page, match: WeReadMatch) -> None:
    if match.href:
        page.goto(urljoin(HOME_URL, match.href), wait_until="commit", timeout=45000)
        page.wait_for_timeout(5000)
        return

    fail(f"微信读书搜索页未找到目标书籍：{match.title}")


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


def page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Error:
        return ""


def ensure_reader_ready(page, match: WeReadMatch) -> None:
    text = page_text(page)
    if any(marker in text for marker in READER_READY_MARKERS):
        return

    current_url = page.url.lower()
    if "/web/reader/" in current_url:
        return

    fail(f"已打开目标书籍，但未确认进入阅读页：{match.title}")


def try_add_best_match(query: str) -> WeReadSeekResult:
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
            wait_for_search_page(page, query)
            candidates = collect_visible_matches(page)
            best_match = select_best_match(query, candidates)
            if best_match is None:
                return WeReadSeekResult(found=False)

            log_info(
                f"站内命中候选：{best_match.title} / {best_match.author} / 推荐值 {best_match.rating:.1f}%"
            )
            open_best_match(page, best_match)
            click_add_to_shelf_if_available(page)
            ensure_reader_ready(page, best_match)
            return WeReadSeekResult(
                found=True,
                title=best_match.title,
                author=best_match.author,
                rating=best_match.rating,
            )
        except TimeoutError as exc:
            fail(f"微信读书站内检索超时: {exc}")
        except Error as exc:
            fail(f"微信读书站内检索失败: {type(exc).__name__}: {exc}")
        finally:
            browser.close()
