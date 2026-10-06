"""DCD 裁定②（模式行为规则接上三个入口）的落码器：5 个文件、逐处锚点、施加前后各量一次字节血统。

⛔ 手写 patch／⛔ `git diff --no-index` 当补丁源（抹 CR）⇒ 这里只做「整段唯一锚点字符串替换」，
   每处锚点必须**恰好命中 1 次**，命中 0 次或 >1 次都直接抛错、不落盘。
基线（现量于权威树 /vol1/1000/docker/doubao-butler，HEAD f517305）：
  butler/modes/engine.py  md5=425726cc7bf8ba48adf1400ee5b5747a lines=302 bytes=10655 crlf=0 endnl=True
  butler/tts/manager.py   md5=978d2cf274e36f8fc6a71826dafec3a9 lines=259 bytes=10690 crlf=0 endnl=True
  butler/tts/adapter.py   md5=7e3046f6d033772094ec4c56657c6d34 lines=83  bytes=3415  crlf=0 endnl=True
  butler/notify/router.py md5=8d05c596031211b58c810bc456d76360 lines=326 bytes=13647 crlf=0 endnl=False
  butler/skills/runner.py md5=a48fade514e036b196326df45f65d50b lines=460 bytes=20864 crlf=0 endnl=True
`notify/router.py` **本来就⛔ 末行换行符** ⇒ 施加后必须还是 False（endnl 逐文件核，⛔ 顺手补一个换行）。

用法：DRY（默认）＝只验锚点并打印将要发生的字节数变化；`--apply`＝落盘。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path("/vol1/1000/docker/doubao-butler")

BASE = {
    "butler/modes/engine.py":   ("425726cc7bf8ba48adf1400ee5b5747a", 302, 10655, True),
    "butler/tts/manager.py":    ("978d2cf274e36f8fc6a71826dafec3a9", 259, 10690, True),
    "butler/tts/adapter.py":    ("7e3046f6d033772094ec4c56657c6d34", 83, 3415, True),
    "butler/notify/router.py":  ("8d05c596031211b58c810bc456d76360", 326, 13647, False),
    "butler/skills/runner.py":  ("a48fade514e036b196326df45f65d50b", 460, 20864, True),
}

GATES_SRC = '''

# ---- 三个入口共用的判定口（DCD 裁定② 20261002-DB六件影子代码-裁定.md:32-42）----
#
# 为什么挂模块级、⛔ 三处各写一遍：fail-open 政策是一份定义（同一判定两腿必须共用 helper），
# 抄三遍下一次改动必漂成三种行为。
# 为什么每次现取、⛔ 构造期注入：app.py:281 建 tts、:316 建 runner，:427 才装 mode_engine
# ⇒ 构造期拿到的必然是 None；现取让「装配顺序」⛔ 决定行为。

def get_mode_engine():
    """现取模式引擎；未装配／取不到⇒None，交由 `_gate` 按 fail-open 放行。"""
    try:
        from butler.runtime import get_runtime
        return getattr(get_runtime(), "mode_engine", None)
    except Exception as e:
        logger.warning("mode engine lookup failed (fail-open): %s: %s", type(e).__name__, e)
        return None


def _gate(judge) -> tuple[bool, str]:
    """唯一的 fail-open 落点，返回 (是否放行, 模式名)。

    DCD② 风险确认原文＝「最容易翻车的不是崩而是『该响的没响』」⇒ 这道门 ⛔ fail-closed：
    判定自身出错一律放行并留 warning（与 integrations/bark.py:89 的 push_guard 同策）。
    模式名只用于日志与失败原因，⛔ 参与判定。
    """
    engine = get_mode_engine()
    if engine is None:
        return True, ""
    try:
        return bool(judge(engine)), engine.current
    except Exception as e:
        logger.warning("mode gate failed (fail-open): %s: %s", type(e).__name__, e)
        return True, getattr(engine, "current", "")


def gate_tts(emergency: bool = False) -> tuple[bool, str]:
    """发声入口＝tts/manager.py 的 speak（全家这张嘴）。"""
    return _gate(lambda eng: eng.can_tts(emergency=emergency))


def gate_bark(silent: bool = False) -> tuple[bool, str]:
    """推送入口＝notify/router.py 的 _to_bark。"""
    return _gate(lambda eng: eng.can_bark(silent=silent))


def gate_skill(skill_id: str, category: str | None = None) -> tuple[bool, str]:
    """技能执行入口＝skills/runner.py 的 run。"""
    return _gate(lambda eng: eng.can_run_skill(skill_id, category))
'''

CAN_RUN_SKILL_OLD = '''    def can_run_skill(self, skill_id: str, category: str = None) -> bool:
        """当前模式是否允许执行指定技能。"""
        allowed = self.rules["skills_allowed"]
        if allowed == "all":
            return True
        if allowed == "none":
            return False

        # 判断技能类别
        skill_cat = category or self._guess_skill_category(skill_id)

        if allowed == "emergency_only":
            return skill_cat == "emergency"
        if allowed == "anomaly_only":
            return skill_cat in ("emergency", "anomaly")
        return True
'''

CAN_RUN_SKILL_NEW = '''    def can_run_skill(self, skill_id: str, category: str = None) -> bool:
        """当前模式是否允许执行指定技能。"""
        allowed = self.rules["skills_allowed"]
        skill_cat = category or self._guess_skill_category(skill_id)

        if allowed == "none":
            return False
        if allowed == "emergency_only":
            ok = skill_cat == "emergency"
        elif allowed == "anomaly_only":
            ok = skill_cat in ("emergency", "anomaly")
        else:
            ok = True

        # 第五枚规则键 care_skills_disabled 此前唯一的读者是零调用的 is_care_skill_disabled
        # ⇒「会客/睡眠不推关怀提醒」这句承诺一直没接上（DCD② 判例：假承诺比缺失更坏）。
        if ok and skill_cat == "care" and self.is_care_skill_disabled():
            return False
        return ok
'''

ALL_RULES_OLD = '''    def get_all_rules(self) -> dict:
        """获取所有模式的行为规则。"""
        return {mode: {"name": MODE_NAMES[mode], **rules} for mode, rules in MODE_RULES.items()}
'''

EDIT = {
    "butler/modes/engine.py": [
        (CAN_RUN_SKILL_OLD, CAN_RUN_SKILL_NEW),
        (ALL_RULES_OLD, ALL_RULES_OLD + GATES_SRC),
    ],
    "butler/tts/manager.py": [
        ("from butler.logging_setup import get_logger\n",
         "from butler.logging_setup import get_logger\nfrom butler.modes.engine import gate_tts\n"),
        ("from butler.tts.playback_queue import PlaybackQueue\n",
         "from butler.tts.playback_queue import PlaybackQueue\nfrom butler.tts.queue import PRIORITY_ALERT\n"),
        ("        nowvoice_voice: str | None = None,\n        trace_id: str = \"\", via: str = \"direct\",\n    ) -> TTSResult | None:\n",
         "        nowvoice_voice: str | None = None,\n        trace_id: str = \"\", via: str = \"direct\",\n        priority: int | None = None,\n    ) -> TTSResult | None:\n"),
        ("        ``trace=``（logging_setup.py）天生取不到请求那头的 id ⇒ 只能把 id 随消息本身带过来。\n        \"\"\"\n",
         "        ``trace=``（logging_setup.py）天生取不到请求那头的 id ⇒ 只能把 id 随消息本身带过来。\n"
         "\n"
         "        ``priority``（DCD 裁定② 20261002）＝这条话的紧急档，取 tts/queue.py 的 1-5 口径；\n"
         "        只有 PRIORITY_ALERT(=1) 能过「只放紧急」的模式门，⛔ priority＝不声明紧急⇒照表压。\n"
         "        \"\"\"\n"
         "        allowed, mode = gate_tts(emergency=(priority == PRIORITY_ALERT))\n"
         "        if not allowed:\n"
         "            logger.info(\"TTS_SUPPRESSED mode=%s via=%s trace=%s priority=%s len=%d: %.30s\",\n"
         "                        mode, via, trace_id or \"-\",\n"
         "                        priority if priority is not None else \"-\",\n"
         "                        len(text or \"\"), text or \"\")\n"
         "            return None\n"),
    ],
    "butler/tts/adapter.py": [
        ("                trace_id=item.trace_id,\n                via=\"queue\",\n            )\n",
         "                trace_id=item.trace_id,\n                via=\"queue\",\n"
         "                # DCD 裁定②：ALERT 豁免要活着走到这张嘴——item.priority 在这条接缝上此前被丢掉。\n"
         "                priority=item.priority,\n            )\n"),
    ],
    "butler/notify/router.py": [
        ("logger = get_logger(\"butler.notify.router\")\n",
         "logger = get_logger(\"butler.notify.router\")\n\n"
         "from butler.modes.engine import gate_bark  # DCD 裁定②：推送入口的模式门（⛔ 循环依赖：modes/⛔ import notify）\n"),
        ("    async def _to_bark(self, note: Notification) -> ChannelResult:\n"
         "        if self.bark is None:\n"
         "            return ChannelResult(CHANNEL_BARK, False, error=\"not_bound: bark\")\n",
         "    async def _to_bark(self, note: Notification) -> ChannelResult:\n"
         "        if self.bark is None:\n"
         "            return ChannelResult(CHANNEL_BARK, False, error=\"not_bound: bark\")\n"
         "        # DCD 裁定②：push_guard.py:223 那句「由调用方在调用前检查模式」的调用方，此前就是零调用的\n"
         "        # can_bark。silent 的口径＝Bark 的 level=passive（只落通知栏、不响）；critical 要不要一并放行\n"
         "        # 属规则表语义（bark_silent 没有紧急例外），已投待裁。\n"
         "        silent = str((note.bark_kwargs or {}).get(\"level\") or \"\").strip().lower() == \"passive\"\n"
         "        allowed, mode = gate_bark(silent=silent)\n"
         "        if not allowed:\n"
         "            logger.info(\"NOTIFY_BARK_BLOCKED mode=%s trace=%s: %.30s\",\n"
         "                        mode, note.trace_id or \"-\", note.text)\n"
         "            return ChannelResult(CHANNEL_BARK, False, error=f\"mode_blocked:{mode}\")\n"),
    ],
    "butler/skills/runner.py": [
        ("from butler.skills.runner_types import SkillContext, SkillResult\n",
         "from butler.skills.runner_types import SkillContext, SkillResult\nfrom butler.modes.engine import gate_skill\n"),
        ("        if not skill.get(\"enabled\") and not (dry_run or force):\n"
         "            return {\"ok\": False, \"error\": \"skill disabled\", \"status\": \"disabled\"}\n",
         "        if not skill.get(\"enabled\") and not (dry_run or force):\n"
         "            return {\"ok\": False, \"error\": \"skill disabled\", \"status\": \"disabled\"}\n"
         "\n"
         "        # DCD 裁定② 20261002：模式行为规则接上技能执行入口（三入口的第三处）。\n"
         "        # force／dry_run＝人工按下的立即执行与试运行，按本文件既有口径跳过编排层检查（⛔ 新造例外）。\n"
         "        # 技能类别只有 _guess_skill_category(skill_id) 一条路（对 id 做子串匹配）；在册技能的 id 现量无一\n"
         "        # 命中 emergency/anomaly 词表（读数与复跑见 scripts/audit_1002/census_mode_gate_radius_1002.py）\n"
         "        # ⇒ 非 daily 档会挡光的根源是「类别猜不中」，把「哪条技能算紧急」显式化已另投待裁。\n"
         "        if not (dry_run or force):\n"
         "            allowed, mode = gate_skill(skill_id)\n"
         "            if not allowed:\n"
         "                logger.info(\"SKILL_BLOCKED mode=%s skill=%s source=%s\", mode, skill_id, source)\n"
         "                return {\"ok\": False, \"error\": f\"当前模式（{mode}）不允许执行该技能\",\n"
         "                        \"status\": \"mode_blocked\"}\n"),
    ],
}

MUST_HAVE = {
    "butler/modes/engine.py": ["def get_mode_engine():", "def _gate(judge)",
                               "def gate_tts(", "def gate_bark(", "def gate_skill(",
                               "if ok and skill_cat == \"care\" and self.is_care_skill_disabled():"],
    "butler/tts/manager.py": ["priority: int | None = None,", "gate_tts(emergency=(priority == PRIORITY_ALERT))",
                              "TTS_SUPPRESSED", "from butler.tts.queue import PRIORITY_ALERT"],
    "butler/tts/adapter.py": ["priority=item.priority,"],
    "butler/notify/router.py": ["gate_bark(silent=silent)", "mode_blocked:{mode}", "level\") or \"\").strip().lower() == \"passive\""],
    "butler/skills/runner.py": ["gate_skill(skill_id)", "\"mode_blocked\"", "from butler.modes.engine import gate_skill"],
}
MUST_NOT = {
    "butler/modes/engine.py": ["        if allowed == \"all\":\n            return True\n"],
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
        ast.parse(text)  # 语法闸门：编译期就失败⇒不落盘
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
