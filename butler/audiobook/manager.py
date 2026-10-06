"""电子书播放管理器（MVP）。

职责：
1. 书架扫描（NAS 上的 EPUB）
2. 播放控制（play/pause/resume/stop/next/prev）
3. 播放队列（逐段 TTS 合成 + 小爱播放 + 轮询检测结束 → 自动下一段）
4. 进度记忆（段落级，每段播完自动保存）

设计要点：
- 不依赖小爱固件队列（不可靠），butler 自己维护队列
- 轮询 _xiaomi_play_status 检测播放结束
- TTS 复用 rt.tts.synthesize（已有缓存）
- 播放期间全程抑制该设备的 conversation 回声
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from butler.audiobook.epub_parser import Chapter, parse_epub
from butler.logging_setup import get_logger

logger = get_logger("butler.audiobook.manager")


@dataclass
class BookProgress:
    book_id: str
    book_name: str
    file_path: str
    chapter: int = 0
    paragraph: int = 0
    total_chapters: int = 0
    last_played: str = ""

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "book_name": self.book_name,
            "file_path": self.file_path,
            "chapter": self.chapter,
            "paragraph": self.paragraph,
            "total_chapters": self.total_chapters,
            "last_played": self.last_played,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BookProgress":
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class PlayState:
    """当前播放状态。"""
    device_id: str = ""
    book_id: str = ""
    book_name: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    chapter_idx: int = 0
    para_idx: int = 0
    playing: bool = False
    paused: bool = False
    task: asyncio.Task | None = None


class AudiobookManager:
    """单例播放管理器。"""

    def __init__(self, rt, data_dir: str, books_dir: str):
        self.rt = rt
        self.data_dir = Path(data_dir)
        self.books_dir = Path(books_dir)
        self.progress_dir = self.data_dir / "audiobook" / "progress"
        self.cache_dir = self.data_dir / "audiobook" / "cache"
        self.progress_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.state = PlayState()
        self._lock = asyncio.Lock()

    # ---------- 书架 ----------

    def scan_library(self) -> list[dict]:
        """扫描 books_dir 下的 EPUB，返回书籍列表。"""
        books = []
        if not self.books_dir.exists():
            logger.warning("books_dir not found: %s", self.books_dir)
            return books
        for f in sorted(self.books_dir.rglob("*.epub")):
            book_id = hashlib.md5(str(f).encode()).hexdigest()[:8]
            books.append({
                "id": book_id,
                "name": f.stem,
                "path": str(f),
                "size": f.stat().st_size,
            })
        logger.info("audiobook library: %d books", len(books))
        return books

    def find_book(self, name: str) -> dict | None:
        """模糊匹配书名。"""
        books = self.scan_library()
        name_lower = name.lower().strip()
        # 精确匹配
        for b in books:
            if b["name"].lower() == name_lower:
                return b
        # 包含匹配
        for b in books:
            if name_lower in b["name"].lower():
                return b
        return None

    # ---------- 进度 ----------

    def _progress_path(self, book_id: str) -> Path:
        return self.progress_dir / f"{book_id}.json"

    def load_progress(self, book_id: str) -> BookProgress | None:
        p = self._progress_path(book_id)
        if not p.exists():
            return None
        try:
            return BookProgress.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("load progress failed: %s", e)
            return None

    def save_progress(self, prog: BookProgress) -> None:
        prog.last_played = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._progress_path(prog.book_id).write_text(
            json.dumps(prog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 播放控制 ----------

    async def play(self, book_name: str, device_id: str = "") -> dict:
        """开始播放指定书籍（从上次进度继续）。"""
        async with self._lock:
            if self.state.playing and not self.state.paused:
                return {"ok": False, "error": "正在播放中，请先暂停或停止"}

            book = self.find_book(book_name)
            if not book:
                return {"ok": False, "error": f"未找到书籍：{book_name}"}

            # 解析章节
            try:
                chapters = parse_epub(book["path"])
            except Exception as e:
                return {"ok": False, "error": f"EPUB 解析失败：{e}"}

            if not chapters:
                return {"ok": False, "error": "EPUB 无有效章节"}

            # 读取进度
            prog = self.load_progress(book["id"])
            ch_idx = prog.chapter if prog else 0
            para_idx = prog.paragraph if prog else 0
            ch_idx = min(ch_idx, len(chapters) - 1)

            # 确定播放设备
            if not device_id:
                device_id = self._default_device()
            if not device_id:
                return {"ok": False, "error": "未指定播放设备"}

            # 停止当前播放
            await self._stop_internal()

            self.state = PlayState(
                device_id=device_id,
                book_id=book["id"],
                book_name=book["name"],
                chapters=chapters,
                chapter_idx=ch_idx,
                para_idx=para_idx,
                playing=True,
                paused=False,
            )
            self.state.task = asyncio.create_task(self._play_loop())
            logger.info("audiobook play: %s ch=%d para=%d dev=%s",
                        book["name"], ch_idx, para_idx, device_id)
            return {"ok": True, "book": book["name"], "chapter": chapters[ch_idx].title,
                    "device": device_id}

    async def pause(self) -> dict:
        if not self.state.playing:
            return {"ok": False, "error": "没有在播放"}
        self.state.paused = True
        await self._stop_device()
        return {"ok": True, "action": "paused"}

    async def resume(self) -> dict:
        if not self.state.playing or not self.state.paused:
            return {"ok": False, "error": "没有暂停的内容"}
        self.state.paused = False
        # 播放循环会自动继续（轮询检测到 paused=False）
        return {"ok": True, "action": "resumed"}

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_internal()
            return {"ok": True, "action": "stopped"}

    async def next_chapter(self) -> dict:
        if not self.state.playing:
            return {"ok": False, "error": "没有在播放"}
        if self.state.chapter_idx >= len(self.state.chapters) - 1:
            return {"ok": False, "error": "已经是最后一章"}
        self.state.chapter_idx += 1
        self.state.para_idx = 0
        await self._stop_device()  # 停止当前段，播放循环会自动进入下一章
        return {"ok": True, "chapter": self.state.chapters[self.state.chapter_idx].title}

    async def prev_chapter(self) -> dict:
        if not self.state.playing:
            return {"ok": False, "error": "没有在播放"}
        if self.state.chapter_idx <= 0:
            return {"ok": False, "error": "已经是第一章"}
        self.state.chapter_idx -= 1
        self.state.para_idx = 0
        await self._stop_device()
        return {"ok": True, "chapter": self.state.chapters[self.state.chapter_idx].title}

    def status(self) -> dict:
        s = self.state
        if not s.book_id:
            return {"playing": False}
        ch = s.chapters[s.chapter_idx] if s.chapter_idx < len(s.chapters) else None
        return {
            "playing": s.playing and not s.paused,
            "paused": s.paused,
            "book": s.book_name,
            "chapter": ch.title if ch else "",
            "chapter_idx": s.chapter_idx,
            "total_chapters": len(s.chapters),
            "paragraph": s.para_idx,
            "device": s.device_id,
        }

    # ---------- 内部播放循环 ----------

    async def _play_loop(self):
        """核心播放循环：逐段合成 + 播放 + 轮询检测结束。"""
        try:
            while self.state.playing:
                if self.state.paused:
                    await asyncio.sleep(0.5)
                    continue

                # 越界检查
                if self.state.chapter_idx >= len(self.state.chapters):
                    logger.info("audiobook finished: %s", self.state.book_name)
                    break

                chapter = self.state.chapters[self.state.chapter_idx]
                if self.state.para_idx >= len(chapter.paragraphs):
                    # 本章播完，下一章
                    self.state.chapter_idx += 1
                    self.state.para_idx = 0
                    self._save_current_progress()
                    continue

                text = chapter.paragraphs[self.state.para_idx]
                if not text.strip():
                    self.state.para_idx += 1
                    continue

                # TTS 合成（复用 rt.tts，有缓存）
                try:
                    result = await self.rt.tts.synthesize(text, voice="zh-CN-YunjianNeural")
                    if not result or not result.public_url:
                        logger.warning("audiobook TTS failed for para %d", self.state.para_idx)
                        self.state.para_idx += 1
                        continue
                except Exception as e:
                    logger.warning("audiobook TTS error: %s", e)
                    self.state.para_idx += 1
                    await asyncio.sleep(1)
                    continue

                # 播放
                url = result.public_url
                dev = self._get_device(self.state.device_id)
                method = getattr(dev, "xiaomi_play", "music") if dev else "music"
                # 从 HA 实体属性获取 xiaoai_id（mina 设备 ID）
                xiaoai_id = ""
                if dev and self.rt.ha:
                    player_entity = getattr(dev, "ha_player_entity", "")
                    if player_entity:
                        try:
                            xiaoai_id = await self.rt.ha.get_xiaoai_id(player_entity) or ""
                        except Exception as e:
                            logger.warning("audiobook get_xiaoai_id failed: %s", e)

                try:
                    if xiaoai_id and self.rt.ha:
                        rj = await self.rt.ha.play_xiaomi_url_once(xiaoai_id, url, method=method)
                        code = (rj or {}).get("code")
                        if str(code) != "0":
                            # 下发没成功就⛔ 再进 _wait_playback_end：那是对一次根本没开始的播放
                            # 空等满 timeout（默认 300s）再翻段——账实不符，且这一段其实没播。
                            logger.warning("audiobook: 播报下发未成功 code=%s，跳过本段：%s",
                                           code, str(rj)[:200])
                            self.state.para_idx += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        logger.warning("audiobook: no xiaoai_id for device %s", self.state.device_id)
                        self.state.para_idx += 1
                        continue
                except Exception as e:
                    logger.warning("audiobook play error: %s", e)
                    self.state.para_idx += 1
                    await asyncio.sleep(1)
                    continue

                # 轮询检测播放结束
                await self._wait_playback_end(xiaoai_id)

                # 播完一段，更新进度
                self.state.para_idx += 1
                self._save_current_progress()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("audiobook play_loop error: %s", e, exc_info=True)
        finally:
            self.state.playing = False
            self.state.task = None

    async def _wait_playback_end(self, xiaoai_id: str, timeout: int = 300):
        """轮询检测播放结束（position 不再前进 / status=2 / pos 重置循环）。"""
        if not self.rt.ha:
            await asyncio.sleep(3)
            return

        prev_pos = -1
        stable_count = 0
        unknown_count = 0
        start = time.time()
        max_pos_seen = 0

        while time.time() - start < timeout:
            if not self.state.playing or self.state.paused:
                return
            try:
                pos, dur, status = await self.rt.ha._xiaomi_play_status(xiaoai_id)
            except Exception:
                await asyncio.sleep(0.5)
                continue

            # 状态整体读不到（status=None 且 pos/dur 全 0）＝设备/云端没答，⛔ 与"还在播"同形：
            # 连续三次就收手，⛔ 把 timeout 烧满后再翻段（播报失败时这里是纯空转）。
            if status is None and pos == 0 and dur == 0:
                unknown_count += 1
                if unknown_count >= 3:
                    logger.warning("audiobook: 连续 %d 次读不到播放状态（device=%s），提前收手，⛔ 等满超时",
                                   unknown_count, xiaoai_id)
                    return
            else:
                unknown_count = 0

            # status=2 表示停止
            if status == 2:
                await asyncio.sleep(0.3)
                return

            # pos 突然大幅回退（>2秒）→ 设备开始循环播放，立即判定结束
            if prev_pos > 2000 and pos < prev_pos - 2000:
                logger.info("audiobook: pos reset %d→%d, loop detected, end", prev_pos, pos)
                return

            # 到达音频末尾附近（最后1秒内）→ 等0.5秒后判定结束
            if dur > 0 and pos >= dur - 1000:
                await asyncio.sleep(0.5)
                return

            # position 不再前进（连续3次相同）→ 播放结束
            if pos == prev_pos and pos > 0:
                stable_count += 1
                if stable_count >= 3:
                    return
            else:
                stable_count = 0
            prev_pos = pos
            if pos > max_pos_seen:
                max_pos_seen = pos

            await asyncio.sleep(0.5)

        logger.warning("audiobook playback timeout after %ds", timeout)

    async def _stop_device(self):
        """停止当前设备播放。"""
        dev = self._get_device(self.state.device_id)
        xiaoai_id = ""
        if dev and self.rt.ha:
            player_entity = getattr(dev, "ha_player_entity", "")
            if player_entity:
                try:
                    xiaoai_id = await self.rt.ha.get_xiaoai_id(player_entity) or ""
                except Exception as e:
                    # P2-9 ②类（批42 组6）：id 留空＝下面那枚停播分支整段跳过，音箱继续播，⛔ 再无声
                    logger.warning("audiobook stop device: get_xiaoai_id failed [%s]: %s", player_entity, e)
        if xiaoai_id and self.rt.ha:
            try:
                await self.rt.ha._xiaomi_stop_playback(xiaoai_id)
            except Exception as e:
                logger.warning("audiobook stop device error: %s", e)

    async def _stop_internal(self):
        if self.state.task and not self.state.task.done():
            self.state.task.cancel()
            try:
                await self.state.task
            except (asyncio.CancelledError, Exception):
                pass
        await self._stop_device()
        self.state.playing = False
        self.state.paused = False
        self.state.task = None

    def _save_current_progress(self):
        if not self.state.book_id or not self.state.chapters:
            return
        prog = BookProgress(
            book_id=self.state.book_id,
            book_name=self.state.book_name,
            file_path="",
            chapter=self.state.chapter_idx,
            paragraph=self.state.para_idx,
            total_chapters=len(self.state.chapters),
        )
        # 从书架找 file_path
        for b in self.scan_library():
            if b["id"] == self.state.book_id:
                prog.file_path = b["path"]
                break
        self.save_progress(prog)

    def _default_device(self) -> str:
        """默认播放设备：客厅左小爱。"""
        if self.rt and self.rt.devices:
            for did, dev in self.rt.devices.devices.items():
                if getattr(dev, "room", "") == "客厅" and getattr(dev, "type", "") == "xiaomi":
                    return did
        return ""

    def _get_device(self, device_id: str):
        if self.rt and self.rt.devices:
            return self.rt.devices.devices.get(device_id)
        return None
