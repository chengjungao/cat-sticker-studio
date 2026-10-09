"""配置文件读写。

原则：读者改 config.json，不改代码。界面上改的设置也写回同一个文件，
所以「界面里填的」和「手改文件」永远是一份东西，不会分叉。
"""
import io
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(HERE, "config.json")

DEFAULTS = {
    "comfy_url": "http://127.0.0.1:8188",
    "models": {
        "unet": "qwen_image_2.1_int8_convrot.safetensors",
        "clip": "qwen3vl_8b_int8_convrot.safetensors",
        "vae": "qwen_image_2.1_vae_bf16.safetensors",
    },
    "generate": {
        "steps": 25, "cfg": 1, "sampler": "euler", "scheduler": "simple",
        "width": 1024, "height": 1024, "resolution": 1024,
        "base_seed": 20261009,
    },
    "sticker": {
        "size": 240, "frame_ms": 400, "edge_trim": 0.055,
        "alpha_cut": 128, "text_band": 0.26,
    },
    "assets": {"banner_width": 750, "banner_height": 400, "banner_feather": 90,
               "banner_trim_y": 0.06, "icon_head_band": 0.45},
    "workdir": "我的表情包",
}


def _merge(base, over):
    """逐层补默认值 —— 读者删掉某一项也能跑起来，不用照抄整份文件。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    if not os.path.exists(CONFIG_PATH):
        save(DEFAULTS)
        return dict(DEFAULTS)
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as f:
            return _merge(DEFAULTS, json.load(f))
    except Exception:
        # 配置文件坏了不该让程序起不来，退回默认值让界面能打开
        return dict(DEFAULTS)


def save(cfg):
    with io.open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2))


def save_and_reload(cfg):
    save(cfg)
    return load()
