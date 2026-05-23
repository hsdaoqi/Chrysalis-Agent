"""
vision_api — 统一视觉 API，调用多模态 LLM 分析图片。

用法:
    from vision_api import ask_vision
    result = ask_vision(image, prompt="描述图片内容")
    # image: PIL Image 或文件路径(str/Path)
    # 返回 str: 成功为模型回复，失败为 'Error: ...'
"""

import base64
import json
import os
import sys
from io import BytesIO
from pathlib import Path

_MAX_PIXELS = 1_440_000
_JPEG_QUALITY = 75


def ask_vision(image, prompt="描述图片内容", max_pixels=None, timeout=60) -> str:
    """调用多模态 LLM 分析图片。

    Args:
        image: PIL Image 对象或文件路径
        prompt: 提问内容
        max_pixels: 最大像素数（自动缩放），默认 1,440,000
        timeout: API 超时秒数

    Returns:
        str: 模型回复文本，或 'Error: ...'
    """
    max_pixels = max_pixels or _MAX_PIXELS
    try:
        img_data, media_type = _prepare_image(image, max_pixels)
    except Exception as e:
        return f"Error: 图片处理失败: {e}"

    config = _load_config()
    if not config:
        return "Error: 未找到可用的 LLM 配置 (configs/llm_models.json)"

    try:
        return _call_openai_compatible(config, img_data, media_type, prompt, timeout)
    except Exception as e:
        return f"Error: API 调用失败: {e}"


def _prepare_image(image, max_pixels: int) -> tuple[str, str]:
    """将图片转为 base64 编码的 JPEG。返回 (base64_data, media_type)。"""
    from PIL import Image

    if isinstance(image, (str, Path)):
        img = Image.open(image)
    else:
        img = image

    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    pixels = w * h
    if pixels > max_pixels:
        scale = (max_pixels / pixels) ** 0.5
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, "image/jpeg"


def _load_config() -> dict | None:
    """从 configs/llm_models.json 加载第一个可用的 LLM 配置。"""
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "configs" / "llm_models.json"
    if not config_path.exists():
        return None

    try:
        configs = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not configs:
        return None

    cfg = configs[0]
    api_key = _expand_env(cfg.get("api_key", ""))
    if not api_key:
        return None

    return {
        "api_key": api_key,
        "base_url": cfg.get("base_url", "").rstrip("/"),
        "model": cfg.get("model", ""),
    }


def _expand_env(value: str) -> str:
    """展开 ${ENV_VAR} 格式的环境变量引用。"""
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        return os.environ.get(env_name, "")
    return value


def _call_openai_compatible(config: dict, img_data: str, media_type: str, prompt: str, timeout: int) -> str:
    """通过 OpenAI 兼容接口发送多模态请求。"""
    import urllib.request
    import urllib.error

    url = f"{config['base_url']}/chat/completions"
    data_uri = f"data:{media_type};base64,{img_data}"

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "max_tokens": 2048,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return f"Error: HTTP {e.code}: {error_body[:500]}"

    choices = result.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return "Error: API 返回空结果"
