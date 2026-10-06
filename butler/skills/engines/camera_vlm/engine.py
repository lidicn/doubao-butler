"""camera_vlm 内置引擎：两种模式。

- ma_analyze（默认）：MA 实时识别（取帧 → VLM 人/场景 → ArcFace 补认），适合问候类
  需要身份/场景描述的技能；可选 LLM 按人设/档案组装口播文本。
- live_vlm：自定义提示词，go2rtc 直接取帧 → doubao VLM 直答，适合「拍照识别热量」
  这类一次性视觉问答（不依赖 MA 预设提示词）。

技术栈复用：rt.tv.health / rt.memory.analyze_room_live / rt.doubao.vision / rt.llm.chat。
"""
from __future__ import annotations

import base64
import random
import time
import urllib.parse

import httpx

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engine.camera_vlm")

# ArcFace 注册名 → 称呼；未命中回退 MA 档案 nickname / 配置 nickname
NICKNAMES = {"lidicn": "大佬", "Kevin": "凯文", "Emily": "爱美丽"}

_MESSY_WORDS = ("乱", "杂乱", "凌乱", "堆积", "散落", "满地", "脏")
_mess_state = {"date": ""}          # 环境提醒槽位：每天最多一次（进程内）

_DEFAULT_GREETING_SYSTEM = (
    "你是「豆包管家」，住在客厅电视里的家庭 AI 管家，说话自然亲切、简短不油腻。"
    "家人按了客厅按钮向你打招呼，你会收到视觉识别结果（在场成员与画面描述）。"
    "请生成一句口语化问候，重点放在对人的关心和当景的自然寒暄上，"
    "每次措辞要有变化、有惊喜感，不要套路化。"
    "除非信息里明确给出「环境提醒槽位」，否则不要提收拾/整洁/杂物这类话题；"
    "即使给了槽位，也只用一句轻轻带过。"
)

_WEEKDAY_KEY = {"Mon": "mon", "Tue": "tue", "Wed": "wed", "Thu": "thu",
                "Fri": "fri", "Sat": "sat", "Sun": "sun"}


class CameraVLMEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        skill = ctx.skill
        brain = skill.get("brain") or {}
        room = (skill.get("senses") or [{}])[0].get("room", "客厅")
        rt = ctx.rt
        meta: dict = {"room": room, "mode": brain.get("mode", "ma_analyze")}

        # 1) TV 在线探活（决定输出通道与降级）
        try:
            tv_online = await rt.tv.health()
        except Exception as e:
            logger.warning("tv health failed: %s", e)
            tv_online = False
        meta["tv_online"] = tv_online

        # 2) 感知
        if brain.get("mode") == "live_vlm":
            text, vlm_meta = await self._live_vlm(ctx, rt, brain, room)
            meta.update(vlm_meta)
            if not text:
                return SkillResult(ok=False, error="VLM 识别失败", status="error",
                                   meta=meta)
            final_text = text
            # 可选二次组装（brain.system 提供时）
            if brain.get("system"):
                composed = await self._compose(rt, brain, [f"视觉识别结果：{text}"])
                final_text = composed or text[: brain.get("max_chars", 80)]
        else:
            person, nickname, scene, analyze_ok = await self._ma_analyze(rt, brain, room)
            meta.update({"person": person, "nickname": nickname,
                         "scene": scene, "analyze_ok": analyze_ok})
            if not analyze_ok:
                return SkillResult(ok=True, text="我在，请说。",
                                   status="tv_offline" if not tv_online else "analyze_failed",
                                   meta=meta)
            # TV 离线时，若角色还有非电视通道（小爱等）仍应正常组装；
            # 只有「唯一的输出通道就是电视且电视离线」才退回应答。
            if not tv_online and not self._has_non_tv_output(rt, ctx):
                return SkillResult(ok=True, text="我在，请说。", status="tv_offline",
                                   meta=meta)
            final_text = await self._compose_greeting(rt, brain, person, nickname, scene)

        return SkillResult(ok=True, text=final_text, meta=meta)

    @staticmethod
    def _has_non_tv_output(rt, ctx) -> bool:
        """角色是否存在不依赖电视的输出通道（如小爱）。

        多设备下 TV 离线不等于没地方说话：晓月只走小爱，主角色也有小爱兜底。
        无法判断（无角色/无设备表）时返回 False，保持原「以 TV 为准」行为。
        """
        role = getattr(ctx, "role", None)
        if rt is None or getattr(rt, "devices", None) is None:
            return False
        if role is None or not getattr(role, "output_devices", None):
            return False
        try:
            for dev in rt.devices.resolve(role.output_devices):
                if getattr(dev, "type", "") != "tv":
                    return True
        except Exception as e:
            logger.warning("resolve role devices failed: %s", e)
        return False

    # ---- 模式一：MA 实时分析（身份 + 场景） ----

    async def _ma_analyze(self, rt, brain: dict, room: str) -> tuple[str, str, str, bool]:
        try:
            analysis = await rt.memory.analyze_room_live(room)
        except Exception as e:
            logger.warning("ma_analyze failed: %s", e)
            return "", "", "", False
        if not analysis.get("ok"):
            return "", "", "", False
        persons = analysis.get("persons") or []
        first = persons[0] if persons and isinstance(persons[0], dict) else {}
        person = str(first.get("name") or "").strip()
        if person in ("未识别成员", "未识别", "陌生人"):
            person = ""
        nickname = (NICKNAMES.get(person) or rt.settings.nickname_of(person)) if person else ""
        scene = str(analysis.get("scene") or "").strip()
        return person, nickname, scene, True

    async def _compose_greeting(self, rt, brain: dict, person: str,
                                nickname: str, scene: str) -> str:
        # 环境提醒槽位：确实杂乱 + 今天没提过 + 概率命中，才允许带一句
        today = time.strftime("%Y-%m-%d")
        messy = any(w in scene for w in _MESSY_WORDS)
        mess_slot = bool(brain.get("mess_slot")) and messy \
            and _mess_state["date"] != today and random.random() < 0.4
        if mess_slot:
            _mess_state["date"] = today

        facts = []
        if person:
            facts.append(f"人脸识别到成员：{person}（称呼：{nickname}）")
        else:
            facts.append("未通过人脸识别到成员（可能无人/背对/未识别）")
        if scene:
            facts.append(f"画面描述：{scene}")
        if mess_slot:
            facts.append("环境提醒槽位：画面确有杂物堆放，可用一句温和带过收拾话题")

        context = brain.get("context") or []
        if person and "member_profile" in context:
            facts.extend(await self._profile_facts(rt, person))
        if person and "memories" in context:
            try:
                mems = await rt.memory.retrieve(f"{person} 兴趣 喜好 习惯", member=person, limit=3)
                if mems:
                    facts.append("已知偏好：" + "；".join(m[:40] for m in mems[:3]))
            except Exception as e:
                logger.warning("memories retrieve failed: %s", e)

        composed = await self._compose(
            rt, brain, facts, default_system=_DEFAULT_GREETING_SYSTEM)
        if composed:
            return composed
        # 模板兜底
        text = f"{nickname}，我在呢。" if nickname else "我在呢，请说。"
        if mess_slot:
            text += "客厅有点乱，记得收拾一下哦。"
        return text

    async def _profile_facts(self, rt, person: str) -> list[str]:
        facts: list[str] = []
        try:
            profile = await rt.memory.get_member_profile(person)
        except Exception as e:
            logger.warning("get_member_profile failed: %s", e)
            return facts
        if not isinstance(profile, dict) or not profile:
            return facts
        if profile.get("nickname"):
            facts.append(f"成员档案称呼：{profile['nickname']}")
        if profile.get("school"):
            facts.append(f"学校：{profile['school']}")
        courses = profile.get("courses") or {}
        today_key = _WEEKDAY_KEY.get(time.strftime("%a"), "")
        if today_key and isinstance(courses, dict) and courses.get(today_key):
            facts.append(f"今日课程：{'、'.join(map(str, courses[today_key]))}")
        routine = (profile.get("routine") or {}).get("weekday") or {}
        if routine:
            facts.append("工作日作息：" + "，".join(f"{k}={v}" for k, v in routine.items()
                                                if isinstance(v, (str, list))))
        if profile.get("interests"):
            facts.append("兴趣：" + "、".join(map(str, profile["interests"])))
        return facts

    # ---- 模式二：自定义提示词直连 VLM ----

    async def _live_vlm(self, ctx, rt, brain: dict, room: str) -> tuple[str, dict]:
        meta: dict = {}
        prompt = brain.get("prompt") or "描述这张画面。"
        frame = await self._fetch_frame(rt, room)
        if not frame:
            meta["error"] = "go2rtc 取帧失败"
            return "", meta
        data_url = "data:image/jpeg;base64," + base64.b64encode(frame).decode()
        # 按角色持久对话线程（跨端续聊 R8）：依赖 doubao2api keep_conversation 特性
        role = getattr(ctx, "role", None)
        # v0.5 兜底：ctx.role 为 None 时回退 butler 主角色，避免每次新建对话
        if role is None and rt is not None and getattr(rt, "roles", None) is not None:
            role = rt.roles.get("butler")
            if role is not None:
                logger.info("camera_vlm: ctx.role None, fallback to butler")
        keep = False
        conv_id = None
        if role is not None and getattr(rt, "role_state", None) is not None:
            keep = True
            conv_id = rt.role_state.get(role.id)
            logger.info("camera_vlm keep_conversation role=%s conv_id=%s",
                        getattr(role, "id", "?"), conv_id)
        try:
            text, new_conv = await rt.doubao.vision(
                prompt, data_url, keep_conversation=keep, conversation_id=conv_id)
            if keep and new_conv and rt.role_state is not None:
                rt.role_state.set(role.id, new_conv)
        except Exception as e:
            logger.warning("doubao vision failed: %s", e)
            meta["error"] = str(e)
            return "", meta
        if text.startswith("图片识别失败"):
            meta["error"] = text
            return "", meta
        meta["vlm_raw"] = text[:200]
        return text.strip(), meta

    async def _fetch_frame(self, rt, room: str) -> bytes | None:
        base = (rt.settings.go2rtc_base_url or "").rstrip("/")
        if not base:
            logger.warning("go2rtc_base_url 未配置")
            return None
        src = urllib.parse.quote(room)
        url = f"{base}/api/frame.jpeg?src={src}"
        auth = (rt.settings.go2rtc_user, rt.settings.go2rtc_pass) \
            if rt.settings.go2rtc_user else None
        try:
            async with httpx.AsyncClient(timeout=12) as c:
                r = await c.get(url, auth=auth)
                r.raise_for_status()
                if len(r.content) < 1024:
                    logger.warning("go2rtc frame too small: %d", len(r.content))
                    return None
                return r.content
        except Exception as e:
            logger.warning("fetch frame failed: %s", e)
            return None

    # ---- LLM 组装 ----

    async def _compose(self, rt, brain: dict, facts: list[str],
                       default_system: str = "") -> str:
        system = brain.get("system") or default_system
        if not system:
            return ""
        try:
            raw, _ = await rt.llm.chat(
                system,
                [{"role": "user", "content": "\n".join(facts)}],
                max_tokens=120,
                temperature=0.9,
            )
            text = raw.strip().strip("\"“”'")
        except Exception as e:
            logger.warning("compose failed: %s", e)
            return ""
        return text[: brain.get("max_chars", 80)] if text else ""

    def describe(self) -> dict:
        return {"name": "摄像头视觉引擎", "modes": ["ma_analyze", "live_vlm"],
                "desc": "取帧→VLM 识别→可选 LLM 组装"}
