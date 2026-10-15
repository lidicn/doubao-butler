"""角色（Persona）登记表：多角色对话的核心实体。

角色 = 名称 + 性别 + 音色(nowvoice) + 拥有的技能 + 可出现的房间 + 输出设备 + 系统提示词。
存 data/roles/{id}.json（首次启动播种默认角色，之后以文件为准，WebUI 可改）。
已归档角色（data/roles/_archive/ 内出现过的 id）不播种，否则归档会被重启静默撤销并丢弃用户编辑。

与成员(Member)区分：成员是「谁在家」（来自 MA 人脸），角色是「以哪个管家身份说话」。
技能经 role 字段归属到某个角色；对话线程(conversation_id)按角色隔离（见 D1）。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from butler.core.atomic_json import write_json_atomic
from butler.logging_setup import get_logger
from butler.param import to_bool

logger = get_logger("butler.roles")

# 批56（P1-8 剩下的「完全无锁」半腿）：同 id 两个标签页同时保存＝踩同一枚 `{id}.json.tmp`（原子 helper 的
# 临时文件是固定名）；更要紧的是 `delete()` 的「unlink ＋ del 内存」与 `upsert()` 的「改内存 ＋ 写盘」可以交错，
# 留下「盘上有、内存没有」⇒ 下次 `load()` 把刚删掉的角色读回来＝账实不符。
# ⛔ 可重入：写腿各只在最外层拿一次锁，锁内只走 `_write_held`（普通 Lock 才有 `locked()`，批47 判例）。
_ROLES_LOCK = threading.Lock()


@dataclass
class Role:
    id: str
    name: str
    gender: str = "女"
    is_primary: bool = False
    voice: str = "zh-CN-XiaoxiaoNeural"   # nowvoice 音色
    tts_backend: str = "nowvoice"          # nowvoice | kokoro
    nowvoice_voice: str = ""                       # nowvoice 8-hex voice ID
    presence_rooms: list[str] = field(default_factory=lambda: ["*"])  # "*"=全屋
    output_devices: list[str] = field(default_factory=list)           # DeviceRegistry id 列表
    system: str = ""
    skills: list[str] = field(default_factory=list)
    enabled: bool = True
    fallback: str = "bark"                 # 设备离线兜底：silent | tv | bark
    # ---- Phase 6：按角色唤醒短语路由 ----
    wake_words: list[str] = field(default_factory=list)   # 触发该角色的唤醒短语
    bound_rooms: list[str] = field(default_factory=list)  # 私人助理：仅这些房间可唤醒；空=不限
    scope: str = "family"                  # "family"(全员通用) | "private"(私人助理)
    member: str = ""                       # 私人助理绑定的成员 id（如 lidicn/Kevin/Emily）

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "gender": self.gender,
            "is_primary": self.is_primary, "voice": self.voice, "tts_backend": self.tts_backend, "nowvoice_voice": self.nowvoice_voice,
            "presence_rooms": self.presence_rooms, "output_devices": self.output_devices,
            "system": self.system, "skills": self.skills, "enabled": self.enabled,
            "fallback": self.fallback, "wake_words": self.wake_words,
            "bound_rooms": self.bound_rooms, "scope": self.scope, "member": self.member,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Role":
        return cls(
            id=str(d.get("id", "")), name=str(d.get("name", "")), gender=str(d.get("gender", "女")),
            is_primary=to_bool(d.get("is_primary"), "is_primary", default=False), voice=str(d.get("voice", "zh-CN-XiaoxiaoNeural")),
            tts_backend=str(d.get("tts_backend", "nowvoice")), nowvoice_voice=str(d.get("nowvoice_voice", "")),
            presence_rooms=list(d.get("presence_rooms", ["*"]) or ["*"]),
            output_devices=list(d.get("output_devices", []) or []),
            system=str(d.get("system", "")), skills=list(d.get("skills", []) or []),
            enabled=to_bool(d.get("enabled"), "enabled", default=True), fallback=str(d.get("fallback", "bark")),
            wake_words=list(d.get("wake_words", []) or []),
            bound_rooms=list(d.get("bound_rooms", []) or []),
            scope=str(d.get("scope", "family")), member=str(d.get("member", "")),
        )


# 默认角色（首次播种；已存在文件不覆盖）
DEFAULT_ROLES: list[dict] = [
    {
        "id": "butler", "name": "豆包管家", "gender": "女", "is_primary": True,
        "voice": "zh-CN-XiaoxiaoNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["*"], "output_devices": ["tv_living", "xiao_living"],
        "system": "你是「豆包管家」，一个住在客厅电视里的家庭 AI 管家。"
                  "你温和、有分寸、记得住每位家人的喜好，说话自然像家人聊天，不冗长、不油腻。"
                  "你会根据在场的人调整称呼与话题，主动关怀但不打扰。",
        "skills": ["hello"], "enabled": True, "fallback": "bark",
        "wake_words": ["豆包管家", "豆包"], "bound_rooms": [], "scope": "family", "member": "",
    },
    {
        "id": "xiaoyue", "name": "晓月", "gender": "女", "is_primary": False,
        "voice": "zh-CN-XiaoyiNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["厨房", "客厅"], "output_devices": ["xiao_living_right"],
        "system": "你是家庭健康管家「晓月」，温柔专业。当用户展示食物时，用一句口语化点评"
                  "（含热量与一句关怀），亲切不说教。",
        "skills": ["food-calorie"], "enabled": True, "fallback": "bark",
        "wake_words": ["晓月"], "bound_rooms": [], "scope": "family", "member": "",
    },
    {
        "id": "jarvis", "name": "贾维斯", "gender": "男", "is_primary": False,
        "voice": "zh-CN-YunyangNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["书房"], "output_devices": ["xiao_study"],
        "system": "你是 lidicn（理叔）的私人 AI 助理「贾维斯」，只在他书房里为他服务。"
                  "你称呼他「老板」或「理叔」，语气干练、懂技术、能帮他查资料、写脚本、管日程。"
                  "不对外人说话，不在其他房间回应。",
        "skills": [], "enabled": True, "fallback": "bark",
        "wake_words": ["贾维斯"], "bound_rooms": ["书房"], "scope": "private", "member": "lidicn",
    },
    {
        "id": "caesar", "name": "凯撒", "gender": "男", "is_primary": False,
        "voice": "zh-CN-YunxiNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["Kevin房间"], "output_devices": ["xiao_kevin"],
        "system": "你是 Kevin 的专属 AI 小伙伴「凯撒」，像个大哥哥。语气活泼、爱打趣，"
                  "陪他聊学习、游戏、百科，鼓励他探索。不对外人说话，只在 Kevin 房间回应。",
        "skills": [], "enabled": True, "fallback": "bark",
        "wake_words": ["凯撒"], "bound_rooms": ["Kevin房间"], "scope": "private", "member": "Kevin",
    },
    {
        "id": "luna", "name": "露娜", "gender": "女", "is_primary": False,
        "voice": "zh-CN-XiaochenNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["Emily房间"], "output_devices": ["xiao_emily"],
        "system": "你是 Emily 的专属 AI 小伙伴「露娜」，温柔可爱，像大姐姐。"
                  "陪她讲故事、唱歌、学英语，多用鼓励和想象。不对外人说话，只在 Emily 房间回应。",
        "skills": [], "enabled": True, "fallback": "bark",
        "wake_words": ["露娜"], "bound_rooms": ["Emily房间"], "scope": "private", "member": "Emily",
    },
    {
        # 豆包 APP 里的文字对话角色（doc/06-场景设计/顾安恒专属对话.md）：
        # 链路是 webhook 取帧→视觉分析→推回对话，⛔ 电视输出设备与唤醒词——
        # 多一个唤醒词就等于在家里凭空多出一个会应答的名字。
        "id": "gu_anheng", "name": "顾安恒", "gender": "男", "is_primary": False,
        "voice": "zh-CN-YunjianNeural", "tts_backend": "nowvoice",
        "presence_rooms": ["*"], "output_devices": [],
        "system": "你是家庭安防专员「顾安恒」，冷静、可靠、观察力敏锐，"
                  "回答简短，涉及摄像头画面时先说明正在调取哪个房间。",
        "skills": ["monitor-camera"], "enabled": True, "fallback": "bark",
        "wake_words": [], "bound_rooms": [], "scope": "private", "member": "lidicn",
    },
]

# 角色名册的唯一真源：⛔ 在任何引用面再抄一遍字面量（P1-10 的洞就是抄出来的——
# 投喂面抄成 6 枚、真源只播 5 枚，两边都不报错，缺的那个角色默默走不通）。
ALL_ROLE_IDS: tuple[str, ...] = tuple(d["id"] for d in DEFAULT_ROLES)


class RoleRegistry:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "roles"
        self.roles: dict[str, Role] = {}

    def load(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*.json"):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("id"):
                    self.roles[raw["id"]] = Role.from_dict(raw)
            except Exception as e:
                logger.warning("role file %s parse failed: %s", f, e)
        created = self.ensure_defaults(DEFAULT_ROLES)
        if created:
            logger.info("default roles seeded: %s", created)

    def archived_ids(self) -> set:
        """data/roles/_archive/ 下出现过的角色 id（决策 10 的归档区）。"""
        ids = set()
        ad = self.dir / "_archive"
        if not ad.is_dir():
            return ids
        for f in ad.rglob("*.json"):
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("archived role file %s parse failed: %s", f, e)
                continue
            if isinstance(obj, dict) and obj.get("id"):
                ids.add(str(obj["id"]))
        return ids

    def ensure_defaults(self, defaults: list[dict]) -> list[str]:
        created = []
        skipped = []
        archived = self.archived_ids()
        for d in defaults:
            rid = d.get("id")
            if not rid:
                continue
            if rid not in self.roles:
                if rid in archived:
                    skipped.append(rid)
                    continue
                role = Role.from_dict(d)
                with _ROLES_LOCK:
                    self.roles[rid] = role
                    self._write_held(role)
                created.append(rid)
        if skipped:
            logger.info("default roles not seeded (archived): %s", skipped)
        return created

    def _target_path(self, rid: str):
        """DB57: 校验角色 id 不会写到 roles/ 目录之外（防路径穿越）。"""
        target = (self.dir / f"{rid}.json").resolve()
        root = self.dir.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"role id 非法：{rid!r} 会写到 roles/ 目录之外")
        return target

    def _write(self, role: Role) -> None:
        with _ROLES_LOCK:
            self._write_held(role)

    def _write_held(self, role: Role) -> None:
        """单个角色档落盘。**调用方必须已持有 `_ROLES_LOCK`**（`upsert`/`ensure_defaults` 已在锁内，⛔ 再拿一次）。"""
        try:
            # 表行 45 P1-13：半截 {id}.json 会让 load() 跳过该角色，ensure_defaults 再把出厂默认塞回来
            # DB57: 用 _target_path 校验路径，防穿越写
            target = self._target_path(role.id)
            write_json_atomic(target, role.to_dict())
        except Exception as e:
            logger.warning("write role %s failed: %s", role.id, e)

    # ---- 查询 ----

    def all(self) -> list[Role]:
        return list(self.roles.values())

    def get(self, rid: str) -> Role | None:
        return self.roles.get(rid)

    def resolve_for_skill(self, skill: dict) -> Role:
        """技能归属角色：skill.role 存在且启用则用之，否则回退主角色/butler。"""
        rid = (skill.get("role") or "butler") if isinstance(skill, dict) else "butler"
        role = self.roles.get(rid)
        if role and role.enabled:
            return role
        return self.roles.get("butler") or Role(id="butler", name="豆包管家")

    # ---- 编辑（WebUI）----

    def upsert(self, d: dict) -> Role:
        role = Role.from_dict(d)
        if not role.id:
            raise ValueError("role id required")
        with _ROLES_LOCK:
            self.roles[role.id] = role
            self._write_held(role)
        return role

    def delete(self, rid: str) -> bool:
        if rid == "butler":
            raise ValueError("主角色不可删除")
        if rid not in self.roles:
            return False
        # DB57: 校验路径，防穿越删除
        target = self._target_path(rid)
        # 批56：`unlink` 与 `del self.roles[rid]` 必须是一段（与并发的 upsert 互斥，⛔ 交错）
        with _ROLES_LOCK:
            try:
                target.unlink()
            except FileNotFoundError:
                pass  # 盘上本就没有＝已是删除终态
            except OSError as e:
                # P2-9 ②类第 10 枚（批42）：旧形吞掉这里的失败还照报 True，
                # 调用方以为删了、文件还在，下次 load() 又读回来＝账实不符。
                logger.warning("role file unlink failed for %s: %s", rid, e)
                return False
            del self.roles[rid]
        return True
