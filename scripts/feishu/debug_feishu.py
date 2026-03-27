#!/usr/bin/env python3
"""调试：逐块找出哪个 block 格式飞书不接受"""
import json
import os
import sys
import time
from pathlib import Path

import requests
import warnings

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_writer import md_to_blocks

APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
DOC = os.environ.get("FEISHU_DOC_TOKEN", "DdxNdV9o5owTK7xwtgmcNRq6ntc")
API = "https://open.feishu.cn/open-apis"
MD_FILE = REPO_ROOT / "trips" / "australia-2026-mayday" / "docs" / "itinerary.md"

if not APP_ID or not APP_SECRET:
    raise SystemExit("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量")

# 获取 token
r = requests.post(f"{API}/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET})
token = r.json()["tenant_access_token"]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print(f"Token len={len(token)}: {token[:30]}...")

# 先清空文档
blist = requests.get(f"{API}/docx/v1/documents/{DOC}/blocks",
    headers=H, params={"page_size": 500}).json()
items = blist["data"]["items"]
page = next(b for b in items if b["block_type"] == 1)
page_id = page["block_id"]
n_children = len(page.get("children", []))
if n_children > 0:
    r = requests.delete(f"{API}/docx/v1/documents/{DOC}/blocks/{page_id}/children",
        headers=H, json={"start_index": 0, "end_index": n_children})
    try:
        d = r.json()
        print(f"Deleted {n_children} existing blocks: code={d.get('code')} msg={d.get('msg')}")
    except Exception:
        print(f"Deleted {n_children} existing blocks: HTTP {r.status_code}, body={r.text[:100]}")
    time.sleep(0.5)

# 读取 md
with open(MD_FILE, encoding="utf-8") as f:
    md = f.read()
blocks = md_to_blocks(md)
print(f"Generated {len(blocks)} blocks")

# 逐块插入，找出失败的
errors = []
inserted = 0
for i, b in enumerate(blocks):
    r = requests.post(f"{API}/docx/v1/documents/{DOC}/blocks/{page_id}/children",
        headers=H, json={"children": [b], "index": inserted})
    d = r.json()
    if d.get("code") != 0:
        errors.append((i, b, d.get("msg", "")))
        print(f"  FAIL [{i}] type={b['block_type']}: {d['msg']}")
        print(f"       block: {json.dumps(b, ensure_ascii=False)[:200]}")
    else:
        inserted += 1
    if i % 50 == 0:
        print(f"  progress: {i}/{len(blocks)}, inserted={inserted}, errors={len(errors)}")
        time.sleep(0.1)

print(f"\nDone. Inserted={inserted}, Errors={len(errors)}")
if errors:
    print("\n=== All errors ===")
    for i, b, msg in errors:
        print(f"  [{i}] type={b['block_type']}: {msg}")
        print(f"       {json.dumps(b, ensure_ascii=False)[:300]}")
