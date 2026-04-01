#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

lark_script="${SCRIPT_DIR}/overwrite_wiki_from_md.sh"

"${lark_script}" \
  "${REPO_ROOT}/trips/qinggan-2026-mayday/docs/青甘五一行程.md" \
  "https://my.feishu.cn/wiki/YLKJw6h0qiQrM0kFpHic6pYhnBd"
