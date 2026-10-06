"""内网 Bark 推送：纯文字兜底发声通道。支持 AES-128-CBC 加密。"""
from __future__ import annotations

import base64
import json
import time

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.bark")

# Bark 内置铃声（常用）
BARK_SOUNDS = [
    "alarm", "anticipate", "bell", "birdsong", "bloom", "calypso",
    "chime", "choo", "descent", "electronic", "fanfare", "glass",
    "gotosleep", "healthnotification", "horn", "ladder", "mailsent",
    "minuet", "multiwayinvitation", "newmail", "newsflash", "noir",
    "paymentsuccess", "shake", "sherwoodforest", "silence", "spell",
    "suspense", "telegraph", "tiptoes", "typewriters", "update",
]


class Bark:
    def __init__(self, settings: Settings):
        self.s = settings
        self._merge_cache: list[dict] = []
        self._encrypt_enabled = bool(settings.bark_encrypt_key and settings.bark_encrypt_iv)
        if self._encrypt_enabled:
            logger.info("Bark encryption enabled (AES-128-CBC)")

    @property
    def _push_url(self) -> str:
        base = self.s.bark_url.rstrip("/")
        if self.s.bark_key:
            return f"{base}/{self.s.bark_key}"
        return f"{base}/push"

    @property
    def _encrypt_url(self) -> str:
        """加密推送的唯一 URL 公式：push() 与 flush_merged() 共用（两处各拼一遍＝有一处会漏，这次漏的是合并腿）。"""
        return self._push_url + "/推送加密"

    def _encrypt(self, plaintext: str) -> str:
        """AES-128-CBC 加密，返回 base64 密文。"""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        key = self.s.bark_encrypt_key.encode("utf-8")
        iv = self.s.bark_encrypt_iv.encode("utf-8")
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded = pad(plaintext.encode("utf-8"), AES.block_size)
        encrypted = cipher.encrypt(padded)
        return base64.b64encode(encrypted).decode("ascii")

    async def push(self, body: str, *, title: str = "豆包管家",
                   subtitle: str | None = None,
                   level: str = "active",
                   sound: str | None = None,
                   volume: int | None = None,
                   icon: str | None = None, image: str | None = None,
                   url: str | None = None, group: str | None = None,
                   badge: int | None = None,
                   call: bool = False,
                   is_archive: bool | None = None,
                   ttl: int | None = None,
                   markdown: str | None = None,
                   priority: str = "info") -> bool:
        """Bark 推送。priority: critical/warning/info，用于风控熔断判断。"""
        if not self.s.bark_url:
            return False

        # 推送风控检查（v1.8 新增）
        try:
            from butler.runtime import get_runtime
            rt = get_runtime()
            pg = getattr(rt, "push_guard", None)
            if pg is not None:
                decision = pg.check_bark(
                    title=title, body=body,
                    group=group or "default",
                    priority=priority, level=level,
                )
                if decision.action == "drop":
                    logger.info("bark dropped by push_guard: %s (%s)", title, decision.reason)
                    return False
                if decision.action == "merge":
                    logger.info("bark merged by push_guard: %s (%s)", title, decision.reason)
                    if len(self._merge_cache) >= 100:
                        self._merge_cache.pop(0)
                    self._merge_cache.append({"title": title, "body": body, "group": group, "ts": time.time()})
                    return False
        except Exception as e:
            # 格5（裁定 C3）：闸门自身出错仍 fail-open（宁多勿漏），但这事必须看得见 ⇒ warning
            logger.warning("bark push_guard check failed (non-blocking): %s", e)

        # 构造完整 payload
        payload: dict = {"title": title, "body": body, "level": level}
        if subtitle:
            payload["subtitle"] = subtitle
        if sound:
            payload["sound"] = sound
        if volume is not None:
            payload["volume"] = max(0, min(10, volume))
        if icon:
            payload["icon"] = icon
        if image:
            payload["image"] = image
        if url:
            payload["url"] = url
        if group:
            payload["group"] = group
        if badge is not None:
            payload["badge"] = badge
        if call:
            payload["call"] = "1"
        if is_archive is not None:
            payload["isArchive"] = "1" if is_archive else "0"
        if ttl:
            payload["ttl"] = ttl
        if markdown:
            payload["markdown"] = markdown

        try:
            async with httpx.AsyncClient(timeout=5) as c:
                if self._encrypt_enabled:
                    # 加密模式：POST 到 /推送加密
                    plaintext = json.dumps(payload, ensure_ascii=False)
                    ciphertext = self._encrypt(plaintext)
                    r = await c.post(self._encrypt_url, data={
                        "ciphertext": ciphertext,
                        "iv": self.s.bark_encrypt_iv,
                    })
                else:
                    # 明文模式：JSON POST 到 /push
                    push_url = self._push_url if self._push_url.endswith("/push") else self._push_url + "/push"
                    r = await c.post(push_url, json=payload)
                if r.status_code != 200:
                    logger.warning("bark push http %s: %s", r.status_code, r.text[:120])
                return r.status_code == 200
        except Exception as e:
            logger.warning("bark push failed: %s", e)
            return False

    async def flush_merged(self, max_per_group: int = 20) -> int:
        """把过载时合并缓存的消息批量推送为一条摘要 Bark。"""
        if not self._merge_cache:
            return 0

        groups: dict[str, list[dict]] = {}
        for item in self._merge_cache:
            g = item.get("group") or "default"
            groups.setdefault(g, []).append(item)

        sent_count = 0
        flushed_groups: list[str] = []
        for group_name, items in groups.items():
            if not items:
                continue

            count = len(items)
            lines = [f"共 {count} 条消息被合并推送（风控过载期间）：\n"]
            for i, item in enumerate(items[:max_per_group]):
                t = item.get("title", "")[:50]
                b = item.get("body", "")[:100]
                lines.append(f"{i+1}. {t}\n   {b}\n")
            if count > max_per_group:
                lines.append(f"... 还有 {count - max_per_group} 条已省略")

            summary_body = "\n".join(lines)
            summary_title = f"【合并通知】{group_name} {count} 条"

            ok = False
            try:
                if self._encrypt_enabled:
                    payload = {"title": summary_title, "body": summary_body, "group": f"merged_{group_name}"}
                    plaintext = json.dumps(payload, ensure_ascii=False)
                    ciphertext = self._encrypt(plaintext)
                    async with httpx.AsyncClient(timeout=5) as c:
                        r = await c.post(self._encrypt_url, data={
                            "ciphertext": ciphertext,
                            "iv": self.s.bark_encrypt_iv,
                        })
                        if r.status_code == 200:
                            ok = True
                else:
                    payload = {"title": summary_title, "body": summary_body, "group": f"merged_{group_name}"}
                    push_url = self._push_url if self._push_url.endswith("/push") else self._push_url + "/push"
                    async with httpx.AsyncClient(timeout=5) as c:
                        r = await c.post(push_url, json=payload)
                        if r.status_code == 200:
                            ok = True
                if ok:
                    sent_count += 1
                    flushed_groups.append(group_name)
                    logger.info("bark merged flush sent: group=%s count=%d", group_name, count)
            except Exception as e:
                logger.warning("bark merged flush failed: %s (group=%s, %d 条留待下轮)", e, group_name, count)

        if flushed_groups:
            self._merge_cache = [i for i in self._merge_cache
                                 if (i.get("group") or "default") not in flushed_groups]
        return sent_count

    def get_merge_cache_size(self) -> int:
        return len(self._merge_cache)
