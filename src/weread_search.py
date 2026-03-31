from __future__ import annotations

from difflib import SequenceMatcher
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Error, TimeoutError, sync_playwright

from utils import DEFAULT_USER_AGENT, fail, launch_browser_context, log_info
from weread_session import verify_session

HOME_URL = "https://weread.qq.com/"
SHELF_URL = "https://weread.qq.com/web/shelf"
SEARCH_URL_TEMPLATE = "https://weread.qq.com/web/search/books?keyword={query}"

SHELF_CARD_SELECTOR = ".wr_index_mini_shelf_card"
SHELF_TITLE_SELECTOR = ".wr_index_mini_shelf_card_content_title"
SHELF_AUTHOR_SELECTOR = ".wr_index_mini_shelf_card_content_author"

RESULT_CARD_SELECTOR = ".wr_bookList_item_container"
ADD_TO_SHELF_SELECTORS = [
    "button:has-text('加入书架')",
    "a:has-text('加入书架')",
    "[role='button']:has-text('加入书架')",
    "button:has-text('放入书架')",
    "a:has-text('放入书架')",
    "[role='button']:has-text('放入书架')",
]
READ_ACTION_SELECTORS = [
    "button:has-text('继续阅读')",
    "a:has-text('继续阅读')",
    "[role='button']:has-text('继续阅读')",
    "button:has-text('开始阅读')",
    "a:has-text('开始阅读')",
    "[role='button']:has-text('开始阅读')",
    "button:has-text('在读')",
    "a:has-text('在读')",
    "[role='button']:has-text('在读')",
    "button:has-text('阅读')",
    "a:has-text('阅读')",
    "[role='button']:has-text('阅读')",
]
IN_SHELF_SELECTORS = [
    "button:has-text('已在书架')",
    "a:has-text('已在书架')",
    "[role='button']:has-text('已在书架')",
    "button:has-text('已加入书架')",
    "a:has-text('已加入书架')",
    "[role='button']:has-text('已加入书架')",
]
READER_READY_MARKERS = ["目录", "继续阅读", "已在书架", "书架", "返回书架"]
RESTRICTED_MARKERS = [
    "版权受限",
    "暂无版权",
    "因版权限制",
    "版权方要求",
    "暂时无法阅读",
    "仅支持在 App 内阅读",
    "仅支持在App内阅读",
    "请前往 App 阅读",
    "请前往App阅读",
]
READY_CONTAINER_SELECTOR = ".readerCatalog, .readerBookInfo, .readerContent"
SHELF_PAGE_TITLE_SELECTOR = ".shelfBook .title, a.shelfBook, .wr_index_mini_shelf_card_content_title"
DETAIL_TITLE_SELECTORS = [
    ".bookInfo_right_header_title",
    ".readerCatalog_bookInfo_title_txt",
    ".readerBookInfo_head",
    ".readerCatalog_bookInfo_right",
]
UNAVAILABLE_IN_WEREAD_MARKERS = [
    "待上架",
    "订阅",
    "已订阅",
    "已预订",
    "查看更多",
    "查看全部",
    "去 App 查看全部",
    "去 App 查看更多",
]

STATE_DUPLICATE_FOUND = "duplicate_found"
STATE_WAITING_FOR_SELECTION = "waiting_for_selection"
STATE_NOT_FOUND = "not_found"
STATUS_UNAVAILABLE_IN_WEREAD = "unavailable_in_weread"
TITLE_SIMILARITY_THRESHOLD = 0.6
DETAIL_TITLE_SIMILARITY_THRESHOLD = 0.75


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


class WeReadActionError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value or "").lower()


def query_matches_title(query: str, title: str) -> bool:
    normalized_query = normalize_lookup_text(query)
    normalized_title = normalize_lookup_text(title)
    return bool(normalized_query and (normalized_query in normalized_title or normalized_title in normalized_query))


def build_title_variants(title: str) -> set[str]:
    variants: set[str] = set()
    normalized_title = normalize_lookup_text(title)
    if normalized_title:
        variants.add(normalized_title)

    for fragment in re.split(r"[\s/|·•—\-（）()《》【】\[\]，,。!！?？:：;；]+", title or ""):
        normalized_fragment = normalize_lookup_text(fragment)
        if len(normalized_fragment) >= 2:
            variants.add(normalized_fragment)

    return variants


def title_similarity_score(query: str, title: str) -> float:
    normalized_query = normalize_lookup_text(query)
    if not normalized_query:
        return 0.0

    scores = [
        SequenceMatcher(None, normalized_query, variant).ratio()
        for variant in build_title_variants(title)
        if variant
    ]
    return max(scores, default=0.0)


def log_title_match(query: str, title: str, score: float, accepted: bool, reason: str | None = None) -> None:
    decision = "Accepted" if accepted else "Rejected"
    suffix = f" ({reason})" if reason else ""
    log_info(f"[Match] 输入: {query} vs 结果: {title} | Score: {score:.2f} -> {decision}{suffix}")


def unavailable_in_weread(message: str) -> None:
    raise WeReadActionError(STATUS_UNAVAILABLE_IN_WEREAD, message)


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
            page.wait_for_timeout(5000)
            return
        except Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            page.wait_for_timeout(1500)

    fail(f"微信读书搜索页打开失败: {last_error or '未完成页面跳转'}")


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


def build_metadata_map(items: list[dict]) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    metadata_map: dict[tuple[str, str], dict] = {}
    metadata_title_map: dict[str, dict] = {}
    for item in items:
        book_info = item.get("bookInfo") or {}
        title = str(book_info.get("title") or "").strip()
        author = str(book_info.get("author") or "").strip()
        if not title:
            continue
        normalized_title = normalize_lookup_text(title)
        key = (normalized_title, normalize_lookup_text(author))
        metadata_map[key] = book_info
        existing = metadata_title_map.get(normalized_title)
        if existing is None:
            metadata_title_map[normalized_title] = book_info
            continue

        current_rating = float(book_info.get("newRating") or 0)
        existing_rating = float(existing.get("newRating") or 0)
        current_reading = int(book_info.get("readingCount") or book_info.get("reading") or 0)
        existing_reading = int(existing.get("readingCount") or existing.get("reading") or 0)
        if (current_rating, current_reading) > (existing_rating, existing_reading):
            metadata_title_map[normalized_title] = book_info

    return metadata_map, metadata_title_map


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


def parse_search_card(
    card,
    metadata_map: dict[tuple[str, str], dict],
    metadata_title_map: dict[str, dict],
) -> WeReadCandidate | None:
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

    normalized_title = normalize_lookup_text(title)
    normalized_author = normalize_lookup_text(author)
    metadata = metadata_map.get((normalized_title, normalized_author))
    if metadata is None:
        metadata = metadata_title_map.get(normalized_title, {})

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
    metadata_map, metadata_title_map = build_metadata_map(fetch_search_metadata(page, query))
    if not metadata_title_map:
        log_info("[Match] 搜索接口未返回可信书目，站内结果全部跳过。")
        return []

    cards = page.locator(RESULT_CARD_SELECTOR)
    total = min(cards.count(), 20)
    candidates: list[WeReadCandidate] = []
    seen: set[tuple[str, str]] = set()

    for index in range(total):
        candidate = parse_search_card(cards.nth(index), metadata_map, metadata_title_map)
        if candidate is None:
            continue

        normalized_title = normalize_lookup_text(candidate.title)
        trusted = normalized_title in metadata_title_map
        score = title_similarity_score(query, candidate.title)
        accepted = trusted and score >= TITLE_SIMILARITY_THRESHOLD
        reason = None if accepted or trusted else "NotInSearchFeed"
        log_title_match(query, candidate.title, score, accepted, reason=reason)
        if not accepted:
            continue

        key = (normalized_title, normalize_lookup_text(candidate.author))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item.rating, item.reading_count), reverse=True)
    return candidates[:limit]


def page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Error:
        return ""


def wait_for_book_surface(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except TimeoutError:
        pass
    except Error:
        pass

    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except TimeoutError:
        pass
    except Error:
        pass

    page.wait_for_timeout(1500)


def locator_identity(locator) -> str:
    try:
        payload = locator.evaluate(
            """
            element => {
              const text = (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();
              return {
                id: element.id || '',
                className: typeof element.className === 'string' ? element.className : '',
                text: text.slice(0, 48),
              };
            }
            """
        )
    except Error:
        return "id=<unknown> class=<unknown> text=<unknown>"

    element_id = payload.get("id") or "<none>"
    class_name = payload.get("className") or "<none>"
    text = payload.get("text") or "<empty>"
    return f"id={element_id} class={class_name} text={text}"


def first_visible_selector(page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=1500):
                return selector
        except Error:
            continue
    return None


def first_visible_action(page, selectors: list[str]) -> tuple[str, str] | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=1500):
                return selector, locator_identity(locator)
        except Error:
            continue
    return None


def detect_visible_action_labels(page) -> list[str]:
    try:
        return page.locator("button, a, [role='button']").evaluate_all(
            """
            (elements, keywords) => {
              const visible = element => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              return elements
                .filter(visible)
                .map(element => (element.innerText || '').trim())
                .filter(text => text && keywords.some(keyword => text.includes(keyword)))
                .slice(0, 12);
            }
            """,
            ["加入书架", "放入书架", "阅读", "在读", "继续阅读", "开始阅读"],
        )
    except Error:
        return []


def extract_detail_title(page) -> str:
    for selector in DETAIL_TITLE_SELECTORS:
        try:
            text = page.locator(selector).first.inner_text(timeout=1500).strip()
        except Error:
            continue
        if not text:
            continue
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first_line:
            return first_line

    body = page_text(page)
    for line in body.splitlines():
        text = line.strip()
        if len(text) >= 2:
            return text
    return ""


def ensure_detail_title_matches(page, query: str, candidate: WeReadCandidate) -> None:
    detail_title = extract_detail_title(page)
    if not detail_title:
        return

    score = title_similarity_score(query, detail_title)
    normalized_query = normalize_lookup_text(query)
    normalized_detail_title = normalize_lookup_text(detail_title)
    accepted = score >= DETAIL_TITLE_SIMILARITY_THRESHOLD or query_matches_title(query, detail_title)
    log_title_match(query, detail_title, score, accepted, reason="DetailPage")
    if accepted:
        return

    candidate_score = title_similarity_score(candidate.title, detail_title)
    if candidate_score >= DETAIL_TITLE_SIMILARITY_THRESHOLD and normalized_query in normalized_detail_title:
        return

    unavailable_in_weread(f"站内详情页标题与检索词不符，已放弃站内入库：{detail_title}")


def click_first_visible(page, selectors: list[str]) -> tuple[str, str] | None:
    action = first_visible_action(page, selectors)
    if action is None:
        return None

    selector, identity = action
    try:
        page.locator(selector).first.click(timeout=4000)
        wait_for_book_surface(page)
        return selector, identity
    except Error:
        return None


def classify_book_page_state(
    *,
    title: str,
    url: str,
    body_text: str,
    has_add_action: bool,
    has_read_action: bool,
    has_ready_container: bool,
    visible_action_labels: list[str],
) -> tuple[str, str]:
    matched_restricted = next((marker for marker in RESTRICTED_MARKERS if marker in body_text), None)
    if matched_restricted is not None:
        return "restricted", f"《{title}》当前显示“{matched_restricted}”，暂时无法自动入库。"

    if has_add_action:
        return "add", f"《{title}》已定位到加入书架按钮。"

    unavailable_marker = next(
        (marker for marker in UNAVAILABLE_IN_WEREAD_MARKERS if marker in body_text),
        None,
    )
    if unavailable_marker is not None and not has_read_action:
        return "unavailable", f"《{title}》当前显示“{unavailable_marker}”，站内仅为待上架占位符，无法入库。"

    if "/web/bookdetail/" in url.lower() and has_ready_container and not has_add_action and not has_read_action:
        return "unavailable", f"《{title}》当前仅展示详情占位页，未提供加入书架或阅读入口，无法站内入库。"

    if has_ready_container or "/web/reader/" in url.lower() or any(marker in body_text for marker in READER_READY_MARKERS):
        return "ready", f"《{title}》已进入阅读页。"

    if has_read_action:
        action_label = next(
            (label for label in visible_action_labels if any(keyword in label for keyword in ["继续阅读", "开始阅读", "在读", "阅读"])),
            "阅读",
        )
        return "read", f"《{title}》当前显示“{action_label}”，准备打开阅读页。"

    visible_actions = "、".join(visible_action_labels[:4]) if visible_action_labels else "无"
    return "unknown", f"未识别到《{title}》的加入书架按钮或阅读入口。可见动作：{visible_actions}"


def inspect_book_page_state(page, candidate: WeReadCandidate) -> tuple[str, str]:
    body_text = page_text(page)
    visible_action_labels = detect_visible_action_labels(page)
    has_add_action = first_visible_selector(page, ADD_TO_SHELF_SELECTORS) is not None
    has_read_action = first_visible_selector(page, READ_ACTION_SELECTORS) is not None
    try:
        has_ready_container = page.locator(READY_CONTAINER_SELECTOR).first.is_visible(timeout=500)
    except Error:
        has_ready_container = False

    return classify_book_page_state(
        title=candidate.title,
        url=page.url,
        body_text=body_text,
        has_add_action=has_add_action,
        has_read_action=has_read_action,
        has_ready_container=has_ready_container,
        visible_action_labels=visible_action_labels,
    )


def wait_for_add_to_shelf_confirmation(page) -> bool:
    for _ in range(20):
        if first_visible_selector(page, IN_SHELF_SELECTORS) is not None:
            log_info("站内入库检测：加入书架按钮已切换为已在书架。")
            return True

        if first_visible_selector(page, ADD_TO_SHELF_SELECTORS) is None:
            log_info("站内入库检测：加入书架按钮已从当前页面消失。")
            return True

        page.wait_for_timeout(500)

    return False


def open_shelf(page) -> None:
    last_error: str | None = None
    for _ in range(3):
        try:
            page.goto(SHELF_URL, wait_until="commit", timeout=45000)
            wait_for_book_surface(page)
            return
        except Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            page.wait_for_timeout(1500)

    fail(f"微信读书书架页打开失败: {last_error or '未知错误'}")


def collect_shelf_titles(page) -> list[str]:
    try:
        titles = page.locator(SHELF_PAGE_TITLE_SELECTOR).evaluate_all(
            """
            elements => elements
              .map(element => (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim())
              .filter(Boolean)
            """
        )
    except Error:
        titles = []

    unique_titles: list[str] = []
    seen: set[str] = set()
    for title in titles:
        normalized_title = normalize_lookup_text(title)
        if not normalized_title or normalized_title in seen:
            continue
        seen.add(normalized_title)
        unique_titles.append(title)
    return unique_titles


def verify_candidate_in_shelf(page, candidate: WeReadCandidate) -> str | None:
    normalized_target = normalize_lookup_text(candidate.title)
    for _ in range(3):
        open_shelf(page)
        titles = collect_shelf_titles(page)
        for title in titles:
            normalized_title = normalize_lookup_text(title)
            if not normalized_target or not normalized_title:
                continue
            if normalized_target in normalized_title or normalized_title in normalized_target:
                return title
        page.wait_for_timeout(1500)
    return None


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


def add_candidate_to_shelf(candidate: WeReadCandidate, *, query: str) -> str:
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
            ensure_detail_title_matches(page, query, candidate)
            reached_read_surface = False
            clicked_add = False
            add_confirmed = False
            last_detail = f"未完成《{candidate.title}》的入库检测。"

            for _ in range(3):
                wait_for_book_surface(page)
                ensure_detail_title_matches(page, query, candidate)
                state, detail = inspect_book_page_state(page, candidate)
                last_detail = detail
                log_info(f"站内入库检测：{detail}")

                if state == "restricted":
                    fail(detail)

                if state == "unavailable":
                    unavailable_in_weread(detail)

                if state == "add":
                    log_info(f"正在点击加入书架：{candidate.title}")
                    clicked = click_first_visible(page, ADD_TO_SHELF_SELECTORS)
                    if clicked is not None:
                        clicked_add = True
                        selector, identity = clicked
                        log_info(f"站内点击元素：selector={selector} | {identity}")
                        add_confirmed = wait_for_add_to_shelf_confirmation(page)
                        if not add_confirmed:
                            fail(f"已点击《{candidate.title}》的加入书架按钮，但未观察到按钮变为已在书架或消失。")
                        continue

                if state == "read":
                    log_info(f"正在打开阅读页：{candidate.title}")
                    clicked = click_first_visible(page, READ_ACTION_SELECTORS)
                    if clicked is not None:
                        selector, identity = clicked
                        log_info(f"站内点击元素：selector={selector} | {identity}")
                        continue

                if state == "ready":
                    reached_read_surface = True
                    break

                page.wait_for_timeout(1500)

            matched_title = verify_candidate_in_shelf(page, candidate)
            if matched_title is not None:
                log_info(f"书架同步检测：已在书架页确认《{matched_title}》。")
                return f"《{matched_title}》已同步至书架。"

            if reached_read_surface or clicked_add or add_confirmed:
                fail("检测到已进入阅读页，但未能成功同步至书架，请手动确认。")

            fail(f"未找到《{candidate.title}》的加入书架按钮，且书架页也未检测到该书。最近状态：{last_detail}")
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
