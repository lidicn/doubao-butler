"""DCD 裁定③（优先级 TTL 落地 + 过期要可查）的落码器：3 个文件、逐处锚点、施加前后各量一次字节血统。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:44-52`。
落点从 `guard/push_guard.py` 挪到 `tts/queue.py`：现网真正会让一句话排队等待的是这条队列
（`notify/router.py:283 → tts_queue.enqueue`），而 guard 的 `_tts_queue` 被 `api/tts_routes.py:280/292`
「播完再 tts_pop()」1:1 排空，条目在里面**不产生年龄** ⇒ 过期分支写在那侧＝再造一条假承诺。
数值仍然只有裁定表里那三个（0／1800／300），⛔ 新增语义。

⛔ 手写 patch／⛔ `git diff --no-index` 当补丁源（抹 CR）⇒ 这里只做「整段唯一锚点字符串替换」，
   每处锚点必须**恰好命中 1 次**，命中 0 次或 >1 次都直接抛错、不落盘。
基线（现量于权威树 /vol1/1000/docker/doubao-butler，HEAD 9aa5862；`md5sum` + `wc -l -c` + `tail -c 1 | od -c`）：
  butler/tts/queue.py        md5=27396bbe09f887f9ac31402cb0859097 lines=570 bytes=23338 endnl=False
  butler/guard/push_guard.py md5=38304cb64d36918e15e8558aa8b7ca15 lines=401 bytes=17032 endnl=True
  butler/tts/singleton.py    md5=fdad587fda831f1091c9d9229d235d7e lines=42  bytes=1589  endnl=True
`lines` 取的是本器 `_stats` 的口径（`splitlines(True)` 计数）：queue.py 的 `wc -l` 是 569，
差的那 1 行就是**没有末行换行符的最后一行**——`wc -l` 数换行符，本器数行。
`queue.py` **本来就⛔ 末行换行符**（末字节是 `)`）⇒ 施加后必须还是 False。

用法：DRY（默认）＝只验锚点并打印将要发生的字节数变化；`--apply`＝落盘。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path("/vol1/1000/docker/doubao-butler")

BASE = {
    "butler/tts/queue.py":        ("27396bbe09f887f9ac31402cb0859097", 570, 23338, False),
    "butler/guard/push_guard.py": ("38304cb64d36918e15e8558aa8b7ca15", 401, 17032, True),
    "butler/tts/singleton.py":    ("fdad587fda831f1091c9d9229d235d7e", 42, 1589, True),
}


def L(*lines: str) -> str:
    return "\n".join(lines)


EDIT = {
    "butler/tts/queue.py": [
        # 1) 文件头那行 TTL 口径：默认 300s ⇒ 档位表
        (
            "- TTL：到期丢弃（默认 300s；ttl_s<=0 表示不设 TTL）",
            "- TTL：到期丢弃（按档位：告警不过期／高优 1800s／标准 300s，见 PRIORITY_TTL_S；ttl_s<=0 表示不设 TTL）",
        ),
        # 2) 档位 → TTL / → 风控词表（单一真源）
        (
            "PRIORITY_NORMAL = 3     # 标准：入队尾（3/4/5 同带）",
            L(
                "PRIORITY_NORMAL = 3     # 标准：入队尾（3/4/5 同带）",
                "",
                "# ── 档位 → TTL（DCD 裁定③ 20261002-DB六件影子代码-裁定.md:44-52）──",
                "# 为什么这张表挂在本模块、⛔ 留在 guard/push_guard.py：",
                "#   那里原有 `PRIORITY_TTL`（critical 0／warning 1800／info 300）全树引用只有定义行本身",
                "#   ＝裁定点名的「空号」；而 guard 的 `_tts_queue` 在现网是被 `api/tts_routes.py:280/292`",
                "#   「播完再 tts_pop()」1:1 排空的计数器，条目在里面**不产生年龄** ⇒ 过期分支写在那侧只会",
                "#   多一条没人走的路。真正让一句话排队等待的是本模块这条队列（`notify/router.py:283`）。",
                "# 数值⛔ 改动，仍是裁定表里那三个；TTL 从**入队（＝进风控）那一刻**起算（`created_at`），",
                "# ⛔「播报失败重投」起算——失败风暴下永不到期，能力就没了（裁定原文）。",
                "PRIORITY_TTL_S: dict[int, float] = {",
                "    PRIORITY_ALERT: 0.0,        # 告警不过期",
                "    PRIORITY_HIGH: 1800.0,      # 30 分钟",
                "    PRIORITY_NORMAL: 300.0,     # 5 分钟（4/5 同带）",
                "}",
                "# 档位 → 风控词表：审计行的 priority 要与 `push_audit` 里既有那套词同一份口径。",
                "PRIORITY_LEVEL_NAME: dict[int, str] = {",
                "    PRIORITY_ALERT: \"critical\",",
                "    PRIORITY_HIGH: \"warning\",",
                "    PRIORITY_NORMAL: \"info\",",
                "}",
                "",
                "",
                "def _band_key(priority: int) -> int:",
                "    \"\"\"4/5 归标准带：与 `_band()` 同一条分带口径。\"\"\"",
                "    if priority == PRIORITY_ALERT:",
                "        return PRIORITY_ALERT",
                "    if priority == PRIORITY_HIGH:",
                "        return PRIORITY_HIGH",
                "    return PRIORITY_NORMAL",
                "",
                "",
                "def ttl_for_priority(priority: int) -> float:",
                "    \"\"\"档位默认 TTL（秒）；0 或负＝不过期。\"\"\"",
                "    return PRIORITY_TTL_S[_band_key(priority)]",
                "",
                "",
                "def level_name_for(priority: int) -> str:",
                "    \"\"\"档位 → critical/warning/info（审计行用，⛔ 在别处再造一份词表）。\"\"\"",
                "    return PRIORITY_LEVEL_NAME[_band_key(priority)]",
            ),
        ),
        # 3) config：档位表成为**可改默认值**，⛔ 硬编码
        (
            "    ttl_s: float = 300.0",
            L(
                "    ttl_s: float = 300.0",
                "    # DCD 裁定③：档位默认 TTL。这里给的是 PRIORITY_TTL_S 的一份可改副本——",
                "    # 要裁数值（比如把高优改成 900s）改配置即可，⛔ 动代码。",
                "    ttl_by_priority: dict[int, float] = field(default_factory=lambda: dict(PRIORITY_TTL_S))",
            ),
        ),
        (
            L(
                "    def volume_for(self, priority: int) -> int:",
                "        return int(self.volume_by_priority.get(priority, self.default_volume))",
            ),
            L(
                "    def volume_for(self, priority: int) -> int:",
                "        return int(self.volume_by_priority.get(priority, self.default_volume))",
                "",
                "    def ttl_for(self, priority: int) -> float:",
                "        \"\"\"档位 TTL；表里没登记的档回落 `ttl_s`（部分覆盖配置时不至于把别的档清零）。\"\"\"",
                "        key = _band_key(priority)",
                "        if key in self.ttl_by_priority:",
                "            return float(self.ttl_by_priority[key])",
                "        return float(self.ttl_s)",
            ),
        ),
        # 4) 构造期注入审计腿（与 on_overload 同一形态）
        (
            "        on_overload: Callable[[str, str], Any] | None = None,",
            "        on_overload: Callable[[str, str], Any] | None = None,\n"
            "        on_expired: Callable[[TTSItem, float], Any] | None = None,",
        ),
        (
            "        self.on_overload = on_overload       # async 或 sync 均可：(text, room)",
            "        self.on_overload = on_overload       # async 或 sync 均可：(text, room)\n"
            "        self.on_expired = on_expired         # DCD 裁定③：(item, now) 过期审计腿",
        ),
        # 5) TTL 进档位：优先级此前在这条接缝上被丢掉（与裁定②的 item.priority 同一形状）
        (
            "        ttl = self.config.ttl_s if item.ttl_s is None else float(item.ttl_s)",
            "        # 调用方显式给的 ttl_s 仍然优先：本批是「接上档位默认」，⛔ 改写调用方的选择。\n"
            "        ttl = self.config.ttl_for(item.priority) if item.ttl_s is None else float(item.ttl_s)",
        ),
        # 6) 过期分支：一条计数 ⇒ 一行审计（计数保留，面板在读）
        (
            L(
                "        keep: deque[TTSItem] = deque()",
                "        dropped = 0",
                "        for it in self._q:",
                "            if it.expires_at <= now:",
                "                dropped += 1",
                "            else:",
                "                keep.append(it)",
                "        if dropped:",
                "            self._q = keep",
                "            self._dropped[\"expired\"] += dropped",
                "        return dropped",
            ),
            L(
                "        keep: deque[TTSItem] = deque()",
                "        expired: list[TTSItem] = []",
                "        for it in self._q:",
                "            if it.expires_at <= now:",
                "                expired.append(it)",
                "            else:",
                "                keep.append(it)",
                "        if expired:",
                "            self._q = keep",
                "            self._dropped[\"expired\"] += len(expired)",
                "            for it in expired:",
                "                self._note_expired(it, now)",
                "        return len(expired)",
                "",
                "    def _note_expired(self, item: TTSItem, now: float) -> None:",
                "        \"\"\"DCD 裁定③「让『为什么没响』可查」：一条过期＝一行审计，⛔ 只留一个计数。",
                "",
                "        审计腿自身出错一律不影响播放队列（fail-open，与裁定② `modes/engine.py::_gate` 同一份政策）。",
                "        \"\"\"",
                "        age = now - (item.created_at or now)",
                "        logger.info(\"TTS_EXPIRED job=%s trace=%s P%d age=%.0fs text=%.30s\",",
                "                    item.job_id or \"-\", item.trace_id or \"-\",",
                "                    item.priority, age, item.text)",
                "        if self.on_expired is None:",
                "            return",
                "        try:",
                "            self.on_expired(item, now)",
                "        except Exception as e:",
                "            logger.warning(\"TTS TTL 审计失败（不阻塞队列）: %s: %s\", type(e).__name__, e)",
            ),
        ),
    ],
    "butler/guard/push_guard.py": [
        (
            "  L4 TTL 过期：按优先级设置不同 TTL",
            "  L4 TTL 过期：执行在真正排队的 butler/tts/queue.py（PRIORITY_TTL_S），本层⛔ 重复实现",
        ),
        (
            L(
                "# 优先级 → TTL（秒），critical 不过期",
                "PRIORITY_TTL = {",
                "    PRIORITY_CRITICAL: 0,  # 0 = 不过期",
                "    PRIORITY_WARNING: 1800,  # 30 分钟",
                "    PRIORITY_INFO: 300,  # 5 分钟",
                "}",
            ),
            L(
                "# 优先级 → TTL 那张表已拆（DCD 裁定③ 20261002:44-52 点名的「空号」：全树引用只有定义行本身）。",
                "# 处置＝「接上」，但接在有人走的那条队列上：`butler/tts/queue.py::PRIORITY_TTL_S`",
                "# （本层的 `_tts_queue` 被 `api/tts_routes.py:280/292` 播完再 pop，1:1 排空，条目在这里不产生年龄；",
                "#   两套队列的去留＝裁定⑤，本批不占）。",
                "# 本层留下的那条腿是 `record_ttl_drop()`：队列侧每次过期往 `push_audit` 写一行，让「为什么没响」可查。",
            ),
        ),
        (
            "DECISION_DROP = \"drop\"  # 丢弃（熔断/过载/TTL）",
            "DECISION_DROP = \"drop\"  # 丢弃（熔断/过载；TTL 的丢弃经 record_ttl_drop 回到这张表）",
        ),
        (
            "          - drop: 丢弃（熔断/过载/TTL）",
            "          - drop: 丢弃（熔断/过载）",
        ),
        (
            "          - drop: 丢弃（熔断/过载/TTL/队列满）",
            "          - drop: 丢弃（熔断/过载/队列满；档位 TTL 见 tts/queue.py 与 record_ttl_drop）",
        ),
        (
            L(
                "    def tts_unlock(self) -> None:",
                "        \"\"\"TTS 播报完成，释放锁，允许下一条。\"\"\"",
                "        self._tts_lock = False",
            ),
            L(
                "    def tts_unlock(self) -> None:",
                "        \"\"\"TTS 播报完成，释放锁，允许下一条。\"\"\"",
                "        self._tts_lock = False",
                "",
                "    # ---- 队列侧过期审计（DCD 裁定③）----",
                "",
                "    def record_ttl_drop(self, *, text: str = \"\", priority: str = PRIORITY_INFO,",
                "                        age_s: float = 0.0, ttl_s: float = 0.0,",
                "                        job_id: str = \"\", trace_id: str = \"\",",
                "                        channel: str = \"tts\", group: str = \"tts\") -> None:",
                "        \"\"\"队列里过期的那条写进 `push_audit`：面板与 `/api/guard/audit` 已经在读这张表。",
                "",
                "        ⛔ 抛异常——风控的库写不动只是少一行审计，⛔ 因此把播放链路带下去（与 `_audit` 同策）。",
                "        job/trace 并进 reason 文本：为审计加两列要走 DDL 守护，不值。",
                "        \"\"\"",
                "        reason = \"ttl expired (age %.0fs > %.0fs) job=%s trace=%s\" % (",
                "            age_s, ttl_s, job_id or \"-\", trace_id or \"-\")",
                "        try:",
                "            self._audit(channel, group, priority, DECISION_DROP, reason, \"\", text)",
                "        except Exception as e:",
                "            logger.warning(\"record_ttl_drop failed (non-blocking): %s: %s\",",
                "                           type(e).__name__, str(e)[:200])",
            ),
        ),
    ],
    "butler/tts/singleton.py": [
        (
            "def init_queue(manager: TTSManager, config: TTSQueueConfig | None = None) -> TTSQueue:",
            L(
                "def _audit_expiry_to_guard(item, now: float) -> None:",
                "    \"\"\"DCD 裁定③：队列的过期审计写进风控那张 `push_audit`（晚绑定）。",
                "",
                "    为什么每次现取、⛔ 构造期注入：`app.py:450` 才挂 `rt.push_guard`，",
                "    队列在 `init_queue` 时就建好了 ⇒ 构造期拿到的必然是 None（与裁定②那三道门同一份口径）。",
                "    \"\"\"",
                "    try:",
                "        from butler.runtime import get_runtime",
                "        pg = getattr(get_runtime(), \"push_guard\", None)",
                "        if pg is None:",
                "            return",
                "        from butler.tts.queue import level_name_for",
                "        created = float(getattr(item, \"created_at\", 0.0) or 0.0)",
                "        expires = float(getattr(item, \"expires_at\", 0.0) or 0.0)",
                "        pg.record_ttl_drop(",
                "            text=str(getattr(item, \"text\", \"\") or \"\"),",
                "            priority=level_name_for(int(getattr(item, \"priority\", 3))),",
                "            age_s=(now - created) if created else 0.0,",
                "            ttl_s=(expires - created) if (expires and created) else 0.0,",
                "            job_id=str(getattr(item, \"job_id\", \"\") or \"\"),",
                "            trace_id=str(getattr(item, \"trace_id\", \"\") or \"\"),",
                "        )",
                "    except Exception as e:",
                "        logger.warning(\"TTS TTL 审计投递失败（不阻塞）: %s: %s\", type(e).__name__, e)",
                "",
                "",
                "def init_queue(manager: TTSManager, config: TTSQueueConfig | None = None,",
                "           on_expired=None) -> TTSQueue:",
            ),
        ),
        (
            "    _queue = TTSQueue(speaker=speaker, config=config)",
            L(
                "    # DCD 裁定③：默认就把审计腿接上——现网只在这里建一次队列，",
                "    # ⛔ 靠调用方记得传参（那正是 `PRIORITY_TTL` 当年没人读的原因）。",
                "    _queue = TTSQueue(speaker=speaker, config=config,",
                "                    on_expired=on_expired or _audit_expiry_to_guard)",
            ),
        ),
    ],
}

MUST_HAVE = {
    "butler/tts/queue.py": [
        "PRIORITY_TTL_S: dict[int, float] = {",
        "def level_name_for(priority: int) -> str:",
        "ttl = self.config.ttl_for(item.priority) if item.ttl_s is None else float(item.ttl_s)",
        "self._note_expired(it, now)",
        "on_expired: Callable[[TTSItem, float], Any] | None = None,",
    ],
    "butler/guard/push_guard.py": [
        "def record_ttl_drop(self, *, text: str = \"\", priority: str = PRIORITY_INFO,",
        "ttl expired (age %.0fs > %.0fs) job=%s trace=%s",
    ],
    "butler/tts/singleton.py": [
        "def _audit_expiry_to_guard(item, now: float) -> None:",
        "on_expired=on_expired or _audit_expiry_to_guard",
    ],
}
MUST_NOT = {
    "butler/tts/queue.py": ["        ttl = self.config.ttl_s if item.ttl_s is None"],
    "butler/guard/push_guard.py": ["PRIORITY_TTL = {", "L4 TTL 过期：按优先级设置不同 TTL"],
    "butler/tts/singleton.py": ["    _queue = TTSQueue(speaker=speaker, config=config)\n"],
}


def _read(rel: str) -> bytes:
    return (REPO / rel).read_bytes()


def _stats(rel: str, b: bytes) -> str:
    crlf = b.count(b"\r\n")
    cr = b.count(b"\r")
    nl = bytes([10])
    return (f"md5={hashlib.md5(b).hexdigest()} lines={len(b.splitlines(True))} "
            f"bytes={len(b)} crlf={crlf} cr_only={cr - crlf} endnl={b.endswith(nl)}")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    for rel, edits in EDIT.items():
        base_md5, base_lines, base_bytes, base_endnl = BASE[rel]
        raw = _read(rel)
        text = raw.decode("utf-8")
        got_md5 = hashlib.md5(raw).hexdigest()
        if got_md5 != base_md5:
            raise SystemExit(f"基线不符 {rel}: md5={got_md5} != {base_md5}（⛔ 在别的树上跑，或别人已动过这个文件）")
        if len(raw) != base_bytes or len(text.splitlines(True)) != base_lines:
            raise SystemExit(f"基线不符 {rel}: lines/bytes 与登记的 BASE 不同")
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                raise SystemExit(f"锚点命中 {n} 次（要求恰好 1）：{rel} :: {old[:60]!r}")
            text = text.replace(old, new, 1)
        out = text.encode("utf-8")
        for probe in MUST_HAVE[rel]:
            if probe not in text:
                raise SystemExit(f"must_have 缺失 {rel}: {probe!r}")
        for probe in MUST_NOT.get(rel, []):
            if probe in text:
                raise SystemExit(f"must_not_have 命中 {rel}: {probe!r}")
        ast.parse(text)
        crlf = out.count(b"\r\n")
        if crlf:
            raise SystemExit(f"{rel} 出现 CRLF={crlf}（基线是 LF 血统，⛔ 整文件改写换行符）")
        endnl = out.endswith(b"\n")
        if endnl != base_endnl:
            raise SystemExit(f"{rel} 末行换行符被改了：{base_endnl} -> {endnl}")
        print(f"{rel} {_stats(rel, raw)} -> {_stats(rel, out)}")
        if apply:
            (REPO / rel).write_bytes(out)
            print(f"  APPLIED|{rel}|" + _stats(rel, _read(rel)))
        else:
            print(f"  DRY|{rel}|未落盘")
    print(f"MODE|{'APPLIED' if apply else 'DRY'}|files={len(EDIT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
