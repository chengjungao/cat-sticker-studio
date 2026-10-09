"""本地 Web 服务：界面 + 接口。

为什么用标准库的 http.server 而不是 Flask：读者解压完直接双击就能用，
不用 pip install，也不会跟 ComfyUI 那边的 Python 环境打架。

出图是长任务（一套 16 帧要两三分钟），所以拆成两半：
- `POST /api/...` 只负责**把任务丢进后台线程**，立刻返回；
- 前端每 1.5 秒拉一次 `GET /api/progress` 拿进度。

长连接（SSE / WebSocket）在「几秒一次」的节奏下没有收益，轮询更简单也更不容易出问题。
"""
import base64
import json
import mimetypes
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config as cfgmod, pack, prompts
from .comfy import ComfyError
from .pipeline import Canceled, StickerMaker

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

MAX_BODY = 64 * 1024 * 1024          # 照片走 base64，留宽一点

# 允许从工程目录里取图的子目录（白名单，防目录穿越）
VIEW_DIRS = ("照片", "原图", "01_表情图", "01_表情图/_帧图", "02_封面",
             "03_聊天面板图标", "04_详情页横幅", "05_对照图")


class State(object):
    """界面要看的运行状态。所有写入都在锁里，避免两个请求撞上。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.running = False
        self.stage = ""
        self.msg = ""
        self.started = 0.0
        self.elapsed = 0.0
        self.queue = {"running": 0, "pending": 0}
        self.total = 0
        self.done = 0
        self.queued = 0
        self.log = []
        self.hero = None
        self.result = None
        self.paused = False
        self.prompts = {}

    def add_log(self, text):
        with self.lock:
            self.log.append("[%s] %s" % (time.strftime("%H:%M:%S"), text))
            self.log = self.log[-150:]

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running, "stage": self.stage, "msg": self.msg,
                "elapsed": round(self.elapsed, 1), "queue": dict(self.queue),
                "total": self.total, "done": self.done, "queued": self.queued,
                "log": list(self.log),
                "hero": self.hero, "result": self.result, "paused": self.paused,
                "prompts": dict(self.prompts),
            }


class App(object):
    def __init__(self, cfg, workdir):
        self.cfg = cfg
        self.dir = os.path.abspath(workdir)
        self.state = State()
        self.maker = None
        self.thread = None
        self._lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)

    # ---------- 任务 ----------

    def _ensure(self):
        if self.maker is None:
            self.maker = StickerMaker(self.cfg, self.dir, on_event=self._on_event)
        else:
            self.maker.cfg = self.cfg
        return self.maker

    def _on_event(self, evt):
        st = self.state
        t = evt.get("type")
        with st.lock:
            if t == "stage":
                st.stage = evt.get("stage", "")
                st.msg = evt.get("msg", "")
            elif t == "submit":
                # ⚠️ 这里只更新「已入队」，不能碰 done。
                # 16 张是一次性全压进队列的，入队那一刻 done 就写满，
                # 进度条会在第 1 张时就假满。
                st.queued = evt.get("queued", st.queued)
                st.total = evt.get("total", st.total)
            elif t == "item_done":
                item = evt.get("item") or {}
                if item.get("ok"):
                    st.done = min(max(st.total, 1), st.done + 1)
                else:
                    st.msg = item.get("msg", "有一张出错了")
            elif t == "hero_ready":
                st.hero = {"path": evt.get("path"), "seed": evt.get("seed")}
            elif t == "prompt":
                st.prompts[evt.get("id", "?")] = evt.get("prompt", "")
            elif t == "log":
                st.log.append("[%s] %s" % (time.strftime("%H:%M:%S"), evt.get("msg", "")))
                st.log = st.log[-150:]
            elif t == "tick":
                st.elapsed = evt.get("elapsed", st.elapsed)
                st.queue = {"running": evt.get("running", 0),
                            "pending": evt.get("pending", 0)}
                if evt.get("total"):
                    st.total = evt["total"]
            elif t == "stage_done":
                if evt.get("stage") == "hero":
                    st.paused = True
                st.result = {"stage": evt.get("stage"), "elapsed": evt.get("elapsed"),
                             "msg": evt.get("msg", ""), "results": evt.get("results", [])}
                if evt.get("msg"):
                    st.msg = evt["msg"]

    def start_job(self, fn, name):
        with self._lock:
            if self.state.running:
                return False, "已经有一个任务在跑，等它结束或先点取消。"
            self.state.reset()
            self.state.running = True
            self.state.stage = name
            self.state.started = time.time()
            self._ensure().reset_cancel()

        def runner():
            try:
                fn(self._ensure())
                self.state.add_log("任务完成：" + name)
            except Canceled:
                self.state.add_log("任务已取消：" + name)
            except (ComfyError, ValueError, OSError) as e:
                self.state.add_log("出错：" + str(e))
                with self.state.lock:
                    self.state.msg = str(e)
            except Exception as e:                       # 兜底，别让线程静默死掉
                import traceback
                self.state.add_log("意外错误：" + str(e))
                traceback.print_exc()
                with self.state.lock:
                    self.state.msg = "意外错误：%s" % e
            finally:
                with self.state.lock:
                    self.state.running = False
                    self.state.stage = ""
                    self.state.elapsed = round(time.time() - self.state.started, 1)

        self.thread = threading.Thread(target=runner, daemon=True)
        self.thread.start()
        return True, "已开始：" + name

    def cancel(self):
        if self.maker:
            self.maker.cancel()
        return True, "已请求取消（会先打断正在跑的那张，再清空排队的）"

    # ---------- 工程状态 ----------

    def photos(self):
        d = os.path.join(self.dir, "照片")
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d)
                      if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")))

    def gifs(self):
        d = os.path.join(self.dir, "01_表情图")
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".gif"))

    def state_dict(self):
        rec = self._ensure().record()
        zips = [f for f in os.listdir(self.dir) if f.lower().endswith(".zip")] \
            if os.path.isdir(self.dir) else []
        return {
            "ok": True, "workdir": self.dir,
            "config": self.cfg,
            "desc": rec.get("desc", ""),
            "from_photo": rec.get("from_photo", True),
            "seed": rec.get("seed", self.cfg["generate"]["base_seed"]),
            "moods": [m["text"] for m in (rec.get("moods") or prompts.DEFAULT_MOODS)],
            "photos": self.photos(),
            "gifs": self.gifs(),
            "has_hero": self._ensure().has_hero(),
            "has_manifest": os.path.exists(os.path.join(self.dir, pack.MANIFEST_NAME)),
            "zips": zips,
            "mood_defaults": [m["text"] for m in prompts.DEFAULT_MOODS],
            "sticker": self.cfg["sticker"],
            "assets_spec": self.cfg["assets"],
        }


def _find_free_port(preferred=8770):
    for port in range(preferred, preferred + 30):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    return preferred


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "CatStickerStudio"

    def log_message(self, fmt, *args):        # 静音访问日志，控制台只留我们的输出
        pass

    # ---------- 基础 ----------

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _file(self, path, ctype=None, download_name=None):
        if not os.path.exists(path) or not os.path.isfile(path):
            return self._send(404, {"error": "找不到文件：%s" % os.path.basename(path)})
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            from urllib.parse import quote
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''%s" % quote(download_name))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("请求太大（照片建议先压到 5MB 以内）")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    def _safe_path(self, rel):
        """把 URL 里的相对路径变成工程目录下的真实路径，并挡住目录穿越。"""
        rel = unquote(rel).replace("\\", "/").lstrip("/")
        root = os.path.abspath(self.app.dir)
        full = os.path.abspath(os.path.join(root, rel))
        if not full.startswith(root + os.sep):
            return None
        head = os.path.relpath(full, root).replace("\\", "/")
        if not any(head == d or head.startswith(d + "/") for d in VIEW_DIRS):
            return None
        return full

    # ---------- 路由 ----------

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        app = self.app
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(WEB_DIR, "index.html"))
            if p.startswith("/static/"):
                name = os.path.basename(unquote(p[len("/static/"):]))
                return self._file(os.path.join(WEB_DIR, name))
            if p.startswith("/img/"):
                full = self._safe_path(p[len("/img/"):])
                if not full:
                    return self._send(403, {"error": "不允许的路径"})
                return self._file(full)
            if p == "/download":
                qs = parse_qs(u.query)
                name = os.path.basename((qs.get("name") or [""])[0])
                if not name:
                    return self._send(400, {"error": "缺少 name"})
                return self._file(os.path.join(app.dir, name), download_name=name)
            if p == "/api/state":
                return self._send(200, app.state_dict())
            if p == "/api/progress":
                return self._send(200, app.state.snapshot())
            if p == "/api/health":
                return self._send(200, app._ensure().comfy.health())
            if p == "/api/models":
                try:
                    return self._send(200, {"ok": True,
                                            **app._ensure().comfy.list_model_choices()})
                except ComfyError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            return self._send(404, {"error": "没有这个接口：%s" % p})
        except ComfyError as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_POST(self):
        p = urlparse(self.path).path
        app = self.app
        try:
            body = self._body()
            routes = {
                "/api/photo": self._photo,
                "/api/photo/delete": self._photo_delete,
                "/api/photo/import": self._import_hero,
                "/api/config": self._config,
                "/api/moods": self._moods,
                "/api/run": self._run,
                "/api/hero": self._hero,
                "/api/frames": self._frames,
                "/api/post": self._post,
                "/api/remake": self._remake,
                "/api/pack": self._pack,
                "/api/open": self._open,
            }
            if p == "/api/cancel":
                ok, msg = app.cancel()
                return self._send(200, {"ok": ok, "msg": msg})
            fn = routes.get(p)
            if not fn:
                return self._send(404, {"error": "没有这个接口：%s" % p})
            return fn(body)
        except (ComfyError, ValueError, OSError) as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    # ---------- 动作 ----------

    @staticmethod
    def _decode_image(raw):
        if not raw:
            raise ValueError("没有拿到图片数据")
        if "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            content = base64.b64decode(raw)
        except Exception:
            raise ValueError("图片数据解析失败")
        if len(content) < 1000:
            raise ValueError("这张图好像没传全，换一张试试")
        return content

    def _photo(self, body):
        content = self._decode_image(body.get("data"))
        name = os.path.basename(body.get("name") or "cat.png")
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            name += ".png"
        # 只留一张：换照片就是重开一局，别让旧照片在后面被误用
        d = os.path.join(self.app.dir, "照片")
        if os.path.isdir(d):
            for f in os.listdir(d):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        dst = self.app._ensure().save_photo(content, name)
        return self._send(200, {"ok": True, "msg": "照片已保存：%s" % os.path.basename(dst),
                                "photos": self.app.photos()})

    def _photo_delete(self, _body):
        d = os.path.join(self.app.dir, "照片")
        n = 0
        if os.path.isdir(d):
            for f in os.listdir(d):
                try:
                    os.remove(os.path.join(d, f))
                    n += 1
                except OSError:
                    pass
        return self._send(200, {"ok": True, "msg": "已清掉 %d 张照片" % n,
                                "photos": []})

    def _import_hero(self, body):
        content = self._decode_image(body.get("data"))
        dst = self.app._ensure().import_hero(content)
        return self._send(200, {"ok": True, "msg": "定妆照已导入",
                                "path": dst})

    def _config(self, body):
        cfg = body.get("config")
        if not isinstance(cfg, dict):
            raise ValueError("config 字段不对")
        self.app.cfg = cfgmod.save_and_reload(cfg)
        if self.app.maker:
            self.app.maker.cfg = self.app.cfg
        return self._send(200, {"ok": True, "msg": "设置已保存", "config": self.app.cfg})

    def _moods(self, body):
        texts = body.get("texts") or []
        if not isinstance(texts, list):
            raise ValueError("texts 要是数组")
        moods = prompts.moods_from_texts([str(t) for t in texts])
        maker = self.app._ensure()
        rec = maker.record()
        rec["moods"] = moods
        maker.save_record(rec)
        return self._send(200, {"ok": True, "msg": "表情词表已保存",
                                "moods": [m["text"] for m in moods]})

    def _run(self, body):
        maker = self.app._ensure()
        rec = maker.record()
        kwargs = dict(
            desc=body.get("desc", rec.get("desc", "")),
            from_photo=bool(body.get("from_photo", rec.get("from_photo", True))),
            seed=body.get("seed") or rec.get("seed"),
            pause_after_hero=bool(body.get("pause_after_hero", False)),
            skip_hero=bool(body.get("skip_hero", False)),
        )
        if body.get("texts"):
            kwargs["moods"] = prompts.moods_from_texts([str(t) for t in body["texts"]])
        ok, msg = self.app.start_job(lambda m: m.generate(**kwargs),
                                     "一键生成整套表情")
        return self._send(200, {"ok": ok, "msg": msg})

    def _hero(self, body):
        maker = self.app._ensure()
        rec = maker.record()
        kwargs = dict(desc=body.get("desc", rec.get("desc", "")),
                      from_photo=bool(body.get("from_photo", rec.get("from_photo", True))),
                      seed=body.get("seed") or rec.get("seed"))
        ok, msg = self.app.start_job(lambda m: m.make_hero(**kwargs), "只出定妆照")
        return self._send(200, {"ok": ok, "msg": msg})

    def _frames(self, body):
        maker = self.app._ensure()
        rec = maker.record()
        kwargs = dict(desc=body.get("desc", rec.get("desc", "")),
                      from_photo=bool(body.get("from_photo", rec.get("from_photo", True))),
                      seed=body.get("seed") or rec.get("seed"))
        ok, msg = self.app.start_job(lambda m: m.make_frames(**kwargs), "批量出帧")
        return self._send(200, {"ok": ok, "msg": msg})

    def _post(self, _body):
        ok, msg = self.app.start_job(lambda m: m.postprocess(), "后处理与提交件")
        return self._send(200, {"ok": ok, "msg": msg})

    def _remake(self, body):
        try:
            index = int(body.get("index") or 0)
        except Exception:
            raise ValueError("index 要是数字")
        if not 1 <= index <= 24:
            raise ValueError("index 超出范围")
        ok, msg = self.app.start_job(lambda m: m.remake(index), "重出第 %d 个表情" % index)
        return self._send(200, {"ok": ok, "msg": msg})

    def _pack(self, _body):
        maker = self.app._ensure()
        result = maker.postprocess()
        z = maker.finish(result)
        return self._send(200, {"ok": True, "msg": "已打包：%s" % os.path.basename(z["zip"]),
                                "zip": z})

    def _open(self, body):
        what = body.get("what") or "folder"
        if what == "zip":
            zips = [f for f in os.listdir(self.app.dir) if f.lower().endswith(".zip")]
            path = os.path.join(self.app.dir, sorted(zips)[-1]) if zips else None
        elif what == "manifest":
            path = os.path.join(self.app.dir, pack.MANIFEST_NAME)
        elif what == "gif":
            path = os.path.join(self.app.dir, "01_表情图")
        else:
            path = self.app.dir
        if not path or not os.path.exists(path):
            return self._send(200, {"ok": False, "error": "还没生成，先点一下对应的按钮"})
        try:
            if os.name == "nt":
                os.startfile(path)                      # noqa: S606
            else:
                import subprocess
                import sys
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
            return self._send(200, {"ok": True, "msg": "已打开"})
        except Exception as e:
            return self._send(200, {"ok": False, "error": "打不开：%s" % e})


def run(cfg, workdir, port=None, open_browser=True):
    Handler.app = App(cfg, workdir)
    port = port or _find_free_port(8770)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("")
    print("  表情包工坊已启动：%s" % url)
    print("  工程目录：%s" % os.path.abspath(workdir))
    print("  关掉这个窗口就停止。")
    print("")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()
    return port
