"""只读普查：DCD 裁定② 落码前，模式门会对**在册技能**产生多大行为变更半径。

⛔ 不是我自己重写一套判定：本脚本 `import butler.modes.engine`，用真 `MODE_RULES`＋真
`ModeEngine.can_run_skill`（绕开 `_load_current()`，它要开时序库），逐档模式逐条技能现算。
⛔ 不碰活库、⛔ 不起服务、只读 `data/skills/{builtin,user,agent}` 的 JSON。

口径说明（读数时要带的限定）：
- 技能类别只有 `_guess_skill_category(skill_id)` 一条路（对 id 做子串匹配）；在册技能 JSON 里
  的 `category` 字段是**模板分类**（greeting/schedule/device/security…），与模式层的
  emergency/anomaly/care 词汇⛔ 同源 ⇒ 本脚本按链路的真实口径算，⛔ 拿模板分类美化。
- `output` 只数类型，⛔ 判会不会真播（引擎／房间／设备解析在链路上游）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

LOADED = ["builtin", "user", "agent"]
MODES = ("daily", "movie", "sleep", "guest", "away")


def _engine(mode: str):
    from butler.modes.engine import ModeEngine, ModeState
    eng = ModeEngine.__new__(ModeEngine)      # ⛔ __init__：它会开时序库
    eng.rt = None
    eng._state = ModeState(mode=mode, since=time.time(), source="census")
    return eng


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else "data/skills")
    if not root.is_dir():
        raise SystemExit(f"目录不存在：{root}")
    skills = []
    bad = 0
    for src in LOADED:
        d = root / src
        if not d.is_dir():
            print(f"MISSING-DIR|{src}")
            continue
        for f in sorted(d.glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                bad += 1
                print(f"UNPARSEABLE|{src}/{f.name}|{type(e).__name__}: {e}")
                continue
            skills.append((f"{src}/{f.name}", raw))
    print(f"SCANNED|files={len(skills)}|unparseable={bad}|dirs={','.join(LOADED)}")

    enabled = [(n, s) for n, s in skills if s.get("enabled") and s.get("status", "enabled") == "enabled"]
    print(f"ENABLED|n={len(enabled)}（半径只算真会跑的）")

    from butler.modes.engine import MODE_RULES
    for mode in MODES:
        eng = _engine(mode)
        blocked, passed = [], []
        for name, s in enabled:
            sid = str(s.get("id") or name)
            cat = eng._guess_skill_category(sid)
            if eng.can_run_skill(sid):
                passed.append(sid)
            else:
                blocked.append(f"{sid}#{cat}")
        print(f"MODE|{mode}|skills_allowed={MODE_RULES[mode]['skills_allowed']}"
              f"|care_disabled={MODE_RULES[mode]['care_skills_disabled']}"
              f"|blocked={len(blocked)}|allowed={len(passed)}")
        if blocked:
            print(f"BLOCKED_IDS|{mode}|" + ",".join(blocked))

    out_types: dict[str, int] = {}
    bark_skills, tts_skills = [], []
    for name, s in enabled:
        for o in (s.get("output") or []):
            if not isinstance(o, dict):
                continue
            t = str(o.get("type") or "")
            out_types[t] = out_types.get(t, 0) + 1
            if t == "bark" and str(s.get("id") or name) not in bark_skills:
                bark_skills.append(str(s.get("id") or name))
            if (t == "xiaomi_speak") or (t == "tv_notify" and o.get("tts")):
                if str(s.get("id") or name) not in tts_skills:
                    tts_skills.append(str(s.get("id") or name))
    print("OUTPUT_TYPES|" + json.dumps(out_types, ensure_ascii=False, sort_keys=True))
    print(f"BARK_SKILLS|n={len(bark_skills)}|" + ",".join(bark_skills))
    print(f"TTS_SKILLS|n={len(tts_skills)}|" + ",".join(tts_skills))
    print(f"SILENT_MODES|bark={ [m for m in MODES if MODE_RULES[m]['bark_silent'] ] }"
          f"|tts={ [m for m in MODES if not MODE_RULES[m]['tts_allowed'] ] }")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
