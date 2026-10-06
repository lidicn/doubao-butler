"""同意判定：把一句人类回复归类为 yes / no / unknown。

设计不变式（整个模块的存在理由）：

    子串只用来否决，永远不用来放行。

yes 只能来自整句精确匹配（或全 token 精确匹配）；否决检查先于放行检查，
所以任何含否决字的输入在结构上不可能得到 yes。AutoForge `af_executor.py:53-66`
与 doubao-butler `core/dialog.py:265-273` 的同族缺陷（「不要」含「要」→ 判成批准、
「不要创建」含「创建」→ 判成确认）在这里不可能复现。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

YES: Final = "yes"
NO: Final = "no"
UNKNOWN: Final = "unknown"

# ── 否决：中文按字符（无分词问题），英文按词（"note" 不该被 "no" 命中）──
# 否决字只会把结论降级，永远不会升级，所以取粗不取细是安全的。
_ZH_VETO_CHARS: Final = frozenset("不别没无非莫勿休未")

_EN_VETO_WORDS: Final = frozenset({
    "no", "not", "dont", "cannot", "cant", "never", "none", "reject", "deny",
    "cancel", "stop", "avoid", "without",
})

_VETO_PHRASES: Final = (
    "算了", "取消", "放弃", "再说", "等等", "先不", "不必", "且慢", "慢着",
    "nope", "nvm", "nevermind",
)

# ── 整句精确肯定表：唯一能产生 yes 的来源 ──────────────────────────────
# 表内不得出现否决字；顺序与 test 共同保证否决优先，改表也不能开后门。
# 应答词（嗯/哦/啊 等 backchannel）不入表：它们只表示"在听"，不表示"同意"，
# 用在不可逆动作的审批门上是扩权。落到 unknown 后由调用方的重问上限兜底。
_EXACT_YES: Final = frozenset({
    "是", "是的", "是啊", "对", "对啊", "对头", "好", "好的", "好嘞", "好吧",
    "行", "行吧", "可以", "同意", "确认", "确定", "批准", "同意执行",
    "要", "要得", "开", "开吧", "打开", "关上", "关掉", "来吧", "来", "走起",
    "中", "成", "就这样", "执行吧", "去做", "开始吧",
    "yes", "y", "yeah", "yep", "ok", "okay", "sure", "doit", "go", "please",
    "confirm", "approved", "agreed",
})

_EXACT_NO: Final = frozenset({"不", "别", "没", "非", "否", "no", "n", "nah"})

#: 同意/否决词汇全集。供 `homesdk.gates` 的 G3 规则识别「仓里又长出一份本地判定表」——
#: 判定必须只有一处定义，词表本身也是那份定义的一部分。
CONSENT_VOCAB: Final = _EXACT_YES | _EXACT_NO | frozenset(_VETO_PHRASES)

# 句尾语气词：剥离后参与精确匹配，剥离本身不产生放行能力
_PARTICLES: Final = ("吧", "呀", "啊", "呢", "了", "哦", "嘛", "哈", "嘞", "呐")

_INTENSIFIER: Final = frozenset({
    "请", "麻烦", "现在", "立刻", "马上", "就", "那", "你", "帮我", "把它", "它",
    "谢谢", "一下",
})

_TOKEN_SPLIT_RE: Final = re.compile(r"[，。！？、；：,.!?;:\"'“”‘’…~～\s]+")
_WS_RE: Final = re.compile(r"\s+")


def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = s.strip().lower().replace("'", "").replace("’", "")
    return _WS_RE.sub(" ", s)


def _has_veto(norm: str) -> bool:
    if any(ch in norm for ch in _ZH_VETO_CHARS):
        return True
    if any(phrase in norm for phrase in _VETO_PHRASES):
        return True
    return any(word in _EN_VETO_WORDS for word in _TOKEN_SPLIT_RE.split(norm) if word)


def _strip_particles(token: str) -> str:
    changed = True
    while changed and token:
        changed = False
        for p in _PARTICLES:
            if token.endswith(p) and len(token) > 1:
                token = token[: -len(p)]
                changed = True
    return token


def classify_answer(text: str) -> str:
    """把一句人类回复归类为 YES / NO / UNKNOWN。

    UNKNOWN 的语义是「再问一次」，不是「终止」，也不是「放行」。
    放行与否只看 `allows_execution`。
    """
    norm = _normalize(text)
    if not norm:
        return UNKNOWN

    if _has_veto(norm) or norm in _EXACT_NO:
        return NO
    if norm in _EXACT_YES:
        return YES

    tokens = [t for t in _TOKEN_SPLIT_RE.split(norm) if t]
    if not tokens:
        return UNKNOWN
    stripped = [_strip_particles(t) for t in tokens]
    if any(t in _EXACT_YES for t in stripped) and all(
        t in _EXACT_YES or t in _INTENSIFIER for t in stripped
    ):
        return YES
    return UNKNOWN


def allows_execution(text: str) -> bool:
    """闸门唯一的读法：只有 YES 返回 True。"""
    return classify_answer(text) == YES
