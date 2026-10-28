#!/bin/zsh
set -eu
cd "${0:A:h:h}"
print "正在配置 Breadcrumb 地图工具…"
if [[ -x .venv/bin/python ]]; then
  task_python=.venv/bin/python
elif command -v python3.13 >/dev/null; then
  python3.13 -m venv .venv
  task_python=.venv/bin/python
else
  python3 -m venv .venv
  task_python=.venv/bin/python
fi
"$task_python" -m pip install -r mac/requirements.txt
if ! command -v brew >/dev/null; then
  print "请先从 https://brew.sh 安装 Homebrew，再重新打开此文件。"
  exit 1
fi
HOMEBREW_NO_AUTO_UPDATE=1 brew install openjdk@17 libmtp
if [[ ! -d /Applications/SdkManager.app ]]; then
  print "请从 https://developer.garmin.com/connect-iq/sdk/ 安装 Garmin SDK Manager 并接受官方协议。"
fi
"$task_python" mac/build_app.py
print "配置完成：打开 build 文件夹中的 Breadcrumb Trail.app 即可。"
open -R 'build/Breadcrumb Trail.app'
