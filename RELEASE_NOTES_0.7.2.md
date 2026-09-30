# SP AI Assistant 0.7.2

**搜索、图片与多模态媒体管线（架构文档 §6）。** 搜索结果从「文字 URL」升级为
结构化 MediaObject：图片搜索工具 image_search 在全部九个 provider 的四条
协议路径（OpenAI Responses / OpenAI 兼容 / Anthropic / Gemini）都有真实
wire path，结果自动缓存到本地工作缓存，Chat UI 渲染图片卡片与来源卡片，
Vision 直接读本地文件分析，形成「搜索 → 图片显示 → 看图分析 → 材质意图」
的完整链路。

## 新增：MediaObject（§6.1）

* `plugin/core/media.py`：`MediaObject` 十二个规格键逐项对齐
  （id / type / title / source_url / image_url / thumbnail_url / width /
  height / license / provider / search_query / metadata）+ 本地管线扩展
  `local_path` / `error`；
* 图片必须缓存到 `%LOCALAPPDATA%\SP AI Assistant\media_cache\`，不直接依赖
  临时远程 URL；下载原子落盘（临时文件 + 改名），失败不留半截文件；
* 下载失败必须可重试：错误带明确原因（`下载失败（URLError）：…`），
  `retry()` 幂等重试；
* 来源、版权/许可字段、搜索关键词随对象全程携带。

## 新增：image_search 工具（§6.2）

* 工具 schema `IMAGE_SEARCH_TOOL`（query + max_results），执行端
  `media.search_images_cached`：Bing 图片聚合端点 → 结构化 MediaObject 列表
  → 逐张下载缓存 → 回执带 local_path / cached / failed；
* 四条协议路径接线：
  * OpenAI 兼容路径：tool_calls 循环内执行并回传 tool 消息；
  * OpenAI Responses：`function_call` → `function_call_output` 回传，最多 3 轮；
  * Anthropic：custom tool → `tool_result` 回传，最多 3 轮；
  * Gemini：functionDeclarations → `functionResponse` 回传，最多 3 轮；
* `painter_actions` 行为不变（终止性调用，直接返回计划）。

## 能力矩阵更新（§5）

* `image_search` 全员声明 —— 前提是四条协议路径都有真实接线；
* 新增 wire 锁测试：`test_image_search_wire_exists_on_every_protocol_path`
  逐路径钉死「声明了就必须有 wire」。这条测试来自一次真实证伪：剪断 Gemini
  的 functionDeclaration 后 165 项测试全绿（矩阵声明与接线之间无锁），
  现在同类缺口会被直接抓住；
* `image_generation` 仍未接线（§8 PBR 云端轮次），继续不声明。

## Chat UI（§6.2）

* 消息里的图片提取支持本地缓存路径（`C:/…` / `file://`）—— 模型把
  image_search 的 local_path 写进 Markdown 即可渲染，不依赖远程 URL；
* 图片卡片来源行区分「本地缓存」与远程 host；
* 系统提示更新：明确告诉模型优先用 image_search 拿参考图、把 local_path
  写进回复、拿到图后基于内容给出分析（颜色/质感/磨损分布）。

## 验证

* `pytest` **166 项全绿**（154 → 166）：新增 `tests/test_media.py` 11 项
  （规格键集 / 序列化往返 / PNG-JPEG 尺寸魔数 / 下载缓存回填与幂等 /
  失败明确错误与重试恢复 / Bing 结果解析 / 完整回执 / 搜索失败不崩溃）
  + wire 锁 1 项；
* 真机环境：Adobe Substance 3D Painter 11.0.0.4202 / 官方 Python API 0.3.4；
* 已证伪：剪断 Gemini image_search wire → wire 锁测试如实失败。

## 版本

0.7.1 → 0.7.2（manifest / 插件入口 / 双 dock / 安装器 / CI workflow /
发布校验，八处同步）。
