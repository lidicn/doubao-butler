"""只读普查：技能盘上现状里 **output 数组** 的类型分布（裁④「静默剥改报错」的硬前置）。

⛔ 不是 grep：`"type"` 在 brain／senses／condition 里同名不同物，grep 会把
`ha_service` 的 brain 侧命中算进 output 侧。本脚本按 JSON 结构只取 `skill["output"][i]["type"]`。
⛔ 只吃 `store.load()` 真会加载的三档目录（`_SOURCES`），其余目录单独列，标「不加载-仅参考」。

`SHAPE_NON_LIST` 那两腿量的是裁④**未落**的另一半（`output` 整体形状不合法＝非 list）：
按现读名单逐枚给 `status`，因为严格化后 `store.load()` 会当场跳过整只技能。
`TRIG_SHAPE_VICTIMS` 是同族另一把尺：`trigger` 归一只留 entry（schedule 才加 cron／interval_s），
旧复数字段 `triggers` 压根不读⇒整枚触发意图落回默认 webhook。本脚本⛔ 改码，只点名。
"""
from __future__ import annotations

import json
import pathlib
import sys

LOADED = ["builtin", "user", "agent"]
WHITELIST = {"tv_notify", "xiaomi_speak", "bark"}
# schema.validate_skill 归一 trigger 时真正留下的键（:62-73 现读）：entry 恒定，
# 仅当 entry=="schedule" 才从 cron / interval_s 里挑一枚。其余键＝静默剥。
TRIGGER_KEPT = {"entry"}
TRIGGER_SCHEDULE_KEPT = {"entry", "cron", "interval_s"}


def _s(v, d: str = "") -> str:
    return str(v).strip() if v is not None else d


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else "data/skills")
    if not root.is_dir():
        raise SystemExit(f"目录不存在：{root}")
    bad_files = 0
    total_out = 0
    n_files = 0
    shape_victims: list[str] = []
    trigger_victims: list[str] = []
    counts: dict[str, int] = {}
    offenders: list[str] = []
    for src in LOADED:
        d = root / src
        if not d.is_dir():
            print(f"MISSING-DIR|{src}")
            continue
        for f in sorted(d.glob("*.json")):
            n_files += 1
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                bad_files += 1
                print(f"UNPARSEABLE|{src}/{f.name}|{type(e).__name__}: {e}")
                continue
            outs = raw.get("output") or []
            trig = raw.get("trigger")
            legacy_plural = "triggers" in raw
            dropped_keys = []
            if isinstance(trig, dict):
                kept = TRIGGER_SCHEDULE_KEPT if _s(trig.get("entry")) == "schedule" else TRIGGER_KEPT
                dropped_keys = sorted(set(trig) - kept)
            if legacy_plural or dropped_keys:
                trigger_victims.append(
                    f"{src}/{f.name}|has_trigger={isinstance(trig, dict)}"
                    f"|legacy_triggers={legacy_plural}|trigger_entry={_s((trig or {}).get('entry')) if isinstance(trig, dict) else '<无 trigger>'}"
                    f"|dropped_keys={dropped_keys}|status={raw.get('status')}|id={raw.get('id')}")
            if not isinstance(outs, list):
                shape_victims.append(
                    f"{src}/{f.name}|output_shape={type(outs).__name__}"
                    f"|status={raw.get('status')}|id={raw.get('id')}")
            engine = (raw.get("brain") or {}).get("engine", "")
            types = []
            for o in outs:
                if not isinstance(o, dict):
                    types.append(f"<非 dict:{type(o).__name__}>")
                    continue
                t = (o.get("type") or "").strip()
                types.append(t)
                total_out += 1
                counts[t] = counts.get(t, 0) + 1
                if t not in WHITELIST:
                    offenders.append(f"{src}/{f.name}#{t}|engine={engine}|id={raw.get('id')}")
            print(f"FILE|{src}/{f.name}|engine={engine}|output={types}")
    print(f"LOADED_FILES={n_files}")
    print(f"SHAPE_NON_LIST={len(shape_victims)}")
    print(f"TRIG_SHAPE_VICTIMS={len(trigger_victims)}")
    for line in trigger_victims:
        print("TRIG_VICTIM|" + line)
    for line in shape_victims:
        print("SHAPE_VICTIM|" + line)
    print(f"SUM|kinds={len(counts)}|total_output_items={total_out}|unparseable={bad_files}")
    print("KINDS|" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print(f"UNKNOWN_OUTPUT_ITEMS={len(offenders)}")
    for line in offenders:
        print("OFFENDER|" + line)
    other = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name not in LOADED]
    print("NOT_LOADED_DIRS|" + ",".join(p.name for p in other))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
