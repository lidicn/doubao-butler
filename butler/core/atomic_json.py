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

from butler.logging_setup import get_logger

logger = get_logger("butler.core.atomic_json")


def read_json_object(path) -> tuple[dict, bool]:
    """整份 JSON dict 的收敛读法：返回 (data, unreadable)。

    判定口径（批70b D 族「读不出来被当成没有」的唯一尺子，各写手⛔ 再自己 try/except）：
      - 文件不存在＝真没有 → `({}, False)`：允许调用方首次整份写入（新装环境播种）；
      - 文件存在但读不出／解析失败／顶层不是 dict → `({}, True)`：现场还在盘上，
        调用方必须把它当成「这份内存态不完整」，拒拿它做整份覆盖写；
      - 正常对象 → `(data, False)`。
    读侧兜底空 dict ⛔ 抛穿成 500（批17 口径），但兜底之后必须留痕，所以这里自己喊一声。
    """
    p = Path(path)
    if not p.exists():
        return {}, False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("%s parse failed, treating as unreadable: %s", p, e)
        return {}, True
    if not isinstance(data, dict):
        logger.error("%s top level is %s, not an object: treating as unreadable",
                     p, type(data).__name__)
        return {}, True
    return data, False


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


def write_lines_atomic(path, lines) -> None:
    """JSONL 那一族的原子写：逐行 + 行尾换行，先写固定名兄弟件再 rename 顶上。

    `write_json_atomic` 吃的是"一个对象"，执行日志（`cron_task/execution_logs.jsonl`）是"一本行"，
    拿它整本塞进一个 JSON 数组会改文件格式；这里只补"⛔ 就地 `open(path,'w')` 截断"那一条腿。
    """
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    payload = "".join(str(line).rstrip("\n") + "\n" for line in lines)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
