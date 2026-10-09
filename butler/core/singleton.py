"""懒单例真源（§20 批 75／D14·D15）：批19 在 `store/db.py` 上钉的那件事，收成一份实现，
站点⛔ 各写一遍双检锁。

坏码形状（check-then-act）＝判定在锁外：
    if _thing is None:            ← 判定在锁外，并发首调各进得来
        _thing = build()          ← 半成品先对外发布，建表／迁移在发布之后才跑
⇒ 并发首调各造一枚，先造的那枚被覆盖后没人关（句柄泄漏）；
⇒ 没建完表的对象已对外可见；`_init` 抛错后坏对象被永久缓存，一次启动期故障变永久故障。

本 helper 一次钉死三条：
  · 热路径无锁——全局上已有值就直接返回，⛔ 把热路径排进全局锁
    （现网多模态 P95 已 6,259ms，热路径再排队就是雪上加霜）；
  · 锁按（模块, 全局名）分行——不同站点／同站点两枚全局互不排队；
  · 双检＋工厂失败不缓存——抛错后全局保持 None，下一次调用能重建。
    （工厂里半途而废的对象由谁关＝站点工厂自己的责任，见各站点 `_build_conn` 的
    `except: c.close(); raise`——helper 只保证坏值绝不发布。）

工厂返回 None 视同未建：下次调用会重跑工厂（lazy 单例不存在合法的 None 值）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

# 每个（模块, 全局变量名）一把锁：站点之间、同站点多枚全局互不排队。
_LOCKS: dict[tuple[str, str], threading.Lock] = {}
# 只保护 `_LOCKS` 注册本身的一次性小锁，不在任何站点的热路径上。
_LOCKS_GUARD = threading.Lock()


def lock_key(gl: dict, var: str) -> tuple[str, str]:
    """按「模块名 + 全局变量名」分行：同模块两枚全局、不同模块同名全局各是各的锁。"""
    return (str(gl.get("__name__", "")), var)


def _lock_for(key: tuple[str, str]) -> threading.Lock:
    lock = _LOCKS.get(key)
    if lock is not None:
        return lock
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def init(gl: dict, var: str, factory: Callable[[], Any]) -> Any:
    """双检锁懒单例：`gl[var]` 已发布⇒无锁直返；未发布⇒该 key 的锁内跑工厂再发布。

    工厂抛错时异常原样上抛、`gl[var]` 保持 None（坏对象绝不缓存），下次调用重建。
    调用方把连接／实例的构造（含失败时关闭半途而废的对象）整个包进 `factory`，
    本函数只保证：**工厂跑完之前，`gl[var]` 对外仍是 None**。
    """
    value = gl.get(var)
    if value is not None:
        return value  # 热路径：已发布，⛔ 抢任何锁
    with _lock_for(lock_key(gl, var)):
        value = gl.get(var)
        if value is not None:  # 双检：等锁期间可能已由别的线程发布
            return value
        value = factory()      # 抛错⇒不发布，全局保持 None
        gl[var] = value
        return value
