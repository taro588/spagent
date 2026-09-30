# SP_AI / spagent 架构索引

本文件是**仓库侧**的架构入口，不是完整规格。完整技术规格是那份
《SP_AI / spagent 完整技术架构文档》（30 节：关键原则、分层架构、多模型协作、
Provider Adapter、媒体管线、Material Intent、PBR 生成、Tool Registry、
验证闭环、权限模型、安装部署、验收标准…）。两份文档分工：

| 文档 | 定位 |
|---|---|
| 完整技术架构文档 | 目标形态与统一规格（跨模型/供应商中立），接手者的第一读物 |
| 本文件 | 仓库现状、代码边界、以及规格条目对应的**落地位置** |

> 规格里 §25 明确要求「版本号、README、安装器、源码中的版本必须统一，
> 避免文档与源码不一致」。所以本文件只写**结构与约束**，不复制规格正文——
> 复制出来的那份一定会漂移。规格正文放在仓库外，改动时两边同步。

---

## 1. 当前仓库形态

```
spagent/
├── plugin/                    # Substance 3D Painter 插件（Python, PySide2/6）
│   ├── sp_ai_assistant.py     # 入口：注册 dock、版本闸门、异常兜底
│   ├── manifest.json          # 版本、兼容矩阵、能力声明
│   ├── core/
│   │   ├── actions.py         # 执行器：把计划落成官方 Painter API 调用
│   │   ├── ai_client.py       # 多 Provider 客户端 + 工具调用回路
│   │   ├── painter_context.py # Painter 状态快照（也用于执行后校验）
│   │   ├── settings.py        # DPAPI 加密的本地配置
│   │   ├── self_check.py      # 插件自检
│   │   ├── qt_compat.py       # PySide2/6 动态选择
│   │   └── tools/             # ★ Tool Registry（能力事实源）
│   └── ui/
│       ├── assistant_dock.py  # 主 dock：可折叠侧栏 + 分割布局
│       ├── chat_dock.py       # ChatGPT 风格对话面板、设置、诊断
│       ├── browser_panel.py   # 内嵌浏览器面板 + watchdog
│       └── host_embed.py      # 跨进程嵌入（SetParent）
├── browser_host/              # 独立 QtWebEngine 浏览器进程（PyInstaller 打包）
├── installer/                 # Inno Setup 安装器（自动探测 Painter 版本/路径）
├── tests/                     # pytest：发布校验 + Tool Registry 契约测试
└── docs/
```

## 2. 关键边界（规格条目 → 落地位置）

| 规格 | 约束 | 落地位置 |
|---|---|---|
| §12 Tool Registry | 白名单 / 模型 schema / 必填参数只有一份来源 | `plugin/core/tools/` |
| §12 1:1 对齐 | registry ↔ 执行分支一一对应 | `tests/test_tool_registry.py::test_registry_and_executors_are_one_to_one` |
| §12 官方入口真实存在 | 每条 `api=` / `api_alternatives=` 必须能在官方 API 里解析 | `tools/painter_api_census.py` + `tests/test_painter_api_conformance.py` |
| §13 执行域分离 | 烘焙 / 导出必须显式声明，禁止跨域猜测 | `plugin/core/tools/domains.py` + `validate_plan` |
| §11 官方 API 优先 | 所有 Painter 操作走官方 Python API | `plugin/core/actions.py` + `plugin/core/painter_api.py` |
| §11 / §28-2 API 版本 | 运行期读 Painter 版本与官方 Python API 版本，并暴露给模型 | `plugin/core/painter_api.py::runtime_info` + `painter_context.api_context` |
| §14 多通道 Material | 参数编辑前先看节点 source mode，不满足条件不硬写 | `plugin/core/painter_api.py::set_geometry_mask` / `set_material_source` |
| §16 事务 | 批量修改包在 `ScopedModification` 内，缺失时显式标记降级 | `painter_api.scoped_modification` + `execute_plan` |
| §17 权限模型 | READ/WRITE/EXPORT/DANGEROUS 四级 | `plugin/core/tools/domains.py` |
| §18.1 API 校验 | 执行后对照真实状态校验 | `plugin/core/tools/verifiers.py` |
| §28-5 真实工程冒烟 | 在真机 Painter 上跑「计划 → 官方 API → 快照校验」，结论可归档、可门禁 | `plugin/core/integration_smoke.py` + `plugin/ui/diagnostics.py` + `tools/check_smoke_report.py` |
| §23 安装部署 | 双击安装、动态探测路径、不假定 C 盘 | `installer/SP_AI_Assistant.iss` |
| §25 UI 无黑边 | 弹性布局，缩放不出现黑色空白 | `plugin/ui/*` + `browser_host/main.py` |

## 3. 测试

```bash
python -m pytest            # 133 项
python tools/painter_api_census.py --check-catalog   # 需装有 Painter
```

`pytest.ini` 把 `tests/validate_*.py` 也纳入收集，CI 跑的是整个 `tests/`。
除 `test_painter_api_conformance.py`（需要 Painter 的 API 声明目录，没有则
自动跳过）之外，其余测试都在 Painter 之外运行：`actions.py` 用
substance_painter 桩导入即可测校验与执行管线，因此 CI 不需要装 Painter。

## 4. 尚未落地（见 docs/roadmap.md）

多模型协作（Master Agent / Capability Router / Specialist Agents）、
Provider Adapter 能力矩阵、媒体管线与 Asset Registry、PBR 云端生成、
视觉校验与 Corrector、Task State Machine。当前只有单模型 + 单轮工具调用，
工具调用回路的骨架已在 `ai_client.py` 和 `actions.py` 里。
