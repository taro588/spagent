# SP AI Assistant 0.7.3

**图片真正显示在对话框里（架构文档 §6.1 验收修正）。** 0.7.2 交付后真机
实测发现：模型调用 image_search 之后，对话里出现的是本地缓存路径文本，
而不是图片。复盘出三层根因，本版全部修掉——现在无论模型把路径写成
Markdown、写成 JSON、还是只贴纯文本，图片都会以卡片网格显示在 Painter
对话面板里。

## 修复：卡片渲染层（根因一）

* 卡片网格原先对本地缓存图写 `<img src="C:\...">`，而对话页是 `setHtml()`
  加载的（无 file: 起点），Qt WebEngine 解析不了这种 src——用户只能看到
  占位框和「本地缓存」字样；
* 新增 `_qimage_data_url()`：已解码的缩略图缩到 300px 宽、编码为
  `data:image/png;base64,...` 直接内嵌进 HTML，显示不再依赖 WebEngine
  的本地文件策略；
* 解码失败的图维持占位框，不崩、不假装显示成功。

## 修复：提取与兜底层（根因二）

* `_extract_images` 新增裸路径兜底：正文里任何位置的
  `media_cache` 绝对路径（正反斜杠、JSON 双反斜杠转义）都当图片抽出，
  归一成单反斜杠再交给 QImage；
* 既有正则的隐藏缺陷一并修掉：真实缓存路径含空格
  （`%LOCALAPPDATA%\SP AI Assistant\media_cache`），段字符排除 `\s`
  会让 Markdown/JSON 形式的路径也永远匹配不上；
* 新增 `ai_client.LAST_IMAGES`：image_search 命中的本地缓存图随
  `meta["images"]` 进 UI，AI 回复自动附加显示——**就算模型不写 Markdown，
  图也出现在对话里**；每轮 `chat()` 开头清空，上一轮的图不串轮；
* 同一张图 Markdown 嵌入 + 裸提及只出一张卡（去重）。

## 修复：提示层（根因三）

* image_search 回执新增 `markdown` 字段：每张缓存成功的图一行现成的
  `![标题](local_path)`，模型整段复制即可；附 `hint` 明确告诉模型
  「不要只输出路径文本」；
* 工具 description 与系统提示同步强化：明令禁止只把路径当纯文本输出。

## 测试

* 新增 `tests/test_chat_dock_images.py`（9 项）：假 PySide6 离线导入
  chat_dock，锁提取（裸路径/双反斜杠/正斜杠/去重）、渲染（data URL 内嵌、
  严禁路径当 src、失败占位）、兜底管线静态形状；
* `tests/test_ai_capabilities.py` 新增 4 项：回执 markdown 片段、
  LAST_IMAGES 记录与去重、chat() 每轮清空、空 query 无副作用；
* `tests/validate_release.py` 新增 `test_image_cards_render_inline_in_chat`
  静态锁；
* 三处证伪全部如实红：剪断 data URL 内嵌 → 2 项红；剪断裸路径兜底 →
  3 项红；剪断 LAST_IMAGES 记录 → 1 项红（证伪前先提交再剪，恢复用
  checkout 不再重演 0.7.2 的回滚事故）；
* 全量 `python -m pytest`：**180 项全绿**（0.7.2 为 166）。

## 升级说明

在 Substance 3D Painter 里重载插件（或重装安装包）后生效。真机端到端
（配 API Key 的会话里真实模型发起 image_search → 回执 → 图片显示）
建议再验一轮：随便问一句「找几张旧铜材质的参考图看看」。
