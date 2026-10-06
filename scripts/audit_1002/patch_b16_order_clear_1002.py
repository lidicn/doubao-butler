r"""批16 一次性落码器：三处「顺序错 / 无条件收尾」导致的静默丢数据。

覆盖（编号＝合并表 `doc/审计报告/_缺陷汇总_供审阅.md` 表行，权威树现读坐标为本文件唯一真源）：
  - 表行 119 B-10 (RB)  `butler/integrations/bark.py`
      :175-178 加密腿 POST 到 `self._push_url`（缺 `/推送加密`，与同文件 `:126` 的 push() 不一致）
      :192     `self._merge_cache.clear()` 无条件执行（HTTP 非 200 与异常都算"发过"）
  - 表行 143 M-19 (RB)  `butler/integrations/memory_agent.py:309/:311` 先 `[-limit:]` 后滤 revoked
  - 表行 46  P1-15 (R3) `butler/core/aliases.py:31-33` 就地截断写（同款正解本仓已有四处：
      `triggers/store.py:90-91`、`api/config_routes.py:36-38`、`api/deps.py:56-59`、`skills/store.py:158-159`）

设计要点（前面批次踩过的坑都在这儿）：
  1. **整块切片定位，不靠行号**：每条锚点给成"连续若干行的原文（不含行尾符）"，在本文件里必须
     唯一命中，命不中或命中多处直接 raise、不写盘；行号会漂（今日 config.py+app.py 同秒 mtime 就是例子）。
  2. **逐行保换行符血统**：新行的行尾取"被替换那一块首行的原行尾"，未动的行按字节原样拷贝。
     bark.py/memory_agent.py 是全 CRLF，aliases.py 是全 LF ⇒ ⛔ 整文件抹平。
  3. **ALREADY_APPLIED 门放在最前**（批14 教训：守卫排在校验门后面＝永远走不到的死分支）。
     重跑必 raise＝这条腿本身就是"一次性"的证明。
  4. 期望产物先算出来再验：ast 可解析、函数数增量对得上、探针短语计数对得上、
     COLLATERAL 短语计数不变（防"编辑吃掉邻行"），全部过了才写盘；写完回读逐字节比对。

用法：python3 -B scripts/audit_1002/patch_b16_order_clear_1002.py            # 干跑
      python3 -B scripts/audit_1002/patch_b16_order_clear_1002.py --apply   # 落盘（重跑必 raise）
"""
import argparse
import ast
import hashlib
import sys

SPEC = {
    "butler/integrations/bark.py": {
        "md5": "494327b43bf448717cebbce2658e9da1", "lines": 196, "bytes": 8490, "cr": 196,
        "sentinel": "def _encrypt_url(",
        "func_delta": 1,
        "edits": [
            (
                ["        return f\"{base}/push\"",
                 "",
                 "    def _encrypt(self, plaintext: str) -> str:"],
                ["        return f\"{base}/push\"",
                 "",
                 "    @property",
                 "    def _encrypt_url(self) -> str:",
                 "        \"\"\"加密推送的唯一 URL 公式：push() 与 flush_merged() 共用"
                 "（两处各拼一遍＝有一处会漏，这次漏的是合并腿）。\"\"\"",
                 "        return self._push_url + \"/推送加密\"",
                 "",
                 "    def _encrypt(self, plaintext: str) -> str:"],
            ),
            (
                ["                    encrypt_url = self._push_url + \"/推送加密\"",
                 "                    r = await c.post(encrypt_url, data={"],
                ["                    r = await c.post(self._encrypt_url, data={"],
            ),
            (
                ["        sent_count = 0",
                 "        for group_name, items in groups.items():"],
                ["        sent_count = 0",
                 "        flushed_groups: list[str] = []",
                 "        for group_name, items in groups.items():"],
            ),
            (
                ["            try:",
                 "                if self._encrypt_enabled:",
                 "                    payload = {\"title\": summary_title, \"body\": summary_body,"
                 " \"group\": f\"merged_{group_name}\"}",
                 "                    plaintext = json.dumps(payload, ensure_ascii=False)",
                 "                    ciphertext = self._encrypt(plaintext)",
                 "                    async with httpx.AsyncClient(timeout=5) as c:",
                 "                        r = await c.post(self._push_url, data={",
                 "                            \"ciphertext\": ciphertext,",
                 "                            \"iv\": self.s.bark_encrypt_iv,",
                 "                        })",
                 "                        if r.status_code == 200:",
                 "                            sent_count += 1",
                 "                else:",
                 "                    payload = {\"title\": summary_title, \"body\": summary_body,"
                 " \"group\": f\"merged_{group_name}\"}",
                 "                    push_url = self._push_url if self._push_url.endswith(\"/push\")"
                 " else self._push_url + \"/push\"",
                 "                    async with httpx.AsyncClient(timeout=5) as c:",
                 "                        r = await c.post(push_url, json=payload)",
                 "                        if r.status_code == 200:",
                 "                            sent_count += 1",
                 "                    logger.info(\"bark merged flush sent: group=%s count=%d\","
                 " group_name, count)",
                 "            except Exception as e:",
                 "                logger.warning(\"bark merged flush failed: %s\", e)",
                 "",
                 "        self._merge_cache.clear()",
                 "        return sent_count"],
                ["            ok = False",
                 "            try:",
                 "                if self._encrypt_enabled:",
                 "                    payload = {\"title\": summary_title, \"body\": summary_body,"
                 " \"group\": f\"merged_{group_name}\"}",
                 "                    plaintext = json.dumps(payload, ensure_ascii=False)",
                 "                    ciphertext = self._encrypt(plaintext)",
                 "                    async with httpx.AsyncClient(timeout=5) as c:",
                 "                        r = await c.post(self._encrypt_url, data={",
                 "                            \"ciphertext\": ciphertext,",
                 "                            \"iv\": self.s.bark_encrypt_iv,",
                 "                        })",
                 "                        if r.status_code == 200:",
                 "                            ok = True",
                 "                else:",
                 "                    payload = {\"title\": summary_title, \"body\": summary_body,"
                 " \"group\": f\"merged_{group_name}\"}",
                 "                    push_url = self._push_url if self._push_url.endswith(\"/push\")"
                 " else self._push_url + \"/push\"",
                 "                    async with httpx.AsyncClient(timeout=5) as c:",
                 "                        r = await c.post(push_url, json=payload)",
                 "                        if r.status_code == 200:",
                 "                            ok = True",
                 "                if ok:",
                 "                    sent_count += 1",
                 "                    flushed_groups.append(group_name)",
                 "                    logger.info(\"bark merged flush sent: group=%s count=%d\","
                 " group_name, count)",
                 "            except Exception as e:",
                 "                logger.warning(\"bark merged flush failed: %s (group=%s,"
                 " %d 条留待下轮)\", e, group_name, count)",
                 "",
                 "        if flushed_groups:",
                 "            self._merge_cache = [i for i in self._merge_cache",
                 "                                 if (i.get(\"group\") or \"default\")"
                 " not in flushed_groups]",
                 "        return sent_count"],
            ),
        ],
        "must": {"_encrypt_url": 3, "\"/推送加密\"": 1, "ok = True": 2, "flushed_groups": 4,
                 "self._merge_cache = [i for i in self._merge_cache": 1},
        "must_absent": ["self._merge_cache.clear()", "encrypt_url = self._push_url",
                        "r = await c.post(self._push_url, data={"],
        "collateral": ["BARK_SOUNDS", "def push(", "def get_merge_cache_size(", "AES-128-CBC",
                       "格5（裁定 C3）", "if len(self._merge_cache) >= 100:"],
    },
    "butler/integrations/memory_agent.py": {
        "md5": "875c7e04a59eaa85e36b051359d813da", "lines": 508, "bytes": 23493, "cr": 508,
        "sentinel": "alive = [m for m in memories",
        "func_delta": 0,
        "edits": [
            (
                ["        \"\"\"召回家庭事实（含 staging）用于对话上下文；跳过 revoked。best-effort。\"\"\"",
                 "        try:",
                 "            memories = await self.list_memories(member)",
                 "        except Exception:",
                 "            return []",
                 "        out: list[str] = []",
                 "        for m in memories[-limit:]:",
                 "            if isinstance(m, dict):",
                 "                if m.get(\"state\") == \"revoked\":",
                 "                    continue",
                 "                content = m.get(\"content\") or m.get(\"text\") or \"\"",
                 "                if content:",
                 "                    out.append(content)",
                 "            elif isinstance(m, str):",
                 "                out.append(m)",
                 "        return out"],
                ["        \"\"\"召回家庭事实（含 staging）用于对话上下文；跳过 revoked。best-effort。",
                 "",
                 "        顺序＝先滤 revoked 再切 limit（批16/M-19：反过来的话，最新 limit 条里",
                 "        每有一条已撤销就少召回一条，且不会往前补）。",
                 "        \"\"\"",
                 "        try:",
                 "            memories = await self.list_memories(member)",
                 "        except Exception:",
                 "            return []",
                 "        alive = [m for m in memories",
                 "                 if not (isinstance(m, dict) and m.get(\"state\") == \"revoked\")]",
                 "        out: list[str] = []",
                 "        for m in alive[-limit:]:",
                 "            if isinstance(m, dict):",
                 "                content = m.get(\"content\") or m.get(\"text\") or \"\"",
                 "                if content:",
                 "                    out.append(content)",
                 "            elif isinstance(m, str):",
                 "                out.append(m)",
                 "        return out"],
            ),
        ],
        "must": {"alive = [m for m in memories": 1, "for m in alive[-limit:]": 1,
                 "m.get(\"state\") == \"revoked\"": 1},
        "must_absent": ["for m in memories[-limit:]:",
                        "                if m.get(\"state\") == \"revoked\":"],
        "collateral": ["async def recall(", "async def list_memories(", "class CredentialMissing",
                       "MA_TOKEN_ENV_KEYS", "async def retrieve("],
    },
    "butler/core/aliases.py": {
        "md5": "b06068afd4df09dd374f05938835df3d", "lines": 118, "bytes": 4283, "cr": 0,
        "sentinel": "os.replace(tmp, ALIAS_FILE)",
        "func_delta": 0,
        "edits": [
            (
                ["import json",
                 "import re"],
                ["import json",
                 "import os",
                 "import re"],
            ),
            (
                ["    def _save(self):",
                 "        ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)",
                 "        ALIAS_FILE.write_text(json.dumps(self.aliases, ensure_ascii=False,"
                 " indent=2), encoding=\"utf-8\")"],
                ["    def _save(self):",
                 "        ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)",
                 "        # 写 tmp 再 os.replace：就地截断写＝崩溃留半个 JSON，下次 _load"
                 " 静默把别名库清零（同款正解见 api/deps.py:56-59）。",
                 "        tmp = ALIAS_FILE.with_name(ALIAS_FILE.name + \".tmp\")",
                 "        tmp.write_text(json.dumps(self.aliases, ensure_ascii=False,"
                 " indent=2), encoding=\"utf-8\")",
                 "        os.replace(tmp, ALIAS_FILE)"],
            ),
        ],
        "must": {"import os": 1, "os.replace(tmp, ALIAS_FILE)": 1,
                 "ALIAS_FILE.with_name(ALIAS_FILE.name + \".tmp\")": 1},
        "must_absent": ["        ALIAS_FILE.write_text(json.dumps(self.aliases"],
        "collateral": ["def learn(", "def match(", "def list_aliases(", "def delete_alias(",
                       "def _extract_keywords(", "def get_alias_store(", "_lock = Lock()"],
    },
}


def split_lines(text):
    lines = text.splitlines(keepends=True)
    contents, terms = [], []
    for line in lines:
        stripped = line.rstrip("\r\n")
        contents.append(stripped)
        terms.append(line[len(stripped):])
    return contents, terms


def func_count(tree):
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


def find_block(contents, block, rel):
    hits = []
    n = len(block)
    for i in range(len(contents) - n + 1):
        if contents[i:i + n] == block:
            hits.append(i)
    if len(hits) != 1:
        raise SystemExit("ANCHOR|%s|hits=%d|head=%s" % (rel, len(hits), block[0][:60]))
    return hits[0]


def apply_file(rel, spec, apply):
    raw = open(rel, "rb").read()
    text = raw.decode("utf-8")
    contents, terms = split_lines(text)
    # ALREADY_APPLIED 必须排在基底校验**之前**：反过来的话重跑只会得到 BASELINE_DRIFT，
    # 这道门就成批14 那种"永远走不到的死分支"。
    if all(any(contents[i:i + len(new)] == new
               for i in range(len(contents) - len(new) + 1))
           for _old, new in spec["edits"]):
        raise SystemExit("ALREADY_APPLIED|%s" % rel)
    got_md5 = hashlib.md5(raw).hexdigest()
    if got_md5 != spec["md5"]:
        raise SystemExit("BASELINE_DRIFT|%s|got=%s|want=%s" % (rel, got_md5, spec["md5"]))
    if len(contents) != spec["lines"] or len(raw) != spec["bytes"] \
            or raw.count(b"\r") != spec["cr"]:
        raise SystemExit("BASELINE_SHAPE|%s|lines=%d/%d bytes=%d/%d cr=%d/%d"
                         % (rel, len(contents), spec["lines"], len(raw), spec["bytes"],
                            raw.count(b"\r"), spec["cr"]))

    working_c, working_t = list(contents), list(terms)
    for old, new in spec["edits"]:
        idx = find_block(working_c, old, rel)
        keep_term = working_t[idx]
        working_c = working_c[:idx] + new + working_c[idx + len(old):]
        working_t = working_t[:idx] + [keep_term] * len(new) + working_t[idx + len(old):]
    out_text = "".join(c + t for c, t in zip(working_c, working_t))
    out_bytes = out_text.encode("utf-8")

    tree = ast.parse(out_text, filename=rel)
    fc_before, fc_after = func_count(ast.parse(text, filename=rel)), func_count(tree)
    if fc_after - fc_before != spec["func_delta"]:
        raise SystemExit("FUNC_COUNT|%s|%d->%d|want_delta=%d"
                         % (rel, fc_before, fc_after, spec["func_delta"]))
    for phrase, want in spec["must"].items():
        if out_text.count(phrase) != want:
            raise SystemExit("MUST_PRESENT|%s|%s|got=%d want=%d"
                             % (rel, phrase, out_text.count(phrase), want))
    for phrase in spec["must_absent"]:
        if phrase in out_text:
            raise SystemExit("MUST_ABSENT|%s|%s" % (rel, phrase))
    for phrase in spec["collateral"]:
        if text.count(phrase) != out_text.count(phrase):
            raise SystemExit("COLLATERAL|%s|%s|%d->%d"
                             % (rel, phrase, text.count(phrase), out_text.count(phrase)))
    cr_after = out_bytes.count(b"\r")
    if spec["cr"] == spec["lines"] and cr_after != len(working_c):
        raise SystemExit("EOL_LOST|%s|cr=%d lines=%d（该文件基底是全 CRLF）" % (rel, cr_after, len(working_c)))
    if spec["cr"] == 0 and cr_after != 0:
        raise SystemExit("CR_INTRODUCED|%s|%d" % (rel, cr_after))
    if not out_text.endswith("\n"):
        raise SystemExit("ENDNL_LOST|%s" % rel)
    print("PLAN|%s|%d->%d lines|%d->%d bytes|cr %d->%d|funcs %d->%d"
          % (rel, len(contents), len(working_c), len(raw), len(out_bytes),
             raw.count(b"\r"), cr_after, fc_before, fc_after))
    if not apply:
        return
    open(rel, "wb").write(out_bytes)
    back = open(rel, "rb").read()
    if back != out_bytes:
        raise SystemExit("WRITE_VERIFY|%s" % rel)
    print("APPLIED|%s|md5=%s|lines=%d|bytes=%d|cr=%d"
          % (rel, hashlib.md5(back).hexdigest(), len(working_c), len(back), back.count(b"\r")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("MODE|dry-run（⛔ 写盘）")
    for rel in sorted(SPEC):
        apply_file(rel, SPEC[rel], args.apply)
    if args.apply:
        print("DONE|files=%d" % len(SPEC))
    return 0


if __name__ == "__main__":
    sys.exit(main())
