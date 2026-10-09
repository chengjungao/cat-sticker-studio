"""表情包工坊 —— 启动入口。

    python app.py                启动界面（工程目录默认 ./我的表情包）
    python app.py --work 我家的猫  指定工程目录
    python app.py --port 9000    指定端口
    python app.py --check        不开界面，只做一次环境自检
    python app.py --open         启动后自动打开工程目录

ComfyUI 要另外装好并在运行。除此之外只需要 Pillow + numpy ——
ComfyUI 的便携版里自带的 python_embeded 两个都有，所以正常路径下**零安装**。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from studio import config as cfgmod            # noqa: E402
from studio.comfy import Comfy, ComfyError     # noqa: E402


def _setup_console():
    """Windows 控制台默认不是 UTF-8，中文会变乱码。"""
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _check_pillow():
    """返回缺的库名列表。这两个是刻意「导入了不用」的探测导入。"""
    miss = []
    try:
        import PIL                       # noqa: F401
    except ImportError:
        miss.append("Pillow")
    try:
        import numpy                     # noqa: F401
    except ImportError:
        miss.append("numpy")
    return miss


def cmd_check(cfg):
    print("=" * 60)
    print("  环境自检")
    print("=" * 60)
    print("  Python      : %s" % sys.version.split()[0])
    print("  执行文件    : %s" % sys.executable)
    print("  配置文件    : %s" % cfgmod.CONFIG_PATH)

    miss = _check_pillow()
    if miss:
        print("  图像库      : ✗ 缺 %s" % "、".join(miss))
        print("      装一条就行：")
        print("        \"%s\" -m pip install pillow numpy" % sys.executable)
        print("      （如果用的是 ComfyUI 便携版自带的 python_embeded，本来就有，"
              "说明这次是用系统 Python 跑的）")
    else:
        import numpy
        from PIL import __version__ as pilv
        print("  图像库      : ✓ Pillow %s / numpy %s" % (pilv, numpy.__version__))

    url = cfg["comfy_url"]
    print("  ComfyUI     : %s" % url)
    c = Comfy(url, timeout=20)
    try:
        rep = c.health()
    except ComfyError as e:
        print("    连不上：%s" % e)
        return 1
    if not rep["ok"]:
        for e in rep["errors"]:
            print("    ✗ %s" % e)
        return 1
    info = rep["info"]
    print("    ✓ 已连通（ComfyUI %s / Python %s）"
          % (info.get("comfyui_version"), info.get("python")))
    if info.get("device"):
        print("    显卡：%s  显存 %.1f GB（空闲 %.1f GB）"
              % (info["device"], info.get("vram_total_gb", 0), info.get("vram_free_gb", 0)))
    print("    队列：执行中 %s / 等待 %s"
          % (info.get("queue_running"), info.get("queue_pending")))
    for w in rep["warnings"]:
        print("    ! %s" % w)

    try:
        m = c.list_model_choices()
    except ComfyError as e:
        print("    读模型清单失败：%s" % e)
        return 1

    print("  模型        :")
    ok = bool(m.get("has_qwen_nodes"))
    if not m.get("has_qwen_nodes"):
        print("    ✗ 没找到 Qwen-Image 的节点。这个工作流需要 ComfyUI 支持 "
              "TextEncodeQwenImage21（较新的版本自带）。")
    for k, label in (("unet", "扩散模型"), ("clip", "文本编码器"), ("vae", "VAE")):
        want = cfg["models"][k]
        avail = m.get(k) or []
        if want in avail:
            print("    ✓ %s：%s" % (label, want))
        else:
            ok = False
            print("    ✗ %s 里没有 %s" % (label, want))
            if avail:
                print("      实际可选（把 config.json 里的名字换成其中一个）：")
                for a in avail[:8]:
                    print("        - %s" % a)
            else:
                print("      这是空的，说明这类模型一个都没装。")

    if miss:
        ok = False
    print("=" * 60)
    print("  结论：%s" % ("环境就绪，可以启动界面了。" if ok
                         else "还差东西，按上面的 ✗ 补齐后再跑一次 --check。"))
    return 0 if ok else 1


def main():
    _setup_console()
    ap = argparse.ArgumentParser(description="表情包工坊：把自家猫的照片做成一套动态表情")
    ap.add_argument("--work", default="", help="工程目录（默认用 config.json 里的 workdir）")
    ap.add_argument("--port", type=int, default=0, help="服务端口（默认自动挑一个）")
    ap.add_argument("--check", action="store_true", help="只做环境自检")
    ap.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    ap.add_argument("--open", action="store_true", help="启动后打开工程目录")
    args = ap.parse_args()

    cfg = cfgmod.load()

    if args.check:
        return cmd_check(cfg)

    work = os.path.abspath(args.work or cfg.get("workdir") or "我的表情包")
    os.makedirs(work, exist_ok=True)
    for d in ("照片", "原图", "01_表情图", "02_封面", "03_聊天面板图标",
              "04_详情页横幅", "05_对照图"):
        os.makedirs(os.path.join(work, d), exist_ok=True)

    if args.open:
        try:
            if os.name == "nt":
                os.startfile(work)          # noqa: S606
        except Exception:
            pass

    from studio import web
    web.run(cfg, work, port=args.port or None, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
