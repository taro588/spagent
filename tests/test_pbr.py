"""PBR 生成管线（规格 §8 / §9）与 Asset Registry（规格 §10）。

为什么钉这组测试：PBR 生成是插件第一次真正「产出资产」的能力（不是改 Painter
状态，而是生成新文件并走生命周期）。三个高危点必须有锁：

1. **PATINA wire 正确性**：三条路线 → 三个官方端点、queue submit/poll/result
   协议、五通道解析（不假定 AO）。锁法：注入 fake fetcher 逐调用断言
   URL/method/payload。
2. **质量门诚实性**：确定性检查（格式/分辨率/一致/无缝）通过才算通过，
   语义项归 needs_vision **绝不计入 passed**——0.7.1 的教训是能力虚报，
   这里同样适用：质量门不得虚报「检查通过」。
3. **状态机纪律**：§10 只许顺次前进；VALIDATED 前不得导入 Painter；
   缓存复用必须命中「同指纹 + 已验证 + 文件还在」的资产。
"""
from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from fake_painter import install, painter  # noqa: E402,F401

from core import asset_registry, pbr  # noqa: E402


# --------------------------------------------------------------- PNG fixture
def _encode_filter(rows, filter_type, channels):
    """按 PNG 规范把原始行编码成带 filter 前缀的数据流（fixture 用的编码器，
    与被测的解码器互为逆运算）。"""
    stride = len(rows[0]) if rows else 0
    out = bytearray()
    prev = bytes(stride)
    for raw in rows:
        if filter_type == 0:
            line = bytes(raw)
        elif filter_type == 1:  # Sub：减去左邻
            line = bytes((raw[i] - (raw[i - channels] if i >= channels else 0)) & 0xFF
                         for i in range(stride))
        elif filter_type == 2:  # Up：减去上邻
            line = bytes((raw[i] - prev[i]) & 0xFF for i in range(stride))
        elif filter_type == 3:  # Average
            line = bytes((raw[i] - (((raw[i - channels] if i >= channels else 0)
                                     + prev[i]) >> 1)) & 0xFF for i in range(stride))
        else:  # Paeth
            def predictor(a, b, c):
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line = bytes((raw[i] - predictor(raw[i - channels] if i >= channels else 0,
                                             prev[i],
                                             prev[i - channels] if i >= channels else 0)) & 0xFF
                         for i in range(stride))
        out.append(filter_type)
        out += line
        prev = bytes(raw)
    return bytes(out)


def _png(width=512, height=512, color_type=2, bit_depth=8,
         rows=None, filter_type=0):
    """构造最小合法 PNG：8-bit（默认 RGB），逐行可指定内容与 filter。"""
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    if rows is None:
        rows = [bytes([v % 256 for v in range(stride)]) for _ in range(height)]
    raw = _encode_filter(rows, filter_type, channels)
    def chunk(ctype, payload):
        return (len(payload).to_bytes(4, "big") + ctype
                + payload + zlib.crc32(ctype + payload).to_bytes(4, "big"))
    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes([bit_depth, color_type, 0, 0, 0]))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _seamless_png(width=512, height=512):
    """边界连续的纹理图：左右列、首末行的逐通道差都在噪声量级（< SEAM_WARN）。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            # 微噪声纹理：5 级小抖动，任意两像素差最多 4 → 边界差必然低于阈值
            v = 128 + ((x * 7 + y * 13) % 5) - 2
            row += bytes([v, 255 - v, 96 + (v % 32)])
        rows.append(bytes(row))
    return _png(width, height, rows=rows)


def _torn_png(width=512, height=512):
    """边界断裂的图：最右列突变成亮白，与左列差 255。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            v = 0 if x < width - 1 else 255  # 最右列突变成亮白
            row += bytes([v, v, v])
        rows.append(bytes(row))
    return _png(width, height, rows=rows)


@pytest.fixture
def five_maps(tmp_path):
    """一套完整的五通道无缝 PNG（质量门应通过）。"""
    paths = {}
    for map_type in pbr.PBR_MAPS:
        path = tmp_path / ("%s.png" % map_type)
        path.write_bytes(_seamless_png())
        paths[map_type] = str(path)
    return paths


# --------------------------------------------------------------- 路线与 key
def test_three_routes_map_to_official_endpoints():
    """§8.1 三条路线 → 官方端点（以当期官方文档核对过，勿凭记忆改）。"""
    assert pbr.PATINA_ROUTES["text_to_pbr"] == "fal-ai/patina/material"
    assert pbr.PATINA_ROUTES["image_to_pbr"] == "fal-ai/patina"
    assert pbr.PATINA_ROUTES["extract"] == "fal-ai/patina/material/extract"


def test_pbr_maps_exclude_ao():
    """§8.2 规格：不要假定 PATINA 提供 AO。"""
    assert "ao" not in pbr.PBR_MAPS and "ambient" not in [m.lower() for m in pbr.PBR_MAPS]
    assert set(pbr.PBR_MAPS) == {"basecolor", "normal", "roughness",
                                 "metalness", "height"}


def test_missing_key_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(pbr, "fal_key", lambda: "")
    with pytest.raises(pbr.PBRError) as exc:
        pbr.patina_generate("text_to_pbr", "旧铜", api_key="")
    assert "fal key" in str(exc.value)


# --------------------------------------------------------------- queue 协议
class _FakeFal:
    """fal queue 协议桩：记录每次调用，按剧本回状态。"""

    def __init__(self, result=None, fail_once=False):
        self.calls = []
        self.result = result or {"images": [
            {"url": "https://fal.media/preview.png"},
            {"map_type": "basecolor", "url": "https://fal.media/bc.png"},
            {"map_type": "normal", "url": "https://fal.media/n.png"},
            {"map_type": "roughness", "url": "https://fal.media/r.png"},
            {"map_type": "metalness", "url": "https://fal.media/m.png"},
            {"map_type": "height", "url": "https://fal.media/h.png"},
        ]}
        self.poll_count = 0

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url,
                           "auth": headers.get("Authorization"), "body": body})
        if method == "POST":
            return 200, {"request_id": "req-123"}
        if url.endswith("/status"):
            self.poll_count += 1
            return 200, {"status": "COMPLETED"}
        if url.endswith("/req-123"):
            return 200, self.result
        raise AssertionError("unexpected url: " + url)


def test_patina_generate_full_protocol():
    fal = _FakeFal()
    out = pbr.patina_generate("text_to_pbr", "风化旧铜", api_key="k1",
                              fetcher=fal, poll_interval=0)
    submit = fal.calls[0]
    assert submit["auth"] == "Key k1"
    assert submit["body"]["prompt"] == "风化旧铜"
    assert submit["body"]["maps"] == ["basecolor", "normal", "roughness",
                                      "metalness", "height"]
    assert submit["body"]["tiling_mode"] == "both"
    assert out["request_id"] == "req-123"
    assert set(out["maps"]) == set(pbr.PBR_MAPS)
    assert out["preview_url"] == "https://fal.media/preview.png"
    # 轮询和取结果的 URL 都带 request_id
    assert any("/requests/req-123/status" in c["url"] for c in fal.calls)
    assert any(c["url"].endswith("/requests/req-123") for c in fal.calls)


def test_patina_generate_rejects_unknown_route():
    with pytest.raises(pbr.PBRError, match="未知生成路线"):
        pbr.patina_generate("text_to_video", "x", api_key="k")


def test_patina_generate_failed_job_is_readable():
    def failing(method, url, headers, body):
        if url.endswith("/status"):
            return 200, {"status": "FAILED", "error": "model overloaded"}
        return 200, {"request_id": "req-x"}
    with pytest.raises(pbr.PBRError, match="生成失败"):
        pbr.patina_generate("text_to_pbr", "石墙", api_key="k",
                            fetcher=failing, poll_interval=0)


def test_image_route_sends_data_uri_and_strength(tmp_path):
    """image_to_pbr：本地参考图转 data URI 后发给官方端点。"""
    img = tmp_path / "ref.png"
    img.write_bytes(_seamless_png(32, 32))
    fal = _FakeFal()
    pbr.patina_generate("image_to_pbr", "把这张转成 PBR", api_key="k",
                        image_url="data:image/png;base64,AAAA", fetcher=fal,
                        poll_interval=0)
    assert fal.calls[0]["url"].endswith("/fal-ai/patina")
    assert fal.calls[0]["body"]["image_url"].startswith("data:image/png;base64,")
    assert "strength" not in fal.calls[0]["body"]  # strength 只属于 material 端点


# --------------------------------------------------------------- 质量门
def test_quality_gate_passes_seamless_set(five_maps):
    report = pbr.validate_pbr_set(five_maps)
    assert report["ok"] is True
    assert not report["failed"]
    # 确定性层只报它真检查过的；语义项绝不能混进 passed
    assert all("无缝" in p or "分辨率" in p for p in report["passed"])
    assert len(report["needs_vision"]) == 5  # §9 表格的语义项
    assert not any("灯光" in p for p in report["passed"]), "语义项不得计入通过"


def test_quality_gate_blocks_missing_channel(five_maps):
    del five_maps["metalness"]
    report = pbr.validate_pbr_set(five_maps)
    assert report["ok"] is not True
    assert any("缺少通道" in item["error"] for item in report["failed"])


def test_quality_gate_blocks_resolution_mismatch(five_maps):
    five_maps["height"] = str(Path(five_maps["height"]).with_name("h2.png"))
    Path(five_maps["height"]).write_bytes(_seamless_png(1024, 1024))
    report = pbr.validate_pbr_set(five_maps)
    assert any("不一致" in item["error"] for item in report["failed"])


def test_quality_gate_blocks_low_resolution(five_maps):
    five_maps["roughness"] = str(Path(five_maps["roughness"]).with_name("r_small.png"))
    Path(five_maps["roughness"]).write_bytes(_seamless_png(256, 256))
    report = pbr.validate_pbr_set(five_maps)
    assert any("低于" in item["error"] for item in report["failed"])


def test_quality_gate_blocks_torn_edges(five_maps):
    five_maps["normal"] = str(Path(five_maps["normal"]).with_name("n_torn.png"))
    Path(five_maps["normal"]).write_bytes(_torn_png())
    report = pbr.validate_pbr_set(five_maps)
    assert any("边界不连续" in item["error"] for item in report["failed"])


def test_quality_gate_rejects_non_png(five_maps):
    five_maps["basecolor"] = str(Path(five_maps["basecolor"]).with_suffix(".bin"))
    Path(five_maps["basecolor"]).write_bytes(b"not a png at all")
    report = pbr.validate_pbr_set(five_maps)
    assert any("PNG" in item["error"] for item in report["failed"])


def test_seam_score_numeric_values():
    """无缝 → 差值 ≈ 0；撕裂 → 远超阈值。数值必须真实可读，不能只报布尔。"""
    seamless = Path(str(pbr.__file__)).parent / "_seamless_test.png"
    torn = Path(str(pbr.__file__)).parent / "_torn_test.png"
    try:
        seamless.write_bytes(_seamless_png(64, 64))
        torn.write_bytes(_torn_png(64, 64))
        good = pbr.seam_score(str(seamless))
        bad = pbr.seam_score(str(torn))
        # 微噪声纹理的边界差不为 0，但必须低于 warning 阈值（连续）
        assert max(good.values()) < pbr.SEAM_WARN
        assert max(bad.values()) > pbr.SEAM_FAIL
    finally:
        seamless.unlink(missing_ok=True)
        torn.unlink(missing_ok=True)


def test_png_decoder_supports_all_filter_types():
    """§9 无缝检查依赖像素级解码：5 种 PNG filter 都必须解得对。"""
    rows = [bytes([(x * 3) & 0xFF for x in range(64)]) for _ in range(8)]
    for filter_type in range(5):
        data = _png(64, 8, color_type=0, rows=rows, filter_type=filter_type)
        width, height, channels, decoded = pbr._decode_png(data)
        assert (width, height, channels) == (64, 8, 1)
        assert decoded == rows, "filter %s 解码不正确" % filter_type


# --------------------------------------------------------------- Asset Registry（§10）
@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_registry, "registry_path",
                        lambda: tmp_path / "asset_registry.json")
    return asset_registry.Registry()


def test_state_machine_only_moves_forward(registry):
    """§10 状态机：顺次前进合法，跳步/回退一律拒绝。"""
    asset = registry.register(asset_registry.Asset(source="铜", provider="patina",
                                                   route="text_to_pbr"))
    order = asset_registry.ASSET_STATES
    assert order == ["GENERATED", "DOWNLOADED", "VALIDATED", "REGISTERED",
                     "IMPORTED_TO_PAINTER", "ASSIGNED", "VERIFIED", "EXPORTED"]
    for next_state in order[1:]:
        asset = registry.advance(asset.asset_id, next_state)
    assert asset.state == "EXPORTED"
    with pytest.raises(asset_registry.RegistryError, match="非法状态迁移"):
        registry.advance(asset.asset_id, "GENERATED")  # 回退
    fresh = registry.register(asset_registry.Asset())
    with pytest.raises(asset_registry.RegistryError, match="非法状态迁移"):
        registry.advance(fresh.asset_id, "REGISTERED")  # 跳步


def test_import_gate_requires_validated(registry):
    """§10：下载完成先验证再导入——VALIDATED 之前禁止导入 Painter。"""
    asset = registry.register(asset_registry.Asset(files={"basecolor": "x.png"}))
    with pytest.raises(asset_registry.RegistryError, match="先验证"):
        asset.require_importable()
    registry.advance(asset.asset_id, "DOWNLOADED")
    with pytest.raises(asset_registry.RegistryError, match="先验证"):
        asset.require_importable()
    registry.advance(asset.asset_id, "VALIDATED")
    with pytest.raises(asset_registry.RegistryError, match="先验证"):
        asset.require_importable()  # VALIDATED 还不行，必须 REGISTERED
    registry.advance(asset.asset_id, "REGISTERED")
    asset.require_importable()  # 不抛即通过


def test_registry_persists_across_instances(registry, tmp_path, monkeypatch):
    """注册表落盘可复读（原子写：不留 .part 残渣）。"""
    asset = registry.register(asset_registry.Asset(source="石墙"))
    registry.advance(asset.asset_id, "DOWNLOADED")
    monkeypatch.setattr(asset_registry, "registry_path",
                        lambda: tmp_path / "asset_registry.json")
    second = asset_registry.Registry()
    assert second.get(asset.asset_id).state == "DOWNLOADED"
    assert not list(tmp_path.glob("*.part")), "不得留下半截临时文件"


def test_duplicate_id_is_refused(registry):
    first = registry.register(asset_registry.Asset(asset_id="dup1"))
    with pytest.raises(asset_registry.RegistryError, match="已存在"):
        registry.register(asset_registry.Asset(asset_id="dup1"))


def test_cache_reuse_hits_verified_same_fingerprint(registry, five_maps):
    """§10 缓存复用：同 provider+route+指纹 + 已验证 + 文件在 → 命中。"""
    asset = asset_registry.Asset(source="旧铜", provider="patina",
                                 route="text_to_pbr", fingerprint="FP1",
                                 files=five_maps)
    registry.register(asset)
    registry.advance(asset.asset_id, "DOWNLOADED")
    assert registry.find_reusable("patina", "text_to_pbr", "FP1") is None  # 未验证
    registry.advance(asset.asset_id, "VALIDATED")
    hit = registry.find_reusable("patina", "text_to_pbr", "FP1")
    assert hit is not None and hit.asset_id == asset.asset_id
    assert registry.find_reusable("patina", "text_to_pbr", "FP2") is None  # 指纹不同
    assert registry.find_reusable("patina", "extract", "FP1") is None      # 路线不同
    # 文件被删 → 不再命中（不把死资产当缓存）
    Path(five_maps["height"]).unlink()
    assert registry.find_reusable("patina", "text_to_pbr", "FP1") is None


def test_failed_asset_is_not_reusable(registry, five_maps):
    asset = asset_registry.Asset(source="x", provider="patina",
                                 route="text_to_pbr", fingerprint="FP",
                                 files=five_maps)
    registry.register(asset)
    registry.advance(asset.asset_id, "DOWNLOADED")
    registry.advance(asset.asset_id, "VALIDATED")
    registry.mark_failed(asset.asset_id, "质量门未通过")
    assert registry.find_reusable("patina", "text_to_pbr", "FP") is None


def test_input_fingerprint_covers_route_prompt_image(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"photo-bytes")
    a = pbr.input_fingerprint("text_to_pbr", "铜", "")
    b = pbr.input_fingerprint("text_to_pbr", "铜", "")
    c = pbr.input_fingerprint("extract", "铜", "")
    d = pbr.input_fingerprint("text_to_pbr", "铁", "")
    e = pbr.input_fingerprint("text_to_pbr", "铜", str(img))
    assert a == b and a != c and a != d and a != e
