"""PBR / 材料生成管线（规格 §8 / §9）—— fal.ai PATINA Provider + 质量门。

规格与官方事实（2026-09 核对 fal.ai 官方文档）：

* §8.1 三条核心路线对应三个官方已验证端点：
    text_to_pbr  → fal-ai/patina/material          文本→PBR（支持 image-to-image 变体）
    image_to_pbr → fal-ai/patina                   成品贴图→PBR 通道预测
    extract      → fal-ai/patina/material/extract  照片→提取目标材质并无缝化
* §8.2 输出五通道：basecolor / normal / roughness / metalness / height。
  **不假定其提供 AO**（规格原文）；若未来需要 AO 必须独立生成/推导，不塞进本模块。
* §8.2 API Key 只能放安全配置层：settings secret（DPAPI 加密的用户级配置）
  或 FAL_KEY 环境变量，源码零硬编码。
* §9 质量门两层分开，互不冒领：
    确定性层（本模块，纯 stdlib 可离线测）：通道齐全、格式有效、分辨率一致、
        位深符合、无缝边界差数值检查；
    视觉层（needs_vision，不在本模块假装通过）：灯光/阴影污染、颜色合理性、
        Normal 方向、材质逻辑一致性等语义项，交给带视觉的模型或人工。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import media
from core.settings import get_secret

# 规格 §8.2：PATINA 的输出通道。AO 不在其中——「不要假定其提供 AO」。
PBR_MAPS: Tuple[str, ...] = ("basecolor", "normal", "roughness", "metalness", "height")

# §8.1 三条路线 → 官方端点（以当期官方文档为准，勿凭记忆改）。
PATINA_ROUTES: Dict[str, str] = {
    "text_to_pbr": "fal-ai/patina/material",
    "image_to_pbr": "fal-ai/patina",
    "extract": "fal-ai/patina/material/extract",
}

QUEUE_BASE = "https://queue.fal.run"

# §9 Seamless 检查阈值：左右/上下边界逐通道平均绝对差（0-255 刻度）。
# 完全无缝理想值为 0；>SEAM_FAIL 视为边界不连续，介于两者之间标记 warning。
SEAM_WARN = 8
SEAM_FAIL = 16

# 规格 §9 Resolution：目标项目的尺寸/位深基线。 Painter 常规工作流下限。
MIN_MAP_RESOLUTION = 512


class PBRError(Exception):
    """PBR 管线错误：消息面向用户，必须能直接读懂。"""


# --------------------------------------------------------------- API Key
def fal_key() -> str:
    """按规格 §8.2 从安全配置层取 fal key；源码与仓库零硬编码。

    优先级：settings secret（DPAPI 加密的本机用户配置）→ FAL_KEY 环境变量。
    都没有返回空串，由调用方在发起请求前给出可读错误。"""
    return (get_secret("fal_key") or os.environ.get("FAL_KEY", "")).strip()


# --------------------------------------------------------------- HTTP
def _http(method: str, url: str, headers: Dict[str, str],
          body: Optional[dict] = None) -> Tuple[int, Any]:
    """默认 fetcher：urllib 直连 fal queue。可注入替换用于测试。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {}),
        **headers,
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:  # noqa: F841 —— fal 4xx/5xx 带 JSON body
        raise
    except Exception as exc:
        raise PBRError("连接 fal.ai 失败（%s）：%s" % (type(exc).__name__, exc)) from exc


def _http_error(status: int, payload: Any) -> PBRError:
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("error") or payload)[:300]
    return PBRError("fal.ai 返回 HTTP %s：%s" % (status, detail or "无详情"))


# --------------------------------------------------------------- 生成
def patina_generate(route: str, prompt: str, api_key: str = "",
                    image_url: str = "", fetcher: Optional[Callable] = None,
                    poll_interval: float = 2.0, max_wait: float = 600.0) -> Dict[str, Any]:
    """提交 PATINA 生成任务并轮询到完成，返回 {request_id, route, prompt, maps}。

    * route 必须是 PATINA_ROUTES 之一（§8.1 三条路线）。
    * maps 是 {map_type: url}，只含 PBR_MAPS 内的通道；输出里不带 map_type 的
      首图（成品渲染图）单独放 preview_url。
    * fetcher 可注入：fn(method, url, headers, body) -> (status, dict)。
    """
    model = PATINA_ROUTES.get(route)
    if not model:
        raise PBRError("未知生成路线 %r，可用：%s" % (route, ", ".join(PATINA_ROUTES)))
    if not str(prompt or "").strip() and not image_url:
        raise PBRError("prompt 和 image 至少要有一个")
    key = (api_key or fal_key()).strip()
    if not key:
        raise PBRError("尚未配置 fal key（PBR 生成需要）：在设置里填 fal key，"
                       "或设置 FAL_KEY 环境变量")
    call = fetcher or _http
    headers = {"Authorization": "Key %s" % key}

    payload: Dict[str, Any] = {"prompt": str(prompt), "maps": list(PBR_MAPS),
                               "tiling_mode": "both", "output_format": "png"}
    if image_url:
        payload["image_url"] = image_url
        if route == "text_to_pbr":
            # 官方文档：patina/material 的 image-to-image 变体按 strength 控制改写强度
            payload["strength"] = 0.75

    status, body = call("POST", "%s/%s" % (QUEUE_BASE, model), headers, payload)
    if status not in (200, 201):
        raise _http_error(status, body)
    request_id = str((body or {}).get("request_id") or (body or {}).get("requestId") or "")
    if not request_id:
        raise PBRError("fal.ai 接受了请求但没有返回 request_id：%s"
                       % json.dumps(body, ensure_ascii=False)[:300])
    status_url = "%s/%s/requests/%s/status" % (QUEUE_BASE, model, request_id)
    result_url = "%s/%s/requests/%s" % (QUEUE_BASE, model, request_id)

    deadline = time.monotonic() + max_wait
    while True:
        code, state = call("GET", status_url, headers, None)
        if code != 200:
            raise _http_error(code, state)
        queue_status = str((state or {}).get("status") or "")
        if queue_status == "COMPLETED":
            break
        if queue_status == "FAILED" or (isinstance(state, dict) and state.get("error")):
            raise PBRError("生成失败：%s"
                           % json.dumps(state, ensure_ascii=False)[:300])
        if time.monotonic() > deadline:
            raise PBRError("生成超时（%ss）：request_id=%s，稍后可用同一参数重试"
                           % (int(max_wait), request_id))
        time.sleep(poll_interval)

    code, result = call("GET", result_url, headers, None)
    if code != 200:
        raise _http_error(code, result)
    images = (result or {}).get("images") or []
    maps: Dict[str, str] = {}
    preview_url = ""
    for item in images:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        map_type = str(item.get("map_type") or "")
        if map_type in PBR_MAPS:
            maps[map_type] = url
        elif not preview_url:
            preview_url = url
    return {"request_id": request_id, "route": route, "prompt": str(prompt),
            "model": model, "maps": maps, "preview_url": preview_url}


def download_pbr_maps(generated: Dict[str, Any],
                      downloader: Optional[Callable] = None) -> Dict[str, str]:
    """把生成回执里的每张通道图下载进 media 本地工作缓存（§6.1 同款原子落盘）。

    返回 {map_type: local_path}；单张失败不阻断整体，失败的通道不进结果，
    质量门会按「通道不齐」拦下。"""
    dl = downloader or media.download
    paths: Dict[str, str] = {}
    for map_type, url in (generated.get("maps") or {}).items():
        obj = media.MediaObject(id="%s:%s" % (generated.get("request_id", ""), map_type),
                                title="PBR %s" % map_type, image_url=url,
                                provider="patina", type="image")
        try:
            dl(obj)
            if obj.local_path:
                paths[map_type] = obj.local_path
        except media.MediaError:
            continue  # 失败原因已记在 obj.error；质量门按通道缺失拦
    return paths


# --------------------------------------------------------------- PNG 解码（质量门用）
def _png_header(data: bytes) -> Tuple[int, int, int, int]:
    """(width, height, bit_depth, color_type)；非法 PNG 抛 PBRError。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 26:
        raise PBRError("不是有效的 PNG 文件")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    bit_depth = data[24]
    color_type = data[25]
    return width, height, bit_depth, color_type


_PNG_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # color_type → 每像素字节数（8-bit 时）


def _decode_png(data: bytes) -> Tuple[int, int, int, List[bytes]]:
    """解码 8-bit 灰度/RGB/灰度A/RGBA PNG → 每行字节串列表。

    只支持质量门需要的最低子集；其余（调色板/低/高位深/隔行）明确报错，
    不静默给出错误结论。"""
    width, height, bit_depth, color_type = _png_header(data)
    channels = _PNG_CHANNELS.get(color_type)
    if bit_depth != 8 or channels is None:
        raise PBRError("不支持的 PNG 变体（bit_depth=%s, color_type=%s）："
                       "质量门只支持 8-bit 灰度/RGB/RGBA" % (bit_depth, color_type))
    if data[28] != 0:  # IHDR interlace
        raise PBRError("不支持隔行 PNG（interlaced）")
    idat = bytearray()
    offset = 8
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if chunk_type == b"IDAT":
            idat += payload
        elif chunk_type == b"IEND":
            break
        offset += 12 + length
    try:
        raw = zlib.decompress(bytes(idat))
    except Exception as exc:
        raise PBRError("PNG 数据流解压失败：%s" % exc) from exc

    stride = width * channels
    rows: List[bytes] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos + 1 + stride > len(raw):
            raise PBRError("PNG 数据流不完整（行数不足）")
        filter_type = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                best = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + best) & 0xFF
        elif filter_type != 0:
            raise PBRError("未知 PNG filter 类型：%s" % filter_type)
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def seam_score(path: str) -> Dict[str, float]:
    """§9 Seamless：左右/上下边界连续性数值检查。

    返回 {"h": 左右边界差, "v": 上下边界差}（0-255，0 = 完全连续）。
    左列 vs 右列、首行 vs 末行逐像素逐通道比较。"""
    data = Path(path).read_bytes()
    width, height, channels, rows = _decode_png(data)
    total_h = count_h = 0
    for row in rows:
        for i in range(channels):
            total_h += abs(row[i] - row[(width - 1) * channels + i])
            count_h += 1
    first, last = rows[0], rows[height - 1]
    total_v = sum(abs(a - b) for a, b in zip(first, last))
    count_v = len(first)
    return {"h": round(total_h / count_h, 2) if count_h else 0.0,
            "v": round(total_v / count_v, 2) if count_v else 0.0}


# --------------------------------------------------------------- §9 质量门
def validate_pbr_set(paths: Dict[str, str]) -> Dict[str, Any]:
    """PBR 质量门（规格 §9）。paths: {map_type: local_path}。

    报告分三类，绝不混淆：
      passed —— 确定性检查通过（格式/齐全/分辨率/位深/一致性/无缝数值）
      failed —— 确定性检查失败（附原因）
      needs_vision —— 语义检查项（§9 表格里的灯光污染/方向/材质逻辑等），
        本模块不做视觉判断，交给视觉模型或人工，**不计入通过**
    """
    passed: List[str] = []
    failed: List[Dict[str, str]] = []
    report: Dict[str, Any] = {"passed": passed, "failed": failed,
                              "needs_vision": [
                                  "BaseColor：是否含明显灯光/阴影污染；颜色是否合理",
                                  "Normal：方向是否正确；是否出现异常噪点/破碎",
                                  "Roughness：结构是否与材质逻辑一致；是否过度平滑",
                                  "Metalness：金属/非金属区域是否合理",
                                  "Height：结构是否与 BaseColor/Normal 一致；避免反向或过强",
                              ]}

    missing = [m for m in PBR_MAPS if not paths.get(m)]
    if missing:
        failed.append({"check": "通道齐全", "error": "缺少通道图：%s" % ", ".join(missing)})
        report["maps"] = {}
        report["ok"] = False
        return report
    report["maps"] = dict(paths)

    sizes: Dict[str, Tuple[int, int, int]] = {}
    for map_type, path in paths.items():
        try:
            data = Path(path).read_bytes()
            width, height, bit_depth, color_type = _png_header(data)
        except (PBRError, OSError) as exc:
            failed.append({"check": "%s 格式" % map_type, "error": str(exc)})
            continue
        if width < MIN_MAP_RESOLUTION or height < MIN_MAP_RESOLUTION:
            failed.append({"check": "%s 分辨率" % map_type,
                           "error": "%sx%s 低于 %s 下限" % (width, height, MIN_MAP_RESOLUTION)})
        sizes[map_type] = (width, height, bit_depth)

    if len(sizes) == len(PBR_MAPS):
        distinct = sorted(set(sizes.values()))
        if len(distinct) > 1:
            failed.append({"check": "分辨率一致",
                           "error": "通道图尺寸/位深不一致：%s"
                                    % {m: sizes[m] for m in sorted(sizes)}})
        else:
            width, height, bit_depth = distinct[0]
            passed.append("分辨率一致（%sx%s，%s-bit）" % (width, height, bit_depth))
        for map_type in PBR_MAPS:
            path = paths[map_type]
            try:
                score = seam_score(path)
            except PBRError as exc:
                failed.append({"check": "%s 无缝" % map_type, "error": str(exc)})
                continue
            worst = max(score.values())
            if worst > SEAM_FAIL:
                failed.append({"check": "%s 无缝" % map_type,
                               "error": "边界不连续（左右差 %s / 上下差 %s，阈值 %s）"
                                        % (score["h"], score["v"], SEAM_FAIL)})
            else:
                passed.append("%s 无缝检查通过（左右差 %s / 上下差 %s%s）"
                              % (map_type, score["h"], score["v"],
                                 "，接近阈值需目视复核" if worst > SEAM_WARN else ""))

    report["ok"] = not failed
    return report


# --------------------------------------------------------------- 输入指纹（供缓存复用）
def input_fingerprint(route: str, prompt: str, image_path: str = "") -> str:
    """生成输入指纹：route + prompt + 参考图内容哈希（§10 缓存复用的键）。"""
    digest = hashlib.sha256()
    digest.update(route.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(prompt).encode("utf-8"))
    if image_path:
        digest.update(b"\x00")
        try:
            digest.update(Path(image_path).read_bytes())
        except OSError:
            digest.update(str(image_path).encode("utf-8"))
    return digest.hexdigest()
