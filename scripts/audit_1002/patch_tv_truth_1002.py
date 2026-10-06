"""批7 落码器（2026-10-02）：电视通知腿把成败说真。

三处改动、四个落点，逐文件按**整行 startswith ＋ 唯一命中**双验（行号现找现打印，
⛔ 手写行号——同文件多处改动时行号会被上一次改动推移，手写死行号必然错位）。
任何一格对不上、命中数≠1、或前后段被误动，就 raise（不落盘）。
三个文件现读都是 LF（CR＝0），写回走 bytes＋splitlines(True)，改前后各数一次。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

LF = b"\n"
CRB = b"\r"

# ── 落点 1：butler/integrations/tv.py ─────────────────────────────
TV_OLD = [
    "    def notify(self, payload: dict) -> None:",
    '        """弹窗通知通道：经 MQTT cmd/notify 推送通知到电视。"""',
    "        if self.mqtt is None:",
    '            logger.warning("tv notify: no mqtt client")',
    "            return",
    "        from butler.bus.topics import PUB_TV_NOTIFY",
    "",
    '        logger.info("Publishing notify to %s title=%s", PUB_TV_NOTIFY, payload.get("title"))',
    "        self.mqtt.publish(PUB_TV_NOTIFY, payload)",
]

TV_NEW = [
    "    def notify(self, payload: dict) -> bool:",
    '        """弹窗通知通道：经 MQTT cmd/notify 推送通知到电视；返回是否真发出去。',
    "",
    "        mqtt 未就绪那腿只 logger.warning 就 return（消息丢掉、不抛），",
    "        所以成败必须由这条腿自己说，⛔ 让调用方替它猜。",
    '        """',
    "        if self.mqtt is None:",
    '            logger.warning("tv notify: no mqtt client")',
    "            return False",
    "        from butler.bus.topics import PUB_TV_NOTIFY",
    "",
    '        logger.info("Publishing notify to %s title=%s", PUB_TV_NOTIFY, payload.get("title"))',
    "        self.mqtt.publish(PUB_TV_NOTIFY, payload)",
    "        return True",
]

# ── 落点 2：butler/notify/router.py 端口声明 ──────────────────────
PORT_OLD = [
    "class TVPopupPort(Protocol):",
    '    """TV 弹窗（§1 无既有契约，此为本模块定义的最小接口）。"""',
    "",
    "    async def popup(",
    '        self, text: str, *, title: str = "", priority: int = 3,',
    '        room: str = "", duration_s: float = 8.0,',
    "    ) -> bool: ...",
]

PORT_NEW = [
    "class TVPopupPort(Protocol):",
    '    """TV 弹窗（§1 无既有契约，此为本模块定义的最小接口）。',
    "",
    "    生产侧绑的是 butler.integrations.tv.TVClient——它给的是 notify(payload)->bool；",
    "    popup 至今只有本模块单测的假件实现过，所以无 tv_payload 那条分支在真身对象上",
    "    拿不到 popup，路由据实记失败（⛔ 把它伪装成已送达）。",
    '    """',
    "",
    "    def notify(self, payload: dict) -> bool: ...",
    "",
    "    async def popup(",
    '        self, text: str, *, title: str = "", priority: int = 3,',
    '        room: str = "", duration_s: float = 8.0,',
    "    ) -> bool: ...",
]

# ── 落点 3：butler/notify/router.py _to_tv 的 tv_payload 腿 ───────
ROUTER_OLD = [
    "            self.tv.notify(note.tv_payload)",
    "            return ChannelResult(CHANNEL_TV, True)",
]

ROUTER_NEW = [
    "            sent = self.tv.notify(note.tv_payload)",
    "            return ChannelResult(CHANNEL_TV, bool(sent),",
    '                                 error=None if sent else "tv client dropped the payload")',
]

# ── 落点 4：tests/test_notify_router.py 既有假件补回执 ────────────
FAKE_OLD = [
    "        def notify(self, payload):",
    "            self.payloads.append(payload)",
]

FAKE_NEW = [
    "        def notify(self, payload):",
    "            self.payloads.append(payload)",
    "            return True       # 批7：客户端这条腿现在给回执",
]

PLANS = [
    ("butler/integrations/tv.py", 5935, 133, [(TV_OLD, TV_NEW)]),
    # router.py 现读：bytes 13199／LF 317／CR 0／splitlines 318／末行无换行符（`        return ch`）
    ("butler/notify/router.py", 13199, 318, [(PORT_OLD, PORT_NEW), (ROUTER_OLD, ROUTER_NEW)]),
    ("tests/test_notify_router.py", 12767, 362, [(FAKE_OLD, FAKE_NEW)]),
]

ABSENT_AFTER = {
    "butler/integrations/tv.py": ["    def notify(self, payload: dict) -> None:"],
    "butler/notify/router.py": ["            return ChannelResult(CHANNEL_TV, True)"],
    "tests/test_notify_router.py": [],
}
PRESENT_AFTER = {
    "butler/integrations/tv.py": ["            return False", "        return True"],
    "butler/notify/router.py": ["            sent = self.tv.notify(note.tv_payload)",
                                "    def notify(self, payload: dict) -> bool: ..."],
    "tests/test_notify_router.py": ["            return True       # 批7"],
}


def _line_matches(actual, expected):
    """空串锚＝必须是空行（写成 startswith 会恒真，等于没验）。"""
    if expected == "":
        return actual.rstrip("\n") == ""
    return actual.startswith(expected)


def _find_unique(window, lines, where):
    hits = [i for i in range(len(lines) - len(window) + 1)
            if all(_line_matches(lines[i + k], window[k]) for k in range(len(window)))]
    if len(hits) != 1:
        raise SystemExit(f"{where}：锚块命中 {len(hits)} 次，期望 1（块首 {window[0]!r}）")
    return hits[0]


def _apply(spec, apply_flag):
    path, base_bytes, base_lines, edits = spec
    p = pathlib.Path(path)
    data = p.read_bytes()
    if len(data) != base_bytes:
        raise SystemExit(f"{path} BASE-BYTES {len(data)}，期望 {base_bytes}")
    if data.count(CRB):
        raise SystemExit(f"{path} 有 CR，与现读 LF 不符")
    lines = data.decode("utf-8").splitlines(True)
    if len(lines) != base_lines:
        raise SystemExit(f"{path} BASE-LINES {len(lines)}，期望 {base_lines}")

    for n, (old, new) in enumerate(edits, 1):
        idx = _find_unique(old, lines, f"{path} 落点{n}")
        print(f"ANCHOR|{path} 落点{n} 命中行 {idx + 1}..{idx + len(old)}（1 起）")
        lines = lines[:idx] + [ln + "\n" for ln in new] + lines[idx + len(old):]

    out = "".join(lines).encode("utf-8")
    if out.count(CRB):
        raise SystemExit(f"{path} 引入了 CR")
    if out.endswith(LF) != data.endswith(LF):
        raise SystemExit(f"{path} 末行换行符血统被改（原 endswith_nl={data.endswith(LF)}）")
    text = out.decode("utf-8")
    for probe in PRESENT_AFTER[path]:
        if probe not in text:
            raise SystemExit(f"{path} 改后缺探针：{probe!r}")
    for probe in ABSENT_AFTER[path]:
        if probe in text:
            raise SystemExit(f"{path} 改后仍残留：{probe!r}")
    print(f"DRY|{path} lines {base_lines}->{len(lines)} bytes {base_bytes}->{len(out)} cr=0")
    if apply_flag:
        p.write_bytes(out)
        print("APPLIED|" + path + "|" + hashlib.md5(out).hexdigest())


def main(argv: list[str]) -> int:
    apply_flag = "--apply" in argv
    for spec in PLANS:
        _apply(spec, apply_flag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
