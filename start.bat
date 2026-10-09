@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "HERE=%~dp0"

rem ─────────────────────────────────────────────────────────────
rem  表情包工坊 启动脚本
rem
rem  它只做一件事：**找到一个带 Pillow 的 Python**，然后用它启动 app.py。
rem  优先顺序是刻意的：
rem    1. 环境变量 CATSTICKER_PYTHON 指定的
rem    2. ComfyUI 便携版自带的 python_embeded  ← 读者大概率什么都不用装
rem    3. 系统 PATH 里的 python
rem  这样「已经装好 ComfyUI 的人」双击就能跑，不用再 pip install 任何东西。
rem ─────────────────────────────────────────────────────────────

set "PY="
if not "%CATSTICKER_PYTHON%"=="" set "PY=%CATSTICKER_PYTHON%"

if not defined PY if exist "%HERE%..\ComfyUI_windows_portable\python_embeded\python.exe" set "PY=%HERE%..\ComfyUI_windows_portable\python_embeded\python.exe"

if not defined PY for %%D in (D C E F G H) do (
  if not defined PY if exist "%%D:\ComfyUI_windows_portable\python_embeded\python.exe" set "PY=%%D:\ComfyUI_windows_portable\python_embeded\python.exe"
)

if not defined PY for %%D in (D C E F G H) do (
  if not defined PY if exist "%%D:\ComfyUI\python_embeded\python.exe" set "PY=%%D:\ComfyUI\python_embeded\python.exe"
)

if not defined PY set "PY=python"

echo.
echo   使用 Python: %PY%
echo.

"%PY%" "%HERE%app.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo   ──────────────────────────────────────────────
  echo   启动没成功。上面如果提到缺 Pillow / numpy，装一条就行：
  echo     "%PY%" -m pip install pillow numpy
  echo.
  echo   如果提示连不上 ComfyUI，先确认 ComfyUI 已经启动。
  echo   想单独体检一次：  "%PY%" "%HERE%app.py" --check
  echo   ──────────────────────────────────────────────
  echo.
  pause
)
endlocal
