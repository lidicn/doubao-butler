# -*- coding: utf-8 -*-
"""批206（2026-10-13）部署面归一门。

现读凭据（读数件 `workorders/readings/1012/b219_deploy_face_1012.txt`、
`b216_inflight_and_ro_1012.txt`、`b207_nasline_area_1012.txt`）：

· 部署面（仓库根）也在两本账里：`docker-compose.yml` 我这侧 78 行、NAS 面 149 行；
  NAS 面上有一整套 P0-10 加固（非 root／cap_drop ALL／no-new-privileges／pids_limit／
  read_only 根＋tmpfs／docker.sock 改走受限代理）＋ `TZ`／`NOWVOICE_*`／`BUTLER_SPEAK_TARGET=tv`
  ＋ songloft 只读挂载 ⇒ ⛔ 换线时拿我这侧覆盖过去，这些一起没。
· 出资人 2026-10-13 令：「不能再把代码拷进容器。也不能直接编辑 nas 上的代码」
  ⇒ 源码挂载 `./butler:/app/butler` 必须转只读（丙案，本席推荐已被采纳）。
· 开发规范 §6.1：端口映射必绑 `192.168.2.200`，⛔ `0.0.0.0`
  ⇒ 现读 `- "8095:8095"` 违例（`b207_nasline_area_1012.txt` 的 compose 段同值）。
· 读数件 `workorders/readings/1013/b206c_literal_envkeys_1013.txt`：受跟踪的 dev 面带活凭据
  —— NAS 面 3 枚（`BARK_KEY`/`BARK_ENCRYPT_KEY`/`BARK_ENCRYPT_IV`）、我这侧 1 枚
  （`BUTLER_WEB_PASSWORD`）⇒ test_09/10 一把尺两面都收（值本体⛔ 出现在任何输出里）。
  同读数件：`.env.example` 我这侧 89 枚 ⊇ NAS 45 枚（NAS 独有=0、同键异值=0）⇒ 记载面以我这侧为准。

本档⛔ import `butler`（只读文件＋自己写的窄解析），是**文件面**验收。
每把解析尺都配「应咬的合成文本」负控格：⛔ 只会点头的尺不配当闸。
"""

import hashlib
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\r\n", "\n")

LAN_IP = "192.168.2.200"
# 负控夹具用的合成凭据形（≥16 位、字母数字混排＝与真值同形制）；⛔ 从 `.env`／NAS 面抄真值
SYNTH_BARK_LITERAL = "SYNTHbarkkey0123456789AB"
SYNTH_WEBPASS_LITERAL = "SYNTHwebpass0123456789ab"
SRC_MOUNT = "./butler:/app/butler"
# 运行时确实要写的两枚挂载（现读证据：源码树自容器启动后被改文件=0，data/tts 是活写入面）
WRITABLE_MOUNTS = ("/app/data", "/app/tts")

HARDENING_KEYS = ("user:", "cap_drop:", "security_opt:", "pids_limit:", "read_only:", "tmpfs:")

# `.env` 真值比对门的口径：长值门槛 10（短值做子串必假阳——`MQTT_USER` 的真值就是通用词，
# 一次撞出 1845 处把真信号埋掉），占位形态直接跳过；作用域⛔ `doc/`（出资人的审计件，归轮换）。
# 只拦**键名带凭据语义**的那批：`MQTT_HOST`/`HA_URL`/`DOUBAO_MODEL` 这类地址与型号名，
# `butler/config.py` 的默认值本身就是它，拦下来是一把恒红尺＝没有闸。
ENV_VALUE_MIN_LEN = 10
ENV_PLACEHOLDER = ("changeme", "your-", "<", "${", "replace", "none", "false", "true")
ENV_SECRET_KEY_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api_?key|credential|signature|\biv\b|\bsig\b|_key$|^key_|access_)")
ENV_SCAN_DIRS = ("tests", "butler", "scripts")
ENV_SCAN_ROOT_FILES = ("docker-compose.yml", "docker-compose.dev.yml", "Dockerfile", ".env.example")


def _env_long_values():
    """返回 `{键名: 真值}`（仅凭据语义键）；`.env` 不在位返回 None（调用方必须判成"未量"，⛔ 绿）。"""
    path = ROOT / ".env"
    if not path.exists():
        return None
    out = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not ENV_SECRET_KEY_RE.search(key):
            continue
        if len(val) < ENV_VALUE_MIN_LEN or any(p in val.lower() for p in ENV_PLACEHOLDER):
            continue
        out[key] = val
    return out


def _env_literal_hits():
    values = _env_long_values()
    if values is None:
        return None
    targets = [ROOT / name for name in ENV_SCAN_ROOT_FILES]
    for d in ENV_SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            targets += [p for p in base.rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".sh", ".json", ".md"}]
    hits = []
    for p in targets:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for key, val in values.items():
            if val in text:
                hits.append((rel, key))
    return hits


def _mount_lines(text: str):
    """volumes 段里的每一枚挂载串（去掉行首 `- ` 与注释）。"""
    out = []
    in_volumes = False
    for raw in text.split("\n"):
        line = raw.split("#")[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^volumes:\s*$", stripped):
            in_volumes = True
            continue
        if in_volumes:
            if not stripped.startswith("- "):
                in_volumes = False
                continue
            out.append(stripped[2:].strip().strip('"').strip("'"))
    return out


def _published_ports(text: str):
    """ports 段里的每一枚发布串（`[[IP:]HOST:]CONTAINER`）。"""
    out = []
    in_ports = False
    for raw in text.split("\n"):
        stripped = raw.split("#")[0].strip()
        if not stripped:
            continue
        if re.match(r"^ports:\s*$", stripped):
            in_ports = True
            continue
        if in_ports:
            if not stripped.startswith("- "):
                in_ports = False
                continue
            out.append(stripped[2:].strip().strip('"').strip("'"))
    return out


def _mount_dest(mount: str) -> str:
    parts = mount.split(":")
    return parts[1] if len(parts) >= 2 else mount


def _mount_is_ro(mount: str) -> bool:
    return mount.split(":")[-1] == "ro"


def _port_bound_to_lan(port: str) -> bool:
    """三段式且首段是 LAN IP ⇒ 合规；两段式（`HOST:CONTAINER`）＝绑 0.0.0.0 ⇒ 违例。"""
    parts = port.split(":")
    return len(parts) == 3 and parts[0] == LAN_IP


# 受跟踪的部署面配置文件（b206c 现读：这四枚是凭据字面量的落点面）
CONFIG_FILES = ("docker-compose.yml", "docker-compose.dev.yml", ".env.example", "Dockerfile")

# 非凭据语义的键尾（值长≧16 也不该报）：主机/端口/URL/用户名/音色/模型一类
_NON_SECRET_SUFFIX = ("USER", "HOST", "PORT", "URL", "PREFIX", "ID", "VOICE", "MODEL",
                      "PATH", "TIMEOUT", "PHRASE", "SECONDS", "DAYS", "WINDOW", "THRESHOLD")
_SECRET_SHAPE = re.compile(r"^[A-Za-z0-9._~+/-]{16,}$")
_PLACEHOLDER = re.compile(r"(your_|change_me|placeholder|example|<|\$\{)", re.I)


def credential_literals(text: str, name: str) -> list:
    """扫 `KEY=value` 形态里的活凭据字面量。

    ⛔ 返回串里带值本体（尺子自己不能成为泄露面）⇒ 只给键名＋行号＋长度＋md5 前 8。
    """
    hits = []
    for lineno, line in enumerate(text.split("\n"), 1):
        m = re.match(r"^\s*-?\s*([A-Z][A-Z0-9_]{2,})=(\S+)\s*$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key.endswith(_NON_SECRET_SUFFIX) or _PLACEHOLDER.search(value) or not _SECRET_SHAPE.match(value):
            continue
        digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:8]
        hits.append(f"{key}@{name}:L{lineno}(len={len(value)},md5={digest})")
    return hits


SYNTH_OK_SAMPLE = """services:
  x:
    environment:
      - MQTT_PASSWORD=${MQTT_PASSWORD}
      - NEW_API_KEY=your_new_api_key_here
      - BUTLER_WEB_USER=admin
      - MQTT_HOST=192.168.2.200
      - NOWVOICE_VOICE=afeb4759
"""


def _service_block(text: str, name: str) -> str:
    """某个服务的行（`  <name>:` 起，到下一个两格缩进键止）。

    没有这把「服务」尺，`/var/run/docker.sock` 那条只能挂在全局 volumes 上，
    会把受限代理自己的合法只读挂载也算成违例（我第一版就这么假红过一次）。
    """
    out, started = [], False
    for raw in text.split("\n"):
        line = raw.split("#")[0].rstrip()
        if line == f"  {name}:":
            started = True
            continue
        if started:
            if line.strip() and not line.startswith("   "):
                break
            out.append(line)
    return "\n".join(out)


BAD_SAMPLE = """services:
  doubao-butler:
    ports:
      - "8095:8095"
    volumes:
      - ./butler:/app/butler
      - /vol1/1000/docker/doubao-butler/data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
"""

PROXY_SAMPLE = """  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
"""


class DeployFaceGate(unittest.TestCase):
    # ---- 尺子先自证会咬（合成文本＝违例形态，必须被同一把尺判出来）----
    def test_00_rulers_bite_on_synthetic_violation(self):
        mounts = _mount_lines(BAD_SAMPLE)
        ports = _published_ports(BAD_SAMPLE)
        self.assertEqual(len(mounts), 3, f"合成文本应解析出 3 枚挂载，实得 {mounts}")
        self.assertTrue(any(_mount_dest(m) == "/app/butler" and not _mount_is_ro(m) for m in mounts),
                        "尺失灵：合成文本里 rw 源码挂载没被认出")
        self.assertEqual(ports, ["8095:8095"], f"合成端口应解析出 1 枚，实得 {ports}")
        self.assertFalse(_port_bound_to_lan("8095:8095"), "尺失灵：两段式发布串被判成已绑 LAN IP")
        self.assertTrue(_port_bound_to_lan("192.168.2.200:8095:8095"))
        two = BAD_SAMPLE + PROXY_SAMPLE
        main = _service_block(two, "doubao-butler")
        self.assertIn("/var/run/docker.sock", main, "尺失灵：主服务块里的 sock 没被看到")
        self.assertNotIn("tecnativa", main, "尺失灵：服务块切过了头，把下一个服务也算进来了")
        self.assertIn("/var/run/docker.sock", _service_block(two, "docker-socket-proxy"))

    # ---- 一、源码挂载只读（出资人令「⛔ 直接编辑 NAS 上的代码」的机械腿）----
    def test_01_butler_mount_is_read_only(self):
        mounts = [m for m in _mount_lines(COMPOSE) if _mount_dest(m) == "/app/butler"]
        self.assertEqual(len(mounts), 1, f"/app/butler 挂载应恰好一枚，实得 {mounts}")
        self.assertTrue(_mount_is_ro(mounts[0]),
                        f"源码挂载⛔ 只读＝「直编 NAS 码」没焊死，现值={mounts[0]}")

    def test_02_runtime_writable_mounts_stay_writable(self):
        """防「一刀切 :ro」把活写入面一起掐死：data/tts 必须仍可写。"""
        mounts = _mount_lines(COMPOSE)
        for dest in WRITABLE_MOUNTS:
            hit = [m for m in mounts if _mount_dest(m) == dest]
            self.assertEqual(len(hit), 1, f"{dest} 应恰好一枚挂载，实得 {hit}")
            self.assertFalse(_mount_is_ro(hit[0]), f"{dest} 被误置只读＝容器写不进数据，现值={hit[0]}")

    # ---- 二、端口绑 LAN IP（开发规范 §6.1）----
    def test_03_published_ports_bound_to_lan_ip(self):
        ports = _published_ports(COMPOSE)
        self.assertGreater(len(ports), 0, "尺失灵：ports 段一枚没解析到")
        bad = [p for p in ports if not _port_bound_to_lan(p)]
        self.assertEqual(bad, [], f"违 §6.1（⛔ 0.0.0.0 发布）：{bad}")

    # ---- 三、NAS 面那套 P0-10 加固必须进我这侧（⛔ 换线时被覆盖）----
    def test_04_hardening_keys_present(self):
        missing = [k for k in HARDENING_KEYS if k not in COMPOSE]
        self.assertEqual(missing, [], f"缺 P0-10 加固腿：{missing}")

    def test_05_docker_socket_proxy_replaces_raw_sock(self):
        main = _service_block(COMPOSE, "doubao-butler")
        self.assertNotIn("/var/run/docker.sock", main,
                         "docker.sock 仍直挂主容器＝P0-10 那条腿被覆盖掉了")
        proxy = _service_block(COMPOSE, "docker-socket-proxy")
        self.assertIn("/var/run/docker.sock", proxy, "受限代理自己没有 sock 挂载，代理链是断的")
        self.assertIn("docker-socket-proxy", COMPOSE, "受限代理服务块没进来")
        self.assertIn("DOCKER_HOST", COMPOSE, "主容器没接 DOCKER_HOST=tcp://docker-socket-proxy:2375")

    def test_06_runtime_env_legs_present(self):
        for key in ("TZ=Asia/Shanghai", "NOWVOICE_VOICE", "BUTLER_SPEAK_TARGET=tv"):
            self.assertIn(key, COMPOSE, f"运行参数腿缺失：{key}（NAS 面现读在册）")

    def test_07_extra_readonly_mounts_present(self):
        mounts = _mount_lines(COMPOSE)
        for frag in ("/nas/books", "/app/songloft_miot"):
            hit = [m for m in mounts if _mount_dest(m) == frag]
            self.assertEqual(len(hit), 1, f"{frag} 挂载没进来，实得 {hit}")
            self.assertTrue(_mount_is_ro(hit[0]), f"{frag} 应只读，现值={hit[0]}")

    # ---- 五、Dockerfile：非 root 运行腿 ----
    def test_08_dockerfile_nonroot_user(self):
        self.assertIn("useradd -r -u 10001", DOCKERFILE, "Dockerfile 没建 butler 用户")
        self.assertRegex(DOCKERFILE, r"(?m)^USER butler\s*$", "Dockerfile 没切到 USER butler")
        self.assertIn("homesdk-0.3.2-py3-none-any.whl", DOCKERFILE,
                      "homesdk 0.3.2 wheel 安装腿不在＝0fa806a 那笔又被抹了")

    # ---- 六、部署面⛔ 凭据字面量（现读 b206c：NAS 面 dev 有 3 枚、我这侧 dev 有 1 枚）----
    def test_09_deploy_face_has_no_credential_literals(self):
        offenders = []
        for name in CONFIG_FILES:
            path = ROOT / name
            if not path.exists():
                continue
            offenders += credential_literals(path.read_text(encoding="utf-8").replace("\r\n", "\n"), name)
        self.assertEqual(offenders, [], f"受跟踪的部署面文件里带活凭据：{offenders}")

    def test_10_credential_ruler_bites_on_synthetic_literal(self):
        """负控：同一把尺必须咬得住合成字面量，且在占位符/插值/用户名形态上不误报。

        夹具值⛔ 从 `.env` 或 NAS 面抄真值（本档 10-13 初版就是这么躺着两枚活凭据的，
        由 test_11 收口）；合成值与真值同形制（≥12 位、字母数字混排），⛔ 只需同形、⛔ 需同值。
        """
        sample = ("services:\n"
                  "  x:\n"
                  "    environment:\n"
                  f"      - BARK_KEY={SYNTH_BARK_LITERAL}\n"
                  f"      - BUTLER_WEB_PASSWORD={SYNTH_WEBPASS_LITERAL}\n"
                  "      - MQTT_PASSWORD=${MQTT_PASSWORD}\n"
                  "      - NEW_API_KEY=your_new_api_key_here\n"
                  "      - BUTLER_WEB_USER=admin\n"
                  f"      - MQTT_HOST={LAN_IP}\n")
        hits = credential_literals(sample, "sample")
        self.assertEqual([h.split("@")[0] for h in hits], ["BARK_KEY", "BUTLER_WEB_PASSWORD"],
                         f"尺不咬或误报，实得 {hits}")
        for h in hits:
            for lit in (SYNTH_BARK_LITERAL[:8], SYNTH_WEBPASS_LITERAL[:8]):
                self.assertNotIn(lit, h, "尺把凭据值本体打印出来了＝泄露面自己造了一枚")
        self.assertEqual(credential_literals(SYNTH_OK_SAMPLE, "sample"), [],
                         f"合规形态被误报：{credential_literals(SYNTH_OK_SAMPLE, 'sample')}")

    # ---- 七、本仓码面⛔ 抄 `.env` 真值（10-13 现量到 test_10 的夹具里躺着两枚活凭据）----
    def test_11_code_face_carries_no_env_literal(self):
        """负控在同档 test_10：那把尺咬得住合成字面量；本格咬的是**真值被抄进码面**。

        作用域＝`tests/`＋`butler/`＋`scripts/`＋仓库根四枚配置件；
        ⛔ 覆盖 `doc/`（出资人的审计件与归档件，本席⛔ 改它内容，泄露面归轮换）。
        """
        hits = _env_literal_hits()
        if hits is None:
            self.skipTest(".env 不在位 ⇒ 本腿未量，⛔ 记成绿")
        # 先证这把尺会咬：值程序化现读，⛔ 手敲凭据字面量（本档 test_10 的前科就是手敲）
        values = _env_long_values()
        if not values:
            self.skipTest("凭据语义键的长值比对集为空 ⇒ 本腿未量，⛔ 记成绿")
        probe_key = sorted(values)[0]
        probe = ROOT / "tests" / "_b206_env_literal_selftest.py"
        try:
            probe.write_text(f"X = '{values[probe_key]}'\n", encoding="utf-8")
            bitten = [(r, k) for r, k in _env_literal_hits() if r.endswith(probe.name)]
            self.assertEqual(bitten, [(probe.relative_to(ROOT).as_posix(), probe_key)],
                             "尺咬不住真值＝恒绿尺，⛔ 它判的 0 不作数")
        finally:
            probe.unlink(missing_ok=True)
        offenders = [f"{k}@{r}" for r, k in _env_literal_hits()]
        self.assertEqual(offenders, [],
                         f"码面抄了 .env 真值（只出键名，⛔ 出值）：{offenders}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
