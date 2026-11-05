# 安装指令（供 AI 助手执行）

本文件不是给人看的操作手册，是给 **AI 助手** 执行安装用的。用户只需把压缩包路径交给你，并按本文件操作。

## 任务

把本压缩包内的 `manual-review-checker` 技能安装或升级到本机 WorkBuddy 技能目录，并验证可用。

本文件同时适用于「首次安装」和「从旧版升级」。升级场景的硬要求：全程只保留一个同名技能目录，且不得丢失用户自定义词表。

## 步骤

### 1. 解压

解压 zip 到临时目录（如 `%TEMP%\mrc-install`），确认得到 `manual-review-checker/SKILL.md`。
不要直接解压进技能目录，先解压到临时目录再复制，便于失败回滚。

### 2. 定位技能目录

按顺序检查以下候选路径，**选择"已存在且其下至少有一个含 SKILL.md 的子目录"的那个**：

- Windows：`%USERPROFILE%\.workbuddypro\skills`
- Windows（老版客户端）：`%USERPROFILE%\.workbuddy\skills`
- macOS / Linux：`~/.workbuddypro/skills`、`~/.workbuddy/skills`

两个都不存在时，创建 `~/.workbuddypro/skills`（Windows 为 `%USERPROFILE%\.workbuddypro\skills`）。

不要凭猜测选。把你实际用的判断依据写进最终报告，一句话即可，例如"检测到 `.workbuddypro\skills` 下已有 3 个技能，装在此处"。

### 3. 备份旧版本（升级场景必做）

若目标目录下已存在 `manual-review-checker`，先把整个目录**移出技能目录**，例如：

- Windows：`%TEMP%\mrc-backup-YYYYMMDD-HHMM\manual-review-checker`
- macOS / Linux：`~/.mrc-backup-YYYYMMDD-HHMM/manual-review-checker`

**不要直接删除**，用户可能改过其中的术语表或模板。
**不要把备份留在技能目录同级**（如 `manual-review-checker.bak-*`）：备份里的 `SKILL.md` 仍带同名 `name` 字段，技能加载器可能挑中备份，造成「装好了却还在跑旧版」。若确实无法移出，至少把备份内的 `SKILL.md` 改名为 `SKILL.md.disabled`。

### 3.1 升级前置：迁移自定义词表

旧版（v1.0）把词表放在技能目录 `assets/terms.json`；新版要求项目词表放在运行扫描的工作目录。覆盖前先检查：

- 旧版 `assets/terms.json` 的 `synonym_groups` 或 `residue_words` 非空时，把该文件另存到用户指定的工作目录并命名为 `terms.json`，报告里写明新路径。
- 为空（或已是新版空模板）则跳过。
- 同时确认用户是否改过 `assets/report-template.html`；改过则先另存副本，不要静默覆盖。

### 4. 复制

把解压出的 `manual-review-checker` 整个目录复制到技能目录下。

### 5. 校验

- 目标路径下 `manual-review-checker/SKILL.md` 存在
- `manual-review-checker/scripts/` 下 6 个脚本齐全：`build.py`、`scan.py`、`render.py`、`check_env.py`、`validate.py`、`_common.py`
- `manual-review-checker/assets/` 下 `report-template.html`、`report-data.example.json`、`terms.json` 齐全；`references/` 下 `checklist.md`、`defect-patterns.md` 齐全
- `manual-review-checker/VERSION` 存在，且版本号、日期与本压缩包内 `VERSION` 逐字一致；按 VERSION 的哈希清单抽查 2–3 个关键文件（`SKILL.md`、`scripts/scan.py`、`assets/report-template.html`）
- 技能目录下没有第二个含 `SKILL.md` 且 `name: manual-review-checker` 的目录（含任何 `.bak-*`）
- 运行环境自检：

  ```
  python <技能目录>/manual-review-checker/scripts/check_env.py
  ```

  - 返回 0：环境就绪
  - 非 0：把"需要处理"那几行原样转告用户，附上安装命令
    `pip install pymupdf openpyxl`
    **缺包不算安装失败**，技能文件已就位，装包后即可用。

### 6. 报告

用 4–6 行告诉用户：

1. 装到了哪个目录（完整路径）
2. 判断依据（为什么是这个目录）
3. VERSION 版本号与旧版去向（升级场景必报备份路径）
4. 环境自检结果（就绪 / 缺哪个包）
5. 需要重启客户端才生效；重开后让 AI 加载技能，第 0 步问句应含「除《术语表》《规格表》外，是否还有其他可用作判定基准的资料」并提到工作目录 `terms.json`，以此确认跑的不是旧备份
6. 升级场景另报：自定义词表是否已迁移及其新路径

不要罗列文件清单，不要复述本文件内容。

### 7. 失败处理

任一步失败：

- 删除已复制的目录
- 把备份从临时路径移回技能目录原名
- 报告失败原因与卡在哪一步

不要留下半装状态。

## 禁止

1. 不要修改 `SKILL.md`、脚本或模板的任何内容——这是分发件，改了会与其他同事的版本不一致。
2. 不要把技能装到**项目级**目录（工作区下的 `.workbuddypro/skills`）。技能是用户级资产，跨项目可用。
3. 不要删除或覆盖用户已有的其他技能。
4. 不要顺手运行 `scan.py` 之类的业务脚本去"测试"——校验到第 5 步为止即可。
