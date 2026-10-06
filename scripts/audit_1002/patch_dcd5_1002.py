"""批12 落码器：DCD 裁定⑤-2「过载语义改＝丢最低优先级、保 ALERT/critical、当前这条不吞」。

裁定原文＝E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:78
本节只动队列侧一家（butler/tts/queue.py）＋两档测试；⑤-1/⑤-3（两套队列归一、两处阈值常量并一处）
有产品语义岔路，已递决策申请，见台账 §13。

护栏（每一条都在 main 里真跑，任何一格不符 ⇒ raise，⛔ 落盘）：
 1. 每个文件施加前 md5／行数／字节／CR 数／末行换行 ＝ §13-2 登记的那组基线定值；
 2. 每个锚点在原文里**恰好命中 1 次**（0 次＝已被施加过或树不对，>1 次＝锚太短会误伤）；
 3. 施加后 ast.parse 三档文件全过（python 字面量里的中文串一旦破坏语法，编译期就炸＝⛔ 写盘）；
 4. CR 数与末行换行**逐文件保持原样**（queue.py 与 test_tts_queue.py 末行无换行＝原状，⛔ 顺手补）；
 5. 写盘后回读 md5 与内存一致，并打印 BEFORE/AFTER 两行读数（改后值只出现在打印里）。

重跑＝raise：锚点已不在，这是它该有的行为（一次性单据），⛔ 把它「修到能重跑」。

用法：
    python3 scripts/audit_1002/patch_dcd5_1002.py            # DRY
    python3 scripts/audit_1002/patch_dcd5_1002.py --apply    # 写盘
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

BASE = {
    "butler/tts/queue.py": dict(md5="af4b1d0676e59f1fb1a06b83d5ab59ad", lines=635,
                                bytes=26958, crlf=0, endnl=False),
    "tests/test_tts_queue.py": dict(md5="8a5367daf146174e0aaa5ebd77ff7718", lines=455,
                                    bytes=16232, crlf=0, endnl=False),
    "tests/test_audit_1002_batch11_dcd3.py": dict(md5="cf602dbc94adee786b9205ce356e7ddb",
                                                  lines=322, bytes=15861, crlf=0, endnl=True),
}

Q = "butler/tts/queue.py"
T = "tests/test_tts_queue.py"
B11 = "tests/test_audit_1002_batch11_dcd3.py"

# (file, 原文锚点, 替换后)
R: list[tuple[str, str, str]] = [
    # ── queue.py ────────────────────────────────────────────────────
    # R1 文件头那句承诺：写着「过载⇒清空」而行为改成丢最低优先＝说了不做
    (Q,
     "- 过载：入队后（含新消息）条数 >= overload_threshold → 清空 + 暂停 overload_pause_s\n"
     "        + 回调 on_overload(文本, 房间) 播报过载通知\n",
     "- 过载：入队后（含新消息）条数 >= overload_threshold → 丢最低优先级档（同带丢最旧）、保 P1 告警档、\n"
     "        当前这条照收；未在暂停中则暂停 overload_pause_s（只挡非 P1 新负载，P1 暂停期间当场能播），\n"
     "        + 回调 on_overload(文本, 房间) 播报过载通知（DCD 裁定⑤-2）\n"),
    # R2 拆掉「整队重置」那枚原因码（留名不留行为＝空号）
    (Q,
     'REASON_REPLACED = "replaced"\nREASON_OVERLOAD_RESET = "overload_reset"\n',
     'REASON_REPLACED = "replaced"\n'),
    # R3 播报文案同样⛔ 再承诺「已清空」
    (Q,
     '    overload_text: str = "TTS 队列过载，已清空并暂停播放。"\n',
     '    overload_text: str = "TTS 队列积压，已丢弃若干条低优先级提醒，告警照常播报。"\n'),
    # R4 暂停期间待遇那句注释
    (Q,
     "        # 过载暂停期间：甩掉非 P1 新负载（P1 保留但不播）\n",
     "        # 过载暂停期间：新来的非 P1 负载直接挡掉（P1 照收，暂停期间当场能播——见 dequeue）\n"),
    # R5 拆「把告警 TTL 顺延到暂停结束后」那条分支：⑤-2 起告警当场能播，前提没了
    (Q,
     "        # 过载暂停期间保留的 P1：暂停期间不可播，TTL 从暂停结束时刻起算，否则告警必在解除前静默过期\n"
     "        if (self._pause_reason == \"overload\" and self._is_paused(now)\n"
     "                and item.priority == PRIORITY_ALERT and ttl > 0):\n"
     "            item.expires_at = max(item.expires_at, self._paused_until + ttl)\n"
     "\n",
     ""),
    # R6 过载那一段本体：整队清空 + 吞掉当前这条 → 让位 + 照收
    (Q,
     "        # 过载保护：入队后（含新消息）>= 阈值 → 清空 + 暂停 + 通知客厅\n"
     "        if len(self._q) + 1 >= self.config.overload_threshold:\n"
     "            dropped = len(self._q) + 1\n"
     "            self._q.clear()\n"
     "            self._paused_until = now + self.config.overload_pause_s\n"
     "            self._pause_reason = \"overload\"\n"
     "            self._overloads += 1\n"
     "            self._dropped[\"overload\"] += dropped\n"
     "            logger.warning(\n"
     "                \"TTS 队列过载：%d 条 >= 阈值 %d，已清空并暂停 %.0fs\",\n"
     "                dropped, self.config.overload_threshold, self.config.overload_pause_s,\n"
     "            )\n"
     "            self._fire_overload()\n"
     "            return EnqueueResult(False, REASON_OVERLOAD_RESET, None, 0, replaced)\n",
     "        # 过载保护（DCD 裁定⑤-2）：入队后（含新消息）>= 阈值 → 丢最低优先级档、保 P1、当前这条照收\n"
     "        if len(self._q) + 1 >= self.config.overload_threshold:\n"
     "            shed = self._shed_lowest_for_room()\n"
     "            self._overloads += 1\n"
     "            self._dropped[\"overload\"] += shed\n"
     "            already_paused = self._pause_reason == \"overload\" and self._is_paused(now)\n"
     "            if not already_paused:            # 暂停只从第一次触发起算，⛔ 被后续过载续期\n"
     "                self._paused_until = now + self.config.overload_pause_s\n"
     "                self._pause_reason = \"overload\"\n"
     "            logger.warning(\n"
     "                \"TTS 队列过载：阈值 %d，丢最低优先级 %d 条（P1 告警档不丢），当前这条照收%s\",\n"
     "                self.config.overload_threshold, shed,\n"
     "                \"\" if already_paused\n"
     "                else \"，并暂停 %.0fs（只挡非 P1 新负载）\" % self.config.overload_pause_s,\n"
     "            )\n"
     "            if not already_paused:\n"
     "                self._fire_overload()\n"),
    # R7 出队侧：过载暂停只挡非 P1（手动暂停⛔ 被放宽，不在裁定⑤ 的面上）
    (Q,
     "        self._sweep(now)\n        if self._is_paused(now):\n            return None\n\n"
     "        cooldown = self._cooldown_active(now)\n",
     "        self._sweep(now)\n"
     "        # 过载暂停只挡非 P1（裁定⑤-2「保 ALERT/critical」）；其它原因的暂停（手动）照旧全挡\n"
     "        overload_pause = self._pause_reason == \"overload\" and self._is_paused(now)\n"
     "        if self._is_paused(now) and not overload_pause:\n            return None\n\n"
     "        cooldown = self._cooldown_active(now)\n"),
    (Q,
     "            if cooldown and item.priority != PRIORITY_ALERT:\n"
     "                return None                  # 冷却期间非 P1 不播（消息保留）\n"
     "            self._q.popleft()\n",
     "            if cooldown and item.priority != PRIORITY_ALERT:\n"
     "                return None                  # 冷却期间非 P1 不播（消息保留）\n"
     "            if overload_pause and item.priority != PRIORITY_ALERT:\n"
     "                # 队里有 P1 它必在队头（插入规则保证分带单调）⇒ 队头不是 P1 就是真没有\n"
     "                return None                  # 过载暂停期间非 P1 继续等（消息保留，⛔ 丢 ⛔ 吞）\n"
     "            self._q.popleft()\n"),
    # R8 让位助手（被 R6 调用，⛔ 只 def 不走——批11 §12-4 那枚空号的教训）
    (Q,
     "    def _fire_overload(self) -> None:\n",
     "    def _shed_lowest_for_room(self) -> int:\n"
     "        \"\"\"过载让位：从最低优先级档开始丢（同带丢最旧），P1 告警档⛔ 丢；返回丢掉几条。\n"
     "\n"
     "        全队列都是 P1 时丢 0 条——当前这条照样进队（裁定⑤-2「不吞」），\n"
     "        此时队列可短暂越过阈值：那是裁定的代价，⛔ 为了腾位置去丢告警。\n"
     "        \"\"\"\n"
     "        shed = 0\n"
     "        while len(self._q) + 1 >= self.config.overload_threshold:\n"
     "            droppable = max({_band(it.priority) for it in self._q} - {0}, default=0)\n"
     "            if droppable == 0:\n"
     "                break                        # 只剩告警档，没得丢\n"
     "            for i, it in enumerate(self._q):\n"
     "                if _band(it.priority) == droppable:\n"
     "                    del self._q[i]           # 该带最旧的一条（带内 FIFO）\n"
     "                    break\n"
     "            shed += 1\n"
     "        return shed\n"
     "\n"
     "    def _fire_overload(self) -> None:\n"),
    # ── 批11 验收件里那句失效的引用 ──────────────────────────────────
    (B11,
     '        """过载暂停期间保留的 P1：TTL 从暂停结束时刻起算那句（queue.py:302）⛔ 因为本批而失效。"""\n',
     '        """告警档 TTL＝0（不过期）⇒ 过载暂停期间它不会被扫掉。\n'
     '        「暂停期间当场能播」是批12（裁定⑤-2）的腿，\n'
     '        见 tests/test_audit_1002_batch12_dcd5.py 的 AlertExemptionTest。\n'
     '        """\n'),
    # ── 既有单测：三枚过载腿按新语义改口（函数数⛔ 变＝shim 闸基线 31） ──
    (T,
     "    REASON_DROPPED_QUIET,\n    REASON_OVERLOAD_RESET,\n    REASON_REPLACED,\n    TTSItem,\n",
     "    REASON_DROPPED_QUIET,\n    REASON_REPLACED,\n    REASON_QUEUED,\n    TTSItem,\n"),
    (T,
     'def test_overload_clears_pauses_and_notifies_living_room():\n'
     '    calls: list[tuple[str, str]] = []\n'
     '    cfg = TTSQueueConfig(overload_threshold=5, overload_pause_s=600, overload_text="过载了")\n'
     '    q, ft = make_queue(config=cfg, on_overload=lambda text, room: calls.append((text, room)))\n'
     '    for i in range(4):\n'
     '        assert q.enqueue(f"m{i}").accepted\n'
     '    res = q.enqueue("m4")\n'
     '    assert res.accepted is False and res.reason == REASON_OVERLOAD_RESET\n'
     '    st = q.status()\n'
     '    assert st["size"] == 0\n'
     '    assert st["paused"] is True and st["pause_reason"] == "overload"\n'
     '    assert st["dropped"]["overload"] == 5\n'
     '    assert calls == [("过载了", "living")]\n'
     '    assert q.enqueue("暂停期间").reason == REASON_DROPPED_PAUSED\n'
     '    assert q.enqueue("暂停期间告警", priority=1).accepted      # P1 保留但不播\n'
     '    assert q.dequeue() is None\n'
     '    ft.advance(601)\n'
     '    item = q.dequeue()\n'
     '    assert item is not None and item.priority == 1\n',
     'def test_overload_sheds_lowest_and_keeps_current_and_alert():\n'
     '    calls: list[tuple[str, str]] = []\n'
     '    cfg = TTSQueueConfig(overload_threshold=5, overload_pause_s=600, overload_text="过载了",\n'
     '                         ttl_by_priority={3: 5000.0})\n'
     '    q, ft = make_queue(config=cfg, on_overload=lambda text, room: calls.append((text, room)))\n'
     '    for i in range(4):\n'
     '        assert q.enqueue(f"m{i}").accepted\n'
     '    res = q.enqueue("m4")\n'
     '    assert res.accepted is True and res.reason == REASON_QUEUED     # ⑤-2：当前这条不吞\n'
     '    st = q.status()\n'
     '    assert st["size"] == 4                                        # 只腾 1 条位置，⛔ 整队清空\n'
     '    assert st["paused"] is True and st["pause_reason"] == "overload"\n'
     '    assert st["dropped"]["overload"] == 1                         # 旧码在这里记 5（把当前这条也算丢）\n'
     '    assert calls == [("过载了", "living")]\n'
     '    assert q.enqueue("暂停期间").reason == REASON_DROPPED_PAUSED\n'
     '    assert q.enqueue("暂停期间告警", priority=1).accepted            # P1 照收\n'
     '    assert q.dequeue().priority == 1                              # 且暂停期间当场能播（⑤-2 保 ALERT）\n'
     '    assert q.dequeue() is None                                    # 告警播完，非 P1 继续等（消息保留）\n'
     '    ft.advance(601)\n'
     '    item = q.dequeue()\n'
     '    assert item is not None and item.priority == 3                # 暂停到点，队列照常走\n'),
    (T,
     '        res = q.enqueue("b")\n        assert res.reason == REASON_OVERLOAD_RESET\n'
     '        await asyncio.sleep(0.01)\n',
     '        res = q.enqueue("b")\n        assert res.accepted is True and res.reason == REASON_QUEUED\n'
     '        await asyncio.sleep(0.01)\n'),
    (T,
     '    res = q.enqueue("b")\n    assert res.reason == REASON_OVERLOAD_RESET\n'
     '    assert q.status()["paused"] is True                       # 清理动作照常完成\n',
     '    res = q.enqueue("b")\n    assert res.accepted is True and res.reason == REASON_QUEUED\n'
     '    assert q.status()["paused"] is True                       # 暂停动作照常完成\n'),
]


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(text.splitlines(True)),
                bytes=len(raw), crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"))


def fmt(rel: str, s: dict) -> str:
    return ("%s md5=%s 行=%d 字节=%d CR=%d 末行换行=%s" % (
        rel, s["md5"], s["lines"], s["bytes"], s["crlf"], s["endnl"]))


def main() -> None:
    apply = "--apply" in sys.argv
    texts: dict[str, str] = {}
    for rel in BASE:
        p = REPO / rel
        if not p.exists():
            raise SystemExit(f"ABORT 文件不存在：{p}")
        raw = p.read_bytes()
        s = stats(raw)
        want = BASE[rel]
        for k, v in want.items():
            if s[k] != v:
                raise SystemExit(f"ABORT {rel} 基线 {k} 不符：现={s[k]} 期={v}（{fmt(rel, s)}）")
        print("BEFORE|" + fmt(rel, s))
        texts[rel] = raw.decode("utf-8")

    for rel, old, new in R:
        n = texts[rel].count(old)
        if n != 1:
            raise SystemExit(f"ABORT 锚点在 {rel} 命中 {n} 次（要求恰 1）：{old[:56]!r}")
        texts[rel] = texts[rel].replace(old, new, 1)

    for rel, text in texts.items():
        ast.parse(text)                              # 语法门：坏了⛔ 落盘
        raw = text.encode("utf-8")
        s = stats(raw)
        if s["crlf"] != BASE[rel]["crlf"]:
            raise SystemExit(f"ABORT {rel} CR 数变了：{s['crlf']} != {BASE[rel]['crlf']}")
        if s["endnl"] != BASE[rel]["endnl"]:
            raise SystemExit(f"ABORT {rel} 末行换行变了（要保原样）")
        print("DRY   |" + fmt(rel, s))

    if not apply:
        print(f"DRY OK 共 {len(R)} 处替换，未写盘。要写：python3 scripts/audit_1002/patch_dcd5_1002.py --apply")
        return
    for rel, text in texts.items():
        (REPO / rel).write_text(text, encoding="utf-8", newline="")
    for rel in BASE:
        back = stats((REPO / rel).read_bytes())
        want = hashlib.md5(texts[rel].encode("utf-8")).hexdigest()
        if back["md5"] != want:
            raise SystemExit(f"ABORT {rel} 回读 md5 不符")
        print("APPLIED|" + fmt(rel, back))


if __name__ == "__main__":
    main()
