# doubao-butler 审计报告 · 第十九轮（判据重写 + D19 根因定位）

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-019`
- **报告日期**：2026-10-08

---

## 一句话结论

**修完上一轮登记的 G1（AFS-04 判据重写）后，顺手把 D19 的根因挖出来了——不是"待实现"，是 `.gitignore` 第 3 行 `tts/` 把 `butler/tts/` 整个包误伤了。实测 `git check-ignore` 命中 `.gitignore:3:tts/`。修复只要一行：改成 `/tts/`。同时发现 `data/` 与 `*.db` 有完全相同的隐患（D21）。**

---

## 一、W81：AFS-04 判据重写（G1 关闭）

上一轮登记的缺口：AFS-04 把 **fail-closed 当成缺陷**——`_load()` 没包 try/except 就报 medium「文件损坏会让加载直接抛异常，无降级路径」。但抛异常正是我们十六轮来一直推荐的写法。

**新判据：不问"有没有 try/except"，问"失败是否可见"。**

| 形态 | 可见性 | 处置 |
|---|---|---|
| 无 try/except（异常冒泡） | 可见（fail-closed） | ⛔ 不报 |
| except + `raise` / `logger.*` | 可见 | ⛔ 不报 |
| except + 纯 pass/return，无日志 | **无痕** | ✅ 报 |

**与 AFS-01 的分工**（避免重复）：AFS-01 问"空值**流向写盘**了吗"（丢数据的后果，high）；AFS-04 问"失败**有痕迹**吗"（可诊断性）。两者有交集但不互为子集——配置文件读取失败后 `except: pass`，不流向写盘（AFS-01 不报），但运维完全无从得知（AFS-04 报）。

**W81b 定为 low 而非 medium**：本规则问的是可观测性，不是会不会丢数据。

**W81c 顺带发现样本的问题**：clean 样本注释写的是"异常有**记录**"，但代码里**没有任何 logger 调用**——注释声称的防护，代码没做。这是第十三轮"样本自己复刻缺陷"的镜像版本：这次是**注释声称做了防护，代码没做**。已修注释。

实测：clean 8 条 low（真问题：不丢数据但无痕）、dirty 12 条、db 47 条。门禁 ok=True，golden 基线已同步。

---

## 二、D19 根因：一行 .gitignore

上一轮我说"补 `butler.tts.*` 需要凭空造 5 个模块，需与开发者确认"。**这个判断是错的**——文件是存在的，只是没进仓库。

**证据链：**

1. 文档里明确引用 14 个 tts 文件的**具体行号**（`butler/tts/manager.py:50-51`、`edge_tts.py:58-59`、`kokoro.py:33-34`、`nowvoice_tts.py:83-84`）
2. `archive/tmp_fix_scripts/` 有 7 个脚本操作过 `butler/tts/manager.py`
3. 路线图记着"本仓从 09-21 才开始跟踪 `butler/tts/`"

**根因实测**（建临时 git 仓库验证）：

```
butler/tts/manager.py  => .gitignore:3:tts/    ← 被忽略
tts/a.mp3              => .gitignore:3:tts/    ← 本意要忽略的（数据卷）
```

`.gitignore` 第 3 行 `tts/` **没有前导斜杠**，gitignore 的无锚点模式匹配**任意层级**目录 ⇒ 本想排除仓库根的音频缓存，结果把 `butler/tts/` 整个包一起吞了。

**修复方案实测（一行）：**

```diff
- tts/
+ /tts/
```

修复后：`butler/tts/manager.py` **不再被忽略** ✓，而仓库根 `tts/a.mp3` **仍被忽略** ✓ —— 原意图完整保留。

---

## 三、D21：同类隐患还有两处

同样的无锚点模式，实测已确认误伤能力：

| 模式 | 误伤对象 | 现状 |
|---|---|---|
| `tts/` | `butler/tts/*` ← **已造成 D19** | 确凿 |
| `data/` | `butler/data/*` | 当前仓库无此路径，潜在风险 |
| `*.db` | `butler/core/test.db` 等 | 会吞掉随仓库分发的测试库文件 |

修复同上：改成 `/data/`；`*.db` 若只想忽略运行时生成，应明确路径。

---

## 四、D19b：另一半不是同一回事

`butler.core.user_location`、`butler.core.ha_client` 实测 **未被 gitignore 忽略**，但仓库里不存在 ⇒ 与 D19a 性质不同，是真的缺失（未实现或被删），仍需开发者确认。

**这个区分很重要**：D19a 是"找回文件"（一行 gitignore），D19b 是"补实现"。混为一谈会给出错误的修复建议。

---

## 五、台账状态

| ID | 严重度 | 状态 | 说明 |
|---|---|---|---|
| D19 | high | still_open | gitignore 误伤，**一行可修** |
| D19b | medium | still_open | 真缺失，需开发者确认 |
| D20 | medium | still_open | requirements 未声明 aiohttp/fastapi/requests |
| D21 | medium | still_open | data/、*.db 同类隐患 |
| D1–D18 | — | fixed_in_patch_copy | 第十三轮起 |

---

## 六、如实说明

- **G2（EXT-BANDIT-B608）本轮仍未核实**——优先级低于 G1 与 D19
- **AFS-04 新判据在 db 上 47 条 low 未做人工分诊**
- **D21 的 `data/` / `*.db` 当前仓库无实际受害文件**，是模式风险而非已发生的损失
- **D19 修复未在补丁副本执行**——`.gitignore` 改动 + 找回文件需要 git 仓库，而本沙箱副本非 git 仓库
- 依赖 CVE 面仍未扫

---

## 七、下一轮方向

1. **G2 核实**（B608 具体位置）
2. **ASM-01 剩余 40 条分诊**
3. **AFS-04 在 db 上 47 条 low 抽样核验**——重点看有没有"真丢数据但被判 low"的
4. **D19b 与开发者确认**

---

## 八、本轮最该记住的一条

**"这个模块不存在"有两种完全不同的原因，混为一谈会给出错误的修复建议。**

上一轮我把 D19 定性为"需凭空造 5 个模块"，因为它看起来就是"5 个模块不存在"。但实际上：

- `butler/tts/*`（14 个文件）→ **被 gitignore 误伤，文件存在，一行可修**
- `butler.core.user_location` / `ha_client` → **真缺失，需要写**

区分两者的代价只有一次 `git check-ignore`，但结论天差地别：一个是运维事故（配置写错），一个是工作量估算。

更一般地说：这已经是本项目第 N 次遇到"**看起来是 A，实际是 B**"。上一轮 AFS-04 看起来是"没降级"，实际是"正确的 fail-closed"；再上一轮状态 PoC 的 unavailable 看起来是"探针失效"，实际是"我自己的样本污染"；更早 `fast_routes` 看起来"有防护"，实际"护栏装在没人走的路径上"。

**每次都是多问一层才发现的。而多问一层的成本，通常只有一次实测。**
