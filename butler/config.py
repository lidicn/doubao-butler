"""配置加载：环境变量 > config.json（人格/成员/规则）> 默认值。

所有敏感字段（密码、token）只从环境变量读取，绝不写入 config.json。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("butler.config")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


# 问询窗口的唯一默认值：字段默认与 env 兜底共用同一个常量，禁两处各写一遍秒数。
PROACTIVE_INQUIRY_TIMEOUT_DEFAULT = 3600


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class MemberConfig:
    """成员人格与称呼配置（与 memory-agent 成员名单对齐）。"""

    id: str = ""
    name: str = ""
    nickname: str = ""          # 管家对TA的称呼，如 "小凯"
    room: str = ""              # 关联房间
    tone: str = ""              # 语气描述
    topics_allow: list[str] = field(default_factory=list)
    topics_block: list[str] = field(default_factory=list)


@dataclass
class PersonaConfig:
    """系统人格 prompt 与全局设定。"""

    system: str = (
        "你是「豆包管家」，一个住在客厅电视里的家庭 AI 管家。"
        "你温和、有分寸、记得住每位家人的喜好，说话自然像家人聊天，不冗长、不油腻。"
        "你会根据在场的人调整称呼与话题，主动关怀但不打扰。"
    )
    greeting_template: str = "{nickname}回来啦，今天过得怎么样？"
    members: list[MemberConfig] = field(default_factory=list)


@dataclass
class Settings:
    # Web
    host: str = "0.0.0.0"
    port: int = 8095
    base_url: str = "http://192.168.2.200:8095"
    web_user: str = ""
    web_password: str = ""
    doubao_webhook_token: str = ""  # P0-9：豆包 webhook 鉴权钥匙（WO-ME-203：空=默认拒绝，不再放行）
    doubao_webhook_allow_unauthenticated: bool = False  # WO-ME-203：显式 true 才允许无凭证请求（仅内网调试）
    allow_no_auth: bool = False  # WO-ME-204：显式 true 才允许未配置 web_user 时放行全站鉴权（仅内网调试）

    # MQTT
    mqtt_host: str = "192.168.2.200"
    mqtt_port: int = 1883
    mqtt_user: str = "butler"
    mqtt_password: str = ""
    mqtt_client_id: str = "butler-core"

    # TV
    tv_http_url: str = "http://192.168.2.238:8080"
    tv_mqtt_prefix: str = "tv/livingroom"
    tvpilot_http_url: str = "http://192.168.2.200:8090"
    deskpilot_http_url: str = "http://192.168.2.201:8765"
    deskpilot_api_token: str = ""

    # 任务看板：TP/DP 上报状态的简单 token
    task_report_token: str = ""
    task_api_base_url: str = "http://192.168.2.200:8095"  # 管家 API 基地址，发给 TP/DP 的上报命令用

    # NAS 运维（new-api 数据库直连 + docker.sock）
    newapi_db_path: str = "/app/data/new-api.db"          # 容器内 new-api SQLite 路径
    docker_socket_path: str = "/var/run/docker.sock"       # docker.sock 挂载路径

    # LLM（大脑）：默认走 new-api（OpenAI 兼容网关，支持 function calling）
    new_api_url: str = "http://192.168.2.200:3001/v1"
    new_api_key: str = ""
    new_api_model: str = "deepseek-v4-flash"

    # doubao2api：纯多模态执行（vision/image/music/tts），不走 tools
    doubao_api_url: str = "http://192.168.2.200:9090/v1/chat/completions"
    doubao_api_key: str = ""
    doubao_model: str = "doubao-seed-1-6"
    doubao_base_url: str = "http://192.168.2.200:9090"
    llm_timeout: int = 30
    # LLM 退避重试（防御性）：偶发 429/5xx/网络抖动不让 ReAct 闭环断开
    llm_retry_max: int = 3          # 最多重试次数（含 429 与 5xx 与连接瞬态）
    llm_retry_backoff: float = 2.0  # 指数退避基数（秒）

    # 语音唤醒词（入口剥离前缀，如「豆包管家」）
    wake_phrase: str = "豆包管家"

    # 小爱音箱输出（HA 服务）：让 butler 主动对小爱说话/唤醒
    xiaomi_notify_entity: str = "notify.xiaomi_cn_330794773_x08a_execute_text_directive_a_5_4"
    xiaomi_wake_entity: str = ""
    room_xiaoai_mapping: dict = field(default_factory=dict)

    # 出声目标：tv=默认（电视 play_url + 兜底小爱）；mqtt=只发布 butler/speak/out，由 Node-RED 独占「嘴巴」（避免 TV 与 小爱 双声）
    speak_target: str = "tv"
    # memory-agent
    memory_agent_url: str = "http://192.168.2.200:8086"
    memory_agent_mcp_url: str = "http://192.168.2.200:8086/mcp"
    memory_agent_token: str = ""
    # REST（/api/vision/*）走 Basic 认证，与 MCP mcp_ 令牌隔离
    memory_agent_user: str = ""
    memory_agent_pass: str = ""
    # MA 侧为管家签发的专用 Bearer（members/presence/insights 窄接口）
    memory_agent_butler_token: str = ""
    # MA v0.6：管家专属 app_token（source=butler，记忆统一入库用，强制派生 source）
    memory_agent_app_token: str = ""

    # AutoForge ask 桥（WO-BUT-012 / WO-BUT-020）
    autoforge_base_url: str = ""
    autoforge_api_token: str = ""
    autoforge_inbox_key: str = ""  # AF ask 通道 HMAC 签名密钥（必须与 AF 侧一致）
    autoforge_answer_window_s: float = 900.0  # AF ask 真人回答窗口（与 AF 侧超时对齐）

    # go2rtc（技能 live_vlm 模式直接取帧）
    go2rtc_base_url: str = "http://192.168.2.200:1984"
    go2rtc_user: str = ""
    go2rtc_pass: str = ""

    # HA
    ha_url: str = "http://192.168.2.200:8123"
    ha_token: str = ""

    # 小爱直连播放（绕过 HA media_player 的单曲循环）：复用 xiaomi_miot 已登录的小米云端 token
    # 用 mina player_play_url 一次性投射 mp3，对应 xiaomusic 不循环的机制
    xiaomi_service_token: str = ""
    xiaomi_user_id: str = ""
    xiaomi_ssecurity: str = ""      # 可选，micoapi 直连接口暂不需要签名
    xiaomi_pass_token: str = ""     # 用于自动刷新 service_token（扫码登录获得，长期有效）
    xiaomi_play_type: str = "2"     # player_play_url 的 type 字段，可改 1/2/"url" 调试

    # Bark
    bark_url: str = "http://192.168.2.200:18273"
    bark_key: str = ""
    bark_encrypt_key: str = ""
    bark_encrypt_iv: str = ""

    # TTS
    tts_primary: str = "edge-tts"
    tts_edge_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_kokoro_url: str = "http://doubao-butler-kokoro:8880/v1/audio/speech"
    nowvoice_token: str = ""
    # 批38 P0-4：NowVoice 生成+下载的单次总预算（秒）。旧形状＝5×(30+2)＝160 s 无总上限。
    nowvoice_timeout_sec: int = 45
    nowvoice_voice: str = "afeb4759"  # Xiaochen 小晨 
    tts_kokoro_voice: str = "zm_Yunzhe"
    tts_speed: float = 1.0
    tts_volume: int = 80
    tts_cache_enabled: bool = True

    # 对话/唤醒
    cooldown_seconds: int = 600
    active_dialog_timeout: int = 10
    waiting_seconds: int = 5
    dedup_window_days: int = 7
    dedup_jaccard_threshold: float = 0.6
    dnd_windows: list[str] = field(default_factory=list)

    # v2.8: 主动服务预算（每角色每日上限，默认 3 次可配）+ 负反馈降频
    proactive_daily_limit: int = 3
    proactive_role_limits: dict = field(default_factory=dict)
    proactive_reduce_floor: int = 1
    # 格11（G5 2026-09-30）：此前硬编码 30 秒，用户来不及回复即 100% 判 timeout。
    proactive_inquiry_timeout_sec: int = PROACTIVE_INQUIRY_TIMEOUT_DEFAULT
    proactive_negative_words: list[str] = field(default_factory=lambda: [
        "不要", "别吵", "闭嘴", "安静", "烦", "别播", "停止", "取消",
    ])

    # 数据目录
    data_dir: str = "/app/data"
    tts_dir: str = "/app/tts"

    persona: PersonaConfig = field(default_factory=PersonaConfig)

    @classmethod
    def load(cls) -> "Settings":
        s = cls(
            host=_env("BUTLER_HOST", "0.0.0.0"),
            port=_env_int("BUTLER_PORT", 8095),
            base_url=_env("BUTLER_BASE_URL", "http://192.168.2.200:8095"),
            web_user=_env("BUTLER_WEB_USER"),
            web_password=_env("BUTLER_WEB_PASSWORD"),
            doubao_webhook_token=_env("DOUBAO_WEBHOOK_TOKEN"),
            doubao_webhook_allow_unauthenticated=_env_bool("DOUBAO_WEBHOOK_ALLOW_UNAUTHENTICATED", False),
            allow_no_auth=_env_bool("BUTLER_ALLOW_NO_AUTH", False),
            mqtt_host=_env("MQTT_HOST", "192.168.2.200"),
            mqtt_port=_env_int("MQTT_PORT", 1883),
            mqtt_user=_env("MQTT_USER", "butler"),
            mqtt_password=_env("MQTT_PASSWORD"),
            mqtt_client_id=_env("MQTT_CLIENT_ID", "butler-core"),
            tv_http_url=_env("TV_HTTP_URL", "http://192.168.2.238:8080"),
            tv_mqtt_prefix=_env("TV_MQTT_PREFIX", "tv/livingroom"),
            tvpilot_http_url=_env("TVPILOT_HTTP_URL", "http://192.168.2.200:8090"),
            deskpilot_http_url=_env("DESKPILOT_HTTP_URL", "http://192.168.2.201:8765"),
            deskpilot_api_token=_env("DESKPILOT_API_TOKEN"),  # WO-BUT-018: 禁止默认值，缺键显式报错
            task_report_token=_env("TASK_REPORT_TOKEN"),  # WO-BUT-018: 禁止默认值，缺键显式报错
            task_api_base_url=_env("TASK_API_BASE_URL", "http://192.168.2.200:8095"),
            newapi_db_path=_env("NEWAPI_DB_PATH", "/app/data/new-api.db"),
            docker_socket_path=_env("DOCKER_SOCKET_PATH", "/var/run/docker.sock"),
            doubao_api_url=_env("DOUBAO_API_URL", "http://192.168.2.200:9090/v1/chat/completions"),
            doubao_api_key=_env("DOUBAO_API_KEY"),  # WO-BUT-017: 禁止默认值，缺键显式报错
            doubao_model=_env("DOUBAO_MODEL", "doubao-seed-1-6"),
            llm_timeout=_env_int("LLM_TIMEOUT", 30),
            llm_retry_max=_env_int("LLM_RETRY_MAX", 3),
            llm_retry_backoff=_env_float("LLM_RETRY_BACKOFF", 2.0),
            new_api_url=_env("NEW_API_URL", "http://192.168.2.200:3001/v1"),
            new_api_key=_env("NEW_API_KEY"),
            new_api_model=_env("NEW_API_MODEL", "deepseek-chat"),
            doubao_base_url=_env("DOUBAO_BASE_URL", "http://192.168.2.200:9090"),
            wake_phrase=_env("WAKE_PHRASE", "豆包管家"),
            xiaomi_notify_entity=_env("XIAOMI_NOTIFY_ENTITY", "notify.xiaomi_cn_330794773_x08a_execute_text_directive_a_5_4"),
            xiaomi_wake_entity=_env("XIAOMI_WAKE_ENTITY", ""),
            speak_target=_env("BUTLER_SPEAK_TARGET", "tv"),
            memory_agent_url=_env("MEMORY_AGENT_URL", "http://192.168.2.200:8086"),
            memory_agent_mcp_url=_env("MEMORY_AGENT_MCP_URL", "http://192.168.2.200:8086/mcp"),
            memory_agent_token=_env("MEMORY_AGENT_TOKEN"),
            memory_agent_user=_env("MEMORY_AGENT_USER"),
            memory_agent_pass=_env("MEMORY_AGENT_PASS"),
            memory_agent_butler_token=_env("MEMORY_AGENT_BUTLER_TOKEN"),
            memory_agent_app_token=_env("MEMORY_AGENT_APP_TOKEN"),
            autoforge_base_url=_env("AUTOFORGE_BASE_URL", ""),
            autoforge_api_token=_env("AUTOFORGE_API_TOKEN", ""),
            autoforge_inbox_key=_env("AUTOFORGE_INBOX_KEY", ""),
            autoforge_answer_window_s=float(_env("AUTOFORGE_ANSWER_WINDOW_S", "900")),
            go2rtc_base_url=_env("GO2RTC_BASE_URL", "http://192.168.2.200:1984"),
            go2rtc_user=_env("GO2RTC_USER"),
            go2rtc_pass=_env("GO2RTC_PASS"),
            ha_url=_env("HA_URL", "http://192.168.2.200:8123"),
            ha_token=_env("HA_TOKEN"),
            xiaomi_service_token=_env("XIAOMI_SERVICE_TOKEN"),
            xiaomi_user_id=_env("XIAOMI_USER_ID"),
            xiaomi_ssecurity=_env("XIAOMI_SSECURITY"),
            xiaomi_pass_token=_env("XIAOMI_PASS_TOKEN"),
            xiaomi_play_type=_env("XIAOMI_PLAY_TYPE", "2"),
            bark_url=_env("BARK_URL", "http://192.168.2.200:18273"),
            bark_key=_env("BARK_KEY"),
            bark_encrypt_key=_env("BARK_ENCRYPT_KEY", ""),
            bark_encrypt_iv=_env("BARK_ENCRYPT_IV", ""),
            tts_primary=_env("TTS_PRIMARY", "edge-tts"),
            tts_edge_voice=_env("TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural"),
            tts_kokoro_url=_env("TTS_KOKORO_URL", "http://doubao-butler-kokoro:8880/v1/audio/speech"),
            tts_kokoro_voice=_env("TTS_KOKORO_VOICE", "zm_Yunzhe"),
            nowvoice_token=_env("NOWVOICE_TOKEN", ""),
            nowvoice_voice=_env("NOWVOICE_VOICE", "afeb4759"),
            nowvoice_timeout_sec=_env_int("NOWVOICE_TIMEOUT_SEC", 45),
            tts_speed=_env_float("TTS_SPEED", 1.0),
            tts_volume=_env_int("TTS_VOLUME", 80),
            tts_cache_enabled=_env_bool("TTS_CACHE_ENABLED", True),
            cooldown_seconds=_env_int("COOLDOWN_SECONDS", 600),
            active_dialog_timeout=_env_int("ACTIVE_DIALOG_TIMEOUT", 10),
            waiting_seconds=_env_int("WAITING_SECONDS", 5),
            dedup_window_days=_env_int("DEDUP_WINDOW_DAYS", 7),
            dedup_jaccard_threshold=_env_float("DEDUP_JACCARD_THRESHOLD", 0.6),
            proactive_daily_limit=_env_int("PROACTIVE_DAILY_LIMIT", 3),
            proactive_reduce_floor=_env_int("PROACTIVE_REDUCE_FLOOR", 1),
            proactive_inquiry_timeout_sec=_env_int("PROACTIVE_INQUIRY_TIMEOUT_SEC", PROACTIVE_INQUIRY_TIMEOUT_DEFAULT),
            data_dir=_env("DATA_DIR", "/app/data"),
            tts_dir=_env("TTS_DIR", "/app/tts"),
        )
        dnd = _env("DND_WINDOWS")
        if dnd:
            s.dnd_windows = [w.strip() for w in dnd.split(",") if w.strip()]
        neg = _env("PROACTIVE_NEGATIVE_WORDS")
        if neg:
            s.proactive_negative_words = [w.strip() for w in neg.split(",") if w.strip()]
        # PROACTIVE_ROLE_LIMITS="butler=5,lidicn=2"：按角色覆盖每日上限
        for pair in _env("PROACTIVE_ROLE_LIMITS", "").split(","):
            role, sep, val = pair.partition("=")
            role, val = role.strip(), val.strip()
            if sep and role and val.isdigit():
                s.proactive_role_limits[role] = int(val)
        s._load_persona()
        # WO-BUT-017: 必需密钥缺键显式报错，禁止静默回退到泄露口令
        if not s.doubao_api_key:
            raise RuntimeError(
                "DOUBAO_API_KEY is required but not set in environment. "
                "Please set it in .env or docker-compose environment."
            )
        # WO-BUT-018: 必需鉴权令牌缺键显式报错（在役值，.env 未覆盖）
        for _attr, _envvar in (("deskpilot_api_token", "DESKPILOT_API_TOKEN"),
                                ("task_report_token", "TASK_REPORT_TOKEN")):
            if not getattr(s, _attr):
                raise RuntimeError(
                    f"{_envvar} is required but not set in environment. "
                    "Please set it in .env or docker-compose environment."
                )
        # WO-BUT-022 R-37: web_password 空串=整站免鉴权，启动拒绝
        if not s.web_password:
            raise RuntimeError(
                "BUTLER_WEB_PASSWORD is required but not set in environment. "
                "Empty password disables authentication entirely. "
                "Please set a strong password in .env or docker-compose environment."
            )
        # TTS_DIR（审计 2026-10-04）：目录缺失时 `app.py` 构建路由的 `StaticFiles(directory=s.tts_dir)`
        # 当场 RuntimeError ⇒ 整个进程起不来，而报错位置离根因隔两个文件。这里建目录
        # （`store/db.py` 对 DATA_DIR 就是这么做的）。建不出来时分两档，⛔ 一律 raise：
        #   · 环境变量显式给了 TTS_DIR ⇒ 显式失败。吞掉它＝把「盘上没这个目录」伪装成「配置没问题」（验收腿 t13）。
        #   · 用的是内置默认值 /app/tts ⇒ 警告＋继续。宿主／开发面根本没有 /app，
        #     一律 raise 会把⛔ 碰 TTS 的 `get_settings()` 消费者一起判死（现量 51 枚 loader ERROR，
        #     读数 1004_b32/discover_full_measure_b33.txt）。验收腿 t14。
        try:
            Path(s.tts_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if "TTS_DIR" in os.environ:
                raise RuntimeError(
                    f"TTS_DIR={s.tts_dir} cannot be created: {e}. "
                    "The /tts mount is served from this directory; startup would fail anyway."
                ) from e
            logger.warning(
                "TTS_DIR=%s (built-in default) cannot be created: %s — /tts will not serve audio "
                "until that directory exists; keeping every other Settings consumer alive.",
                s.tts_dir, e)
        return s

    def _load_persona(self) -> None:
        """从 DATA_DIR/config.json 读取人格/成员/规则覆盖（不覆盖环境变量里的连接配置）。"""
        cfg_path = Path(self.data_dir) / "config.json"
        if not cfg_path.exists():
            return
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return
        pc = self.persona
        if isinstance(raw.get("system"), str):
            pc.system = raw["system"]
        if isinstance(raw.get("greeting_template"), str):
            pc.greeting_template = raw["greeting_template"]
        members = raw.get("members")
        if isinstance(members, list):
            pc.members = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                pc.members.append(
                    MemberConfig(
                        id=str(m.get("id", "")),
                        name=str(m.get("name", "")),
                        nickname=str(m.get("nickname", "")),
                        room=str(m.get("room", "")),
                        tone=str(m.get("tone", "")),
                        topics_allow=list(m.get("topics_allow", []) or []),
                        topics_block=list(m.get("topics_block", []) or []),
                    )
                )

    # --- 便捷查询 ---

    def member_by_name(self, name: str) -> MemberConfig | None:
        name = (name or "").strip()
        for m in self.persona.members:
            if m.name == name or m.nickname == name:
                return m
        return None

    def nickname_of(self, name: str) -> str:
        mc = self.member_by_name(name)
        if mc and mc.nickname:
            return mc.nickname
        return name or "家人"

    def mask_secrets(self) -> dict[str, Any]:
        """返回用于日志/状态的脱敏配置快照。"""
        d = self.__dict__.copy()
        for k in ("mqtt_password", "doubao_api_key", "new_api_key", "memory_agent_token", "memory_agent_pass", "memory_agent_butler_token", "memory_agent_app_token", "go2rtc_pass", "ha_token", "web_password", "bark_key", "doubao_webhook_token", "xiaomi_service_token", "xiaomi_pass_token", "xiaomi_ssecurity", "autoforge_api_token", "autoforge_inbox_key"):
            if d.get(k):
                d[k] = "***"
        return d


# 全局单例（在 app.lifespan 中初始化）
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def set_settings(s: Settings) -> None:
    global _settings
    _settings = s
