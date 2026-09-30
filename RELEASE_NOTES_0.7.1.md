# 0.7.1 — Provider 能力矩阵与视觉门（规格 §5 / §4.2）

## 这版做什么

按规格 §5「Provider Adapter 统一编排接口，而不是统一削弱能力」与 §4.2
「Capability Router」，把能力从「写死在分支代码里」变成「声明在矩阵里」。

**能力矩阵**（`plugin/core/ai_client.py`）：每个 Provider 声明
`capabilities`（vision / web_search / image_search / image_generation /
tool_calling，键名与规格一致）+ `model_capabilities`（模型级覆盖，按模型名
子串匹配）。声明纪律：**只声明 wire path 真接通的能力** ——
`tool_calling`（painter_actions 已接到每条协议路径）与 `web_search`
（原生工具或 Bing 兜底）全员声明；`vision` 只在消息转换函数真能送图的
OpenAI / Anthropic / Gemini / 本地兼容服务上声明；`image_search` /
`image_generation` 尚未接线（§6 MediaObject、§8 PBR 云端），任何
provider 都不虚报。

**查询 API**：`provider_capabilities(provider, model)` / `supports(provider,
capability, model)` —— Master Agent 未来按能力路由的入口；换入 `qwen-vl-*`、
`kimi-*vl*`、`glm-4v*` 这类视觉模型时自动放开 vision。

**视觉门**（`vision_gate`）：带参考图而当前模型不支持图片输入时，**发送前**
拦下并告知可换的视觉模型清单；输入与参考图都保留。此前这条路径会把图发给
一个必然报错的服务端，用户只能看到难懂的 provider 错误。

**能力可见化**：模型选择器 tooltip 实时显示当前 provider+模型 的真实能力。

## 验证

* 本轮改动不触碰 Painter 执行链路（Tool Registry / 冒烟均不动）；冒烟基线
  沿用 0.7.0 在 Painter 11.0.0.4202 / 官方 API 0.3.4 上的真机验收结论。
* `pytest` **154 项全绿**（146 → 154）：新增 `tests/test_ai_capabilities.py`
  8 项 —— 矩阵键集完整、已接线必声明、未接线不得声明、vision 事实与消息
  转换接线逐 provider 锁定、模型级覆盖 add/remove、视觉门放行与拦截。
  已证伪：临时给 DeepSeek 虚报 vision，两条事实锁测试如实失败。
* 版本号八处同步：manifest / sp_ai_assistant / chat_dock ×2 /
  assistant_dock / iss ×2 / workflow yml / validate_release。

## 用户可见变化

* DeepSeek / Kimi / Qwen / GLM / MiniMax（文本模型线）带参考图发送时，
  不再收到服务端报错，而是明确的「当前模型不支持图片输入 + 可换模型清单」；
  换成 OpenAI / Claude / Gemini 或换入 *vl* / *4v* 视觉模型即可带图发送。
* 模型选择器悬停可见当前能力（图片输入 / 联网搜索 / 工具调用）。

## 不变的东西

* Tool Registry、执行管线、冒烟链路（0.7.0 已真机验收）全部不动；
* 本地兼容服务（OpenAI Compatible）的 vision 默认放行 —— 本地装什么模型
  由用户自己判断，插件不越权阻断。
