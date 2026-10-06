"""一次性落码器：DCD 裁定④（输出白名单）里现网零受害者的四条腿。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md` 议题④（裁定 B）。
验收＝`tests/test_audit_1002_batch9_dcd4.py` 改前 `Ran 12 / failures=6`
（六枚红＝schema 两条、模板一条、needs_review 一条、RISKY_OUTPUTS 残留一条、conflict 一条；
 另有六枚是防过度收紧的绿桩）。

改四个文件：
  schema.py     白名单外的 output 当场报错（裁定 B-2 里「dict 条目但 type 不合法」那一半）
  templates.py  删「设备控制」模板那条 ha_service output（B-3）
  sandbox.py    needs_review 按 brain.engine 判；assess_risk／_can_auto_approve 的恒空 output 分支删；
                 RISKY_OUTPUTS 常量删（删完它就是第二枚「定义在盘上没人调」的影子定义）（B-4）
  conflict.py   _detect_action_conflict 改按 brain.engine=ha_action ＋ brain.entity 取设备（B-4）

⛔ 本脚本不落 B-2 的另一半（output 整体形状不合法＝非 list／非 dict 条目，剥光后回落 tv_notify）：
现读 24 份在册技能里恰好 2 份（`user/anti_addiction_alert.json`、`user/device_inspection.json`，
都 status=enabled、output 是 dict、字段还是旧的 `triggers` 复数）会因此整只不进索引＝要人裁的后果。
所以脚本末尾专门钉一条探针：那句 `if not isinstance(o, dict): continue` 必须还在——它在＝这一半没落，
它没了＝我越界了。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

CRB = b"\r"
LF = b"\n"

SPEC = [
    {
        "path": "butler/skills/schema.py",
        "md5": "3888cd45356639cba4e872ce65c67f71", "bytes": 8002, "lines": 212, "defs": 5,
        "cuts": [
            {
                "name": "output 归一循环",
                "old": [
                    "    outputs = []\n",
                    "    for o in raw.get(\"output\") or []:\n",
                    "        if not isinstance(o, dict):\n",
                    "            continue\n",
                    "        otype = _s(o.get(\"type\"), \"tv_notify\")\n",
                    "        if otype in _OUTPUTS:\n",
                    "            out_config = {\"type\": otype, \"tts\": _b(o.get(\"tts\"), True)}\n",
                    "            # 保留 room / role / device 等扩展字段\n",
                    "            for key in (\"room\", \"role\", \"device\"):\n",
                    "                if key in o:\n",
                    "                    out_config[key] = o.get(key)\n",
                    "            outputs.append(out_config)\n",
                    "    if not outputs:\n",
                    "        outputs = [{\"type\": \"tv_notify\", \"tts\": True}]\n",
                ],
                "new": [
                    "    outputs = []\n",
                    "    rejected = []\n",
                    "    for o in raw.get(\"output\") or []:\n",
                    "        if not isinstance(o, dict):\n",
                    "            continue\n",
                    "        otype = _s(o.get(\"type\"), \"tv_notify\")\n",
                    "        if otype not in _OUTPUTS:\n",
                    "            rejected.append(otype)\n",
                    "            continue\n",
                    "        out_config = {\"type\": otype, \"tts\": _b(o.get(\"tts\"), True)}\n",
                    "        # 保留 room / role / device 等扩展字段\n",
                    "        for key in (\"room\", \"role\", \"device\"):\n",
                    "            if key in o:\n",
                    "                out_config[key] = o.get(key)\n",
                    "        outputs.append(out_config)\n",
                    "    if rejected:\n",
                    "        # DCD 裁定④-B：白名单外的 output 当场报错。⛔ 静默剥——剥光就回落成\n",
                    "        # tv_notify(tts=True)：面板以为建好了「开灯」，实际得到一只只会说不会做、\n",
                    "        # 且因为名单里没有 ha_service 而骗过 conflict／sandbox 两道门的哑巴。\n",
                    "        return None, f\"output 类型不支持：{sorted(set(rejected))}（可用：{sorted(_OUTPUTS)}）\"\n",
                    "    if not outputs:\n",
                    "        outputs = [{\"type\": \"tv_notify\", \"tts\": True}]\n",
                ],
            },
        ],
        "must_have": [
            "if otype not in _OUTPUTS:",
            "output 类型不支持",
            "if not isinstance(o, dict):",
        ],
        "must_not": ["        if otype in _OUTPUTS:\n"],
    },
    {
        "path": "butler/skills/templates.py",
        "md5": "36d0b6395b32a4325efb9beb24c809f8", "bytes": 13754, "lines": 320, "defs": 5,
        "cuts": [
            {
                "name": "设备控制模板的 ha_service output 项",
                "old": [
                    "            \"output\": [\n",
                    "                {\"type\": \"ha_service\", \"domain\": \"homeassistant\", \"service\": \"{{action}}\", \"entity_id\": \"{{entity_id}}\"},\n",
                    "                {\"type\": \"tv_notify\"},\n",
                    "            ],\n",
                ],
                "new": [
                    "            # DCD 裁定④-B：⛔ 在这里放 ha_service。output 白名单只有三种，那条写进来\n",
                    "            # 也会被 schema 拒；设备控制走 brain.engine=ha_action（一条路，不留两套）。\n",
                    "            \"output\": [\n",
                    "                {\"type\": \"tv_notify\"},\n",
                    "            ],\n",
                ],
            },
        ],
        "must_have": ["设备控制走 brain.engine=ha_action"],
        "must_not": ["\"type\": \"ha_service\""],
    },
    {
        "path": "butler/skills/sandbox.py",
        "md5": "924b2d9d81701d3e2ccf27b273679526", "bytes": 6733, "lines": 197, "defs": 11,
        "cuts": [
            {
                "name": "RISKY_OUTPUTS 定义",
                "old": [
                    "# 危险输出类型（需要人工审批）\n",
                    "RISKY_OUTPUTS = {\"ha_service\", \"mqtt_publish\"}\n",
                    "\n",
                ],
                "new": [],
            },
            {
                "name": "needs_review 的危险输出分支",
                "old": [
                    "        # 包含危险输出的需要审批\n",
                    "        outputs = skill.get(\"output\", []) or []\n",
                    "        for o in outputs:\n",
                    "            if o.get(\"type\") in RISKY_OUTPUTS:\n",
                    "                return True\n",
                ],
                "new": [
                    "        # DCD 裁定④-B：能动设备的东西住在 brain.engine（output 白名单里没有那种类型），\n",
                    "        # 所以审批门按引擎判。改前这里查那个「危险输出」常量＝恒空＝真会动设备的技能直接免审。\n",
                    "        engine = (skill.get(\"brain\") or {}).get(\"engine\", \"\")\n",
                    "        if engine and engine not in SAFE_ENGINES:\n",
                    "            return True\n",
                ],
            },
            {
                "name": "assess_risk 的 outputs 局部变量",
                "old": [
                    "        outputs = skill.get(\"output\", []) or []\n",
                    "        trigger = (skill.get(\"trigger\") or {}).get(\"entry\", \"\")\n",
                ],
                "new": [
                    "        trigger = (skill.get(\"trigger\") or {}).get(\"entry\", \"\")\n",
                ],
            },
            {
                "name": "assess_risk 的输出风险分支",
                "old": [
                    "        # 输出风险\n",
                    "        for o in outputs:\n",
                    "            if o.get(\"type\") in RISKY_OUTPUTS:\n",
                    "                risks.append(f\"输出类型 '{o['type']}' 可能影响外部系统\")\n",
                    "\n",
                ],
                "new": [],
            },
            {
                "name": "_can_auto_approve 的输出分支",
                "old": [
                    "        # 输出不能有危险类型\n",
                    "        outputs = skill.get(\"output\", []) or []\n",
                    "        for o in outputs:\n",
                    "            if o.get(\"type\") in RISKY_OUTPUTS:\n",
                    "                return False\n",
                ],
                "new": [
                    "        # DCD 裁定④-B：这条恒空的 output 分支删掉——上面那句引擎白名单就是同一件事，\n",
                    "        # 而被删的那个「危险输出」常量里的两个类型，schema 白名单根本放不进来。\n",
                ],
            },
        ],
        "must_have": ["能动设备的东西住在 brain.engine"],
        "must_not": ["RISKY_OUTPUTS"],
        "must_count": {"SAFE_ENGINES": 4},
    },
    {
        "path": "butler/skills/conflict.py",
        "md5": "7e71603bcf9dea437e25d0494f3ffa87", "bytes": 14850, "lines": 356, "defs": 13,
        "cuts": [
            {
                "name": "action_conflict 的设备提取",
                "old": [
                    "        for s in skills:\n",
                    "            for o in (s.get(\"output\") or []):\n",
                    "                if o.get(\"type\") == \"ha_service\":\n",
                    "                    entity = o.get(\"entity_id\", \"\")\n",
                    "                    if entity:\n",
                    "                        skill_devices[s[\"id\"]].add(entity)\n",
                ],
                "new": [
                    "        for s in skills:\n",
                    "            # DCD 裁定④-B：设备控制住在 brain.engine=ha_action，实体在 brain.entity。\n",
                    "            # 改前这里找 output 里的 ha_service.entity_id＝schema 白名单永不放行该词⇒ 这道\n",
                    "            # 冲突检测恒空（两个相反动作控制同一盏灯也检不出来）。\n",
                    "            brain = s.get(\"brain\") or {}\n",
                    "            if brain.get(\"engine\") != \"ha_action\":\n",
                    "                continue\n",
                    "            entity = (brain.get(\"entity\") or \"\").strip()\n",
                    "            # 带占位符的实体（{{entity}} 之类）此刻不是具体设备，⛔ 拿模板串当设备比＝造假冲突\n",
                    "            if entity and \"{{\" not in entity:\n",
                    "                skill_devices[s[\"id\"]].add(entity)\n",
                ],
            },
        ],
        "must_have": ["brain.get(\"engine\") != \"ha_action\""],
        "must_not": ["if o.get(\"type\") == \"ha_service\":"],
    },
]


def _find_run(lines, block, where):
    hits = []
    n = len(block)
    for i in range(len(lines) - n + 1):
        if lines[i:i + n] == block:
            hits.append(i)
    if len(hits) != 1:
        raise SystemExit(f"{where}：连续 {n} 行整行等值命中 {len(hits)} 次，期望 1 → 首行 {block[0][:60]!r}")
    return hits[0]


def _defs(lines):
    return sum(1 for ln in lines if ln.startswith(("def ", "async def ", "    def ", "    async def ")))


def main(argv):
    do_apply = "--apply" in argv
    staged = {}
    for spec in SPEC:
        p = pathlib.Path(spec["path"])
        data = p.read_bytes()
        got = hashlib.md5(data).hexdigest()
        if got != spec["md5"] or len(data) != spec["bytes"] or data.count(CRB):
            raise SystemExit(f"BASE-MISMATCH {p}: md5={got} bytes={len(data)} cr={data.count(CRB)}")
        lines = data.decode("utf-8").splitlines(True)
        if len(lines) != spec["lines"] or _defs(lines) != spec["defs"]:
            raise SystemExit(f"BASE-STRUCT {p}: lines={len(lines)}/{spec['lines']} defs={_defs(lines)}/{spec['defs']}")
        cur = list(lines)
        for cut in spec["cuts"]:
            idx = _find_run(cur, cut["old"], f"{p}#{cut['name']}")
            print(f"ANCHOR|{p.name}|{cut['name']}|start={idx + 1}|{len(cut['old'])}->{len(cut['new'])} 行")
            cur[idx:idx + len(cut["old"])] = cut["new"]
        out = "".join(cur).encode("utf-8")
        text = out.decode("utf-8")
        if out.count(CRB) or out.endswith(LF) != data.endswith(LF):
            raise SystemExit(f"{p}: 血统被改（CR 或末行换行符）")
        out_lines = out.decode("utf-8").splitlines(True)
        if _defs(out_lines) != spec["defs"]:
            raise SystemExit(f"{p}: 定义数变了 {_defs(out_lines)}!={spec['defs']}")
        for probe in spec["must_have"]:
            if probe not in text:
                raise SystemExit(f"{p}: 新文本探针丢失 {probe!r}")
        for gone in spec["must_not"]:
            if gone in text:
                raise SystemExit(f"{p}: 旧文本仍在 {gone!r}")
        for probe, want in spec.get("must_count", {}).items():
            n = text.count(probe)
            if n != want:
                raise SystemExit(f"{p}: {probe!r} 出现 {n} 次，期望 {want}（少＝门没接上，多＝有落点没点开）")
        expect = spec["lines"] + sum(len(c["new"]) - len(c["old"]) for c in spec["cuts"])
        if len(out_lines) != expect:
            raise SystemExit(f"{p}: 行数期望 {expect}（基数 {spec['lines']} ＋ 各切块真实增量）实得 {len(out_lines)}")
        print(f"DRY|{p.name} lines {spec['lines']}->{len(out_lines)} bytes {spec['bytes']}->{len(out)} cr=0 defs={spec['defs']}")
        staged[p] = (out, data)

    if do_apply:
        for p, (out, _old) in staged.items():
            p.write_bytes(out)
            print(f"APPLIED|{p}|{hashlib.md5(out).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
