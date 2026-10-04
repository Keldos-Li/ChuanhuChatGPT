# Agent 维护与离线验证

Agent 使用主项目的 Python 环境与 `openai==3.22.0`，不需要第二个 SDK 环境。
运行代码集中在 `modules/agent/`；`modules/models/OpenAIAgents.py` 保留主界面模型适配职责，普通模型能力仍由 `modules/model_capabilities.py` 管理。

## 会话、历史与文件

- Agent 第一轮建立会话后，主模型选择器锁定。新建聊天或打开其他历史可以离开该会话；Agent 子模型和推理强度仍可以在下一轮更新。
- 历史记录保存模型选择与非秘密参数。切换历史会恢复模型、系统提示及参数，历史中的 API key、授权字段和连接地址不作为凭据来源。
- 服务端会话绑定仍保存在原 `agent_data/bindings.sqlite3`，模块迁移不改变已有会话身份。
- 完整终态回答先保存，再开放操作。文字终态后的文件下载仍占用同对话任务槽，文件收尾完成后才能发送下一轮；切换或新建其他对话不受影响。旧 UI 观察器不能覆盖新会话或写回后台绑定。
- `modules/agent/tasks.py` 的任务注册表属于单个服务进程：同 owner、同 conversation/session 原子互斥，不同对话允许并发；总计最多 8 个任务，同 owner 最多 3 个，不建立等待队列。普通模型保持原来的前台执行行为。
- 切换、新建、刷新或关闭浏览器只结束 UI 订阅。Agent 本地线程继续接收、节流保存回答并下载文件；后台等待授权不会自动批准工具请求。Stop 捕获具体任务的 generation/session/turn，切页不改变其目标。
- 提交前保存 intent/generation，收到 session/turn 回执和终态时强制保存；历史 JSON 使用原子替换。状态未知时禁止重复提交，服务重启后只读查询原任务并补下载，不能把缺少 session 当成未发送。
- 服务进程关闭后，本地接收和下载会停止；云端任务是否继续由供应商决定，可能继续产生 API 费用。重启后通过服务端私有绑定恢复查询。本实现不提供多服务进程的数据库租约，不承诺跨实例互斥。
- 文件缓存按服务端用户与会话隔离。上传原件不会因清理 Agent 私有副本而删除。

## MCP 管理员授权

普通无凭据的公网 MCP 可以独立使用，不依赖工具搜索或程序化工具调用。
应用服务器的探测会固定经验证的公网 IP，并保留原 Host 与 TLS SNI；不会跟随重定向或继承环境代理。
因此仅依赖代理或重定向才能访问的服务器需要调整部署，不能用放松地址检查替代。

凭据或私网服务必须由管理员通过服务端环境变量 `CHUANHU_AGENT_MCP_AUTHORIZATION` 绑定。
它是以服务器标签为键的 JSON 对象，例如：

```json
{
  "work-mcp": {
    "server_url": "https://mcp.example.com/mcp",
    "authorization_env": "WORK_MCP_TOKEN",
    "allowed_tools": ["read_issue"],
    "owners": ["alice"]
  }
}
```

管理员绑定仅授予权限，不会自动加入用户工具配置。用户仍需在 Agent 的 MCP 配置中填写 JSON 数组，例如：

```json
[
  {
    "server_label": "work-mcp",
    "server_url": "https://mcp.example.com/mcp",
    "authorization_env": "WORK_MCP_TOKEN",
    "allowed_tools": ["read_issue"]
  }
]
```

`WORK_MCP_TOKEN` 的实际值只由管理员设置在服务器环境中；用户界面和历史中只保存引用名，不能填写真实凭据。

`owners` 使用已认证用户名；只有显式 `shared: true` 才表示部署范围内共享，包含匿名用户。
`allowed_tools` 必须覆盖用户请求的明确工具子集。服务地址与凭据变量名必须完全匹配绑定。
只有管理员显式设置 `allow_private: true` 才允许绑定的私网目标或 HTTP 凭据端点；此设置需要评估内部服务风险。
已有授权配置如果缺少用户范围或工具范围，会安全拒绝，须由管理员补齐，不能从浏览器配置自动扩大权限。
探测地址固定仅约束本应用的请求；托管 Agent 对已批准 MCP 的后续访问仍依赖服务端和托管平台的网络行为。

## 测试

使用完整项目环境安装 `requirements.txt` 与 `requirements_tests.txt` 后，在项目根目录执行。JavaScript 验证还需要已有 Node.js：

```sh
pip install -r requirements.txt -r requirements_tests.txt
python -m pytest tests -q
node tests/javascript/model-capabilities.test.cjs
python tests/production_build_smoke.py
```

测试使用合成数据、SDK 请求形状与真实 Gradio 回调，不需要付费 API 请求，不应加入用户真实聊天、凭据或配置。
其余 JavaScript 用例由 Python 集成测试传入真实服务端标记后执行，不应直接通配启动需要标准输入的脚本。
Agent 相关用例按行为组织于 `tests/agent/`，共享模型夹具位于 `tests/agent_fixtures.py`，普通主界面测试保留在 `tests/`。
离线预览可用 `python tests/main_chat_preview.py --port 8897` 启动，用于浏览器检查；其成功不代表真实供应商端到端验证。

Windows 安全文件实现面向 Windows 10 1709+ 的本地 NTFS；网络盘、SUBST、设备路径和重解析点安全拒绝。
Windows 专属测试必须在原生 Windows 执行；Linux 上的跳过不能视为 Windows 验收通过。
