"""O-4（DCD 裁定 D 提前项）：homesdk http/auth 接入前的新旧键名并行对比，只读、不装、不改容器。

Why this shape: 裁定 D 原文「`homesdk http/auth 接入`现在就做……DB 是 homesdk 首个真实消费方」。
DB 现在只用到 `consent`（`butler/core/dialog.py:22,265`），而 SDK 的 `peer_url()` /
`get_auth_headers()` 有一套**自己的键名规则**，解析不到就抛、**不回退内置地址**；DB 这一侧的寻址是
`_env("<KEY>", "<写死的内网默认值>")`。所以「接入」之前必须把两边的键名差量量出来，
否则接入＝把一堆解析失败当成「没配」，比现在更静默。

三边都不靠我抄表，也不靠 SDK 的 docstring（说明书不算落盘证据）：
- DB 侧键名：AST 解析 `butler/config.py` 里所有 `_env("KEY"[, default])`（键名 + 有无默认值 + 行号），
  再并上盘上 `.env` 的**键名集合**；⛔ 手挑属性名（v2 就是这么写的，两枚猜错落成 None）。
- SDK 侧候选：AST 抽 `auth.py` 里 `LEGACY_TOKEN_KEYS/USER_KEYS/PASS_KEYS` 三张表的键名元组
  （抽出来一律 `tuple()` 定死——v2 用 list + `+=` 原地改，把自己抽的表改坏了，读数作废重跑），
  加上 `peer_url`/`token_for`/`_basic_credentials` 的形制（`PEER_<N>_URL`/`<N>_URL`/`<N>_BASE_URL`、
  `TOKEN_<N>`、`USER_<N>`/`PASS_<N>`/`PASSWORD_<N>`），各自 ± `HOMESDK_` 前缀。
- 命中与否：**哨兵探链**——把 `homesdk.config._lookup` 换成一次只放一枚 `{候选键: SENTINEL}` 的桩
  （basic 放两枚：user 一枚 + pass 一枚，因为 `_basic_credentials` 缺一侧即返回 None），
  看 `peer_url(peer)` / `token_for(peer)` / `get_auth_headers(peer)` / `_basic_credentials(peer)`
  会不会把它取出来。取出来＝这一枚键真在那条链上（跑出来的，不是读出来的）。

自证三腿（缺一条这尺就不算会咬）：
1. 正对照：每个 peer 的 canonical 键（`PEER_<N>_URL`）必须被 `peer_url` 取到，否则 PROBE-RED——
   形制猜错时⛔ 许把结果报成「SDK 不认这条键」；
2. fail-closed 口径：全空环境下 `peer_url` 必须抛 `PeerUnknown`、`require_auth_headers` 必须抛
   `AuthMissing`、`get_auth_headers` 必须等于 `{}`；
3. 表自证：legacy 表在探链跑完之后再抽一次，两份逐字节对账；不相等＝我的探链把源表改坏了（v2 就犯过）

凭据边界：⛔ 打印任何值。盘上 `.env` 只取键名集合；唯一读值的是「成对键是否同值」那一条
（`GO2RTC_USER|PASS`、`BUTLER_WEB_USER|PASSWORD`），出纸只落 `same_value` 布尔与长度。
探链用的全部是同一枚哨兵串；出口前 `assert SENTINEL not in text`。

用法：
    python3 scripts/homesdk_peer_probe.py [--sdk-src /tmp/hs030] [--out /tmp/homesdk_peer_probe.json]
    （--sdk-src / HOMESDK_SRC 不给 → 只出 DB 侧，报告写 sdk=skipped，⛔ 当成「SDK 没有这条键」）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTINEL = "SENTINEL-FOR-KEY-CHAIN-PROBE"
SENTINEL2 = "SENTINEL2-FOR-BASIC-PROBE"
PREFIX = "HOMESDK_"

#: 要看哪几个对端。peer 名按 homesdk 的规范化规则写（`memory-agent` 是 SDK 表里的形制）。
PEERS = [
    ("butler", ("task_api_base_url", "butler_base_url", "self_url"), "自指：管家自己的对外地址"),
    ("tv", ("tv_http_url",), "DB 挂在 TV_HTTP_URL 上的地址（现网其实是人脸服务的 8080）"),
    ("tvpilot", ("tvpilot_http_url",), "TVPilot 电视端 HTTP"),
    ("deskpilot", ("deskpilot_http_url",), "DeskPilot（Windows 手）"),
    ("face", (), "人脸节点：DB 侧没有独立 URL 键（只在 tv_http_url 上借道）"),
    ("go2rtc", ("go2rtc_base_url",), "go2rtc 流媒体"),
    ("memory-agent", ("memory_agent_url",), "memory-agent"),
    ("ha", ("ha_url",), "Home Assistant"),
]

def norm(peer):
    return "".join(c if c.isalnum() else "_" for c in peer.strip()).strip("_").upper()


def dashed(peer):
    return peer.strip().lower().replace("_", "-")


def scan_config_py(path):
    """AST: 每枚 `_env("KEY"[, default])` -> {has_default, lineno}；另出 属性名 -> [键名]。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    rows = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "_env"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        key = node.args[0].value
        has_default = (len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                       and node.args[1].value is not None)
        rows[key] = {"has_default": bool(has_default), "lineno": node.lineno}
    attrs = {}
    for node in ast.walk(tree):
        call = None
        name = None
        if isinstance(node, ast.keyword) and node.arg and isinstance(node.value, ast.Call):
            call, name = node.value, node.arg
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            name = node.targets[0].id if node.targets and isinstance(node.targets[0], ast.Name) else None
        if call is None or name is None:
            continue
        f = call.func
        if isinstance(f, ast.Name) and f.id == "_env" and call.args and isinstance(call.args[0], ast.Constant):
            attrs.setdefault(name, []).append(call.args[0].value)
    return rows, attrs


def env_key_names(path):
    """只要 `=` 左边的**名字**。其余值一概不读。"""
    names = set()
    if not os.path.isfile(path):
        return names
    with open(path, encoding="utf-8", errors="replace") as fh:
      for line in fh:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            names.add(m.group(1))
    return names


def scan_auth_tables(path):
    """AST 抽 SDK 自己的三张 legacy 表。值一律定成 tuple——v2 在这里返回 list 又被 `+=` 原地改大，
    把自己抽出来的表印成了 8/16 条假读数，那一版读数作废。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    want = ("LEGACY_TOKEN_KEYS", "LEGACY_USER_KEYS", "LEGACY_PASS_KEYS")
    tables = {n: {} for n in want}
    for node in ast.walk(tree):
        val, targets = None, []
        if isinstance(node, ast.Assign):
            val, targets = node.value, [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            val, targets = node.value, [node.target.id]
        for name in targets:
            if name not in want or not isinstance(val, ast.Dict):
                continue
            for k, v in zip(val.keys, val.values):
                if not isinstance(k, ast.Constant):
                    continue
                if isinstance(v, (ast.Tuple, ast.List)):
                    keys = tuple(e.value for e in v.elts if isinstance(e, ast.Constant))
                else:
                    keys = ()
                tables[name][k.value] = keys
    return tables


def forms_for(peer):
    name = norm(peer)
    return {
        "url": ("PEER_%s_URL" % name, "%s_URL" % name, "%s_BASE_URL" % name),
        "token": ("TOKEN_%s" % name,),
        "user": ("USER_%s" % name,),
        "pass": ("PASS_%s" % name, "PASSWORD_%s" % name),
    }


def with_prefix(forms):
    out = set()
    for f in forms:
        out.add(f)
        out.add(PREFIX + f)
    return out


def candidates(kind, peer, db_keys, tables):
    f = with_prefix(forms_for(peer)[kind])
    table = {"token": "LEGACY_TOKEN_KEYS", "user": "LEGACY_USER_KEYS", "pass": "LEGACY_PASS_KEYS"}[kind] \
        if kind in ("token", "user", "pass") else None
    legacy = ()
    if table:
        t = tables.get(table, {})
        aliases = (dashed(peer), norm(peer).lower().replace("_", "-"))
        legacy = tuple(sorted({k for a in aliases for k in t.get(a, ())}))
    forms = f | with_prefix(legacy)
    if kind == "url":
        mine = {k for k in db_keys if k.endswith(("_URL", "_BASE_URL"))}
    else:
        mine = set(db_keys)
    return sorted(forms | {PREFIX + k for k in mine} | mine), legacy


def load_sdk(src):
    if not src:
        return None, "sdk=skipped (no --sdk-src / HOMESDK_SRC)"
    p = os.path.join(src, "homesdk")
    if not os.path.isdir(p):
        return None, "sdk=ABSENT at %s" % src
    sys.path.insert(0, src)
    try:
        import homesdk  # noqa: F401
        from homesdk import auth as hs_auth
        from homesdk import config as hs_config
        from homesdk import http as hs_http
    except Exception as e:  # pragma: no cover - environment leg
        return None, "sdk=IMPORT-FAILED %s: %s" % (type(e).__name__, e)
    return (hs_http, hs_auth, hs_config), "sdk=%s from %s" % (getattr(homesdk, "__version__", "?"), src)


def chain_probe(sdk, peer, cands, tables):
    """哨兵探链：url / token / basic 三条链，一次一枚（basic 一次两枚）。"""
    hs_http, hs_auth, hs_config = sdk
    real = hs_config._lookup

    def stub(table):
        return lambda key: table.get(key)

    urls, toks, basics_u, basics_p = [], [], set(), set()
    try:
        for k in cands["url"]:
            hs_config._lookup = stub({k: SENTINEL})
            try:
                if hs_http.peer_url(peer) == SENTINEL:
                    urls.append(k)
            except Exception:
                pass
        for k in cands["token"]:
            hs_config._lookup = stub({k: SENTINEL})
            try:
                if hs_auth.token_for(peer) == SENTINEL:
                    hdrs = hs_auth.get_auth_headers(peer)
                    toks.append({"key": k,
                                 "header_names": sorted(hdrs),
                                 "bearer_shape": hdrs.get("Authorization") == "Bearer " + SENTINEL})
            except Exception:
                pass
        for u in cands["user"]:
            for pw in cands["pass"]:
                hs_config._lookup = stub({u: SENTINEL, pw: SENTINEL2})
                try:
                    pair = hs_auth._basic_credentials(peer)
                except Exception:
                    pair = None
                if pair == (SENTINEL, SENTINEL2):
                    basics_u.add(u)
                    basics_p.add(pw)
        hs_config._lookup = stub({})
        raised_url = raised_req = None
        try:
            hs_http.peer_url(peer)
        except Exception as e:
            raised_url = type(e).__name__
        try:
            hs_auth.require_auth_headers(peer)
        except Exception as e:
            raised_req = type(e).__name__
        empty = hs_auth.get_auth_headers(peer)
    finally:
        hs_config._lookup = real
    return {"url_keys": urls,
            "token_keys": toks,
            "basic_user_keys": sorted(basics_u),
            "basic_pass_keys": sorted(basics_p),
            "unset_peer_url_raises": raised_url,
            "unset_require_auth_raises": raised_req,
            "unset_auth_headers": empty}


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/homesdk_peer_probe.json")
    ap.add_argument("--sdk-src", default=os.environ.get("HOMESDK_SRC"))
    args = ap.parse_args()

    cfg_rows, cfg_attrs = scan_config_py(os.path.join(TREE, "butler", "config.py"))
    disk_keys = env_key_names(os.path.join(TREE, ".env"))
    db_keys = set(cfg_rows) | disk_keys
    sdk, sdk_note = load_sdk(args.sdk_src)
    tables = {"LEGACY_TOKEN_KEYS": {}, "LEGACY_USER_KEYS": {}, "LEGACY_PASS_KEYS": {}}
    tables_after_first_scan = None
    if sdk:
        tables = scan_auth_tables(os.path.join(args.sdk_src, "homesdk", "auth.py"))
        tables_after_first_scan = json.dumps(tables, sort_keys=True)

    out = {"tree": TREE, "sdk": sdk_note,
           "db_env_keys_in_config_py": len(cfg_rows),
           "db_env_key_names_on_disk": len(disk_keys),
           "db_env_keys_union": len(db_keys),
           "sdk_legacy_tables": tables,
           "peers": []}
    red = []

    for peer, attr_guesses, note in PEERS:
        old_url_keys = sorted({k for a in attr_guesses for k in cfg_attrs.get(a, [])})
        row = {"peer": peer, "note": note,
               "db_url_keys_by_suffix": sorted(k for k in db_keys
                                               if k.endswith(("_URL", "_BASE_URL"))
                                               and norm(peer) in norm(k))}
        if sdk:
            cands = {"url": candidates("url", peer, db_keys, tables)[0],
                     "token": candidates("token", peer, db_keys, tables)[0],
                     "user": candidates("user", peer, db_keys, tables)[0],
                     "pass": candidates("pass", peer, db_keys, tables)[0]}
            r = chain_probe(sdk, peer, cands, tables)
            row["sdk_forms"] = {k: sorted(with_prefix(v)) for k, v in forms_for(peer).items()}
            row.update({k: r[k] for k in ("url_keys", "basic_user_keys", "basic_pass_keys",
                                          "unset_peer_url_raises", "unset_require_auth_raises")})
            row["token_keys"] = [t["key"] for t in r["token_keys"]]
            row["token_header_bearer"] = all(t["bearer_shape"] and t["header_names"] == ["Authorization"]
                                             for t in r["token_keys"])
            canon = "PEER_%s_URL" % norm(peer)
            if canon not in r["url_keys"]:
                red.append("URL 正对照没咬：%s 竟不被 peer_url 取到（我这把尺可疑，⛔ 据此判 SDK）" % canon)
            if r["unset_peer_url_raises"] != "PeerUnknown":
                red.append("URL fail-closed 不符：%s 空环境抛 %s" % (peer, r["unset_peer_url_raises"]))
            if not r["token_keys"]:
                red.append("TOKEN 正对照没咬：%s 链上一枚都没有（尺子可疑）" % peer)
            if r["unset_require_auth_raises"] != "AuthMissing" or r["unset_auth_headers"] != {}:
                red.append("TOKEN fail-closed 不符：%s %s / %s" %
                           (peer, r["unset_require_auth_raises"], r["unset_auth_headers"]))
            db_set = db_keys
            row["db_url_on_chain"] = sorted(set(r["url_keys"]) & db_set)
            row["db_token_on_chain"] = sorted(set(row["token_keys"]) & db_set)
            row["db_basic_on_chain"] = sorted((set(r["basic_user_keys"]) | set(r["basic_pass_keys"])) & db_set)
            row["passwordish_on_token_chain"] = [k for k in row["db_token_on_chain"]
                                                 if k.endswith(("_PASSWORD", "_PASS"))]
        out["peers"].append(row)

    # DB 侧默认值地图：哪些 URL 键带写死默认（这些正是 SDK 拒绝提供的那一类）
    url_defaults = []
    for key, meta in sorted(cfg_rows.items()):
        if key.endswith(("_URL", "_BASE_URL")):
            url_defaults.append({"key": key, "has_hardcoded_default": meta["has_default"],
                                 "lineno": meta["lineno"], "set_on_disk": key in disk_keys})
    out["db_url_keys_with_defaults"] = url_defaults

    pairs = {}
    envfile = {}
    try:
        envfile = dict(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$",
                                  _read_text(os.path.join(TREE, ".env")), re.M))
    except OSError:
        pass
    for a, b in (("GO2RTC_USER", "GO2RTC_PASS"), ("BUTLER_WEB_USER", "BUTLER_WEB_PASSWORD")):
        va, vb = envfile.get(a), envfile.get(b)
        pairs["%s|%s" % (a, b)] = {"both_present": bool(va is not None and vb is not None),
                                   "same_value": (va is not None and va == vb),
                                   "len_a": len(va or ""), "len_b": len(vb or "")}
    out["credential_pairs"] = pairs
    if tables_after_first_scan is not None:
        rescan = json.dumps(scan_auth_tables(os.path.join(args.sdk_src, "homesdk", "auth.py")), sort_keys=True)
        out["legacy_table_rescan_identical"] = (rescan == tables_after_first_scan)
        if rescan != tables_after_first_scan:
            red.append("表自证红：探链跑完后 legacy 表再抽一次不一样了（我的探链改了源表）"
                       " before=%s after=%s" % (tables_after_first_scan, rescan))
    out["probe_red"] = red

    text = json.dumps(out, ensure_ascii=False, indent=2)
    assert SENTINEL not in text and SENTINEL2 not in text, "probe leaked a value"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)

    print("PROBE %s | DB keys: config.py=%d .env=%d union=%d | out=%s"
          % (sdk_note, len(cfg_rows), len(disk_keys), len(db_keys), args.out))
    print("LEGACY-TABLES(pristine) %s" % json.dumps(tables, ensure_ascii=False, sort_keys=True))
    for r in out["peers"]:
        if sdk:
            print("PEER %-13s url_chain_db=%-24s token_chain_db=%-40s basic_db=%s pwd_on_bearer=%s hdr_bearer_only=%s"
                  % (r["peer"],
                     ",".join(r["db_url_on_chain"]) or "NONE",
                     ",".join(r["db_token_on_chain"]) or "NONE",
                     ",".join(r["db_basic_on_chain"]) or "NONE",
                     r["passwordish_on_token_chain"], r["token_header_bearer"]))
        else:
            print("PEER %-13s sdk=skipped" % r["peer"])
    print("--- DB URL 地图（SDK 拒绝提供的那一类）---")
    for e in out["db_url_keys_with_defaults"]:
        print("URL  %-26s hardcoded_default=%-5s on_disk=%-5s config.py:%d"
              % (e["key"], e["has_hardcoded_default"], e["set_on_disk"], e["lineno"]))
    for k, v in sorted(out["credential_pairs"].items()):
        print("PAIR %-44s both_present=%s same_value=%s len=%d/%d"
              % (k, v["both_present"], v["same_value"], v["len_a"], v["len_b"]))
    if red:
        print("PROBE-RED " + " | ".join(red))
    else:
        print("SELFCHECK PASS: canonical `PEER_<N>_URL` 每 peer 都命中；空环境 peer_url 抛 PeerUnknown、"
              "require_auth_headers 抛 AuthMissing、get_auth_headers == {}；legacy 表未被原地改")
    print("PROBE_DONE")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
