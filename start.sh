#!/usr/bin/env bash
# 表情包工坊 启动脚本（macOS / Linux）
#
# 跟 start.bat 同一个思路：先找 ComfyUI 便携版自带的 python_embeded（它一定带 Pillow），
# 找不到再退到 PATH 里的 python3。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

PY="${CATSTICKER_PYTHON:-}"

if [ -z "$PY" ]; then
  for c in \
    "$HERE/../ComfyUI_windows_portable/python_embeded/python.exe" \
    "$HERE/../ComfyUI/python_embeded/python.exe" \
    "$HOME/ComfyUI/python_embeded/python.exe"
  do
    [ -x "$c" ] && PY="$c" && break
  done
fi

if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

echo
echo "  使用 Python: $PY"
echo

"$PY" "$HERE/app.py" "$@"
RC=$?

if [ "$RC" != "0" ]; then
  echo
  echo "  ──────────────────────────────────────────────"
  echo "  启动没成功。若提示缺 Pillow / numpy："
  echo "    \"$PY\" -m pip install pillow numpy"
  echo "  若提示连不上 ComfyUI，先确认它已启动。"
  echo "  单独体检：  \"$PY\" \"$HERE/app.py\" --check"
  echo "  ──────────────────────────────────────────────"
  echo
fi
exit $RC
