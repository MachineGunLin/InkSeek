from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Error, sync_playwright

from cover_service import ensure_epub_cover
from upload_weread import run_upload
from utils import (
    BASE_DIR,
    DEFAULT_USER_AGENT,
    DOWNLOADS_DIR,
    ensure_runtime_dirs,
    fail,
    launch_browser_context,
    log_info,
    sanitize_filename,
    unique_path,
)

SEARCH_URL_TEMPLATE = "https://standardebooks.org/ebooks?query={query}"
SOURCE_NAME = "Standard Ebooks"
DETAIL_LINK_PATTERN = re.compile(r"^/ebooks/[^/]+/[^/?#]+/?$")

TITLE_SELECTORS = ["h1"]
AUTHOR_SELECTORS = ["h2", ".author"]
DOWNLOAD_LINK_SELECTORS = [
    "a:has-text('Compatible epub')",
    "a:has-text('Advanced epub')",
    "a[href$='.epub']",
]
QUERY_ALIASES = {
    "科学怪人": "frankenstein",
    "弗兰肯斯坦": "frankenstein",
    "科學怪人": "frankenstein",
}


@dataclass
class SearchMatch:
    title: str
    author: str
    detail_url: str
    download_selector: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过公开书源检索并获取 EPUB")
    parser.add_argument("query", help="检索关键词")
    return parser


def first_nonempty_text(page, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            text = locator.inner_text(timeout=1500).strip()
            if text:
                return text
        except Error:
            continue
    return ""


def first_author_text(page) -> str:
    try:
        items = page.locator("a[href^='/ebooks/']").evaluate_all(
            """
            els => els.map(el => ({
              text: (el.innerText || el.textContent || '').trim(),
              href: el.getAttribute('href')
            }))
            """
        )
    except Error:
        return ""

    for item in items:
        href = (item.get("href") or "").strip()
        text = (item.get("text") or "").strip()
        if re.match(r"^/ebooks/[^/]+/?$", href) and text:
            return text
    return ""


def search_candidates(page, query: str) -> list[str]:
    search_url = SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Error as exc:
        fail(f"公开书源搜索页打开失败: {type(exc).__name__}: {exc}")

    try:
        raw_links = page.locator("a[href^='/ebooks/']").evaluate_all(
            """
            els => els.map(el => ({
              href: el.getAttribute('href'),
              text: (el.innerText || el.textContent || '').trim()
            }))
            """
        )
    except Error as exc:
        fail(f"公开书源搜索结果读取失败: {type(exc).__name__}: {exc}")

    results: list[str] = []
    seen: set[str] = set()
    for item in raw_links:
        href = (item.get("href") or "").strip()
        if not DETAIL_LINK_PATTERN.match(href):
            continue
        absolute = urljoin(page.url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append(absolute)

    return results


def resolve_download_selector(page) -> str:
    for selector in DOWNLOAD_LINK_SELECTORS:
        try:
            locator = page.locator(selector).first
            href = locator.get_attribute("href", timeout=1500)
            if href and href.endswith(".epub"):
                return selector
        except Error:
            continue
    return ""


def inspect_candidate(page, detail_url: str) -> SearchMatch | None:
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
    except Error:
        return None

    title = first_nonempty_text(page, TITLE_SELECTORS)
    if not title:
        return None

    author = first_author_text(page) or first_nonempty_text(page, AUTHOR_SELECTORS) or "Unknown Author"
    download_selector = resolve_download_selector(page)
    if not download_selector:
        return None

    return SearchMatch(
        title=title,
        author=author,
        detail_url=detail_url,
        download_selector=download_selector,
    )


def find_best_match(query: str) -> SearchMatch:
    log_info("正在检索资源...")

    with sync_playwright() as playwright:
        browser, context = launch_browser_context(
            playwright,
            headless=True,
            use_storage_state=False,
            user_agent=DEFAULT_USER_AGENT,
        )
        page = context.new_page()

        candidates = search_candidates(page, query)
        if not candidates:
            browser.close()
            fail(f"未找到与“{query}”相关的公开 EPUB 资源")

        detail_page = context.new_page()
        try:
            for detail_url in candidates[:8]:
                match = inspect_candidate(detail_page, detail_url)
                if match is not None:
                    return match
        finally:
            browser.close()

    fail(f"未找到与“{query}”相关的可下载 EPUB 资源")


def download_match(match: SearchMatch) -> str:
    file_stem = sanitize_filename(f"{match.author}_{match.title}", default_stem="book")
    log_info("正在获取文件...")
    with sync_playwright() as playwright:
        browser, context = launch_browser_context(
            playwright,
            headless=True,
            use_storage_state=False,
            user_agent=DEFAULT_USER_AGENT,
            accept_downloads=True,
        )
        page = context.new_page()

        try:
            page.goto(match.detail_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            with page.expect_download(timeout=30000) as download_info:
                page.locator(match.download_selector).first.click()
            download = download_info.value
            suggested = sanitize_filename(download.suggested_filename or file_stem, default_stem=file_stem)
            if not suggested.endswith(".epub"):
                suggested = f"{suggested}.epub"
            target_path = unique_path(DOWNLOADS_DIR / suggested)
            download.save_as(str(target_path))
        except Error as exc:
            browser.close()
            fail(f"文件下载失败: {type(exc).__name__}: {exc}")
        except Exception as exc:
            browser.close()
            fail(f"文件下载失败: {type(exc).__name__}: {exc}")

        browser.close()

    relative_path = target_path.relative_to(BASE_DIR)
    log_info(f"获取文件成功：{relative_path}")
    return str(target_path)


def run_seek(query: str) -> None:
    ensure_runtime_dirs()
    normalized_query = query.strip()
    if not normalized_query:
        fail("检索关键词不能为空")

    search_query = QUERY_ALIASES.get(normalized_query, normalized_query)
    if search_query != normalized_query:
        log_info(f"已将检索词标准化为: {search_query}")

    match = find_best_match(search_query)
    log_info(f"已命中资源：{match.title} / {match.author} ({SOURCE_NAME})")
    download_path = download_match(match)
    covered_path = ensure_epub_cover(download_path, title=match.title, author=match.author)
    run_upload(str(covered_path))


def main() -> None:
    args = build_parser().parse_args()
    run_seek(args.query)


if __name__ == "__main__":
    main()
