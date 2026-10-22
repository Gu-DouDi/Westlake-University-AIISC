#!/bin/zsh

set -u
cd "$(dirname "$0")" || exit 1

PORT="${1:-8765}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3，无法启动网页。"
  echo "请先从 https://www.python.org/downloads/macos/ 安装 Python 3。"
  echo
  read -r "?按回车键关闭窗口……"
  exit 1
fi

python3 "serve_local.py" --port "$PORT"
STATUS=$?

if (( STATUS != 0 )); then
  echo
  read -r "?启动失败。按回车键关闭窗口……"
fi

exit "$STATUS"
