# SP AI Assistant 0.7.4

PBR 云端生成与质量门（规格 §8/§9）+ 资产注册表（规格 §10）。

## 新增

- **PATINA 生成管线**（`plugin/core/pbr.py`，纯 stdlib）：
  - 三条官方路线：Text→PBR（`fal-ai/patina/material`）、Image→PBR
    （`fal-ai/patina`）、Photo Extract（`fal-ai/patina/material/extract`）
  - Queue API 全流程：提交 → 轮询 → 取结果（fetcher 可注入，离线可测）
  - 五通道图下载落盘（复用 media 原子写）；本地图片自动转 data URI 上传
  - API Key 只从安全配置层（DPAPI）或环境变量读取，源码零硬编码
- **PBR 质量门**（规格 §9，确定性层）：通道齐全、分辨率一致与下限、
  PNG 格式校验（含 filter 0–4 全类型解码器）、无缝边界数值检查；
  语义检查项（灯光污染/法线方向/材质逻辑）如实标 `needs_vision`，
  绝不虚报通过。不假定提供 AO 通道。
- **资产注册表**（`plugin/core/asset_registry.py`，规格 §10）：
  - 八态状态机 GENERATED→DOWNLOADED→VALIDATED→REGISTERED→
    IMPORTED_TO_PAINTER→ASSIGNED→VERIFIED→EXPORTED，只进不跳；
  - 每资产带稳定 ID / 来源 / 参数 / 文件哈希，JSON 原子落盘；
  - 同指纹缓存复用（必须已过 VALIDATED 且文件完好）；
  - **VALIDATED 之前禁止导入 Painter**（先验证后导入，硬门）。
- **`pbr_generate` 工具**：schema + 执行器挂到全部四条协议路径
  （OpenAI Responses / OpenAI 兼容 / Anthropic / Gemini）；
  回执带质量报告、文件路径、现成 Markdown 展示片段；生成结果图
  进 LAST_IMAGES 直显管线（0.7.3 纪律：不依赖模型写 Markdown）。

## 测试

- 202 → **203 项全绿**（新增 `tests/test_pbr.py` 22 项：PATINA 协议桩、
  质量门正反例、状态机非法迁移、持久化、缓存复用、输入指纹；
  pbr_generate 四路径 wire 锁 1 项）。
- 证伪三处如实红：剪断 seam 检查 → 2 红；剪断状态机校验 → 1 红；
  剪断 Gemini pbr 声明 → wire 锁红（该锁正是证伪发现的缺口——
  剪断时旧测试全绿，裸奔确认后补锁）。

## 已知边界

- PATINA 调用需要用户自配 fal.ai API Key（设置面板或
  `SPAI_FAL_KEY` 环境变量）；无 Key 时工具回执给出可操作指引，
  不会中断会话。
- 端到端「真实生成 → 质量门 → 导入 Painter」需配 Key 的真机会话验收；
  离线测试覆盖协议形状、质量门判定与状态机全部迁移规则。
