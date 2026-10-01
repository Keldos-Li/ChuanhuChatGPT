# 插件系统验证记录

验证日期：2026-10-01。开发工作树为 `codex/extension-system`，起点为原 `extension` 分支的 `fe06e88669041768bdbcf3e6c47bfb3fd9dc10a0`。原工作目录没有修改配置或工作区文件。

## 可复现的离线检查

使用 Python 3.11 建立隔离环境，安装 `requirements_tests.txt`，然后执行：

```sh
.venv/bin/python -m pytest -q tests
.venv/bin/python tests/ui_smoke.py
.venv/bin/python tests/project_ui_mock.py --build-only
.venv/bin/python tests/project_ui_mock.py --build-only --language en_US
```

单元与集成测试覆盖配置原子持久化、损坏状态关闭插件、manifest 校验、重复 ID、失败导入回滚、相对 helper 导入与旧裸模块冲突、回调归属与生成器关闭、禁用后事件防护、暂存安装默认关闭、真实临时 Git 仓库 fetch/脏更新拒绝，以及用户数据隔离。

Agent 模拟 SDK 测试覆盖目标 root turn、重放事件、断流未知结局、继续排除旧 turn、显式函数白名单、恢复、取消、产物限制、专用凭据来源与创建不明时对账。真实 Gradio HTTP 验收覆盖任务、继续、两个浏览器、下载、恢复、清理、双击和远程取消。首个 yield 后关闭生成器必须恢复未提交状态、释放 owner 锁，允许重新发送；继续任务关闭则保留上一个已完成 turn。

真实项目布局离线入口成功构建 247 个组件，使用项目主题、CSS、JS 和真实插件。它通过 AST 提取主入口布局，使用合成聊天/Agent 数据，不读取配置、密钥或导入 provider。历史、训练、计费、更新操作没有绑定。可用 `tests/project_ui_mock.py --port 8773` 打开浏览器验收，但不代表完整应用启动和全部 provider 已验证。

## 实际 API 验证范围

仅执行过一次用户授权的合成付费任务：1 个 session、1 次初始任务、0 次继续、0 次自动重试。目标 turn 完成，托管沙箱报告创建并读回测试文件；随后删除了该测试 session。没有保存 usage/token 金额，不报告实际费用。

最终 UI 的继续、函数工具、取消、恢复、产物下载和后来补充的网络/子 Agent 禁用 payload 经过离线与模拟验证，未逐项再次付费验证。验证脚本默认不发送请求；不要未经授权执行 `optional/agents/probe.py --execute`。

## 上游兼容状态

2026-10-01 fetch 后的上游 `main` 是 `2c7d5304c215520dbc6077f185fc45d8e0c8d919`；共同祖先为 `c020b2ddc0fc230ef4f2398152a89d8a2d907f0a`。原 extension 与当前上游的三方合并预检仍存在冲突，涉及 `ChuanhuChatbot.py`、`locale/en_US.json`、`modules/config.py`、`modules/models/base_model.py`。上游最近改变了全项目 locale 标识体系。

本轮尚未 rebase/merge 上游，也未在其新 locale 基线上运行完整应用。因此这组改动可以作为原 PR1200 extension 分支的后续提交，但不能据此宣称 PR 已解决冲突或可立即合入当前上游。需要另行解决冲突并复验启动、生命周期、翻译与 provider 集成。

当前上游没有 `modules/extensions.py`、plugin_callbacks、plugin_context 等插件基础设施。本轮生命周期修复依赖尚未合入的 PR1200；直接拆成上游小 PR 会隐含引入整个插件系统，不符合独立小修复的范围。本轮不为此强拆或发布未验收主功能。
