"""平台要求的另外三件：封面、聊天面板图标、详情页横幅。

| 素材           | 尺寸      | 格式      | 体积    | 透明底 |
|----------------|-----------|-----------|---------|--------|
| 表情封面图     | 240×240   | PNG       | ≤500KB  | **必须** |
| 聊天面板图标   | 50×50     | PNG       | ≤100KB  | **必须** |
| 详情页横幅     | 750×400   | PNG / JPG | ≤500KB  | 不要求 |
| 单个表情       | 240×240   | GIF       | ≤500KB  | 建议透明 |

三条最容易踩的：

1. **封面不要另出一张图**，直接拿定妆照那张已经抠净、裁好的 ——
   跟整批表情同一个裁切窗口，比例才一致。

2. **图标不能把整只猫压进 50²**，会糊成一团。取**头部**。
   ⚠️ 而且取景框不能按「前景宽 × 0.62」估 —— Q 版猫的头本来就将近占满整个宽度，
   估出来头顶和耳朵会顶到边。正解是先量**上半段的实际横向跨度**（＝含耳朵的头宽），
   再按它开一个正方形，四周留 11%。

3. **横幅有三条硬要求：避免任何文字、避免白底、避免透明底。**
   第一版给米白底就是踩了「避免白底」这条。用明确的彩色底（默认淡蓝）。
   另外横幅不能「等比缩到宽 750 再上下裁成 400」—— 那只留了中间一半，
   头顶的毛和爪子全被切掉。正解是原图**不裁**、缩成高 400 贴右侧，
   左边用原图取样的底色铺满，交界处做一段羽化，接缝看不见。
"""
import io
import json
import os

from PIL import Image, ImageDraw

from .cutout import alpha_bbox, crop_square, font

HEAD_BAND = 0.45       # 量头宽时只取前景高度的上半段（那一段就是头 + 耳朵）
HEAD_MARGIN = 1.22     # 取景框边长 = 头宽 × 这个系数（四周约留 11%）


def head_box(rgba):
    """按头部真实宽度反推图标取景框。返回 (box, 头宽像素)。"""
    bb = alpha_bbox(rgba)
    if not bb:
        w, h = rgba.size
        return (0, 0, min(w, h), min(w, h)), min(w, h)
    a = rgba.split()[-1].point(lambda v: 255 if v > 8 else 0)
    band = a.crop((bb[0], bb[1], bb[2], bb[1] + int((bb[3] - bb[1]) * HEAD_BAND)))
    hb = alpha_bbox(band.convert("RGBA")) or bb
    hw = hb[2] - hb[0]
    side = int(hw * HEAD_MARGIN)
    cx = bb[0] + (hb[0] + hb[2]) // 2
    cy = bb[1] + int(hw * 0.52)
    return (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2), hw


def spec_ok(kind, want_size, limit_kb, path):
    """按平台表格逐条判，全过才算过。返回一份可写进清单的报告。"""
    im = Image.open(path)
    kb = os.path.getsize(path)
    corner = im.split()[-1].getpixel((0, 0)) if im.mode in ("RGBA", "LA") else 255
    rows = [
        ("格式 PNG", im.format == "PNG"),
        ("带透明通道", im.mode in ("RGBA", "LA")),
        ("尺寸 %s" % kind, im.size == want_size),
        ("体积 ≤%dKB（实际 %.1fKB）" % (limit_kb, kb / 1024.0), kb <= limit_kb * 1024),
        ("四角透明（角点 alpha=%d）" % corner, corner < 16),
    ]
    return {"file": os.path.basename(path), "kb": round(kb / 1024.0, 1),
            "size": list(im.size),
            "checks": [{"item": n, "ok": bool(o)} for n, o in rows],
            "pass": all(o for _, o in rows)}


def make_cover(hero_rgba, path, size=240, trim=0.055, trim_bottom=0.0):
    """封面 = 定妆照用同一个裁切窗口裁出来。不另出图。"""
    crop_square(hero_rgba, trim, size, trim_bottom).save(path)
    return spec_ok("%dx%d" % (size, size), (size, size), 500, path)


def make_icon(hero_rgba, path, size=50):
    """图标 = 定妆照的头部。返回 (报告, 头宽, 取景框, 四边留白)。"""
    box, hw = head_box(hero_rgba)
    head = hero_rgba.crop(box).resize((size, size), Image.LANCZOS)
    head.save(path)
    rep = spec_ok("%dx%d" % (size, size), (size, size), 100, path)
    ib = alpha_bbox(head) or (0, 0, size, size)
    pads = {"上": ib[1], "左": ib[0], "右": size - ib[2], "下": size - ib[3]}
    rep["head_width_px"] = hw
    rep["pads_px"] = pads
    return rep, hw, box, pads


def make_banner(raw, path_png, width=750, height=400, feather=90, trim_y=0.06,
                also_jpg=True):
    """把模型出的原图，做成 750×400 的详情页横幅。

    ⚠️ 两个坑：

    1. **原图是正方形，不是横版。** 图生图的输出尺寸跟着第一张参考图走，
       而参考图（定妆照）是 1024² —— 所以就算把工作流的宽高设成 1344×768，
       出来还是 1344²。这不是 bug，是那条链路的特点。
    2. **不能「缩到宽 750 再上下裁成 400」**：那只留了中间 53%，
       猫头顶那撮毛和爪子都被切掉了。

    做法：原图上下各裁一点点（默认 6%，纯留白），等比缩成高 400 贴**正中间**，
    左右两侧用原图取样的底色铺满，交界处做一段羽化，接缝看不见。
    注意是居中而不是靠右 —— 官方要求横幅「避免任何文字」，
    左侧那片留白不会放字，靠右反而让整条看起来偏沉。
    """
    src = raw.convert("RGB") if isinstance(raw, Image.Image) else Image.open(raw).convert("RGB")
    if trim_y > 0:
        t = int(src.height * trim_y)
        src = src.crop((0, t, src.width, src.height - t))
    scale = float(height) / src.height
    w2 = max(1, int(round(src.width * scale)))
    img = src.resize((w2, height), Image.LANCZOS)

    bg = src.getpixel((6, src.size[1] // 2))          # 取左侧中部的底色
    canvas = Image.new("RGB", (width, height), bg)
    x0 = max(0, (width - w2) // 2)

    # 两侧用**原图自身最外一列像素**横向拉伸铺满。
    # 为什么不直接用取样到的底色：原图背景有极轻微的渐变，
    # 取色只取了一个点，铺上去会跟原图上下两端的背景差出一点点，
    # 在交界处留下一条很淡的竖线。用自己那一列拉伸就完全同源，接缝不存在。
    if x0 > 0:
        rw = width - w2 - x0
        canvas.paste(img.crop((0, 0, 1, height)).resize((x0, height), Image.NEAREST), (0, 0))
        if rw > 0:
            canvas.paste(img.crop((w2 - 1, 0, w2, height)).resize((rw, height), Image.NEAREST),
                         (x0 + w2, 0))

    mask = Image.new("L", (w2, height), 255)
    dm = ImageDraw.Draw(mask)
    for i in range(feather):
        v = int(255.0 * i / feather)
        dm.line([(i, 0), (i, height - 1)], fill=v)
        dm.line([(w2 - 1 - i, 0), (w2 - 1 - i, height - 1)], fill=v)
    canvas.paste(img, (x0, 0), mask)

    canvas.save(path_png)
    out = [path_png]
    if also_jpg:
        p_jpg = os.path.splitext(path_png)[0] + ".jpg"
        canvas.save(p_jpg, quality=92)
        out.append(p_jpg)
    return {"files": out, "bg_sample": list(bg), "subject_px": [w2, height],
            "kb": {os.path.basename(p): round(os.path.getsize(p) / 1024.0, 1) for p in out},
            "pass": all(os.path.getsize(p) <= 500 * 1024 for p in out)}


def preview_board(cover_path, icon_path, out_path):
    """提交件对照板：封面与图标各放在浅底 / 深底上，图标再放大 6 倍查描边与留白。"""
    cover = Image.open(cover_path).convert("RGBA")
    icon = Image.open(icon_path).convert("RGBA")
    W, H = 700, 668
    cv = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(cv)
    d.text((20, 14), "提交件对照 · 封面 240x240（实际尺寸）", font=font(18), fill=(60, 60, 60))
    for x, col, lab in ((20, (245, 245, 245), "浅色底"), (290, (38, 38, 38), "深色底")):
        cv.paste(col, (x, 50, x + 240, 290))
        cv.paste(cover, (x, 50), cover)
        d.rectangle([x, 50, x + 239, 289], outline=(150, 150, 150))
        d.text((x, 296), lab, font=font(14), fill=(90, 90, 90))
    d.text((20, 300), "图标 50x50（实际尺寸）", font=font(18), fill=(60, 60, 60))
    for x, col in ((20, (245, 245, 245)), (80, (38, 38, 38))):
        cv.paste(col, (x, 332, x + 50, 382))
        cv.paste(icon, (x, 332), icon)
        d.rectangle([x, 332, x + 49, 381], outline=(150, 150, 150))
    big = icon.resize((300, 300), Image.NEAREST)
    cv.paste(big, (170, 332), big)
    d.rectangle([170, 332, 469, 631], outline=(150, 150, 150))
    d.text((170, 640), "上：图标放大 6 倍，查描边与留白", font=font(18), fill=(60, 60, 60))
    cv.save(out_path, quality=92)
    return out_path


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))
