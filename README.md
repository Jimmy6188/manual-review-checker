# manual-review-checker

对技术文档做提交前审校，输出单文件 HTML 报告与 Excel 问题清单。适用于摩托车 / ATV / UTV 等车辆用户手册，也适用于一般技术出版物。

> **一句话定位**：只报告问题，不改写原文；不臆断正确值；机器扫描防漏，人工目视防误报。

## 核心特性

1. 三级流程：机器扫描（`scan.py`）→ 分章通读 → 目视复核（`render.py`）。
2. 区分「终稿 / 中途版」：中途版的占位、空值单列「待补」，不计入严重错误，避免把「资料未到」误判成「漏内容」。
3. 单一数据源：一份 `data.json` 同时生成 HTML 报告（可筛选 / 搜索 / 打印）与 Excel 清单（分级配色 / 自动筛选 / 汇总页）。
4. 印刷页码与 PDF 页序自动换算。

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

1. 环境自检：

```bash
python scripts/check_env.py
```

2. 机器扫描 PDF（输出六类线索：页码偏移、目录偏差、占位、空页、术语变体、疑似阶段）：

```bash
python scripts/scan.py <file.pdf> -o review.json
```

3. 渲染指定页供目视复核（页码用**印刷页码**，脚本自动换算 PDF 页序）：

```bash
python scripts/render.py <file.pdf> 23,39,72 -o pages
```

4. 把审校结果落成 `data.json`（结构照 `assets/report-data.example.json`），一条命令出 HTML + Excel：

```bash
python scripts/build.py data.json -o <输出目录>
```

## 目录结构

```
SKILL.md                        技能本体（供 AI 助手调用）
INSTALL-FOR-AGENT.md            安装指令（供 AI 助手执行）
安装与使用说明.md                人读的安装与使用说明
references/checklist.md         十类检查项
references/defect-patterns.md   八类高频病灶 + 判别要点
scripts/scan.py                 机器扫描
scripts/render.py               页面渲染（目视复核用）
scripts/build.py                data.json → HTML 报告 + Excel 清单
scripts/check_env.py            环境自检
scripts/_common.py              共享的页码偏移推断
assets/terms.json               术语变体表（按文档品类裁剪）
assets/report-data.example.json 数据模板
assets/report-template.html     报告骨架（由 build.py 调用）
```

## 使用约定

审校前需确认三件事（详见 `SKILL.md` 第 0 步）：

1. 文档阶段——终稿（付印 / 交客户）还是中途版（资料未齐）。
2. 基准资料——有无《术语表》《规格表》。
3. 对照版本——有无外文原版 / 上一版可对照。
