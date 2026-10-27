"""GFlow 模型名适配；网络和生成调用使用未修改的 flow2api 源码。"""
import re
from typing import Any, Dict, Optional, Tuple
from src.core.model_resolver import (
    ASPECT_RATIO_MAP, OPENAI_IMAGE_SIZE_RE,
    _infer_aspect_ratio_from_images,
    resolve_model_name as _resolve_upstream_model,
)
from src.core.logger import debug_logger

VIDEO_MODERN_BASES = {
    "veo-3.1-fast", "veo-3.1-lite", "veo-3.1-quality", "omni-1.1-flash",
}

def _normalize_duration(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = int(round(value))
        return val if val > 0 else None
    if isinstance(value, str):
        val_str = (
            value.lower()
            .replace("seconds", "")
            .replace("second", "")
            .replace("sec", "")
            .replace("s", "")
            .replace("秒", "")
            .strip()
        )
        try:
            val = int(round(float(val_str)))
            return val if val > 0 else None
        except ValueError:
            return None
    return None

def _extract_video_params(request) -> Tuple[Optional[str], Optional[int]]:
    """从请求中提取视频 aspectRatio 和 duration 参数。"""
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None

    if request is None:
        return aspect_ratio, duration

    def _read_val(obj: Any, *keys: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            for k in keys:
                if k in obj and obj[k] is not None:
                    return obj[k]
            return None
        for k in keys:
            if hasattr(obj, k):
                v = getattr(obj, k, None)
                if v is not None:
                    return v
        extra = getattr(obj, "__pydantic_extra__", None) or {}
        for k in keys:
            if k in extra and extra[k] is not None:
                return extra[k]
        return None

    # 1. 直接从 request 顶层读取
    duration = _normalize_duration(
        _read_val(request, "duration", "durationSeconds", "duration_seconds", "seconds")
    )
    aspect_raw = _read_val(
        request, "aspectRatio", "aspect_ratio", "orientation", "aspect"
    )

    # 2. 从 generationConfig 读取
    gen_config = getattr(request, "generationConfig", None)
    if gen_config is not None:
        if duration is None:
            duration = _normalize_duration(
                _read_val(gen_config, "duration", "durationSeconds", "duration_seconds", "seconds")
            )
        video_config = _read_val(gen_config, "videoConfig", "video_config")
        if video_config is not None:
            if duration is None:
                duration = _normalize_duration(
                    _read_val(video_config, "duration", "durationSeconds", "duration_seconds", "seconds")
                )
            if aspect_raw is None:
                aspect_raw = _read_val(video_config, "aspectRatio", "aspect_ratio", "orientation", "aspect")
        if aspect_raw is None:
            aspect_raw = _read_val(gen_config, "aspectRatio", "aspect_ratio", "orientation", "aspect")

    # 3. 从 extra 或 extra_body 读取
    extra = getattr(request, "__pydantic_extra__", None) or {}
    if duration is None:
        duration = _normalize_duration(
            _read_val(extra, "duration", "durationSeconds", "duration_seconds", "seconds")
        )
    if aspect_raw is None:
        aspect_raw = _read_val(extra, "aspectRatio", "aspect_ratio", "orientation", "aspect")

    extra_body = extra.get("extra_body") or extra.get("extraBody") or {}
    if isinstance(extra_body, dict):
        if duration is None:
            duration = _normalize_duration(
                _read_val(extra_body, "duration", "durationSeconds", "duration_seconds", "seconds")
            )
        eb_vid = extra_body.get("videoConfig") or extra_body.get("video_config") or {}
        eb_gen = extra_body.get("generationConfig") or extra_body.get("generation_config") or {}
        if duration is None:
            duration = _normalize_duration(
                _read_val(eb_vid, "duration", "durationSeconds", "duration_seconds", "seconds")
            )
        if duration is None:
            duration = _normalize_duration(
                _read_val(eb_gen, "duration", "durationSeconds", "duration_seconds", "seconds")
            )
        if aspect_raw is None:
            aspect_raw = _read_val(eb_vid, "aspectRatio", "aspect_ratio", "orientation", "aspect")
        if aspect_raw is None:
            aspect_raw = _read_val(eb_gen, "aspectRatio", "aspect_ratio", "orientation", "aspect")

    # 4. 从 OpenAI size 尺寸推断视频方向
    if aspect_raw is None:
        sz = _read_val(request, "size") or _read_val(extra, "size")
        if isinstance(sz, str):
            m = OPENAI_IMAGE_SIZE_RE.match(sz.strip())
            if m:
                w, h = int(m.group("w")), int(m.group("h"))
                aspect_raw = "portrait" if h > w else "landscape"

    # 归一化 aspect_ratio
    if aspect_raw:
        ar_str = str(aspect_raw).lower().strip()
        if ar_str in ("9:16", "9-16", "portrait"):
            aspect_ratio = "portrait"
        elif ar_str in ("16:9", "16-9", "landscape"):
            aspect_ratio = "landscape"
        else:
            mapped = ASPECT_RATIO_MAP.get(ar_str)
            if mapped in ("landscape", "portrait"):
                aspect_ratio = mapped
            else:
                aspect_ratio = None

    return aspect_ratio, duration

def normalize_model_alias(m: str) -> str:
    """统一模型前缀与比例后缀，例如 gflow-nano-banana-* -> gemini-*，-16-9 -> -landscape。"""
    if m.startswith("gflow-"):
        m = m[len("gflow-"):]
    prefixes = [
        ("nano-banana-2-lite", "gemini-3.1-flash-lite-image"),
        ("nano-banana-2.0-lite", "gemini-3.1-flash-lite-image"),
        ("nano-banana-pro", "gemini-3.0-pro-image"),
        ("nano-banana-2.1", "gemini-3.1-flash-image"),
        ("nano-banana", "gemini-3.1-flash-image"),
    ]
    for nb, gem in prefixes:
        if m == nb or m.startswith(nb + "-"):
            m = gem + m[len(nb):]
            break

    ratios = [
        ("-16-9", "-landscape"),
        ("-16:9", "-landscape"),
        ("-9-16", "-portrait"),
        ("-9:16", "-portrait"),
        ("-1-1", "-square"),
        ("-1:1", "-square"),
        ("-4-3", "-four-three"),
        ("-4:3", "-four-three"),
        ("-3-4", "-three-four"),
        ("-3:4", "-three-four"),
    ]
    for r_num, r_name in ratios:
        if r_num in m:
            m = m.replace(r_num, r_name)
    return m

def resolve_model_name(model: str, request=None, model_config=None, images=None) -> str:
    """先转换插件别名；图片和标准模型交由原版解析，保留视频秒数/参考图映射。"""
    model = normalize_model_alias(model)
    if model in VIDEO_MODERN_BASES:
        aspect_ratio, duration = (
            _extract_video_params(request) if request else (None, None)
        )

        if not aspect_ratio:
            aspect_ratio = _infer_aspect_ratio_from_images(images, video_mode=True)

        if not aspect_ratio or aspect_ratio not in ("landscape", "portrait"):
            aspect_ratio = "landscape"

        if model == "omni-1.1-flash":
            allowed_durations = [4, 6, 8, 10]
            duration = min(allowed_durations, key=lambda x: abs(x - duration)) if duration else 8
        else:
            allowed_durations = [4, 6, 8]
            duration = min(allowed_durations, key=lambda x: abs(x - duration)) if duration else 8

        has_images = bool(images)
        if has_images:
            if model == "omni-1.1-flash":
                resolved = f"omni-1.1-flash-{duration}s-{aspect_ratio}"
            elif model == "veo-3.1-quality":
                resolved = f"veo-3.1-quality-i2v-{duration}s-{aspect_ratio}" if duration in (4, 6) else f"veo-3.1-quality-8s-{aspect_ratio}"
            else:
                family = "fast" if "fast" in model else "lite"
                resolved = f"veo-3.1-{family}-i2v-{duration}s-{aspect_ratio}" if duration in (4, 6) else f"veo-3.1-{family}-r2v-8s-{aspect_ratio}"
        else:
            resolved = f"{model}-{duration}s-{aspect_ratio}"

        if model_config and resolved in model_config:
            debug_logger.log_info(
                f"[MODEL_RESOLVER] video model auto-mapped: {model} -> {resolved} "
                f"(duration={duration}s, aspect={aspect_ratio}, has_images={has_images})"
            )
            return resolved

        debug_logger.log_warning(
            f"[MODEL_RESOLVER] video model {model} mapping failed (target={resolved}), using the original model name"
        )
        return model
    return _resolve_upstream_model(
        model, request=request, model_config=model_config, images=images,
    )
