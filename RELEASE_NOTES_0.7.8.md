# SP AI Assistant 0.7.8

规格 §22（缓存 / 成本 / 分辨率纪律）+ §28 第 7 项收尾：统一缓存层与
分辨率成本策略落地并接线到搜索与 PBR 管线。

## §22 统一缓存层（`plugin/core/cache.py`，新）

规格原文四条纪律逐条落地：

1. 搜索结果、图片、PBR 结果**三类产物统一索引**（`CATEGORIES`），
   大文件留在各自目录，索引只存路径 + 元数据，不落两遍二进制。
2. 缓存键 = **prompt + reference + 参数 + provider + model**
   （`make_key`）：规范化（去空白 / 键排序 / JSON 稳定序列化）后取
   sha256 前 32 位。**任一项不同都不是同一次生成，绝不命中别人的
   缓存**——宁可多花一次请求，不可给错结果。
3. 磁盘索引原子落盘（`.part` → `replace`），`%LOCALAPPDATA%\SP AI
   Assistant\cache\index.json`。
4. TTL（默认 7 天）+ LRU（条目数 / 字节双上限）淘汰；被淘汰条目
   **只移出索引、不删产物文件**——产物可能仍被 Registry / UI 卡片
   引用。

命中统计口径量化「省下的请求数」：`stats()["saved_requests"]` 即命中
次数，每次命中 = 少发一次网络请求。

不 `import substance_painter` / Qt，可离线（CI）导入断言。

## §22 分辨率与成本策略（`plugin/core/resolution.py`，新）

- 按**目标平台 / Texture Set 尺寸**决定导出分辨率，不无条件上最高档。
- **两阶段**：低成本低分辨率 draft 先验证，通过质量门才升 final。
- 导出分辨率受 **GPU 与项目限制**封顶；`capped` 只标记「曾经想要
  更高、被硬约束压下来了」，用户主动选择低规格不算被压。

## 接线（`plugin/core/ai_client.py`）

- `image_search`：命中缓存直接返回（`receipt["cache"] == "hit"`），
  未命中才发请求并写入缓存（`"miss"`）。命中前校验产物文件仍存活
  （`_search_receipt_files_alive`），**文件已丢的缓存不算命中**——
  杜绝「假命中」把死路径当结果显示。
- **既有图片直显管线保持不变**：markdown 内嵌 + `LAST_IMAGES` 兜底
  逻辑零改动，缓存只在其上游省请求。
- PBR 回执携带分辨率计划，与 Registry 生命周期并存（PBR 资产仍由
  Registry 管八态，缓存只管「同输入不重复生成」）。

## 测试

- 测试 264 → **303 全绿**。新增：
  - `tests/test_cache.py`：键构成（provider / model / 参数任一不同都
    换键）、TTL 过期不供、LRU 双上限、淘汰不删产物、命中统计。
  - `tests/test_resolution.py`：目标平台档位、draft→final 两阶段、
    GPU / Texture Set 封顶、`capped` 语义。
  - `tests/test_ai_capabilities.py`：新增缓存**接线锁**——命中、
    **不假命中**、缓存键含 query 与条数三项，防止 0.7.4「wire 锁
    缺口」教训重演。
- 修一处**测试污染**：测试直接调 `_run_image_search` 会写到真实
  `%LOCALAPPDATA%` 缓存目录，已加隔离 fixture，并把已写入的污染
  索引清掉。

## 证伪（三处，逐处剪断 → 确认落盘 → 跑测试）

1. 剪断缓存键 `provider` 维度 → `test_cache.py` 红 1。
2. 剪断 TTL 过期判定（恒不过期）→ `test_expired_entry_is_not_served` 红。
3. 剪断 `image_search` 缓存命中接线 → `test_ai_capabilities.py` 红 5
   （含命中与不假命中锁）。

三处均按纪律先 commit 再剪断，剪断后 grep 确认落盘、跑测试看到真红，
再恢复源码复跑全绿。

## 浏览器保护锁合规声明

本次为**非**浏览器功能版本。唯一触及浏览器保护文件的是
`plugin/ui/assistant_dock.py` 的版本号默认值（`version_text` 0.7.7 →
0.7.8，运行时实际由 `AssistantDock(PLUGIN_VERSION)` 覆盖），归入
显式声明的 `[browser]` 提交，未改动任何浏览器行为；`browser_panel.py`
与 `host_embed.py` 本版零改动。

## 已知边界

- 缓存 TTL 清理由读写路径顺带触发（`get` / `put` / `purge`），无后台
  定时线程——Painter 里不引入常驻协程。
- 同输入不同 provider 视为两条缓存（成本纪律优先于复用率）。
