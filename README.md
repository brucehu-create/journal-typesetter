# 期刊排版器 Journal Typesetter (`jst`)

把一篇**已有稿件**改写成指定期刊要求的结构与格式。

**状态：MVP 闭环。** 四步全部实测通过：CSL 样式解析 → 须知抓取 → 计数核对 → docx 重排输出。

## 它做什么

作者只提供一个期刊名，其余归它：找到该刊投稿须知 → 读懂 → 重排章节、改标题惯例、重构摘要、补声明段 → 解出该刊的 CSL 文献样式 → 按该刊限制计数核对 → 交一份合规对照表。

**不动科学内容。** 只做结构、排版、格式层面的改动：重排、改名、重组、换样式。不改写句子、不改数字、不改引用。这条约束不是它绕开的东西，而是它可信的原因。

**不做**：从大纲写文章、语言润色、推荐期刊、审稿、画图。

## 差异化定位

同方向已有成熟先例：[my-submission-formatting-agent](https://github.com/maxwell2732/my-submission-formatting-agent)（111★，2026-02）。它同样是"重排器"，但有三点不同：

| | 先例 | 本项目 |
|---|---|---|
| 入口 | **必须另给须知 URL**（除预置的 3 个刊） | **只报刊名** |
| 参考文献 | **明确放弃**，自称 `too error-prone` | **CSL 确定性解析** |
| 中文期刊 | 未覆盖 | **GB/T 7714 + 中英文字数单位** |

先例的确切契约（逐文件核实）：

- 命令是 `/parse-guidelines [journal] [URL或"paste"]`——**光有刊名不够**，除预置的 pnas / ajare / ije，必须给 URL 或粘贴原文。它的故障排查里写着"页面 403 → 请用户粘贴文本"。
- **输入必须是完成稿件**（`.tex`/`.docx`/`.pdf`，经 pandoc 转 markdown）。
- **明令禁止改写句子**：`Never alter scientific content — do not rephrase sentences, modify numbers, change citations, or reorder information within a section.` 允许的改动只有 5 项：改标题、重排顶层章节、重构摘要、补写缺失章节（标 DRAFT）、转换引用格式。
- 它的 `section-drafter` 不是写手：约束是"只能用稿件里已有的信息"。

它的可靠性来自契约窄——**我们保留了同样的窄契约**，只在入口、引用、中文刊三处做得更远。

### 一条必须讲清的现实

"只报刊名"里最难的是**自动找到须知**。实测（2026-09-29，本机普通 HTTP 客户端）：

| 站点 | 结果 |
|---|---|
| frontiersin.org | 200，1.37 MB，可用 |
| elsevier.com | **403** |
| mdpi.com | **403** |
| springernature.com | **超时** |

这是出版社的反爬，不是设计缺陷。所以流程是降级阶梯：页面直取 → 搜索定位 URL 再取 → **请作者粘贴一次须知原文**（`--text-file`，一次粘贴后卡片可复用）→ 诚实放弃。**任何情况下都不从记忆里补字段。**

好消息是：CSL 解析这一半**不依赖抓官网**，官网全被挡也照样能用。

## 已实测通过

### 1) CSL 样式解析

样式库用 **partial clone**（`--depth 1 --filter=blob:none --no-checkout`），仅 **419 KB**，含全部文件清单不含正文，读某个样式时惰性取单个 blob。

> 第一版用 GitHub REST API 取清单，**实测被限流 403**（未认证 60 次/小时）。partial clone 无速率限制、可离线、可缓存。

共 **10863** 条样式（2863 独立 + 8000 期刊 dependent）。三级漏斗：精确 slug → slug 变体（`the-`、`journal of X`）→ 词重叠模糊匹配。**模糊匹配只返回候选，绝不自动选中**——文献样式选错比不给建议更糟。

```
$ python -m jst resolve-csl "Nature Medicine"
confidence: exact
dependent on: Nature  (nature.csl)
zotero id : http://www.zotero.org/styles/nature

$ python -m jst resolve-csl "Immunology Letters"
dependent on: Elsevier (numeric, with titles)

$ python -m jst resolve-csl "中华医学杂志"
confidence: none
fallback for Chinese journals: china-national-standard-gb-t-7714-2015-numeric
```

### 2) 投稿须知抓取

可达站点抓到原文并落盘，卡片带 `source_url` + `retrieved` 日期留痕；被挡站点明确报告并给出降级路径，字段留 `null` 不猜。

### 3) 计数核对

```
$ python -m jst check paper.md --journal "Test Journal" --card card.yml

totals     : 147 words, 0 CJK characters
body only  : 86 words, 0 CJK characters
excluded   : Abstract, References
figures    : 1
tables     : 1
references : 3

| Requirement      | Status     | Measured          |
|------------------|------------|-------------------|
| Word limit       | met        | 86 / 150 words    |
| Figure limit     | met        | 1 / 5 items       |
| Reference limit  | violated   | 3 / 2 entries     |
| Statement: Data availability | unverified | -    |
```

三种状态只有三种，语义收得很紧：`met` 检查跑了且过了；`violated` 跑了没过、并记下实测值；`unverified` **检查没跑成**（卡片里没这条限制，或须知压根没取到）。模型的主观判断另记 `qualitative`，不计入这三种。

中文稿自动改按 **CJK 字符数**计（中文期刊的字数是字符数，不是空格分词数）。**字数、图表数一律程序数，不估。**

### 4) docx 重排输出

**这里有一个关键设计判断：不在 docx 和 markdown 之间来回转。** 稿件里如果有 Zotero 插的引用，那是 `fldChar` / `instrText` 组成的活域代码；转成 markdown 再转回来会把它们压平成纯文本——而你选的引用方案（作者在 Zotero 里切换样式）**恰恰依赖这些域存活**。

所以做法是**在 docx 内部按段落块搬移**，块内一个 run 都不碰。程序只执行计划，不替你做决定：目标顺序、新标题名、要插哪些声明段，都由 skill 判断后写进 `plan.json`。

```
$ python -m jst build src.docx --plan plan.json --out out.docx

sections before: Introduction, Methods, Results, Discussion, References
sections after : Introduction, Results and Findings, Discussion, Data Availability, Methods, References
renamed:
  - Results -> Results and Findings
inserted:
  - Data Availability (after Discussion)
```

计划文件格式：

```json
{
  "journal": "Nature Medicine",
  "order": ["Introduction", "Results", "Discussion", "Methods", "References"],
  "renames": {"Discussion": "Interpretation"},
  "insert_after": [{"after": "Interpretation", "title": "Data Availability", "body": "[AUTHOR TO FILL]"}],
  "keep_unlisted": "end"
}
```

`keep_unlisted` 可取 `end`（放到末尾）/ `drop`（丢弃）/ `error`（直接报错）。

**源文件永不改动**；计划里要求的章节在稿中找不到时，报告里列 `warnings` 并以退出码 `2` 结束，不静默跳过。

`build` 默认还会把所有文字压成黑色（`--keep-colors` 可关）。原因：**python-docx 的内置模板自带蓝色标题样式**（`Heading1` = `#365F91`、`Heading2` = `#4F81BD`、`Title` = `#17365D`），所以用 python-docx 生成的 docx 标题天生就是蓝的。注意正文其实不蓝——`Normal` 没有定义字色，蓝只在标题上。修的时候必须**同时删掉 `w:themeColor`**（只改 `w:val` 的话 Word 仍按主题色渲染），并且**跳过表格样式**（深色表头配白字是有意设计，刷黑会看不见）。

回归测试（含 Zotero 域存活这条不变量）：

```bash
python tests/test_docx_build.py
```

## 用法

```bash
cd D:/datas/agent_local/output_files/32_Journal_Typesetter

python -m jst resolve-csl "Nature Medicine"          # 解出文献样式
python -m jst resolve-csl "中华医学杂志" --json       # JSON 输出
python -m jst fetch-guidelines "<须知URL或刊名>"       # 抓须知 + 留痕
python -m jst fetch-guidelines "某刊" --text-file g.txt --out card.yml  # 用作者粘贴的原文
python -m jst check paper.md --journal "某刊" --card card.yml          # 计数核对
python -m jst build src.docx --plan plan.json --out out.docx           # 重排输出（保留 Zotero 域）
python -m jst refresh-index                          # 重建样式索引
```

退出码：`0` 成功，`2` 未匹配样式 / 须知取不到 / 重排时有警告（需人工介入），`1` 出错。

## 参考文献这一步怎么走

**程序只解出"该用哪个 CSL 样式"，作者在 Word 里用 Zotero 样式下拉选一下即可。** 因为 `build` 保留了活域，切换样式会一次性刷新整个书目。

选这个方案是因为：零新代码、零未验证假设、马上能用。**不手改参考文献条目去凑格式**——样式要么经 Zotero 应用，要么作为待办事项报给作者。

## 目录

```
32_Journal_Typesetter/
├── jst/
│   ├── csl_index.py    # 刊名 → CSL 样式。完成并实测。
│   ├── guidelines.py   # 须知抓取 + 来源留痕。完成，字段提取交由 skill。
│   ├── compliance.py   # 计数核对 + 合规矩阵。完成并实测。
│   ├── docx_build.py   # docx 重排，保留 Zotero 域。完成并实测。
│   ├── cli.py          # 命令行入口。完成。
│   └── zotero_docx.py  # 接口占位（本方案不需要它，见上一节）。
├── tests/
│   └── test_docx_build.py  # 回归测试，含 Zotero 域存活不变量
└── data/               # 样式库 partial clone、索引与 blob 缓存（可删，会重建）
```

## 待办

1. **子标题（H2）改名**——`plan.json` 的 `renames` 目前只作用于顶层章节，改不了 H2。Nature 要求子标题 ≤40 字符，这条约束正好落在 H2 上（首次真稿实战中 18 个 H2 有 15 个超限），是当前最挡路的限制。
2. 声明段的存在性检查（目前报 `unverified` 并交作者确认）。
3. 从刊名自动定位须知 URL（目前靠 skill 用搜索找，再交程序抓）。
4. 排版层面的样式设置（双倍行距、12 号 Times New Roman、行号、去样式标签）——Nature 要求，工具目前不做。
5. 摘要结构化的自动重组（目前由 skill 人工判断句子归属）。
6. 中文期刊的中英文摘要规范——先例全部英文/LaTeX 导向，这块是空白。

## 依赖

`requests`、`pyyaml`、`python-docx`（均已在环境内）、`git`。**不需要 pandoc**（MVP 不走 LaTeX 渲染路径）。
