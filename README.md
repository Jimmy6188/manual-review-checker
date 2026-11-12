# manual-review-checker

对技术文档做提交前审校，输出单文件 HTML 报告与 Excel 问题清单。适用于摩托车 / ATV / UTV 等车辆用户手册，也适用于一般技术出版物。

> **一句话定位**：只报告问题，不改写原文；不臆断正确值；机器扫描防漏，人工目视防误报。

## 核心特性

1. 三级流程：机器扫描（`scan.py`）→ 分章通读 → 目视复核（`render.py`）。
2. 区分「终稿 / 中途版」：中途版的占位、空值单列「待补」，不计入严重错误，避免把「资料未到」误判成「漏内容」。
3. 单一数据源：一份 `data.json` 同时生成 HTML 报告（可筛选 / 搜索 / 打印）与 Excel 清单（分级配色 / 自动筛选 / 汇总页）。
4. 印刷页码与 PDF 页序自动换算。
5. 支持译文验收：中文定稿的英译版按「源-靶对照」审校，报告增列源文位置与源文原文；STE 简化技术英语仅按子集 6 条判定，不冒称全量合规。

## 前置要求

1. Python 3.9+
2. 依赖：`pymupdf`、`openpyxl`

```bash
pip install pymupdf openpyxl
```

国内网络慢可加镜像：

```bash
pip install pymupdf openpyxl -i https://mirrors.aliyun.com/pypi/simple
```

## 快速开始

0. 先出设计属性台账（逻辑检查的基准，不做这步就只能靠通读碰运气）：结构照 `assets/design-attributes.example.json`，12 格封闭槽位从规格表／零件表／目录／图注抽取，每格带页码，抽不出填 null 标「未述及」。**审校方先出初稿，客户只做确认/更正**，不要反过来让客户从零填。

1. 环境自检：

```bash
python scripts/check_env.py
```

2. 机器扫描 PDF（输出六类线索：页码偏移、目录偏差、占位、空页、术语变体、疑似阶段）。项目词表 `terms.json` 放当前工作目录，未提供则跳过术语类扫描：

```bash
python scripts/scan.py <file.pdf> -o review.json
```

译文验收版加 `--lang en`，追加残留汉字页、STE 子集分布、数值+单位比对、数字写法、图注行五类线索。给 `--source` 中文定稿即逐项比对数值与单位：

```bash
python scripts/scan.py <file_en.pdf> --lang en --source <中文定稿.pdf> -o review.json
```

3. 渲染指定页供目视复核（页码用**印刷页码**，脚本自动换算 PDF 页序）：

```bash
python scripts/render.py <file.pdf> 23,39,72 -o pages
```

4. 把审校结果落成 `data.json`（结构照 `assets/report-data.example.json`），一条命令出 HTML + Excel：

```bash
python scripts/build.py data.json -o <输出目录>
```

译文验收版另可填 `srcIssues[]`（源文侧问题：`loc`/`act`/`quote`/`issue`，`act` 取 必改·建议·待确认）。填了会在报告里出现「五、源文侧问题」一节，Excel 多一张同名工作表，**不占译文三级统计**——这份清单是给中文编辑改定稿用的。不填则整节隐藏。

## 目录结构

```
SKILL.md                        技能本体（供 AI 助手调用）
INSTALL-FOR-AGENT.md            安装指令（供 AI 助手执行）
安装与使用说明.md                人读的安装与使用说明
references/checklist.md         十类检查项
references/checklist-translation.md  译文验收版附加清单（L1–L8 + STE 子集）
references/defect-patterns.md   九类高频病灶 + 判别要点
scripts/scan.py                 机器扫描（`--lang en` 启用译文版附加扫描）
scripts/render.py               页面渲染（目视复核用）
scripts/build.py                data.json → HTML 报告 + Excel 清单
scripts/validate.py             data.json 摘录核对（三态，只警告）
scripts/check_env.py            环境自检
scripts/_common.py              共享的页码偏移推断
assets/terms.json               只读示例模板（项目词表放工作目录）
assets/design-attributes.example.json  设计属性台账只读模板（项目台账放工作目录）
assets/do-not-translate.json    不可译清单只读模板（项目清单放工作目录）
assets/report-data.example.json 数据模板（单语版）
assets/report-data.translation.example.json  数据模板（译文验收版）
assets/report-template.html     报告骨架（由 build.py 调用）
```

## 使用约定

审校前需确认四件事（详见 `SKILL.md` 第 0 步）：

1. 文档性质——单语版，还是译文验收版？单语版**不限语种**（只给一版、没有可对照原文的即属此类）；译文版需说清**中文是翻译稿还是英文是翻译稿**（另一版即原文），并确认译制链条、不可译清单与**参数本地化口径**（默认与源文逐字一致，不换算）。
2. 文档阶段——终稿（付印 / 交客户）还是中途版（资料未齐）。
3. 基准资料——除《术语表》《规格表》外，有无其他可用作判定基准的资料（认证文件／整车参数表、供应商数据、客户规格清单等）；译文版另需中英对照术语表。
4. 对照版本（仅单语版问）——有无外文原版 / 上一版可对照。译文版不重复问：原文已由第 1 问确定，版本号由审校方从文件本身登记；只给一版时退化为单语审校，不得报漏译误译。
