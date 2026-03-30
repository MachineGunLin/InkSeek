from __future__ import annotations

from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from ebooklib import ITEM_COVER, epub
from PIL import Image, ImageDraw, ImageFont

from utils import log_info

MIN_COVER_WIDTH = 480
MIN_COVER_HEIGHT = 640
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 2560
BACKGROUND_TOP = (240, 232, 214)
BACKGROUND_BOTTOM = (218, 201, 170)
TEXT_PRIMARY = (34, 31, 28)
TEXT_SECONDARY = (86, 76, 64)


def _load_font(size: int):
    for name in ("Georgia.ttf", "Times New Roman.ttf", "Songti.ttc", "STSong.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _iter_cover_items(book: epub.EpubBook):
    for item in book.get_items():
        if item.get_type() == ITEM_COVER:
            yield item
            continue
        name = (item.get_name() or "").lower()
        if "cover" in name and name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            yield item


def _cover_quality_ok(book: epub.EpubBook) -> bool:
    for item in _iter_cover_items(book):
        try:
            image = Image.open(BytesIO(item.get_content()))
            width, height = image.size
            if width >= MIN_COVER_WIDTH and height >= MIN_COVER_HEIGHT:
                return True
        except Exception:
            continue
    return False


def _normalize_toc_entry(entry, counter: list[int]):
    if isinstance(entry, epub.Link):
        uid = entry.uid or f"toc-link-{counter[0]}"
        counter[0] += 1
        return epub.Link(entry.href, entry.title, uid)

    if isinstance(entry, tuple) and len(entry) == 2:
        head, children = entry
        normalized_children = [_normalize_toc_entry(child, counter) for child in children]
        normalized_children = [child for child in normalized_children if child is not None]
        if isinstance(head, epub.Section):
            return (epub.Section(head.title, head.href), normalized_children)
        if isinstance(head, epub.Link):
            return (_normalize_toc_entry(head, counter), normalized_children)

    file_name = getattr(entry, "file_name", None)
    title = getattr(entry, "title", "") or getattr(entry, "get_name", lambda: "")()
    if file_name:
        uid = getattr(entry, "uid", None) or getattr(entry, "id", None) or f"toc-item-{counter[0]}"
        counter[0] += 1
        return epub.Link(file_name, title, uid)

    return None


def _normalize_book_for_write(book: epub.EpubBook) -> None:
    counter = [1]
    normalized = [_normalize_toc_entry(entry, counter) for entry in book.toc]
    normalized = [entry for entry in normalized if entry is not None]
    if normalized:
        book.toc = tuple(normalized)


def _read_opf_path(source: zipfile.ZipFile) -> str:
    container_xml = source.read("META-INF/container.xml")
    root = ET.fromstring(container_xml)
    namespace = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//c:rootfile", namespace)
    if rootfile is None:
        raise ValueError("未找到 EPUB container rootfile")
    opf_path = rootfile.attrib.get("full-path", "").strip()
    if not opf_path:
        raise ValueError("EPUB rootfile 路径为空")
    return opf_path


def _inject_cover_assets(path: Path, cover_bytes: bytes) -> None:
    with zipfile.ZipFile(path, "r") as source:
        opf_path = _read_opf_path(source)
        opf_bytes = source.read(opf_path)
        root = ET.fromstring(opf_bytes)
        namespace_uri = root.tag[root.tag.find("{") + 1 : root.tag.find("}")]
        namespace = {"opf": namespace_uri}
        ET.register_namespace("", namespace_uri)

        opf_dir = PurePosixPath(opf_path).parent
        cover_image_name = "inkseek-cover.jpg"
        cover_page_name = "inkseek-cover.xhtml"
        cover_image_path = str(opf_dir / cover_image_name) if str(opf_dir) != "." else cover_image_name
        cover_page_path = str(opf_dir / cover_page_name) if str(opf_dir) != "." else cover_page_name

        metadata = root.find("opf:metadata", namespace)
        manifest = root.find("opf:manifest", namespace)
        spine = root.find("opf:spine", namespace)
        if metadata is None or manifest is None or spine is None:
            raise ValueError("EPUB 结构不完整，缺少 metadata/manifest/spine")

        cover_image_id = "inkseek-cover-image"
        cover_page_id = "inkseek-cover-page"

        def qname(tag: str) -> str:
            return f"{{{namespace_uri}}}{tag}"

        image_item = manifest.find(f"opf:item[@id='{cover_image_id}']", namespace)
        if image_item is None:
            image_item = ET.SubElement(manifest, qname("item"))
        image_item.set("id", cover_image_id)
        image_item.set("href", cover_image_name)
        image_item.set("media-type", "image/jpeg")
        image_item.set("properties", "cover-image")

        page_item = manifest.find(f"opf:item[@id='{cover_page_id}']", namespace)
        if page_item is None:
            page_item = ET.SubElement(manifest, qname("item"))
        page_item.set("id", cover_page_id)
        page_item.set("href", cover_page_name)
        page_item.set("media-type", "application/xhtml+xml")

        meta = metadata.find("opf:meta[@name='cover']", namespace)
        if meta is None:
            meta = ET.SubElement(metadata, qname("meta"))
        meta.set("name", "cover")
        meta.set("content", cover_image_id)

        first_itemref = spine.find(f"opf:itemref[@idref='{cover_page_id}']", namespace)
        if first_itemref is None:
            itemref = ET.Element(qname("itemref"))
            itemref.set("idref", cover_page_id)
            spine.insert(0, itemref)

        cover_page = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Cover</title>
    <style type="text/css">
      html, body { margin: 0; padding: 0; }
      body { text-align: center; }
      img { width: 100%%; height: auto; }
    </style>
  </head>
  <body>
    <img src="%s" alt="Cover" />
  </body>
</html>
""" % cover_image_name

        with tempfile.NamedTemporaryFile(prefix="inkseek_cover_zip_", suffix=".epub", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        with zipfile.ZipFile(temp_path, "w") as target:
            for info in source.infolist():
                if info.filename in {opf_path, cover_image_path, cover_page_path}:
                    continue
                target.writestr(info, source.read(info.filename))

            target.writestr(opf_path, ET.tostring(root, encoding="utf-8", xml_declaration=True))
            target.writestr(cover_image_path, cover_bytes)
            target.writestr(cover_page_path, cover_page.encode("utf-8"))

    temp_path.replace(path)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = [part for part in text.replace("\n", " ").split(" ") if part]
    if not words:
        return []

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


def _build_cover_image(title: str, author: str) -> bytes:
    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_TOP)
    draw = ImageDraw.Draw(image)

    for row in range(CANVAS_HEIGHT):
        ratio = row / max(CANVAS_HEIGHT - 1, 1)
        color = tuple(
            int(BACKGROUND_TOP[index] * (1 - ratio) + BACKGROUND_BOTTOM[index] * ratio)
            for index in range(3)
        )
        draw.line([(0, row), (CANVAS_WIDTH, row)], fill=color)

    draw.rectangle(
        [(120, 120), (CANVAS_WIDTH - 120, CANVAS_HEIGHT - 120)],
        outline=(255, 255, 255),
        width=6,
    )
    draw.rectangle(
        [(170, 170), (CANVAS_WIDTH - 170, CANVAS_HEIGHT - 170)],
        outline=(120, 108, 90),
        width=2,
    )

    title_font = _load_font(112)
    author_font = _load_font(54)
    label_font = _load_font(42)

    draw.text((180, 240), "INKSEEK EDITION", font=label_font, fill=TEXT_SECONDARY)

    max_text_width = CANVAS_WIDTH - 360
    title_lines = _wrap_text(draw, title.strip() or "Untitled", title_font, max_text_width)
    if not title_lines:
        title_lines = ["Untitled"]

    cursor_y = 520
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_width = bbox[2] - bbox[0]
        draw.text(((CANVAS_WIDTH - line_width) / 2, cursor_y), line, font=title_font, fill=TEXT_PRIMARY)
        cursor_y += 160

    author_text = author.strip() or "Unknown Author"
    author_bbox = draw.textbbox((0, 0), author_text, font=author_font)
    author_width = author_bbox[2] - author_bbox[0]
    draw.text(
        ((CANVAS_WIDTH - author_width) / 2, CANVAS_HEIGHT - 420),
        author_text,
        font=author_font,
        fill=TEXT_SECONDARY,
    )

    draw.line(
        [(260, CANVAS_HEIGHT - 520), (CANVAS_WIDTH - 260, CANVAS_HEIGHT - 520)],
        fill=(126, 112, 91),
        width=3,
    )

    output = BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


def ensure_epub_cover(file_path: str | Path, *, title: str = "", author: str = "") -> Path:
    path = Path(file_path).resolve()
    if path.suffix.lower() != ".epub":
        return path

    log_info("正在检查 EPUB 封面...")
    try:
        book = epub.read_epub(str(path))
    except Exception as exc:
        log_info(f"封面检查失败，跳过封面补齐: {type(exc).__name__}: {exc}")
        return path

    if _cover_quality_ok(book):
        log_info("现有封面可用，跳过封面补齐。")
        return path

    cover_bytes = _build_cover_image(title=title, author=author)
    cover_name = "cover.jpg"
    try:
        book.set_cover(cover_name, cover_bytes, create_page=True)
    except TypeError:
        book.set_cover(cover_name, cover_bytes)

    try:
        _inject_cover_assets(path, cover_bytes)
    except Exception as exc:
        log_info(f"封面补齐失败，跳过封面注入: {type(exc).__name__}: {exc}")
        return path

    log_info("封面补齐完成。")
    return path
