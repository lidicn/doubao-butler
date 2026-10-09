# doubao-butler 审计报告 · 第十八轮（门禁修复 + 判据缺口）

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-018`
- **报告日期**：2026-10-08

---

## 一句话结论

**门禁从 ok=False 回到 ok=True（34 项）。状态 PoC 那 4 个 unavailable 的根因是 `_module_of` 把"相对 cwd 的路径"当成了模块名——又是同一条主线的第七次发作；修完才发现还有第二层：第十七轮我往共享样本里加的 ASM 样例是顶层裸 import，把 PoC 的 import 打挂了。另外登记了一条判据缺口 G1：AFS-04 把 fail-closed 当成缺陷。**

---

## 一、门禁 FAIL 的根因：两层

**第一层（W80）**——`_module_of` 只处理"绝对路径"这一种写法：

DO 规则输出的 `file` 是**相对仓库根**的 `selftest/cases/dirty/sample.py`，
而 cwd 恰好是仓库根 ⇒ `p.exists()` 为真 ⇒ 跳过了 `p = src / fpath` 那一支
⇒ 取到的是相对 cwd 的点分名 `selftest.cases.dirty.sample`
⇒ `ModuleNotFoundError: No module named 'selftest'`
⇒ **4 个目标全 unavailable** ⇒ 门禁 FAIL。

正确应为相对 src 的 `sample`。

**第二层（W80b）**——修完第一层还是 unavailable，报 `No module named 'pkgcore'`：

`pkgcore.*` 是**我第十七轮往共享样本里加的 ASM-01 样例**，写成了顶层裸 import。
状态 PoC 要 `import sample` ⇒ 直接炸。

**教训：往共享样本里加东西会影响所有其他分析器的 PoC。** 一个分析器的样例
可以是"故意 import 不存在模块"，但它不能因此让别的分析器测不了。
改法是包 `try/except`——ASM-01 只看静态 `ImportFrom` 节点，包在 try 里照样命中。

修完：dirty **4/4 data_lost**（TagStore / CodeStore / TokenStore / PrefStore），门禁回绿。

---

## 二、这是同一条主线的第七次发作

| 编号 | 只处理了哪种写法 |
|---|---|
| W77 | 只认 `self.path`（6 处写手只有 1 处这么叫） |
| W78c | 不认元组/列表解包 |
| W79 | 只查模块第一层 |
| W79c | 不认循环变量别名 |
| W79d | 包名写死 `butler` |
| W79f | 包名→导入名未映射（pillow/PIL 等 5 例） |
| **W80** | **只处理绝对 fpath，不处理相对 cwd 的 fpath** |

规律：**辅助函数的作者心里只有"最常见的那一种输入形态"，其余形态让代码
走进一个语法正确、逻辑自洽、但结论错误的分支。**

---

## 三、G1：AFS-04 把 fail-closed 当成缺陷（判据缺口）

实测：clean 样本 `sample.py:151 _load` ——

```python
if self.path.is_file():
    self._codes = json.loads(self.path.read_text(encoding="utf-8"))
```

**没有 try/except**，AFS-04 报 medium："文件损坏会让加载直接抛异常，无降级路径"。

**但抛异常正是我们十六轮来一直推荐的 fail-closed。** 而"降级"（静默返回空）
恰恰是 D1–D18 全部缺陷的根因。

这条规则的**前提是错的**：它把"没有降级"当成缺陷，而正确语义是
**"要么可见地失败、要么可见地降级，唯独不能静默当没有"**。

判据应重写为"失败是否可见"，而不是"是否有 try/except"。
本轮只登记（`round-018/rule-gaps.md`），不改规则——改动影响面大，需单独一轮。
门禁 drift 里见到 `[+clean] AFS-04` 属预期假阳性。

同时登记 G2：`EXT-BANDIT-B608` 在 dirty 上新增 1 条，未核实具体位置。

---

## 四、本轮产出

| 项 | 内容 |
|---|---|
| W80 | `_module_of` 先按 src 归一成绝对路径再取相对 |
| W80b | 共享样本的"装配缺失"样例包 `try/except`，不污染其他 PoC |
| 门禁 | **ok=True，34 项**；状态 PoC dirty 4/4 data_lost |
| 判据缺口 | G1（AFS-04）、G2（B608）已登记 |
| 台账 | D19 标注最高优先级（唯一影响"能否启动"的缺陷） |

---

## 五、如实说明

- **G1 只登记未修**，门禁里那 2 条 `[+clean] AFS-04` 是已知假阳性
- **G2 未核实**
- **D19 未进补丁**：补 `butler.tts.*` 需要凭空造 5 个模块，属于"实现缺失"
  而非"改一处逻辑"，需与开发者确认这 5 个模块本应是什么
- 依赖 CVE 面仍未扫

---

## 六、下一轮方向

1. **G1 修判据**：AFS-04 从"有没有 try/except"改为"失败是否可见"
2. **D19 定性**：与开发者确认 `butler.tts.*` 是待实现、还是被误删、还是应从别处引入
3. **ASM-01 剩余 40 条分诊**
4. **G2 核实**

---

## 七、本轮最该记住的一条

**共享样本的每一次改动，都是对其他所有分析器的一次回归测试。**

第十三轮我遇到过反过来的情况：标着"正确写法"的样本自己复刻了缺陷，
导致门禁一直靠规则的漏报维持绿色。本轮是镜像的一例——
标着"缺陷写法"的样本（ASM 样例：import 不存在的模块）因为写成了裸 import，
把**别的**分析器的 PoC 打挂了。

两次都说明：**样本不是私有的**。加东西之前要问的不是"我的规则能不能命中它"，
而是"它会不会让别人测不了"。
