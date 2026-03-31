from __future__ import annotations

import argparse
import re
import difflib
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Error, sync_playwright
from bs4 import BeautifulSoup

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

BASE_URL = "https://annas-archive.gl"
SEARCH_URL_TEMPLATE = f"{BASE_URL}/search?q={{query}}&ext=epub"
SOURCE_NAME = "Anna's Archive"
DETAIL_LINK_PATTERN = re.compile(r"^/md5/[0-9a-f]{32}$")

TITLE_SELECTORS = ["h1", ".line-clamp-3", ".text-3xl"]
AUTHOR_SELECTORS = [".italic", ".text-lg.italic"]
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
    download_url: str
    source_name: str
    file_size: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过公开书源检索并获取 EPUB")
    parser.add_argument("query", help="检索关键词")
    return parser


def extract_download_links(html_content: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_content, "html.parser")
    results = []

    for a_tag in soup.find_all("a", href=True):
        text = a_tag.get_text(separator=" ", strip=True)
        if ".epub" in text.lower():
            href = a_tag["href"]
            url = urljoin(BASE_URL, href)

            source = "Unknown"
            size = "Unknown"

            match = re.search(r"^(.*?)\s*\(\.epub,\s*(.*?)\)", text, re.IGNORECASE)
            if match:
                raw_source = match.group(1).strip()
                source = re.sub(r"^Option\s*#\d+:\s*", "", raw_source, flags=re.IGNORECASE).strip()
                size = match.group(2).strip()
            else:
                if "(" in text:
                    source = text.split("(")[0].strip()
                    source = re.sub(r"^Option\s*#\d+:\s*", "", source, flags=re.IGNORECASE).strip()
                size_match = re.search(r"(\d+(?:\.\d+)?\s*[KMG]B)", text, re.IGNORECASE)
                if size_match:
                    size = size_match.group(1)

            results.append({"url": url, "source": source, "size": size})
    return results


def search_candidates(page, query: str) -> list[str]:
    search_url = SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Error as exc:
        fail(f"公开书源搜索页打开失败: {type(exc).__name__}: {exc}")

    try:
        # 仅限定在主搜索结果容器中提取，避免抓取顶部的“近期热门”或侧边栏
        # AA 的主结果通常在 .flex.flex-col.gap-4 容器下
        container = page.locator(".flex.flex-col.gap-4").first
        if container.count() == 0:
            # 回退到全局查找，但限制在结果项中
            items = page.locator(".flex.pt-3.pb-3")
        else:
            items = container.locator(".flex.pt-3.pb-3")

        raw_data = items.evaluate_all(
            """
            els => els.map(el => {
                const link = el.querySelector('a[href^="/md5/"]');
                return {
                    href: link ? link.getAttribute('href') : null,
                    title: link ? (link.innerText || '').trim() : ''
                };
            }).filter(item => item.href)
            """
        )
    except Error as exc:
        fail(f"公开书源搜索结果读取失败: {type(exc).__name__}: {exc}")

    results: list[str] = []
    seen: set[str] = set()
    for item in raw_data:
        href = item["href"]
        title = item["title"]
        if not DETAIL_LINK_PATTERN.match(href):
            continue
        
        # 标题相似度校验：过滤掉与搜索词完全不相关的推荐内容
        similarity = difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()
        if similarity < 0.4:
            continue

        absolute = urljoin(page.url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append(absolute)

    return results


def inspect_candidate(page, detail_url: str) -> SearchMatch | None:
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Error:
        return None

    # 获取标题和作者（由于AA结构多变，这里简单尝试几个选择器）
    title = page.locator("h1").first.inner_text(timeout=2000).strip() or "Unknown Title"
    author = "Unknown Author"
    author_loc = page.locator(".italic").first
    if author_loc.count() > 0:
        author = author_loc.inner_text(timeout=1000).strip()

    content = page.content()
    options = extract_download_links(content)
    if not options:
        return None

    # 优先级策略：Cloudflare > IPFS > 其他
    best_option = None
    for opt in options:
        src = opt["source"].lower()
        if "cloudflare" in src:
            best_option = opt
            break
    if not best_option:
        for opt in options:
            if "ipfs" in opt["source"].lower():
                best_option = opt
                break
    if not best_option:
        best_option = options[0]

    return SearchMatch(
        title=title,
        author=author,
        detail_url=detail_url,
        download_url=best_option["url"],
        source_name=best_option["source"],
        file_size=best_option["size"],
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
    log_info(f"正在从 {match.source_name} 获取文件 ({match.file_size})...")
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
            # AA 下载通常需要点击后等待重定向或直接下载
            page.goto(match.download_url, wait_until="domcontentloaded", timeout=45000)
            
            # 如果页面上有“Click here to download”之类的文字，尝试点击
            download_btn = page.locator("a:has-text('Click here'), a:has-text('下载'), a:has-text('download'), a:has-text('Download now')").first
            
            # AA 免费/慢速通道通常有很长的准备时间或验证，这里将超时增加到 180s
            with page.expect_download(timeout=180000) as download_info:
                if download_btn.count() > 0:
                    download_btn.click()
                else:
                    # 有些链接是直接触发下载的，或者是点击后还需要二次确认
                    pass
            
            download = download_info.value
            suggested = sanitize_filename(download.suggested_filename or file_stem, default_stem=file_stem)
            if not suggested.endswith(".epub"):
                suggested = f"{suggested}.epub"
            target_path = unique_path(DOWNLOADS_DIR / suggested)
            download.save_as(str(target_path))
        except Error as exc:
            browser.close()
            fail(f"文件下载过程中出错: {type(exc).__name__}: {exc}")
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
