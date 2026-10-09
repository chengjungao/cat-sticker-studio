"""把上面那些模块串成「一键生成」。

流程四步，顺序不能换：

    1. 定妆照   —— 拿读者的猫照片当参考图，出一张 Q 版卡通定妆照
    2. N×2 帧   —— 以定妆照为**唯一**参考图，逐张出每一帧
    3. 后处理   —— 抠图 → 统一窗口裁 240 → 合成 GIF → 封面/图标/横幅
    4. 打包     —— 提交清单 + zip

第 1 步是整套的唯一一致性来源。它错了，后面 16 张全跟着错。
所以留了一个 `pause_after_hero` 开关：花 20 秒看一眼定妆照，
比后面重出 16 张便宜得多。

批量出帧用的是「**一次把全部任务压进队列，再统一轮询**」——
让显卡一直有活干，中间不用管。
"""
import io
import json
import os
import threading
import time

from PIL import Image

from . import assets, pack, prompts, sticker
from .cutout import cutout, crop_square, board
from .comfy import Comfy, ComfyError
from .workflows import build_i2i, build_t2i

DIR_PHOTO = "照片"
DIR_RAW = "原图"
DIR_GIF = "01_表情图"
DIR_COVER = "02_封面"
DIR_ICON = "03_聊天面板图标"
DIR_BANNER = "04_详情页横幅"
DIR_BOARD = "05_对照图"

RECORD = "生成记录.json"


class Canceled(Exception):
    pass


def _safe(name):
    """文件名里不能有的字符，换成下划线。文案会被写进文件名。"""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "表情"


class StickerMaker(object):
    def __init__(self, cfg, workdir, on_event=None):
        self.cfg = cfg
        self.dir = os.path.abspath(workdir)
        self.comfy = Comfy(cfg["comfy_url"])
        self.on_event = on_event or (lambda e: None)
        self._stop = threading.Event()

    # ---------- 取消 ----------

    def cancel(self):
        """真取消：先打断正在跑的那张，再清空后面排队的。"""
        self._stop.set()
        try:
            self.comfy.interrupt()
            self.comfy.clear_queue()
        except ComfyError:
            pass

    def reset_cancel(self):
        self._stop.clear()

    def _check(self):
        if self._stop.is_set():
            raise Canceled()

    def _emit(self, evt):
        try:
            self.on_event(evt)
        except Exception:
            pass

    # ---------- 目录与输入 ----------

    def sub(self, *parts):
        p = os.path.join(self.dir, *parts)
        d = p if not os.path.splitext(p)[1] else os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        return p

    def photos(self):
        d = os.path.join(self.dir, DIR_PHOTO)
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in sorted(os.listdir(d))
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))]

    def record(self):
        p = os.path.join(self.dir, RECORD)
        if os.path.exists(p):
            try:
                with io.open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_record(self, rec):
        with io.open(os.path.join(self.dir, RECORD), "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=2))

    def save_photo(self, content, filename="cat_1.png"):
        """把界面上传的照片落到工程目录。"""
        dst = self.sub(DIR_PHOTO, filename)
        with open(dst, "wb") as f:
            f.write(content)
        return dst

    # ---------- 第 1 步：定妆照 ----------

    def hero_path(self):
        return os.path.join(self.dir, DIR_RAW, "hero.png")

    def has_hero(self):
        return os.path.exists(self.hero_path())

    def make_hero(self, desc="", from_photo=True, seed=None):
        rec = self.record()
        seed = int(seed if seed is not None else rec.get("seed")
                   or self.cfg["generate"]["base_seed"])
        gen = dict(self.cfg["generate"])
        gen["width"] = gen["height"] = self.cfg["sticker"].get("gen_size", 1024)

        prompt = prompts.hero_prompt(desc, from_photo=from_photo)
        self._emit({"type": "stage", "stage": "hero",
                    "msg": "正在出定妆照（整套一致性的来源）…"})
        self._emit({"type": "prompt", "id": "hero", "prompt": prompt})

        if from_photo:
            ph = self.photos()
            if not ph:
                raise ComfyError("还没有照片。请先上传一张猫的照片，"
                                 "或者关掉「用照片」改用文字描述。")
            ref = self.comfy.upload_image(ph[0], "cat_ref.png")
            wf = build_i2i(self.cfg["models"], gen, prompt, seed, "cs_hero",
                           ref, negative=prompts.NEGATIVE)
        else:
            wf = build_t2i(self.cfg["models"], gen, prompt, seed, "cs_hero",
                           negative=prompts.NEGATIVE)

        dst = self.sub(DIR_RAW, "hero.png")
        self._run_one(wf, dst, "hero", "定妆照")
        self._emit({"type": "hero_ready", "path": dst, "seed": seed, "prompt": prompt})
        return dst

    def import_hero(self, content):
        """读者自己有满意的定妆照，可以直接丢进来跳过第 1 步。"""
        dst = self.sub(DIR_RAW, "hero.png")
        with open(dst, "wb") as f:
            f.write(content)
        return dst

    # ---------- 第 2 步：逐帧出图 ----------

    def frame_name(self, i, text, tag):
        return "%02d_%s_%s" % (i, _safe(text), tag)

    def make_frames(self, desc="", from_photo=True, moods=None, seed=None, only=None):
        """出所有帧。`only` 传索引列表时只出那几张。"""
        rec = self.record()
        seed = int(seed if seed is not None else rec.get("seed")
                   or self.cfg["generate"]["base_seed"])
        desc = desc or rec.get("desc", "")
        from_photo = rec.get("from_photo", from_photo) if "from_photo" in rec else from_photo
        moods = moods or rec.get("moods") or prompts.DEFAULT_MOODS

        if not self.has_hero():
            raise ComfyError("还没有定妆照，没法出表情。")

        gen = dict(self.cfg["generate"])
        gen["width"] = gen["height"] = self.cfg["sticker"].get("gen_size", 1024)

        # 定妆照当**唯一**参考图，只上传一次
        self._emit({"type": "stage", "stage": "frames", "msg": "正在上传定妆照…"})
        ref = self.comfy.upload_image(self.hero_path(), "cs_hero_ref.png")

        jobs = []
        total = sum(len(m["frames"]) for m in moods)
        idx_done = 0
        for i, m in enumerate(moods, 1):
            if only and i not in only:
                idx_done += len(m["frames"])
                continue
            for j, state in enumerate(m["frames"]):
                tag = "abcdefgh"[j] if j < 8 else str(j)
                name = self.frame_name(i, m["text"], tag)
                dst = os.path.join(self.dir, DIR_RAW, name + ".png")
                if os.path.exists(dst) and not (only and i in only):
                    idx_done += 1
                    continue
                pr = prompts.frame_prompt(m["text"], state, desc, from_photo=from_photo)
                # ⭐ 同一张的两帧用**同一个种子**：模型看到的是同一张底片，
                # 只改「动作与神态」那一处，其余照旧。这是让 GIF 不乱跳的关键。
                fseed = seed + i * 10
                wf = build_i2i(self.cfg["models"], gen, pr, fseed, "cs_" + name,
                               ref, negative=prompts.NEGATIVE)
                self._emit({"type": "prompt", "id": name, "prompt": pr})
                pid = self.comfy.submit(wf)
                jobs.append({"id": name, "pid": pid, "dst": dst, "index": i,
                             "text": m["text"], "tag": tag, "state": state,
                             "seed": fseed})
                self._emit({"type": "submit", "id": name,
                            "queued": idx_done + len(jobs), "total": total})
                self._check()
            idx_done += len(m["frames"])

        if not jobs:
            self._emit({"type": "stage_done", "stage": "frames", "elapsed": 0,
                        "results": [], "msg": "没有需要新出的帧"})
            return []

        self._emit({"type": "stage", "stage": "frames",
                    "msg": "共 %d 张已入队，开始等待…" % len(jobs)})
        t0 = time.time()
        results = self._wait_all(jobs, t0, total)
        ok = sum(1 for r in results if r["ok"])
        self._emit({"type": "stage_done", "stage": "frames",
                    "elapsed": round(time.time() - t0, 1),
                    "results": results, "msg": "%d/%d 张出图成功" % (ok, len(results))})
        return results

    # ---------- 第 3 步：后处理 ----------

    def postprocess(self, moods=None, desc=""):
        rec = self.record()
        moods = moods or rec.get("moods") or prompts.DEFAULT_MOODS
        st = self.cfg["sticker"]
        size, frame_ms = int(st["size"]), int(st["frame_ms"])
        trim, cut = float(st["edge_trim"]), int(st["alpha_cut"])
        trim_b = float(st.get("edge_trim_bottom", 0.0))

        self._emit({"type": "stage", "stage": "post", "msg": "正在抠图、裁切、合成 GIF…"})
        # ⭐ 抠图前先算一次「统一裁切窗口」的依据：用定妆照判底色是否干净
        hero_rgba, hero_bg = cutout(self.hero_path())
        if hero_bg < 0.15:
            self._emit({"type": "log",
                        "msg": "⚠️ 定妆照的绿底只占 %.0f%%，模型可能没给干净的绿幕，"
                               "成品边上可能带残留。" % (hero_bg * 100)})

        items, tiles, labels = [], [], []
        for i, m in enumerate(moods, 1):
            text = m["text"]
            frames = []
            for j in range(len(m["frames"])):
                tag = "abcdefgh"[j] if j < 8 else str(j)
                p = os.path.join(self.dir, DIR_RAW,
                                 self.frame_name(i, text, tag) + ".png")
                if os.path.exists(p):
                    rgba, _bg = cutout(p)
                    frames.append(crop_square(rgba, trim, size, trim_b))
            if len(frames) < 2:
                self._emit({"type": "log",
                            "msg": "⚠️ 「%s」只有 %d 帧，跳过。" % (text, len(frames))})
                continue

            m_metric = sticker.diff_metrics(frames[0], frames[1],
                                            float(st["text_band"]))
            gif_name = "%02d_%s_%d.gif" % (i, _safe(text), size)
            gif_path = self.sub(DIR_GIF, gif_name)
            info = sticker.make_gif(frames[0], frames[1], gif_path,
                                    frame_ms=frame_ms, alpha_cut=cut)
            m_metric.update(info)
            m_metric.update({"text": text, "gif_name": gif_name,
                             "frame1": m["frames"][0][:28],
                             "frame2": m["frames"][1][:28]})
            items.append(m_metric)
            tiles.append(frames[0])
            labels.append(text)
            # 帧图也留一份，方便读者自己再调
            for j, f in enumerate(frames):
                f.save(self.sub(DIR_GIF, "_帧图",
                                "%02d_%s_帧%d_%d.png" % (i, _safe(text), j + 1, size)))
            self._emit({"type": "item_done", "item": {
                "id": "sticker_%d" % i, "ok": True,
                "msg": "「%s」交并比 %.3f，文案带变化 %.1f%%，%.1fKiB"
                       % (text, m_metric["iou"], m_metric["text_band_change"] * 100,
                          m_metric["gif_bytes"] / 1024.0)}})

        # 两版对照：微信浅色 / 深色聊天窗。透明边的问题只在深底上看得出来
        if tiles:
            board(tiles, 4, (245, 245, 245),
                  "微信浅色聊天底 · %d×%d 实际尺寸" % (size, size),
                  self.sub(DIR_BOARD, "_对照_浅底.jpg"), tile=size, labels=labels)
            board(tiles, 4, (38, 38, 38),
                  "微信深色聊天底 · %d×%d 实际尺寸" % (size, size),
                  self.sub(DIR_BOARD, "_对照_深底.jpg"), tile=size, labels=labels)

        # ---- 封面 / 图标 ----
        self._emit({"type": "stage", "stage": "assets", "msg": "正在做封面与图标…"})
        cover_rep = assets.make_cover(hero_rgba, self.sub(DIR_COVER, "封面_240.png"),
                                      size=size, trim=trim, trim_bottom=trim_b)
        icon_rep, hw, box, pads = assets.make_icon(hero_rgba,
                                                  self.sub(DIR_ICON, "图标_50.png"), size=50)
        assets.preview_board(self.sub(DIR_COVER, "封面_240.png"),
                             self.sub(DIR_ICON, "图标_50.png"),
                             self.sub(DIR_BOARD, "_提交件对照.jpg"))

        # ---- 横幅（要另出一张横版） ----
        banner_rep = None
        try:
            banner_rep = self.make_banner(desc)
        except (ComfyError, Canceled) as e:
            self._emit({"type": "log", "msg": "横幅没做出来：%s（其余素材不受影响）" % e})

        return {"items": items, "cover": cover_rep, "icon": icon_rep,
                "hero_bg_ratio": round(hero_bg, 3), "head_width_px": hw,
                "icon_pads_px": pads, "banner": banner_rep}

    def make_banner(self, desc="", seed=None):
        """详情页横幅。横版另出一张图，再合成到 750×400。"""
        a = self.cfg["assets"]
        W, H = int(a["banner_width"]), int(a["banner_height"])
        rec = self.record()
        seed = int(seed if seed is not None else (rec.get("seed") or 0)
                   + 1000 or self.cfg["generate"]["base_seed"] + 1000)
        raw = os.path.join(self.dir, DIR_RAW, "banner_raw.png")
        if not os.path.exists(raw):
            gen = dict(self.cfg["generate"])
            gen["width"], gen["height"] = 1344, 768      # 项目的横版画幅
            self._emit({"type": "stage", "stage": "banner",
                        "msg": "正在出详情页横幅（横版）…"})
            ref = self.comfy.upload_image(self.hero_path(), "cs_hero_banner.png")
            wf = build_i2i(self.cfg["models"], gen, prompts.banner_prompt(desc), seed,
                           "cs_banner", ref, negative=prompts.BANNER_NEGATIVE)
            self._run_one(wf, raw, "banner", "横幅")
        rep = assets.make_banner(raw, self.sub(DIR_BANNER, "横幅_%dx%d.png" % (W, H)),
                                 width=W, height=H, feather=int(a["banner_feather"]),
                                 trim_y=float(a.get("banner_trim_y", 0.06)))
        # 横幅也要留一张对照，确认它确实不是白底、没有文字
        board([Image.open(rep["files"][0]).convert("RGB")], 1, (245, 245, 245),
              "详情页横幅 %d×%d（避免任何文字 / 避免白底 / 避免透明底）" % (W, H),
              self.sub(DIR_BOARD, "_横幅对照.jpg"), tile=W, pad=14)
        return rep

    # ---------- 第 4 步：清单与打包 ----------

    def finish(self, result, desc="", title="我家的猫"):
        rec = self.record()
        cfg = self.cfg
        items = result["items"]
        ious = [m["iou"] for m in items] or [0]
        gif_kb = "、".join("%.1fK" % (m["gif_bytes"] / 1024.0) for m in items)
        ctx = {
            "n": len(items), "size": int(cfg["sticker"]["size"]),
            "frames": items[0]["gif_frames"] if items else 0,
            "frame_ms": int(cfg["sticker"]["frame_ms"]), "gif_kb": gif_kb,
            "gif_ok": "✅ %d 张，类型统一为动态" % len(items),
            "cover_name": os.path.basename(result["cover"]["file"]),
            "cover_kb": result["cover"]["kb"],
            "cover_ok": "✅" if result["cover"]["pass"] else "❌",
            "icon_name": os.path.basename(result["icon"]["file"]),
            "icon_kb": result["icon"]["kb"],
            "icon_ok": "✅" if result["icon"]["pass"] else "❌",
            "banner_kb": (pack._fmt_kb(result["banner"]["kb"])
                          if result.get("banner") else "未生成"),
            "banner_ok": ("✅" if result["banner"]["pass"] else "❌")
                         if result.get("banner") else "—",
            "iou_range": ("%.3f ~ %.3f" % (min(ious), max(ious))) if items else "—",
            "models": "%s / %s / %s" % (cfg["models"]["unet"], cfg["models"]["clip"],
                                        cfg["models"]["vae"]),
            "steps": cfg["generate"]["steps"], "cfg": cfg["generate"]["cfg"],
            "sampler": cfg["generate"]["sampler"], "scheduler": cfg["generate"]["scheduler"],
            "resolution": cfg["generate"]["resolution"],
            "base_seed": rec.get("seed", cfg["generate"]["base_seed"]),
            "photo_note": ("用读者上传的照片（%d 张）"
                           % len(self.photos())) if self.photos() else "未用照片，纯文字描述",
            "desc": desc or rec.get("desc") or "（未填）",
            "when": time.strftime("%Y-%m-%d %H:%M"),
            "moods": [{"text": m["text"], "gif_name": m["gif_name"],
                       "frame1": m["frame1"], "frame2": m["frame2"],
                       "iou": m["iou"], "text_band_change": m["text_band_change"]}
                      for m in items],
        }
        pack.build_manifest(os.path.join(self.dir, pack.MANIFEST_NAME), ctx)
        z = pack.zip_all(self.dir, os.path.join(self.dir, "%s表情包.zip" % title))
        return z

    # ---------- 一键到底 ----------

    def generate(self, desc="", from_photo=True, moods=None, seed=None,
                 pause_after_hero=False, skip_hero=False):
        """完整流程。`pause_after_hero` 时出完定妆照就停下等确认。"""
        rec = self.record()
        rec.update({"desc": desc, "from_photo": bool(from_photo),
                    "moods": moods or rec.get("moods") or prompts.DEFAULT_MOODS,
                    "seed": int(seed if seed is not None else
                                rec.get("seed") or self.cfg["generate"]["base_seed"])})
        self.save_record(rec)
        t_all = time.time()

        if not skip_hero or not self.has_hero():
            self.make_hero(desc, from_photo=from_photo, seed=rec["seed"])
            if pause_after_hero:
                self._emit({"type": "stage_done", "stage": "hero",
                            "elapsed": round(time.time() - t_all, 1), "results": [],
                            "msg": "定妆照好了，看一眼再决定要不要继续"})
                return {"paused": True, "hero": self.hero_path()}

        self.make_frames(desc, from_photo=from_photo, moods=rec["moods"], seed=rec["seed"])
        result = self.postprocess(moods=rec["moods"], desc=desc)
        z = self.finish(result, desc=desc)
        result["zip"] = z
        result["elapsed"] = round(time.time() - t_all, 1)
        self._emit({"type": "stage_done", "stage": "all",
                    "elapsed": result["elapsed"], "results": [],
                    "msg": "全部完成，用时 %.1f 秒" % result["elapsed"]})
        return result

    # ---------- 内部 ----------

    def _run_one(self, wf, dst, key, label):
        t0 = time.time()
        pid = self.comfy.submit(wf)
        self._emit({"type": "submit", "id": key, "queued": 1, "total": 1})

        def tick(elapsed, running, pending):
            self._emit({"type": "tick", "id": key, "elapsed": round(elapsed, 1),
                        "running": running, "pending": pending})

        ok, images, msg = self.comfy.wait(pid, on_tick=tick,
                                          should_stop=self._stop.is_set)
        if not ok:
            self._emit({"type": "failed", "id": key, "msg": msg})
            raise ComfyError(msg)
        if not images:
            raise ComfyError("出图成功但没拿到图片，检查工作流里有没有保存节点")
        im = images[0]
        b = self.comfy.fetch_image(im["filename"], im["subfolder"], im["type"])
        with open(dst, "wb") as f:
            f.write(b)
        self._emit({"type": "done", "id": key, "elapsed": round(time.time() - t0, 1),
                    "label": label})
        return dst

    def _wait_all(self, jobs, t0, total):
        pending = {j["pid"]: j for j in jobs}
        results = []
        last_emit = 0.0
        while pending:
            self._check()
            time.sleep(1.5)
            for pid, j in list(pending.items()):
                try:
                    entry = self.comfy.history(pid)
                except ComfyError:
                    continue
                if entry is None:
                    continue
                st = entry.get("status") or {}
                ok = st.get("status_str") == "success"
                images = self.comfy._images_of(entry)
                item = {"id": j["id"], "index": j["index"], "tag": j["tag"],
                        "ok": bool(ok and images)}
                if item["ok"]:
                    try:
                        im = images[0]
                        b = self.comfy.fetch_image(im["filename"], im["subfolder"],
                                                   im["type"])
                        with open(j["dst"], "wb") as f:
                            f.write(b)
                    except Exception as e:
                        item["ok"] = False
                        item["msg"] = str(e)[:160]
                else:
                    msgs = [str(m)[:160] for m in (st.get("messages") or [])[:2]]
                    item["msg"] = "出图失败 " + " | ".join(msgs)
                results.append(item)
                self._emit({"type": "item_done", "item": item,
                            "left": len(pending) - 1, "total": total})
                del pending[pid]

            now = time.time()
            if now - last_emit > 2:
                last_emit = now
                running = pend = 0
                try:
                    q = self.comfy.queue()
                    running = len(q.get("queue_running") or [])
                    pend = len(q.get("queue_pending") or [])
                except ComfyError:
                    pass
                self._emit({"type": "tick", "elapsed": round(now - t0, 1),
                            "running": running, "pending": pend, "left": len(pending)})
        return results

    def remake(self, index, desc="", moods=None, seed=None):
        """重出第 index 个表情（两张帧一起重出），再顺手重做后处理。"""
        rec = self.record()
        moods = moods or rec.get("moods") or prompts.DEFAULT_MOODS
        # 先把旧的删掉，make_frames 才会重出
        for j in range(len(moods[index - 1]["frames"])):
            tag = "abcdefgh"[j]
            p = os.path.join(self.dir, DIR_RAW,
                             self.frame_name(index, moods[index - 1]["text"], tag) + ".png")
            if os.path.exists(p):
                os.remove(p)
        self.make_frames(desc or rec.get("desc", ""),
                         from_photo=rec.get("from_photo", True),
                         moods=moods, seed=seed or rec.get("seed"), only=[index])
        return self.postprocess(moods=moods, desc=desc)
