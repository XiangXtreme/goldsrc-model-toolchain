# build-goldsrc-models

面向 Agent 的 GoldSrc MDL v10 创作指导 Skill。它把 the303 教程的 Blender 5.2 等价经验组织为场景工作流、显式坑点和交付判断，而不是在 Skill 内维护 Blender 插件、编译器和 pipeline 基础设施。

## 内容边界

- `SKILL.md`：创作入口、工作流路由、基础/增强验收选择和交付规则。
- `references/pitfalls.md`：按症状检索的已复现坑点库。
- `references/workflow-*.md`：静态材质、动画角色、高级可导出特效、物理烘焙和导入修复流程。
- `references/the303-coverage.md`：34 页教程的执行证据与知识入口映射。
- `scripts/install_toolchain.py`：下载并验证固定公开 Extension Release 的薄安装器。

Extension、StudioMDL、Pillow、SourceIO reader、contract 实现、pipeline、fixtures 和发布工具位于公开仓库：

<https://github.com/XiangXtreme/goldsrc-model-toolchain>

官方 `ahujasid/blender-mcp` 始终由用户独立安装和更新，Skill 安装器不会修改它或 Codex 配置。

## 验证

```powershell
python -m unittest discover -s .github/tests -v
python %USERPROFILE%\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
python scripts/install_toolchain.py
```

Skill 目录不得包含 `tools/`、二进制、wheels、pipeline、fixture、`work/`、`artifacts/`、`outputs/`、缓存或嵌套 `SKILL.md`。
