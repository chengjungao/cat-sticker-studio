"""构造提交给 ComfyUI 的 API 格式工作流。

两个容易踩的坑，都在这里处理掉了：

1. **顶层只能放节点。** 加任何 `_comment` / `meta` 之类的键，ComfyUI 校验时会崩，
   只回一个没有详情的 500。所以注释写在这里，不写进 JSON。

2. **图生图的采样起点取自「文本编码节点」的第 3 个输出**（`["5", 2]`），
   不是 EmptyLatentImage。Qwen-Image 的参考图走的是「并排的上下文」通道。
   ⚠️ 顺带一个副作用：**输出尺寸会跟着第一张参考图走**。
   所以想出一张 750×400 的横幅，不能指望把 EmptyLatentImage 的宽高改小 ——
   得让模型出一张横版，再自己裁。

参数一律来自 config.json，读者改配置或界面里的表单即可，不用碰代码。
"""


def _loaders(models, gen):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "qwen_image",
                         "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "4": {"class_type": "QwenImage21Cache",
              "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
    }


def _tail(gen, seed, prefix, latent_from):
    return {
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "seed": int(seed),
                         "steps": int(gen["steps"]), "cfg": float(gen["cfg"]),
                         "sampler_name": gen["sampler"], "scheduler": gen["scheduler"],
                         "positive": ["5", 0], "negative": ["5", 1],
                         "latent_image": latent_from, "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def build_t2i(models, gen, prompt, seed, prefix, width=None, height=None, negative=""):
    """文生图。用在「没人给照片，只有文字描述」这条路上。"""
    w = int(width or gen["width"])
    h = int(height or gen["height"])
    wf = _loaders(models, gen)
    wf["5"] = {"class_type": "TextEncodeQwenImage21",
               "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": negative,
                          "vae": ["3", 0], "resolution": int(gen["resolution"])}}
    wf["6"] = {"class_type": "EmptyLatentImage",
               "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf.update(_tail(gen, seed, prefix, ["6", 0]))
    return wf


def build_i2i(models, gen, prompt, seed, prefix, ref_name, negative="", ref2=None):
    """图生图：把定妆照当参考图，逐张出表情。

    ⚠️ 只挂 `images.image_1` 一张参考图，**不要**顺手把上一张的产物也挂进去。
    误差会逐张累积，几张之后主角就认不出来了。
    `image_2` 起的槽位留给别的用途（这张表情包工坊里没用上），不留给上一张。

    `ref2` 是可选的第二张参考图，用在「定妆照 + 用户照片一起喂」这条路上。
    """
    wf = _loaders(models, gen)
    wf["10"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
    enc = {"clip": ["2", 0], "prompt": prompt, "negative_prompt": negative,
           "vae": ["3", 0], "resolution": int(gen["resolution"]),
           "images.image_1": ["10", 0]}
    if ref2:
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": ref2}}
        enc["images.image_2"] = ["11", 0]
    wf["5"] = {"class_type": "TextEncodeQwenImage21", "inputs": enc}
    wf["6"] = {"class_type": "EmptyLatentImage",
               "inputs": {"width": int(gen["width"]), "height": int(gen["height"]),
                          "batch_size": 1}}
    wf.update(_tail(gen, seed, prefix, ["5", 2]))
    return wf
