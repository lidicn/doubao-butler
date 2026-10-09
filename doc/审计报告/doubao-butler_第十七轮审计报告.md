# doubao-butler 审计报告 · 第十七轮（装配完整性）

- **审计对象**：`lidicn/doubao-butler`（`/data/workspace/repos/db`）
- **轮次**：`round-017`
- **报告日期**：2026-10-08

---

## 一句话结论

**新增「装配完整性」分析器 ASM-01，确证 D19：主程序 `butler/app.py:86-87` 顶层 import 了不存在的 `butler.core.user_location`，`import butler.app` 直接失败——主程序根本起不来。另有 D20：requirements.txt 未声明 aiohttp / fastapi / requests。ASM-01 迭代了五版（W79a–f），其中两版是我自己引入的失效：包名写死导致样本 0 命中；把"项目包"与"标准库"并进同一集合导致逻辑反转、10 条 stdlib 误报。**

---

## 一、D19：主程序无法启动（确证）

实测：

```
import butler.app
→ ImportError: cannot import name 'user_location' from 'butler.core'

import butler.tts.manager
→ ModuleNotFoundError: No module named 'butler.tts.manager'
```

**5 个模块被引用但不存在**：`butler.tts.manager` / `singleton` / `helper` / `edge_tts` / `nowvoice_tts`，加 `butler.core.user_location`、`butler.core.ha_client`。

佐证：41 个 pytest 在**收集阶段**就有 5 个错误，全部指向 tts 装配缺失。

这不是"某个功能不可用"，是**整个服务起不来**。

## 二、D20：依赖未声明（确证）

`aiohttp`（7 处）、`fastapi`（2 处）、`requests`（1 处）被 import，但不在 `requirements.txt` ⇒ 干净环境部署必 ImportError。

---

## 三、ASM-01 的五次迭代，两次是我自己引入的失效

| 版本 | 问题 | 表现 |
|---|---|---|
| W79d | 包名写死 `top not in ("butler",)` | 门禁 dirty 样本（用 `pkgcore.*`）**0 命中** ⇒ FAIL |
| W79e | 把"项目包"与"标准库"并进**同一个集合** | 逻辑反转：`ast`/`json`/`os` 等 **10 条 stdlib 误报** |
| W79f | 包名→导入名未映射 | `pillow→PIL`、`beautifulsoup4→bs4`、`pycryptodome→Crypto`、`paho-mqtt→paho`、`python-multipart→multipart` 全部误报成装配缺失 |

**W79e 最值得记**：一个集合承担了两种相反语义——① 属于本项目 ⇒ 要解析、解析不到就报；② 属于外部 ⇒ 直接跳过。合二为一后无法表达，判据恰好反了。修法是**只收外部名，语义单一**："不在集合里就当本项目模块查"。

**W79f 是同一条主线的又一例**：包名和导入名不一致是常态（5/5 都中）。

三次的共同点与前十轮记的完全一致——**失效表现为"命中变多/变少"，而不是报错**，只能靠 dirty/clean 双向样本夹出来。

---

## 四、ASM-01 最终能力

- **静态 import**：`import X` / `from X import Y`
- **动态 import**：字符串形式的 `_SMOKE_MODULES` 列表也能查
- **区分三种 ImportError**：
  - 装配缺失 → **high**（本项目模块不存在）
  - 环境缺口 → 不报（homesdk 等私有/未安装依赖）
  - from-import 名字缺失 → **medium**

双向样本：dirty 6 命中（high 全中）、clean **0 命中**（零误报）。

全仓库：62 命中（high 27 / medium 35），其中 12 条确证 D19、10 条确证 D20。

---

## 五、如实说明

- **门禁当前 ok=False**，FAIL 项是**状态 PoC 门禁**（dirty 4 目标全 unavailable，探针到不了），**与 ASM-01 无关**，是上一轮遗留
- drift=5：`state_defects AFS-04` ×2（clean 新命中）、`external_tool_defects EXT-BANDIT` B603/B608 ×1（dirty 新命中）—— 需下轮核验是规则变宽还是真实新增
- **ASM-01 未做全量人工核验**：62 条里 12 条确证 D19、10 条确证 D20，其余 40 条（medium 35 + 少量 high）未逐条看
- **依赖 CVE 面仍未扫**

---

## 六、下一轮方向

1. **修状态 PoC 门禁**（dirty 4 目标 unavailable）—— 门禁必须回绿
2. **核验 drift 5 条**：AFS-04 ×2 出现在 clean 上，可能是规则变宽导致误报
3. **ASM-01 剩余 40 条人工分诊**
4. **D19 进补丁**：这是唯一影响"能不能启动"的缺陷，优先级最高

---

## 七、本轮最该记住的一条

**双向样本（dirty + clean）是唯一能夹出"逻辑反转"的手段。**

W79e 那次，如果我只跑 dirty，会看到 7 条命中、觉得规则在工作；只有 clean 上报出 `ast`/`json`/`os`，才暴露判据方向反了。

而这类错误**永远不会报错**——语法合法、逻辑自洽、输出看起来合理。它只会安静地给出错误结论。

这条与前面十六轮累计的二十余次"静默失效"是同一条纪律的又一次兑现：**任何"看起来在工作"都要先怀疑工具，再相信结论。**
