"""色键抠图 + 去绿边。

模型给的是「纯绿色背景」。把绿换成透明，看着简单，两个地方会翻车：

**一、不能按「像素绿不绿」一刀切。**
猫身上完全可能有绿色（眼睛、项圈、怀里的东西）。一刀切会把这些地方也抠成窟窿。
正解是**从画面四角往外做连通域填充**：只有「跟画面边缘连着的绿」才算背景，
被角色围在中间的绿不动。

**二、抠完必须去绿边（despill）。**
模型渲染时绿背景会往角色轮廓上渗一圈。这圈在浅色底上看不出来，
一贴到微信的深色聊天底就变成一圈荧光绿毛边，非常显眼。
做法是把**紧贴背景的那一圈前景像素的绿通道**压到不超过红蓝的较大值。

⚠️ 依赖 Pillow + numpy。ComfyUI 便携版自带的 python_embeded 两个都有，
所以正常路径下读者不需要额外装东西（见 start.bat）。
"""
import os
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_mask_green(img):
    """标出「与画面边缘连通的绿」。返回 bool 数组，True = 背景。"""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = (g > 90) & (g - r > 25) & (g - b > 25)
    h, w = green.shape
    bg = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if green[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if green[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and green[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                q.append((ny, nx))
    return bg


def cutout(src):
    """抠成透明底 RGBA，并做去绿边。`src` 可以是路径或 PIL 图。

    返回 (RGBA 图, 背景占比)。背景占比是用来判断「模型这次给的绿幕干不干净」的：
    正常在 0.4~0.8 之间；低于 0.15 说明模型几乎没给绿底，这张建议重出。
    """
    img = src.convert("RGB") if isinstance(src, Image.Image) else Image.open(src).convert("RGB")
    bg = load_mask_green(img)
    a = np.asarray(img).astype(np.int16)

    # 去绿边：贴着背景的前景像素，把绿压到不超过红蓝的较大值
    fg = ~bg
    near = np.zeros_like(bg)
    near[1:, :] |= bg[:-1, :]
    near[:-1, :] |= bg[1:, :]
    near[:, 1:] |= bg[:, :-1]
    near[:, :-1] |= bg[:, 1:]
    spill = fg & near
    cap = np.maximum(a[..., 0], a[..., 2])
    a[..., 1] = np.where(spill & (a[..., 1] > cap), cap, a[..., 1])
    a = np.clip(a, 0, 255).astype(np.uint8)

    rgba = np.dstack([a, np.where(bg, 0, 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA"), float(bg.mean())


def crop_square(rgba, trim=0.055, size=240, trim_bottom=0.0):
    """统一裁切窗口 → 缩到 size²。

    关键不是「每张各自裁紧」。那样每张的表情都会顶满画面、大小参差，
    八张摆一起像八个不同的人做的。
    正解是**所有图用同一个裁切窗口**，比例才一致。

    ⚠️ **上下不裁一样多**，这是踩过的坑：文案带就在画面下方，
    四周各裁 5.5% 会把字的下半截切掉（「谢谢」只剩上面一半）。
    所以顶部照裁、**底部一刀不裁**，左右居中。
    裁出来的仍然是正方形（边长按高度算），只是取景框整体上移了一点。
    """
    w, h = rgba.size
    side = max(1, int(h * (1 - trim - trim_bottom)))
    top = int(h * trim)
    left = max(0, (w - side) // 2)
    return rgba.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.LANCZOS)


def alpha_bbox(img):
    """前景（alpha > 8）的外接框。"""
    return img.split()[-1].point(lambda v: 255 if v > 8 else 0).getbbox()


def font(sz):
    """找一款系统中文字体。找不到就退到默认字体（对照板上的字会难看，但不影响成品）。"""
    for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\PingFang.ttc",
              "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                pass
    return ImageFont.load_default()


def board(tiles, cols, bg, title, path, tile=240, pad=18, labels=None, quality=92):
    """把几张图排在指定底色上，导出一张对照图。

    表情包必须看**两个底色**：微信的浅色聊天窗和深色聊天窗。
    透明边的问题（灰边、绿光）只在深色底上才看得出来。
    """
    rows = (len(tiles) + cols - 1) // cols
    label_h = 22 if labels else 0
    W = cols * tile + pad * (cols + 1)
    H = rows * (tile + label_h) + pad * (rows + 1) + 46
    cv = Image.new("RGB", (W, H), bg if isinstance(bg, tuple) else tuple(bg))
    d = ImageDraw.Draw(cv)
    dark = sum(cv.getpixel((0, 0))) < 380
    fg = (240, 240, 240) if dark else (60, 60, 60)
    d.text((pad, 14), title, font=font(20), fill=fg)
    for i, t in enumerate(tiles):
        c, r = i % cols, i // cols
        x = pad + c * (tile + pad)
        y = 46 + pad + r * (tile + pad + label_h)
        if t.mode == "RGBA":
            cv.paste(t, (x, y), t)
        else:
            cv.paste(t, (x, y))
        d.rectangle([x, y, x + tile - 1, y + tile - 1], outline=(150, 150, 150))
        if labels:
            d.text((x, y + tile + 4), str(labels[i]), font=font(14), fill=fg)
    cv.save(path, quality=quality)
    return path
