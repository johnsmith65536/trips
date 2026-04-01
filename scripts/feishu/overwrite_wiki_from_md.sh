#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

usage() {
  cat <<'EOF'
用法:
  scripts/feishu/overwrite_wiki_from_md.sh <markdown文件> <飞书文档URL> [--title 标题] [--keep-top-heading]

说明:
  使用 lark-cli 的 docs +update overwrite 模式，将本地 Markdown 直接覆盖到指定飞书文档或知识库文档。
  默认标题取 Markdown 文件名（去掉 .md 后缀）；也可用 --title 显式指定。
  默认会在以下条件同时满足时，自动删除正文开头重复的一级标题:
  1. 第一行是 "# 某个标题"
  2. 该标题与文档标题相同

参数:
  <markdown文件>     本地 Markdown 文件路径
  <飞书文档URL>      /wiki/ 或 /docx/ 链接
  --title 标题       显式指定飞书文档标题；默认取 Markdown 文件名（去掉 .md 后缀）
  --keep-top-heading 保留正文开头的一级标题，不做自动去重

示例:
  scripts/feishu/overwrite_wiki_from_md.sh \
    trips/qinggan-2026-mayday/docs/qinggan-itinerary.md \
    https://my.feishu.cn/wiki/YLKJw6h0qiQrM0kFpHic6pYhnBd
EOF
}

if ! command -v lark-cli >/dev/null 2>&1; then
  echo "错误: 未找到 lark-cli，请先安装并完成登录授权。" >&2
  exit 1
fi

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 1
fi

md_input="$1"
doc_url="$2"
shift 2

title=""
keep_top_heading="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)
      if [[ $# -lt 2 ]]; then
        echo "错误: --title 缺少参数" >&2
        exit 1
      fi
      title="$2"
      shift 2
      ;;
    --keep-top-heading)
      keep_top_heading="true"
      shift
      ;;
    *)
      echo "错误: 不支持的参数 $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$md_input" = /* ]]; then
  md_path="$md_input"
else
  md_path="${REPO_ROOT}/${md_input}"
fi

if [[ ! -f "$md_path" ]]; then
  echo "错误: Markdown 文件不存在: $md_path" >&2
  exit 1
fi

first_line="$(sed -n '1p' "$md_path")"
filename="$(basename "$md_path")"
default_title="${filename%.md}"

if [[ -z "$title" ]]; then
  title="$default_title"
fi

markdown="$(<"$md_path")"

if [[ "$keep_top_heading" != "true" && "$first_line" =~ ^#\ (.+)$ ]]; then
  heading_title="${BASH_REMATCH[1]}"
  if [[ "$heading_title" == "$title" ]]; then
    markdown="$(printf '%s' "$markdown" | sed '1{/^# .*/d; /^$/d;}')"
  fi
fi

echo "覆盖飞书文档:"
echo "  Markdown: $md_path"
echo "  Doc URL:  $doc_url"
echo "  Title:    $title"
echo "  Mode:     overwrite"

lark-cli docs +update \
  --as user \
  --doc "$doc_url" \
  --mode overwrite \
  --new-title "$title" \
  --markdown "$markdown"
