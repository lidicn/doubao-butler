#!/usr/bin/env python3
"""部署窗收口对账尺（只读，只发 GET）：一次跑完本窗所有「改盘上-待进程生效」的格子。

    python3 scripts/post_deploy_reconcile.py --expect pre     # 重启前：新格应全部缺席
    python3 scripts/post_deploy_reconcile.py --expect post    # 重启后：新格应全部到位

凭据口径（2026-10-01 现读 `butler/api/deps.py:267` 定的）：
    `ALLOW_PASSWORD_AS_BEARER` 缺省值是 **"false"** ⇒ 拿网页口令当 Bearer 这条路默认是关的，
    实测 `Bearer <BUTLER_WEB_PASSWORD>` 在 `/api/status` 上回 401「未登录」。所以本尺按下面优先级取凭据，
    且**⛔ 猜口令重试**：`check_auth` 的 Bearer 侧刹车是「同源 60s 内错 20 次即暂时拒判」（`deps.py:38-40`），
    拿它当试错空间会把整台 NAS 的内部调用一起锁掉。
      1) 环境变量 BUTLER_PROBE_COOKIE —— 浏览器登录后 `butler_auth` 那一枚的值
      2) 环境变量 BUTLER_PROBE_TOKEN  —— `BUTLER_SERVICE_TOKENS` 里给探针单开的一枚具名钥匙
      3) 都没有 ⇒ 判「未采-鉴权」，rc=2（这不是「字段不存在」，两件事分得很清）

其余三条设计约束也都是被读数逼出来的：口令⛔ 出现在 argv／进程表（`ps` 全机可读）；
每格自己声明凭什么判绿；ADM_STATUS 那格本尺看不到（它不发 MQTT）⇒ 出口固定「人工-无尺」，
抓到载荷后用 `--adm-payload-file` 交回来才升级成测出来的结论。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

STATUS_LEGS = (
    ("ledgers", ("data", "ledgers")),
    ("write_failures", ("data", "ledgers", "write_failures")),
    ("trigger_evaluations", ("data", "ledgers", "trigger_evaluations")),
    ("ma_surfaces", ("data", "connections", "memory_agent_surfaces")),
)
AUDIT_NEW_KEYS = ("heartbeat", "execution", "execution_last_24h", "caliber_note",
                  "evaluation", "evaluation_last_24h")


def dig(obj, path):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def bearer_from_env_file(env_file):
    """兜底：仓库 .env 里的 BUTLER_WEB_PASSWORD。现读口径下它多半会被拒（默认闸关），
    留着只为让「被拒」这件事由尺子说出口，而不是靠人猜。"""
    if not (env_file and os.path.exists(env_file)):
        return None
    with open(env_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("BUTLER_WEB_PASSWORD="):
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    return None


def build_headers(args):
    cookie = os.environ.get("BUTLER_PROBE_COOKIE")
    if cookie:
        return {"Cookie": "butler_auth=" + cookie}, "环境变量 BUTLER_PROBE_COOKIE(cookie)"
    tok = os.environ.get("BUTLER_PROBE_TOKEN")
    if tok:
        return {"Authorization": "Bearer " + tok}, "环境变量 BUTLER_PROBE_TOKEN(bearer)"
    pw = bearer_from_env_file(args.env_file)
    if pw:
        return ({"Authorization": "Bearer " + pw},
                "%s 的 BUTLER_WEB_PASSWORD(bearer；现读口径下预期被拒，见文件头注释)" % args.env_file)
    return {}, "无处可取（两个环境变量都没设，env-file 里也没读到键）"


def http_get(url, headers, timeout):
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = None
        return e.code, body
    except Exception as e:
        return 0, {"transport_error": "%s: %s" % (type(e).__name__, str(e)[:120])}


# ── 跨仓契约：直接复用现成探针件，⛔ 在这把尺里重抄一遍判据 ────────────
CONTRACTS_REL = os.path.join("tests", "test_cross_repo_contracts.py")


def _load_env_literally(path):
    """按字面把 .env 塞进 env（只 setdefault，⛔ shell source＝值里的 $ 会被展开成假红灯）。

    只有契约那 8 条腿需要它——它们内部走 Settings.load()，宿主裸跑时没 env 会整排
    RuntimeError，被探针的兜底 except 记成 FAIL＝把「我没给环境」读成「对端契约不符」。
    本尺 HTTP 那几格照旧只从 --env-file 取 bearer，本函数⛔ 改变那几格的取法。
    """
    n = 0
    if not os.path.isfile(path):
        return n
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
                v = v[1:-1]
            if k and k.replace("_", "").isalnum():
                os.environ.setdefault(k, v)
                n += 1
    return n


def run_contracts(rows, ran, env_file):
    """返回一段要印出来的口径文本；⛔ 把 skip 并回 VERDICT（那是两件事）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, CONTRACTS_REL)
    if not os.path.isfile(path):
        rows.append(("跨仓契约", "NOT_MEASURED", "探针件不在盘上：%s" % path))
        return "跨仓契约：未采（⛔ 探针件指不到盘上的文件）"
    loaded = _load_env_literally(os.path.join(root, env_file))
    try:
        spec = importlib.util.spec_from_file_location("_contracts_in_reconcile", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_contracts_in_reconcile"] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        rows.append(("跨仓契约", "RED", "探针件装不起来：%s: %s" % (type(e).__name__, str(e)[:120])))
        return "跨仓契约：RED（装不起来，%s）" % type(e).__name__
    mod.RESULTS.clear()
    buf = io.StringIO()
    rc = 1
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.run_all()
    finally:
        sys.modules.pop("_contracts_in_reconcile", None)
    legs = len(mod.RESULTS)
    n_pass = sum(1 for _, v, _ in mod.RESULTS if v == mod.PASS)
    n_fail = sum(1 for _, v, _ in mod.RESULTS if v == mod.FAIL)
    n_skip = sum(1 for _, v, _ in mod.RESULTS if v == mod.SKIP)
    skipped = ",".join(sorted(leg for leg, v, _ in mod.RESULTS if v == mod.SKIP)) or "-"
    if legs == 0:
        rows.append(("跨仓契约", "RED", "ZERO_DENOMINATOR＝一条腿没记账，⛔ 拿 0 当全过"))
    elif n_fail:
        rows.append(("跨仓契约", "RED", "fail=%d legs=%d（对端返回体与契约不符）" % (n_fail, legs)))
    elif n_skip:
        rows.append(("跨仓契约", "NOT_MEASURED",
                     "legs=%d pass=%d skip=%d [%s]＝连不上那几条没量到" % (legs, n_pass, n_skip, skipped)))
    else:
        rows.append(("跨仓契约", "GREEN", "legs=%d 全 PASS" % legs))
    ran.append("跨仓契约")
    return ("跨仓契约：legs=%d pass=%d fail=%d skip=%d｜探针自报 rc=%d"
            "（⛔ 把 rc=0 读成全量测到：skip 按它自己的规矩不算红）｜env 字面加载 %d 行\n%s"
            % (legs, n_pass, n_fail, n_skip, rc, loaded, buf.getvalue().rstrip()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8095")
    ap.add_argument("--expect", choices=("pre", "post"), required=True)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--adm-payload-file", default=None)
    ap.add_argument("--no-contracts", action="store_true",
                    help="跳过跨仓契约那 8 条腿（它们发现网 GET）")
    args = ap.parse_args()

    headers, cred_src = build_headers(args)
    rows, ran = [], []
    post = args.expect == "post"

    code, body = http_get(args.base_url + "/api/status", headers, args.timeout)
    audit_code, audit_body = (None, None)
    if code == 200:
        ran.append("transport /api/status")
        rows.append(("transport /api/status", "GREEN", "HTTP 200"))
        for name, path in STATUS_LEGS:
            v = dig(body, path)
            if v is None:
                rows.append((name, "GREEN" if not post else "RED",
                             "字段缺席 @%s" % ".".join(path)))
                continue
            ran.append(name)
            if name == "ledgers":
                blocks = {k: x for k, x in v.items() if isinstance(x, dict)}
                scored = [k for k, x in sorted(blocks.items())
                          if "last_ts" in x or "read_error" in x or "unavailable" in x]
                detail = "子账 %d 本[%s]；带 ts 或显式缺因 %d 本[%s]" % (
                    len(blocks), ",".join(sorted(blocks)), len(scored), ",".join(scored))
                bite = len(scored) >= 2
            elif name == "write_failures":
                detail = "键%s rows_24h=%s total_rows=%s" % (
                    sorted(v), v.get("rows_24h"), v.get("total_rows"))
                bite = "rows_24h" in v and "total_rows" in v
            else:
                detail = "四枚有无位(⛔值) %s" % {
                    k: bool(v.get(k)) for k in ("mcp", "butler", "app", "basic")}
                bite = set(v) >= {"mcp", "butler", "app", "basic"}
            rows.append((name, "GREEN" if bite else "RED",
                         detail if bite else detail + "；字段在场但结构不符"))
        audit_code, audit_body = http_get(args.base_url + "/api/triggers/audit", headers, args.timeout)
    elif code in (401, 403):
        rows.append(("transport /api/status", "NOT_MEASURED_AUTH",
                     "HTTP %s %s" % (code, str(body)[:100])))
        for name, _ in STATUS_LEGS:
            rows.append((name, "NOT_MEASURED_AUTH", "鉴权未过 ⇒ ⛔ 记成「字段不存在」"))
        rows.append(("audit 双口径", "NOT_MEASURED_AUTH", "鉴权未过"))
    else:
        rows.append(("transport /api/status", "RED", "HTTP %s %s" % (code, str(body)[:140])))
        for name, _ in STATUS_LEGS:
            rows.append((name, "NOT_MEASURED", "连面不通"))
        rows.append(("audit 双口径", "NOT_MEASURED", "连面不通"))

    if audit_code is not None:
        ran.append("audit 双口径")
        present2 = [k for k in AUDIT_NEW_KEYS if isinstance(audit_body, dict) and k in audit_body]
        if audit_code != 200:
            rows.append(("audit 双口径", "RED" if post else "GREEN",
                         "HTTP %s %s" % (audit_code, str(audit_body)[:120])))
        elif len(present2) == len(AUDIT_NEW_KEYS):
            detail = "四键齐[%s] caliber_note=%d 字" % (
                ",".join(present2), len(str(audit_body.get("caliber_note", ""))))
            rows.append(("audit 双口径", "GREEN" if post else "RED",
                         detail if post else "pre 期望下四键已齐 ⇒ 基底（活进程是旧码）作废：" + detail))
        else:
            miss = [k for k in AUDIT_NEW_KEYS if k not in present2]
            rows.append(("audit 双口径", "GREEN" if not post else "RED",
                         "缺键[%s] HTTP 200" % ",".join(miss)))

    if args.adm_payload_file:
        ran.append("adm 新鲜度契约")
        try:
            p = json.load(open(args.adm_payload_file, encoding="utf-8"))
        except Exception as e:
            rows.append(("adm 新鲜度契约", "RED", "载荷读不出：%s" % e))
            p = None
        if p is not None:
            hb, stale = p.get("hb_interval_sec"), p.get("stale_after_sec")
            age = time.time() - float(p.get("ts") or 0)
            detail = "service=%s hb=%s stale=%s age=%.0fs keys=%s" % (
                p.get("service"), hb, stale, age, sorted(p))
            rows.append(("adm 新鲜度契约", "GREEN" if (hb and stale and age <= float(stale)) else "RED", detail))
    else:
        rows.append(("adm 新鲜度契约", "人工-无尺",
                     "本尺不发 MQTT ⇒ 按下方命令订阅 caps 主题，抓到后带 --adm-payload-file 复跑"))

    contract_note = None if args.no_contracts else run_contracts(rows, ran, args.env_file)

    w = max(len(r[0]) for r in rows)
    red = sum(1 for r in rows if r[1] == "RED")
    auth_blocked = sum(1 for r in rows if r[1] == "NOT_MEASURED_AUTH")
    for name, verdict, detail in rows:
        print("%-*s | %-16s | %s" % (w, name, verdict, detail))
    print("\n分母：期望=%s｜跑了 %d 格 %s｜未采 %d 格｜鉴权挡住 %d 格｜凭据来源=%s" % (
        args.expect, len(ran), ran, sum(1 for r in rows if r[1] == "NOT_MEASURED"),
        auth_blocked, cred_src))
    if contract_note:
        print("\n" + contract_note)
    print("MQTT 那格的取法（口令只落在 0600 的文件里，⛔ 命令行与进程表）：")
    print("  umask 077 && vi /tmp/mosq.opt        # 两行：user <用户名> / password <口令>")
    print("  mosquitto_sub -h 127.0.0.1 -p 1883 --options /tmp/mosq.opt \\")
    print("      -t 'adm/doubao-butler/status' -C 1 -v")   # 载荷＝字面量 online/offline，⛔ 当 JSON 解析
    print("  mosquitto_sub -h 127.0.0.1 -p 1883 --options /tmp/mosq.opt \\")
    print("      -t 'adm/doubao-butler/caps' -C 1 > /tmp/adm_caps.json")
    print("  # --adm-payload-file 指 /tmp/adm_caps.json：新鲜度两格（hb/stale）现在随 caps 下发")
    print("  rm -f /tmp/mosq.opt                  # 用完即删，并 ls 反证")
    if auth_blocked:
        print("\nVERDICT 未采-鉴权(%d 格)｜补 BUTLER_PROBE_COOKIE 或 BUTLER_PROBE_TOKEN 后重跑；"
              "⛔ 拿网页口令反复试（Bearer 侧刹车 60s/20 次会锁来源）" % auth_blocked)
        return 2
    print("\nVERDICT %s" % ("RED(%d)" % red if red else "GREEN"))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
