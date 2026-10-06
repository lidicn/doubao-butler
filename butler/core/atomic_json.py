"""整份 JSON 的原子写：先写同目录的临时兄弟文件，再一次 rename 顶上去。

为什么要有它：仓里多处「就地截断写」（`open(path,"w")` 或 `Path.write_text` 直接盖目标文件）崩在写一半，
盘上就留下半截 JSON——下次读侧要么静默归零（别名/角色/设备被当成出厂状态），要么把异常抛穿成接口 500
（`doc/审计报告/_缺陷汇总_供审阅.md` 表行 45 :65、94 :114、109 :129、127 :147）。
仓内本已有 5 份各写各的原子实现，临时文件命名两种写法并存（`x.json.tmp` 与 `x.tmp`）⇒ 收敛到这一份。

临时文件用**固定名**（⛔ 随机后缀/uuid）：崩窗留下的固定名会被下一轮整文件覆盖，
随机后缀则每崩一次多一个孤儿，违反 `开发规范.md` 的 ⛔ .bak 堆积。

它测不到什么：⛔ fsync——只保证「rename 之前目标文件一个字节都不动」，⛔ 保证掉电后 tmp 里的字节已落物理盘
（这条取舍记在台账 §18-4：热路径每次命中都同步写盘，加 fsync 是把成本乘到每一次命中上）。
父目录由调用方负责（各站点本就各有自己的 mkdir 口径，归一化是另一件事）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path, data) -> None:
    """把 data 序列化成 JSON 并原子替换 path。序列化失败即抛出，目标与临时文件都不碰。"""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
