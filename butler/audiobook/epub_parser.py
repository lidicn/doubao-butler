"""EPUB 解析：提取章节标题和正文文本。

依赖：ebooklib + beautifulsoup4 + lxml
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub


@dataclass
class Chapter:
    index: int
    title: str
    content: str
    paragraphs: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.paragraphs and self.content:
            self.paragraphs = split_paragraphs(self.content)


def split_paragraphs(text: str, max_len: int = 300) -> list[str]:
    """把章节正文切成适合 TTS 的段落。

    优先按自然段（换行/句号/问号/叹号）切，每段不超过 max_len 字。
    """
    # 先按换行切
    raw = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    result = []
    for p in raw:
        if len(p) <= max_len:
            result.append(p)
        else:
            # 长段落按句子切
            sentences = re.split(r'(?<=[。！？!?；;])', p)
            buf = ""
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(buf) + len(s) <= max_len:
                    buf += s
                else:
                    if buf:
                        result.append(buf)
                    buf = s
            if buf:
                result.append(buf)
    return result


def parse_epub(filepath: str | Path) -> list[Chapter]:
    """解析 EPUB，返回章节列表（按阅读顺序）。"""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"EPUB not found: {path}")

    book = epub.read_epub(str(path))

    # 用 spine 顺序（阅读顺序），而不是 get_items_of_type 的任意顺序
    spine_ids = [item[0] for item in book.spine]
    id_to_item = {item.get_id(): item for item in book.get_items()}

    chapters: list[Chapter] = []
    idx = 0
    for sid in spine_ids:
        item = id_to_item.get(sid)
        if item is None:
            continue
        # 只处理 XHTML/HTML 文档
        media_type = getattr(item, "media_type", "") or ""
        if media_type not in ("application/xhtml+xml", "text/html", "application/xml"):
            continue

        try:
            soup = BeautifulSoup(item.get_content(), "lxml")
        except Exception:
            soup = BeautifulSoup(item.get_content(), "html.parser")

        # 移除 script/style
        for tag in soup(["script", "style"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        if not text or len(text) < 200:
            # 跳过封面、目录、版权页等短章节
            continue

        # 标题：优先 h1/h2/h3，否则用第一个非空行
        title = ""
        for h in soup.find_all(["h1", "h2", "h3"]):
            t = h.get_text(strip=True)
            if t:
                title = t
                break
        if not title:
            first_line = text.split("\n")[0][:40]
            title = first_line

        chapters.append(Chapter(index=idx, title=title, content=text))
        idx += 1

    return chapters


def get_book_info(filepath: str | Path) -> dict:
    """获取书籍元信息（书名、作者、章节数）。"""
    path = Path(filepath)
    book = epub.read_epub(str(path))
    title = ""
    author = ""
    try:
        title = book.get_metadata("DC", "title")[0][0]
    except Exception:
        title = path.stem
    try:
        author = book.get_metadata("DC", "creator")[0][0]
    except Exception:
        author = "未知"
    chapters = parse_epub(path)
    return {
        "title": title,
        "author": author,
        "chapters": len(chapters),
        "file_path": str(path),
        "file_size": path.stat().st_size,
    }
