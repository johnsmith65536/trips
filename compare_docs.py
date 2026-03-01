#!/usr/bin/env python3
"""对比两个飞书文档的 block 结构差异"""
import json
import os
import sys
import time
import threading
import re
from typing import Optional

import requests

API_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token = None
        self._token_expire = 0
        self._token_lock = threading.Lock()
        self._session = requests.Session()

    def _get_token(self):
        with self._token_lock:
            if self._token and time.time() < self._token_expire - 60:
                return self._token
            r = self._session.post(
                f"{API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=15,
            )
            r.raise_for_status()
            d = r.json()
            self._token = d["tenant_access_token"]
            self._token_expire = time.time() + d.get("expire", 7200)
            return self._token

    def _h(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get(self, path, params=None):
        r = self._session.get(f"{API_BASE}{path}", headers=self._h(), params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"GET {path} [{d.get('code')}]: {d.get('msg')}")
        return d.get("data", {})

    def get_wiki_node(self, node_token):
        return self._get("/wiki/v2/spaces/get_node", params={"token": node_token})["node"]

    def list_all_blocks(self, doc_id):
        blocks_map = {}
        page_token = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            d = self._get(f"/docx/v1/documents/{doc_id}/blocks", params=params)
            for b in d.get("items", []):
                blocks_map[b["block_id"]] = b
            if not d.get("has_more"):
                break
            page_token = d["page_token"]
        return blocks_map


BLOCK_TYPE_NAMES = {
    1: "page",
    2: "text/paragraph",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    22: "divider",
    23: "image",
    24: "isv",
    26: "add_ons",
    27: "image(old)",
    28: "bitable",
    29: "iframe",
    30: "diagram",
    31: "table",
    33: "grid",
    34: "table_cell",
    35: "grid_column",
    36: "view/sync",
    40: "quote_container",
    41: "mindnote",
    43: "task",
}


def get_block_text(block):
    """提取 block 中的纯文本内容（用于对比）"""
    btype = block.get("block_type")
    type_name = BLOCK_TYPE_NAMES.get(btype, f"type_{btype}")

    # 找到内容字段
    content_keys = {
        2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
        6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
        10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered",
        14: "code", 15: "quote", 17: "todo",
    }
    key = content_keys.get(btype)
    if key and key in block:
        content = block[key]
        elements = content.get("elements", [])
        texts = []
        for el in elements:
            if "text_run" in el:
                texts.append(el["text_run"].get("content", ""))
        return "".join(texts)
    return ""


def flatten_blocks(blocks_map, parent_id, depth=0):
    """按深度优先遍历，返回有序 (depth, block) 列表"""
    parent = blocks_map.get(parent_id)
    if not parent:
        return []
    result = []
    for child_id in parent.get("children", []):
        child = blocks_map.get(child_id)
        if not child:
            continue
        result.append((depth, child))
        if child.get("children"):
            result.extend(flatten_blocks(blocks_map, child_id, depth + 1))
    return result


def describe_block(depth, block):
    btype = block.get("block_type")
    type_name = BLOCK_TYPE_NAMES.get(btype, f"type_{btype}")
    text = get_block_text(block)
    indent = "  " * depth
    children_count = len(block.get("children", []))
    extra = f" [{children_count} children]" if children_count else ""
    text_preview = f' "{text[:60]}"' if text else ""
    return f"{indent}[{type_name}]{extra}{text_preview}"


def count_by_type(flat_blocks):
    counts = {}
    for _, block in flat_blocks:
        btype = block.get("block_type")
        name = BLOCK_TYPE_NAMES.get(btype, f"type_{btype}")
        counts[name] = counts.get(name, 0) + 1
    return counts


def extract_node_token(url):
    m = re.search(r"/wiki/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"无法解析 wiki token: {url}")
    return m.group(1)


def main():
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not (app_id and app_secret):
        print("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        sys.exit(1)

    # 官方导入文档 vs 工具生成文档
    official_url = "https://my.feishu.cn/wiki/SM66wUwomipR11k0Mbjc3Ha0nog"
    tool_url = "https://my.feishu.cn/wiki/HEGHwh8gCiGcQSklepWcLUJRnC7"

    official_token = extract_node_token(official_url)
    tool_token = extract_node_token(tool_url)

    client = FeishuClient(app_id, app_secret)

    print("获取官方文档信息...")
    official_node = client.get_wiki_node(official_token)
    official_doc_id = official_node["obj_token"]
    print(f"  官方文档 ID: {official_doc_id}, 标题: {official_node.get('title', '?')}")

    print("获取工具文档信息...")
    tool_node = client.get_wiki_node(tool_token)
    tool_doc_id = tool_node["obj_token"]
    print(f"  工具文档 ID: {tool_doc_id}, 标题: {tool_node.get('title', '?')}")

    print("\n读取官方文档 blocks...")
    official_map = client.list_all_blocks(official_doc_id)
    print(f"  共 {len(official_map)} 个 block")

    print("读取工具文档 blocks...")
    tool_map = client.list_all_blocks(tool_doc_id)
    print(f"  共 {len(tool_map)} 个 block")

    # 找 page block
    official_page = next((b for b in official_map.values() if b["block_type"] == 1), None)
    tool_page = next((b for b in tool_map.values() if b["block_type"] == 1), None)

    official_flat = flatten_blocks(official_map, official_page["block_id"])
    tool_flat = flatten_blocks(tool_map, tool_page["block_id"])

    print(f"\n官方文档顶层子块数: {len(official_page.get('children', []))}")
    print(f"工具文档顶层子块数: {len(tool_page.get('children', []))}")

    # 按类型统计
    official_counts = count_by_type(official_flat)
    tool_counts = count_by_type(tool_flat)

    all_types = sorted(set(official_counts) | set(tool_counts))
    print("\n=== Block 类型统计对比 ===")
    print(f"{'类型':<20} {'官方':>8} {'工具':>8} {'差异':>8}")
    print("-" * 50)
    for t in all_types:
        o = official_counts.get(t, 0)
        g = tool_counts.get(t, 0)
        diff = g - o
        flag = " ⚠️" if diff != 0 else ""
        print(f"{t:<20} {o:>8} {g:>8} {diff:>+8}{flag}")

    # 保存详细 block 列表
    print("\n=== 详细 block 列表 ===")
    print("\n--- 官方文档 ---")
    for depth, block in official_flat:
        print(describe_block(depth, block))

    print("\n--- 工具文档 ---")
    for depth, block in tool_flat:
        print(describe_block(depth, block))

    # 逐行对比（按位置）
    print("\n=== 逐位置对比（前50个顶层块）===")
    official_top = [(d, b) for d, b in official_flat if d == 0]
    tool_top = [(d, b) for d, b in tool_flat if d == 0]
    max_len = max(len(official_top), len(tool_top))
    diffs = []
    for i in range(min(max_len, 80)):
        o = official_top[i] if i < len(official_top) else None
        g = tool_top[i] if i < len(tool_top) else None
        o_btype = o[1].get("block_type") if o else None
        g_btype = g[1].get("block_type") if g else None
        o_text = get_block_text(o[1])[:50] if o else ""
        g_text = get_block_text(g[1])[:50] if g else ""
        o_name = BLOCK_TYPE_NAMES.get(o_btype, f"type_{o_btype}") if o_btype else "—"
        g_name = BLOCK_TYPE_NAMES.get(g_btype, f"type_{g_btype}") if g_btype else "—"
        if o_btype != g_btype or o_text != g_text:
            diffs.append(i)
            marker = "❌"
        else:
            marker = "✅"
        print(f"[{i:3d}] {marker} 官方: [{o_name}] {o_text!r:<40}  工具: [{g_name}] {g_text!r}")

    print(f"\n共 {len(diffs)} 处顶层块差异（位置: {diffs[:20]}{'...' if len(diffs)>20 else ''}）")

    # 保存原始 JSON 供进一步分析
    with open("/tmp/official_blocks.json", "w") as f:
        json.dump(official_flat_raw := [(d, b) for d, b in official_flat], f, ensure_ascii=False, indent=2, default=str)
    with open("/tmp/tool_blocks.json", "w") as f:
        json.dump([(d, b) for d, b in tool_flat], f, ensure_ascii=False, indent=2, default=str)
    print("\n原始 block JSON 已保存到 /tmp/official_blocks.json 和 /tmp/tool_blocks.json")


if __name__ == "__main__":
    main()
