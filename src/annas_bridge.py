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
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
    except Error as exc:
        fail(f"公开书源搜索页打开失败: {type(exc).__name__}: {exc}")

    try:
        # 获取页面所有 /md5/ 链接及其标题
        # 我们寻找 main 区域内的链接，并排除 "Recent downloads" 部分
        raw_data = page.evaluate(
            """
            () => {
                const results = [];
                // 尝试定位 "Results" 标题后的区域，或者直接在 main 中查找
                const main = document.querySelector('main') || document.body;
                const links = Array.from(main.querySelectorAll('a[href^="/md5/"]'));
                
                // 排除可能是 "Recent downloads" 的容器
                const isRecentDownload = (el) => {
                    let p = el.parentElement;
                    while (p && p !== main) {
                        if (p.innerText && p.innerText.includes('Recent downloads')) return true;
                        p = p.parentElement;
                    }
                    return false;
                };

                links.forEach(el => {
                    if (isRecentDownload(el)) return;
                    results.push({
                        href: el.getAttribute('href'),
                        title: (el.innerText || '').trim()
                    });
                });
                return results;
            }
            """
        )
    except Error as exc:
        fail(f"公开书源搜索结果读取失败: {type(exc).__name__}: {exc}")

    if not raw_data:
        # 如果没找到，尝试最宽松的全局查找
        raw_data = page.locator('a[href^="/md5/"]').evaluate_all(
            "els => els.map(el => ({ href: el.getAttribute('href'), title: (el.innerText || '').trim() }))"
        )

    results: list[str] = []
    seen: set[str] = set()
    found_debug: list[str] = []
    
    query_words = [w.lower() for w in re.split(r"\s+", query) if w]

    for item in raw_data:
        href = item["href"]
        title = item["title"]
        if not href or not DETAIL_LINK_PATTERN.match(href):
            continue
        
        found_debug.append(f"{title} ({href})")
        
        # 宽松匹配策略：
        # 1. 相似度 > 0.4
        # 2. 或者 标题包含查询词中的任何一个关键词
        similarity = difflib.SequenceMatcher(None, query.lower(), title.lower()).ratio()
        word_match = any(word in title.lower() for word in query_words)
        
        if similarity < 0.4 and not word_match:
            continue

        absolute = urljoin(page.url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append(absolute)

    if not results and found_debug:
        log_info("未发现匹配项，在此列出找到的 MD5 链接供排查：")
        for debug_str in found_debug[:5]:
            log_info(f"  - {debug_str}")

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
    log_info(f"正在准备从 {match.source_name} 获取文件...")
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
            # 第一步：进入下载镜像详情或慢速下载页
            page.goto(match.download_url, wait_until="domcontentloaded", timeout=60000)
            
            # 检查是否进入了中间的 slow_download 页面
            if "/slow_download/" in page.url:
                log_info("正在排队等候慢速通道响应 (可能需要 20-60 秒)...")
                # 等待“Download now”按钮出现，通常伴随着倒计时结束
                # 按钮文本通常包含 "Download now" 或 图标 📚
                final_btn_selector = "a:has-text('Download now'), a:has-text('下载'), a:has-text('📚')"
                try:
                    page.wait_for_selector(final_btn_selector, state="visible", timeout=180000)
                    download_btn = page.locator(final_btn_selector).first
                except Error:
                    fail("等候超时：慢速下载按钮未能在 180 秒内出现。")
            else:
                # 可能是直接下载页或有其他“Click here”按钮
                download_btn = page.locator("a:has-text('Click here'), a:has-text('下载'), a:has-text('download'), a:has-text('Download now')").first

            # 触发真实文件流下载
            with page.expect_download(timeout=180000) as download_info:
                if download_btn.count() > 0:
                    download_btn.click()
                else:
                    # 如果没找到按钮但也没触发下载，说明页面逻辑可能有变
                    log_info("未发现显式下载按钮，尝试静默等待下载触发...")
            
            download = download_info.value
            suggested = sanitize_filename(download.suggested_filename or file_stem, default_stem=file_stem)
            if not suggested.endswith(".epub"):
                suggested = f"{suggested}.epub"
            target_path = unique_path(DOWNLOADS_DIR / suggested)
            download.save_as(str(target_path))
        except Error as exc:
            browser.close()
            fail(f"通道连接或等候超时: {type(exc).__name__}: {exc}")
        except Exception as exc:
            browser.close()
            fail(f"下载流处理失败: {type(exc).__name__}: {exc}")

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
