# GoldSrc Model Toolchain Workspace

本文件是本仓库的项目级维护说明，适用于根目录下的 Skill、插件和宿主脚本。

## 源码边界

- `skill/build-goldsrc-models/` 是 Codex Skill 的唯一源码目录。
- `plugin/goldsrc_model_toolchain/` 是 Blender Extension 的唯一源码目录。
- `workspace-manifest.json` 是两者的兼容性绑定入口。
- 安装目录只是部署目标，不是源码目录。不要直接在安装目录修改代码。

## 常用命令

在仓库根目录执行：

```powershell
python scripts/validate_workspace.py
python scripts/sync_install.py --all --dry-run
python scripts/sync_install.py --all
python -m unittest discover -s scripts/tests -v
python -m unittest discover -s skill/build-goldsrc-models/.github/tests -v
python scripts/audit_repository.py
```

运行测试时设置 `PYTHONDONTWRITEBYTECODE=1`，避免在仓库中产生 Python 缓存。模型、Blend、SMD、BMP、QC、MDL、报告、ZIP 和临时目录必须放在仓库与 Skill 目录之外。

## 同步规则

- Skill 同步只从 `skill/build-goldsrc-models/` 更新受管文件到 Codex Skill 安装目录。
- 插件同步必须先通过 Blender 5.2 Extension validate/build，再安装 ZIP 到 Blender 的 `user_default` 仓库。
- 官方 `ahujasid/blender-mcp` 始终由外部安装和管理；不要复制、打包、启用或更新它。
- 需要修改安装流程时，优先修改 `scripts/sync_install.py`，不要新增第二套复制逻辑。

## 版本与发布

- 当前正式版本为插件 `1.4.1`、API `1`、Skill Release `v1.4.1`；后续未发布改动应从下一开发版本开始管理。
- 修改 Skill 与插件的兼容关系时，同时检查 `workspace-manifest.json`、`tool-manifest.json` 和 Skill 的 `scripts/toolchain-release.json`。
- 未形成正式 Release 前，不要把开发中的源码声明为已发布版本，也不要覆盖既有 Release 标签。
- 任何共享行为或验证规则的修改都应补充对应测试或回归 fixture。

## 修改后的验收

至少运行工作区校验、插件测试、Skill 测试和 `git diff --check`。涉及插件源码、manifest 或路径时，还要运行 `scripts/audit_repository.py` 和 `scripts/sync_install.py --all --dry-run`。

除非用户明确要求，不提交、不推送、不修改远程 GitHub 设置，也不删除用户已有的安装目录配置。
