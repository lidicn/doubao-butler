"""批216（step3 搬码队列第 5 笔）：NAS 线 `f535842` — 设备命令的**成功账**不能靠「解析出命令」。

缺口现读（`workorders/判定表-step3-逐笔-20261013.md` §一-f535842）：
1. `execute_device_command` 返回 `None`（`butler/api/doubao_webhook.py:344`），失败在函数内
   `except Exception` 里吞掉（`:472-488`：warning＋Bark 推送），调用方拿不到真假；
2. `:839` 往 `chat_logs` 记的 `success` 是 `1 if device_cmds else 0` ⇒ **只要解析出命令就记成功**，
   命令全数被打回也记 1＝「失败报成功」族（批43 同族判据）；
3. `换台` 那腿只 `logger.warning("TV zap failed")`（`:427-428`），⛔ 走 批44 的 `HACommandRejected` ⇒
   电视没换台而账上是绿的。

判据分两半（⛔ 只做其中一半＝这一族历史上就只补了一腿）：
- **行为腿**：把 `get_runtime` 与 `httpx.AsyncClient` 换成桩（网络＝系统边界，此处不可免），
  现读「成功返 True／HTTP 4xx 返 False 且推 Bark／zap 失败返 False 且推 Bark」；
- **形状腿**：`handle_webhook` 里必须累计 `device_cmds_ok`、`chat_logs` 的 success 位⛔ 再用
  「解析出命令」当成功、返回体带 `device_commands_ok`。形状腿配**应咬**合成件（旧形制要红）。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBHOOK_PY = REPO_ROOT / "butler" / "api" / "doubao_webhook.py"


def _webhook_module():
    from butler.api import doubao_webhook
    return doubao_webhook


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """httpx.AsyncClient 的形状替身：只回答「post 成不成」，⛔ 出门。"""

    def __init__(self, *a, status_code=200, text="", **kw):
        self._status = status_code
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        return _Resp(self._status, self._text)


def _stub_rt(zap_result=(True, "ok"), push_log=None):
    async def _zap(channel):
        return zap_result

    async def _push(**kw):
        if push_log is not None:
            push_log.append(kw)

    return types.SimpleNamespace(
        settings=types.SimpleNamespace(ha_url="http://ha.invalid", ha_token="t"),
        tv=types.SimpleNamespace(zap=_zap),
        bark=types.SimpleNamespace(push=_push),
        self_evolution=None,
    )


def _run_command(params, zap_result=(True, "ok"), status_code=200):
    """现读一枚命令的真假返回值（桩只打在系统边界上）。"""
    w = _webhook_module()
    pushes: list[dict] = []
    rt = _stub_rt(zap_result=zap_result, push_log=pushes)
    real_get_runtime = w.get_runtime
    real_client = w.httpx.AsyncClient
    w.get_runtime = lambda: rt
    w.httpx.AsyncClient = lambda *a, **kw: _FakeClient(status_code=status_code)
    try:
        outcome = asyncio.run(w.execute_device_command(params))
    finally:
        w.get_runtime = real_get_runtime
        w.httpx.AsyncClient = real_client
    return outcome, pushes


class ReturnValueCarriesTruth(unittest.TestCase):
    def test_success_returns_true(self):
        outcome, pushes = _run_command({"device": "客厅灯", "action": "打开",
                                        "entity_id": "light.living_room"})
        self.assertIs(outcome, True, "命令成功却没返回可判别的真值＝调用方无从计数")
        self.assertEqual(pushes, [], "成功也推 Bark＝聊天记录被告警污染（本档注释在册）")

    def test_http_rejection_returns_false_and_pushes(self):
        outcome, pushes = _run_command({"device": "客厅灯", "action": "打开",
                                        "entity_id": "light.living_room"}, status_code=404)
        self.assertIs(outcome, False, "HA 打回 404 仍返 True／None＝失败报成功")
        self.assertEqual(len(pushes), 1, f"设备没拧动而手机没收到告警：{pushes}")

    def test_tv_zap_failure_returns_false_and_pushes(self):
        outcome, pushes = _run_command({"device": "电视", "action": "换台",
                                        "entity_id": "media_player.tv"},
                                        zap_result=(False, "zap boom"))
        self.assertIs(outcome, False, "换台失败只 warning 不上账＝电视没动而账上是绿的")
        self.assertEqual(len(pushes), 1, f"zap 失败没推 Bark：{pushes}")


class WebhookAccountsTruth(unittest.TestCase):
    """形状腿：`handle_webhook` 里的成功位必须吃 `device_cmds_ok`。"""

    @staticmethod
    def _funcs(source: str) -> dict[str, ast.AST]:
        return {n.name: n for n in ast.walk(ast.parse(source))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def _webhook_body(self, source: str) -> str:
        fn = self._funcs(source).get("handle_webhook")
        self.assertIsNotNone(fn, "取不到 handle_webhook＝探测口径失效")
        return ast.get_source_segment(source, fn) or ""

    def test_ruler_bites_on_the_old_form(self):
        """应咬腿：旧形制（解析出命令就记 1、循环里不计数）必须被这条尺判红。"""
        old = ("async def handle_webhook(payload):\n"
               "    for cmd in device_cmds:\n"
               "        await execute_device_command(cmd)\n"
               "    conn.execute(SQL, (1 if device_cmds else 0,))\n"
               "    return {\"device_commands\": len(device_cmds)}\n")
        body = self._webhook_body(old)
        self.assertNotIn("device_cmds_ok", body, "合成件构造失败＝尺子没东西可判")
        self.assertIn("1 if device_cmds else 0", body)

    def test_counts_and_gates_the_success_bit(self):
        src = WEBHOOK_PY.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        body = self._webhook_body(src)
        self.assertRegex(body, r"device_cmds_ok\s*=\s*0",
                         "handle_webhook 里没有成功计数器")
        self.assertRegex(body, r"if await execute_device_command\([^)]*\)\s*:",
                         "调用点没吃 execute_device_command 的返回值＝计数器永远是 0")
        self.assertRegex(body, r"device_cmds_ok\s*\+=\s*1", "计数器从未自增")
        self.assertNotIn("1 if device_cmds else 0", body,
                         "chat_logs 的 success 位仍吃「解析出命令」＝失败也记成功")
        self.assertRegex(body, r"device_cmds\s+and\s+device_cmds_ok\s*==\s*len\(device_cmds\)",
                         "success 位没改成「全部命令都成功」的口径")

    def test_response_exposes_ok_count(self):
        src = WEBHOOK_PY.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        body = self._webhook_body(src)
        self.assertIn('"device_commands_ok"', body,
                      "返回体不带 ok 计数＝上游（webhook 调用方）看到的仍是命令条数")


class ShapeRulerCalibration(unittest.TestCase):
    """形状尺自己：一条应咬（旧形制要红）＋一条应放行（新形制要绿）。"""

    _INIT = re.compile(r"device_cmds_ok\s*=\s*0")
    _OLD = "1 if device_cmds else 0"

    def test_bites_on_the_old_form(self):
        old = "conn.execute(SQL, (1 if device_cmds else 0,))\n"
        self.assertIn(self._OLD, old, "旧形制认不出来＝这把尺恒绿")
        self.assertIsNone(self._INIT.search(old), "计数器缺失时尺子⛔ 点头")

    def test_passes_on_the_new_form(self):
        new = ("device_cmds_ok = 0\n"
               "success = 1 if device_cmds and device_cmds_ok == len(device_cmds) else 0\n")
        self.assertIsNotNone(self._INIT.search(new))
        self.assertNotIn(self._OLD, new, "把新形制读成旧形制＝这把尺恒红")


if __name__ == "__main__":
    unittest.main()
