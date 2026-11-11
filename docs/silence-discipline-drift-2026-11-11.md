# 静默纪律漂移 · 2026-11-11（**更正版**：原「违规」判断作废）

> 状态：**原指控作废，未改动任何他人文件**。本文保留错误与纠正过程，因为错误的成因比结论更有用。

## 一、原判断（作废）

初版记录写：`spiders/clinicaltrials-cn/` 来自 `kind=generate` 工单
`20261006-clinicaltrials-941ede99.yaml`，却写了 `schedule:`，违反 canon 的静默纪律。

**这是错的。** 判据用错了：我按**名字相似**（`clinicaltrials` ≈ `clinicaltrials-cn`）去配工单。

## 二、推翻它的证据（工单自带精确键）

工单里有 `unit` 字段，且 `validate_ticket` 强制它形如 `spiders/<slug>/`：

```
20261006-clinicaltrials-941ede99.yaml  →  unit = 'spiders/clinicaltrials/'
```

而实际交付的是 **`spiders/clinicaltrials-cn/`** ——**两棵不同的单元**：

| 单元 | source_url | 说明 |
|---|---|---|
| `spiders/clinicaltrials/` | `https://clinicaltrials.gov/` | **既有单元**（工单指向它），`schedule: "13 2 9 * *"`，注释写着「点亮（批次二）」= 走的是**静默合入后人工点亮**的正常路径 |
| `spiders/clinicaltrials-cn/` | `https://clinicaltrials.gov/api/v2/studies` | 另一会话新增的中国境内子集，`schedule: "47 2 6 * *"` |

全仓 55 张 generate 工单逐张核过：**没有任何一张**声明 `unit: spiders/clinicaltrials-cn/`。
故 `clinicaltrials-cn` **不是**任何生成单的产物 → 它带 schedule **不构成违反**。

## 三、顺带量出一个更重要的事实：那条纪律在本仓 89% 不被执行

用 `unit` 精确键（不是名字）统计全部 generate 工单覆盖的单元：

| 口径 | 数 |
|---|---|
| generate 工单声明的唯一单元 | 51 |
| 单元实际存在 | 48（3 张工单的单元尚未落地） |
| **其中带 `schedule:` 的** | **43（89%）** |
| 未带 schedule 的 | `fred-cpi`、`hkex-disclosure`、`sec-edgar-facts`、`usaspending-awards`、`wfp-food-price` |

也就是说：**"生成单产物不写 schedule"这条，本仓大多数单元都没有照做**。

## 四、这条纪律为什么**无法**做成门禁（结论：不做）

我原本建议加一条 `generation-silence-violation` 规则。**否决，两条硬理由**：

1. **会造成 43 个假阳性**。按 `unit` 精确键统计，43 个既有单元会被判违规——它们多数是
   正常路径（静默合入 → 之后某笔「点亮」提交补上 schedule，如 `clinicaltrials` 的
   「点亮（批次二）」）。
2. **manifest 里没有"何时/由谁点亮"的证据**，所以门禁**在原理上分不开**这两种情形：
   - 交付时就带了 schedule（该拦）
   - 交付后经人工门点亮（该放）

  分不开就不能做成门禁——一个 43/48 都红的门，只会训练人忽略它。

**要做成可执行的，先要补一个证据位**：点亮动作要么在工单状态里留痕（如
`lit_at`/`lit_by`），要么在 manifest 上加 `lit:` 声明（"何时、依据什么、谁批的"）。
没有这个位，纪律只能靠 review，不能靠门。

## 五、我对自己这个错误的处置

- 本文保留原判断与纠正过程（不删痕迹）。
- 不改任何他人 manifest。
- **把两次「按名字配源」的错误记成一条纪律**（见下）。

## 六、纪律（这次是自己踩出来的）

> **名字不是键。** 本仓同一上游常有多棵单元（`clinicaltrials` / `clinicaltrials-cn`），
> 名字相似度会把两件不同的事判成一件。
> 配源要用**精确键**：工单用 `unit` 字段，漏斗用 `_name_key` 归一后的全等，
> 归档用归档目录名。任何"看起来像"的匹配都要在落笔前找一条机器可核的键。

这条和本仓既有的两个坑同源：`_name_key` 里给新行起带括号后缀的名字（导致重复 mention
不被合并）、以及"同一事实写在四面各自漂移"。
