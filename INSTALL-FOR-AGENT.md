# 安装指令（供 AI 助手执行）

本文件不是给人看的操作手册，是给 **AI 助手** 执行安装用的。用户只需把压缩包路径交给你，并按本文件操作。

## 任务

把本压缩包内的 `manual-review-checker` 技能安装到本机 WorkBuddy 技能目录，并验证可用。

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

### 3. 备份旧版本

若目标目录下已存在 `manual-review-checker`，先改名为 `manual-review-checker.bak-YYYYMMDD-HHMM`。
**不要直接删除**，用户可能改过其中的术语表或模板。

### 4. 复制

把解压出的 `manual-review-checker` 整个目录复制到技能目录下。

### 5. 校验

- 目标路径下 `manual-review-checker/SKILL.md` 存在
- `manual-review-checker/scripts/build.py`、`scan.py`、`render.py`、`check_env.py`、`_common.py` 均存在
- `manual-review-checker/assets/report-template.html`、`terms.json` 均存在
- 运行环境自检：

  ```
  python <技能目录>/manual-review-checker/scripts/check_env.py
  ```

  - 返回 0：环境就绪
  - 非 0：把"需要处理"那几行原样转告用户，附上安装命令
    `pip install pymupdf openpyxl`
    **缺包不算安装失败**，技能文件已就位，装包后即可用。

### 6. 报告

用 3–5 行告诉用户：

1. 装到了哪个目录（完整路径）
2. 判断依据（为什么是这个目录）
3. 环境自检结果（就绪 / 缺哪个包）
4. 需要重启客户端才生效

不要罗列文件清单，不要复述本文件内容。

### 7. 失败处理

任一步失败：

- 删除已复制的目录
- 把备份改回原名
- 报告失败原因与卡在哪一步

不要留下半装状态。

## 禁止

1. 不要修改 `SKILL.md`、脚本或模板的任何内容——这是分发件，改了会与其他同事的版本不一致。
2. 不要把技能装到**项目级**目录（工作区下的 `.workbuddypro/skills`）。技能是用户级资产，跨项目可用。
3. 不要删除或覆盖用户已有的其他技能。
4. 不要顺手运行 `scan.py` 之类的业务脚本去"测试"——校验到第 5 步为止即可。
