"""打包：生成提交清单 + 压成一个 zip。

清单里的每个数字都是**这次真跑出来的**，不是抄模板 ——
读者上传前照着对一遍，就知道哪一件有问题。
"""
import io
import os
import zipfile

MANIFEST_NAME = "提交清单.md"

TEMPLATE = u"""# 提交清单 · 微信表情专辑（一套 {n} 张{kind}）

> 由表情包工坊生成。下面每个数字都是本次实际产出，可直接对照平台后台的规格说明核。
> 生成时间：{when}

## 一、四类素材

| 平台要求 | 规格 | 本套文件 | 实测 | 判定 |
|---|---|---|---|---|
| 表情图 8~24 张 | 动态 GIF / 240×240 / ≤500KB | `01_表情图/` 下 {n} 个 GIF | {size}×{size}，{frames} 帧，{frame_ms}ms/帧，永久循环，{gif_kb} | {gif_ok} |
| 表情封面图 1 张 | **PNG 须透明背景** / 240×240 / ≤500KB | `02_封面/{cover_name}` | {cover_kb} KiB，四角透明 | {cover_ok} |
| 聊天面板图标 1 张 | **PNG 须透明背景** / 50×50 / ≤100KB | `03_聊天面板图标/{icon_name}` | {icon_kb} KiB，四角透明 | {icon_ok} |
| 详情页横幅 1 张 | PNG 或 JPG / 750×400 / ≤500KB / **避免任何文字、避免白底、避免透明底** | `04_详情页横幅/` | {banner_kb}，彩色底（无文字、不透明） | {banner_ok} |

## 二、另外三条硬要求，逐条对

| 要求 | 本套怎么满足 |
|---|---|
| 同一套专辑的表情**须统一为动态或静态** | 全部为动态（GIF） |
| 各表情图**风格须统一** | 同一张定妆照当唯一参考图，风格段逐字相同，每张只换动作与文案 |
| 各表情图应有**足够的差异** | {n} 个动作互不相同 |
| 动态表情**须循环播放，节奏流畅不卡顿** | 永久循环（loop=0），{frame_ms}ms/帧，a→b→a 让首尾接得上 |
| **若仅为满足动态要求而无意义地缩放、平移、晃动、闪烁，不能通过审核** | 两帧是内容真的变了：同 seed、同参考图，只改一处状态（见下表）。两帧前景交并比 {iou_range} |
| **横幅避免任何文字、避免白底、避免透明底** | 无文字；彩色底；不透明 |

## 三、{n} 张的文案与动作

| # | 文件 | 文案 | 第 1 帧 | 第 2 帧 | 两帧交并比 | 文案带变化 |
|---|---|---|---|---|---|---|
{rows}

## 四、提交前后要知道的三件事（平台口径）

1. **审核 5 个工作日**反馈。
2. **上架后每自然月只能改 1 次**，改完重新排队。提交前把尺寸、透明底、文案自查一遍。
3. 个人账号绑定微信号即可收款，赞赏收入直接进绑定微信号的零钱。

## 五、本套是怎么来的

- 模型：{models}
- 采样：{steps} 步 / cfg {cfg} / {sampler} / {scheduler}，分辨率 {resolution}
- 种子：基础 {base_seed}（定妆照与每张表情的种子都在 `生成记录.json` 里）
- 角色参考：{photo_note}
- 角色描述：{desc}
- 提示词：`生成记录.json` 里存了每一张的完整提示词，想复跑照抄即可

## 六、还想更好看的话

- **帧数**：现在是 2 帧（＋回位帧共 3 帧）。体积余量很大，想更顺可以扩到 4~6 帧。
- **文案**：界面上可以改。⚠️ 单行 **2~4 个汉字**是模型能画对的范围，再多就开始缺笔画。
- **不满意某一张**：在界面上单独重出那一张就行，不用整套重跑。
"""


def _fmt_kb(items):
    return "、".join("%s %.1fKiB" % (k, v) for k, v in items.items())


def build_manifest(path, ctx):
    """写清单。`ctx` 里的字段由 pipeline 填。"""
    rows = []
    for i, m in enumerate(ctx["moods"], 1):
        rows.append("| %d | `%s` | %s | %s | %s | %.3f | %.1f%% |"
                    % (i, m["gif_name"], m["text"], m["frame1"], m["frame2"],
                       m["iou"], m["text_band_change"] * 100))
    c = dict(ctx)
    c["rows"] = "\n".join(rows)
    c.setdefault("kind", "动态")
    text = TEMPLATE.format(**c)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def zip_all(root, zip_path, manifest_name=MANIFEST_NAME):
    """把成品目录压成一个 zip。排除中间产物（原图、生成记录之外的调试件）。"""
    include_dirs = ("01_表情图", "02_封面", "03_聊天面板图标", "04_详情页横幅", "05_对照图")
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        mf = os.path.join(root, manifest_name)
        if os.path.exists(mf):
            z.write(mf, manifest_name)
            n += 1
        for d in include_dirs:
            full = os.path.join(root, d)
            if not os.path.isdir(full):
                continue
            for fn in sorted(os.listdir(full)):
                p = os.path.join(full, fn)
                if os.path.isfile(p):
                    z.write(p, os.path.join(d, fn))
                    n += 1
    return {"zip": zip_path, "files": n,
            "mb": round(os.path.getsize(zip_path) / 1048576.0, 2)}
