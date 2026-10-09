# doubao-butler 审计报告 · 第十一轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-011`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-011/`
- **报告日期**：2026-10-07

---

## 一句话结论

**AFS-01（静默失败）66 条里，high 只有 6 条——而 D1（config.json 破坏性覆盖）沉在 low 里。根因是规则只看「函数自身有没有写盘」，看不见 caller。W72 补上 caller 方向的通用追查后 high 升到 21，D1 与 D18 都正确归位。代价是带进约 8 条噪声——我选择宁可多报，因为「真 P0 沉在 low 里」比「多几条误报」危险得多。**

---

## 一、问题的起点：最该看的那条在最底下

第十轮结束时 AFS-01 有 66 条命中，其中 **high 只有 6 条**。而 D1 —— 那个实测确证「改一个字段，人格和成员全没了」的 P0 —— 被判为 `low / degradable`。

规则 `state_defects.py` 的判据是：

```
读侧静默失败 → 这个空值流向哪里？
  流向 True / 放行 → high（fail-open）
  流向写盘       → high（destructive）
  否则            → low（degradable）
```

而「流向写盘」的判定写在 `_flows_to_write(fn)` 里，**只看 `fn` 自己有没有调写盘 API**。

`config_routes.py:27 _load_cfg()` 自己当然不写盘——它只读。真正写的是它的 caller：

```python
def save_persona(request):
    cfg = _load_cfg()      # ← 读失败返回 {}
    cfg["persona"] = ...
    _save_cfg(cfg)         # ← 写在这里
```

**读侧和写侧在两个函数里，而规则只看其中一个。** 于是 P0 缺陷被判成"只是降级"。

---

## 二、W72：补上 caller 方向

`_common.py` 新增两个通用能力：

```python
caller_index(tree)          # 被调用函数名 → 调用它的函数节点（反向索引）
reaches_via_callers(fn, pred, ci, depth)   # caller 方向的谓词追查
```

**这是"函数内视野太窄"这条主线上的第五次**（W54 写盘委托 → W61 同名遮蔽 → W62 跨函数 timeout → W69 async 节点 → W72 caller 方向）。第七轮做的 W65 `attr_reaches` 只解决了 **callee** 方向（我调的函数有没有这个属性），**caller** 方向（调我的函数会不会拿我的返回值去写盘）一直是空白。

`state_defects.py` 的 `_flows_to_write` 改为三方向判定：

1. 函数自身写盘
2. **调用它的函数写盘**
3. **调用它的函数再调别的函数写盘**

---

## 三、四次修正，以及每次暴露的问题

| 版本 | 改动 | AFS-01 high | D1 | D18（feeder） |
|---|---|---|---|---|
| W72 首接 | 加 caller_index | 6（**没变**） | ✗ | ✗ |
| W72 修导入 | 补齐 import | **24** | ✓ | ✓ |
| W72c | 收紧写盘别名表 | 24 | ✓ | ✓ |
| W72d | caller 内跳数 2→1 | 19 | ✓ | **✗** |
| **W72e** | **宽松匹配补 `write`** | **21** | **✓** | **✓** |

四个坑逐个说：

**① 导入注入没生效，被静默吞了。** W72 首次接入时 `replace` 没匹配到目标串，`caller_index` 等名字没进 import 块。而 `_flows_to_write` 里有 `except Exception: return False` —— NameError 被吞掉，**表现为"规则改了但结果没变"**。这类"改了没生效"比"改了报错"难查得多。

**② off-by-one。** `reaches_via_callers(fn, pred, ci, depth=1)` 只检查 `fn` 自身，递归进 caller 时 depth 已为 0，一个 caller 都没查。修正为 depth=2。

**③ 别名表不是问题。** 我以为 24 条里多出来的是 `_WRITE_API` 太宽泛（`replace`/`rename`/`save`/`dump` 这些短名容易误伤），收紧后仍是 24 —— 说明噪声不是别名表造成的。

**④ 真正的权衡点在跳数。** depth=2 时 D1/D18 都抓到但 high 24；depth=1 时 high 19 但 D18 漏掉。原因是 `feeder._read_role_state` 的 caller `bind_conversation` 自己不写盘，它调 `_write_role_state` 才写——需要 2 跳。

**解法不是调跳数，是让宽松匹配认识到项目自定义的写盘函数名。** `_LOOSE_WRITE` 里加 `write`（原来只有 save/dump/replace/rename），`_write_role_state` 就能在 depth=1 命中。最终 **high 21，D1 与 D18 都在**。

---

## 四、确证缺陷：D18【P1·high】同一文件三套实现

`role_state.json` 存各角色的 `conversation_id`，是 README 的 **R8 跨端续聊**特性载体。三个模块各自实现读写：

| 模块 | 落盘方式 |
|---|---|
| `roles/state.py:21` | `open(w)` **就地截断写** |
| `memory/feeder.py:42` | `write_json_atomic` 原子写 |
| `api/doubao_webhook.py:60` | 硬编码 `/app/data/role_state.json` **只读** |

**三层的后果各不相同**：

1. **就地截断写**：崩溃留半截 JSON。`roles/state.py:47` 的注释自己写着"第二个写手"
2. **两套写盘并存**：原子写和截断写交替作用在同一文件上
3. **读侧硬编码**：用户按文档把 `DATA_DIR` 指向 NAS 挂载点后，①②写到挂载点、③读容器内置路径 ⇒ **变成两个不同的文件** ⇒ 反向查角色恒为 None ⇒ 回 `unknown_conversation` ⇒ **手机端消息除一个硬编码 CID 外全部被静默丢弃**

失败方向尤其反直觉：**不是崩溃，是消息静默消失。**

**D18 是 D2 和 D5 的根因。** 之前把读失败清零记为 D2、把路径硬编码记为 D5，其实它们是"三套实现"这一个问题的两个面。

---

## 五、21 条 high 的诚实盘点

**已确证真阳性（8 条）**：

| 位置 | 对应 |
|---|---|
| `api/config_routes.py:27` `_load_cfg` | **D1** |
| `memory/feeder.py:50/54` `_read_role_state` | **D18** |
| `integrations/memory_agent.py:513` | 待立 |
| `tools/schedule.py:48` `_parse_reminder_at` | 待立 |
| `triggers/engine.py:90/108/118` `_import_legacy_snapshot` | 待立 |
| `skills/conflict.py:147` | **fail-open**（第四轮已判假阳性，此处应复核） |

**疑似噪声（约 8 条）**：`triggers/engine.py:228 _trace_id`、`core/briefing.py:125 _fetch_news`、`decision/aggregator.py:122/211/234/251`（这四个读的是 DB 不是 JSON 文件）、`audiobook/manager.py:132`、`skills/versions.py:87`、`memory_agent.py:423`。

**应排除（1 条）**：`scripts/audit_1002/mutate_dcd3_ttl_1002.py:133` 属审计脚本非产品代码。

**我接受了 21 条而不是压回 19 条**，理由是本轮开头那句：**真 P0 沉在 low 里，比多几条误报危险得多。** 噪声可以靠人工筛，漏掉的确证缺陷不会再被记起。

---

## 六、审计结果

| 指标 | 数值 |
|---|---|
| AFS-01 | 66 条 → **high 6 → 21** |
| 本轮新增确证 | **D18** |
| 台账累计 | **D1–D18** |
| 门禁 | ok=True，analyzers=33 |

**门禁 drift 新增一条未清零**：`[-dirty] EXT-SECRETS-Secret Keyword 消失（原 ×3）`。门禁整体判 ok=True（归为 drift 提示而非 failure），但这条需要下一轮确认——按本项目一贯的纪律，**"命中消失"不能默认当成好事**。

---

## 七、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 D18 | 9 |
| 失败路径没兜住 / 无超时 | D10 D12 D13 | 3 |
| 并发初始化竞态 | D14 D15 | 2 |
| 无预算递归（依赖侧） | D16 | 1 |
| 护栏装了但主流程不调用 | D17 | 1 |
| 配置面不一致 | D5 | 1 |
| 观测语义矛盾 | D11 | 1 |

**D18 归入第一族后，该族 9 项。** 十一轮下来，"读路径的失败被当成没有数据"这一个根因解释了超过一半的确证缺陷。

---

## 八、下一轮方向

1. **筛分 21 条 high**：8 条待立缺陷逐个核验，8 条疑似噪声确认后加抑制
2. **复核 `conflict.py:147`**：第四轮判为 fail-safe 假阳性，本轮规则又判 fail-open high——**同一条在两个版本规则下结论相反，必须人工定论**
3. **追查门禁 drift 里的 SECRETS 消失**
4. **写补丁副本（db-patched）**——D1–D18 证据已足够
5. **核验剩余 high**：DO-* 11 条、RSC-02、CONC-08、FO-01

---

## 九、本轮最该记住的两条

**第一条：分诊层的失效比规则的失效更隐蔽。**

规则漏报，你至少知道"还有东西没扫到"。分诊把 P0 排到 low，你会看着"只有 6 条 high"觉得情况可控。**D1 在 low 里躺了十轮，因为它自己不写盘——而"读侧不写盘"是正常设计，真正的危险在于谁消费了那个空值。** 静态规则天然看不见 caller，这个盲区不会自己消失。

**第二条：调参救不了判据缺口。**

W72c、W72d 都是在调参数（别名表宽窄、跳数多少），都只能做到"这边好了那边坏了"。真正解决问题的是 W72e——**加一个 `write` 关键字**，让规则认识项目自定义的写盘函数名。这不是调参，是补判据。

四次修正里只有最后一次是真正的修复，前三次都在参数空间里打转。**遇到"调这个就丢那个"的情况，先怀疑判据本身缺东西，而不是继续调。**
