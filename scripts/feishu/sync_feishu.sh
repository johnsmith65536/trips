#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -z "${FEISHU_APP_ID:-}" || -z "${FEISHU_APP_SECRET:-}" ]]; then
  echo "请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/feishu_writer.py" \
  "${REPO_ROOT}/trips/qinggan-2026-mayday/docs/qinggan-itinerary.md" \
  "https://my.feishu.cn/wiki/YLKJw6h0qiQrM0kFpHic6pYhnBd"
