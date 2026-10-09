"""抠好的图 → 240² 动态表情。

三个地方决定 GIF 能不能用：

**一、GIF 的透明是「1 位」的。**
只有「透明」和「不透明」两种，没有半透明。抠出来的抗锯齿边缘上有一圈半透明像素，
不处理的话 GIF 存不下，要么变成一圈灰边、要么变成实心方块。
做法是**硬阈值**：alpha ≥ 128 算实心，其余算透明。
⚠️ 不要用「拼一块浅灰底板」绕过这件事 —— 那样在微信的深色聊天窗里会露出一个浅灰方块。
角色带粗黑描边，边缘就是描边本身，量化不会糊边。

**二、`optimize=True` 会偷偷换调色板，而透明是调色板里的一个索引。**
所以**必须逐帧读回**确认四角真透明。只查第 1 帧会漏掉
「第一帧透明、后面几帧变成实心方块」这种毛病 —— 这正是最常出现的情况。

**三、两帧之间要「只有该动的地方动」。**
所以这里把三件事量成数字：前景交并比（身体漂没漂）、变化像素比例、
**文案带变化**（GIF 里字闪是最扎眼的缺陷，人眼会盯着字看）。
"""


def hard_alpha(img, alpha_cut=128):
    """把抗锯齿的半透明边缘压成实心 / 透明二选一，GIF 才存得下。"""
    a = img.split()[3].point(lambda v: 255 if v >= alpha_cut else 0)
    out = img.copy()
    out.putalpha(a)
    return out


def to_p_frame(img, alpha_cut=128):
    """RGBA → P（调色板图），并留出最后一个索引用作透明键。"""
    import numpy as np
    from PIL import Image

    rgba = hard_alpha(img.convert("RGBA"), alpha_cut)
    rgb = rgba.convert("RGB")
    n = 255
    p = rgb.quantize(colors=n, method=Image.MEDIANCUT)
    alpha = np.asarray(rgba.split()[3], dtype=np.uint8)
    idx = np.frombuffer(p.tobytes(), dtype=np.uint8).reshape(p.size[1], p.size[0]).copy()
    idx[alpha < alpha_cut] = n                      # 透明像素指向最后一个索引
    pal = p.getpalette()[: n * 3] + [0, 0, 0]
    q = Image.frombytes("P", p.size, idx.tobytes())
    q.putpalette(pal)
    return q


def diff_metrics(a, b, text_band=0.26):
    """量两帧差在哪。三个数字回答三个问题。"""
    import numpy as np

    aa = np.asarray(a).astype(np.float32)
    bb = np.asarray(b).astype(np.float32)
    fa, fb = aa[..., 3] > 8, bb[..., 3] > 8
    union, inter = fa | fb, fa & fb
    iou = float(inter.sum()) / max(1, int(union.sum()))
    d = np.abs(aa[..., :3] - bb[..., :3]).mean(axis=2)
    changed = float((d[union] > 24).mean()) if union.any() else 0.0

    def centroid(m):
        ys, xs = np.nonzero(m)
        return (xs.mean(), ys.mean()) if len(xs) else (0.0, 0.0)

    ca, cb = centroid(fa), centroid(fb)
    shift = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5

    # 文案带：画面下方那一段。GIF 里字闪是最扎眼的缺陷
    h = a.size[1]
    top = int(h * (1 - text_band))
    band = float((d[top:, :] > 24).mean())
    return {"iou": round(iou, 3), "changed_ratio": round(changed, 3),
            "shift_px": round(float(shift), 2), "text_band_change": round(band, 3)}


def gif_check(path, alpha_cut=128):
    """读回 GIF，**逐帧**确认四角真是透明。

    返回 (最差角点的 alpha, transparency 索引, 帧数)。
    """
    from PIL import Image
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    worst = 0
    for i in range(n):
        im.seek(i)
        rgba = im.convert("RGBA")
        w, h = rgba.size
        c = max(rgba.getpixel(p)[3]
                for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
        worst = max(worst, c)
    return worst, im.info.get("transparency"), n


def make_gif(frame_a, frame_b, path, frame_ms=400, alpha_cut=128, loop=True,
             hold_last=True):
    """合成一条动态表情，并当场校验。

    帧序是 a → b → a：最后一帧回到第一帧，循环才接得上，不会「跳一下」。
    `loop=True` 是平台要求 —— 动态表情必须循环播放。
    """
    frames = [frame_a, frame_b]
    if hold_last:
        frames = frames + [frame_a]
    ps = [to_p_frame(f, alpha_cut) for f in frames]
    ps[0].save(path, save_all=True, append_images=ps[1:], duration=frame_ms,
               loop=0 if loop else 1, optimize=True, disposal=2, transparency=255)
    corner, trans, nf = gif_check(path, alpha_cut)
    import os
    return {"gif_bytes": os.path.getsize(path), "gif_frames": nf,
            "corner_alpha": corner, "transparency_index": trans}
