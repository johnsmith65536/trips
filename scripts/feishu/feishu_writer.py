#!/usr/bin/env python3
"""
将 Markdown 文件写入飞书文档（Wiki）

原理：利用飞书官方 Import API 将 MD 转为 docx（效果与手动导入完全一致），
      然后将生成的 blocks 递归复制到目标 wiki 页面，最后删除临时文档。

使用方法:
    python feishu_writer.py <markdown文件> <飞书wiki链接> [选项]

示例:
    python feishu_writer.py 攻略.md https://my.feishu.cn/wiki/HEGHwh8gCiGcQSklepWcLUJRnC7

环境变量:
    FEISHU_APP_ID     飞书应用 App ID
    FEISHU_APP_SECRET 飞书应用 App Secret

需要的应用权限:
    wiki:wiki / wiki:wiki:readonly / wiki:node:read
    docx:document
    drive:drive
    drive:file

前置条件:
    pip install requests
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from typing import Optional

import requests

API_BASE = "https://open.feishu.cn/open-apis"

# 不需要递归复制子节点的叶子 block 类型（直接复制整个 block）
LEAF_BLOCK_TYPES = {
    2,   # text / paragraph
    3, 4, 5, 6, 7, 8, 9, 10, 11,  # heading1-9
    12,  # bullet
    13,  # ordered
    14,  # code
    15,  # quote
    17,  # todo
    22,  # divider
    24,  # isv (plugin)
    29,  # iframe
    30,  # diagram (mermaid etc)
}

# 图片相关 block：token 是文档私有的，跨文档复制无意义，直接跳过
SKIP_BLOCK_TYPES = {
    23,  # image
    27,  # image (旧版字段名)
    26,  # add_ons
    28,  # bitable embed
}

# 需要递归处理子节点的容器 block 类型
CONTAINER_BLOCK_TYPES = {
    31,  # table
    33,  # grid (multi-column layout)
    34,  # table_cell
    35,  # grid_column
    36,  # view (sync block)
    40,  # quote_container
    41,  # mindnote
    43,  # task (grouped todos)
}


# ─────────────────────────── 飞书客户端 ───────────────────────────

class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: Optional[str] = None
        self._token_expire = 0
        self._token_lock = threading.Lock()
        self._local = threading.local()  # 每线程独立 Session

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _get_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expire - 60:
                return self._token
            r = self._session().post(
                f"{API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=15,
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != 0:
                raise RuntimeError(f"获取 token 失败: {d.get('msg')}")
            self._token = d["tenant_access_token"]
            self._token_expire = time.time() + d.get("expire", 7200)
            return self._token

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _request(self, method: str, path: str, retries: int = 5, **kwargs) -> dict:
        """统一 HTTP 请求：指数退避重试 429，非 2xx 直接抛出。"""
        url = f"{API_BASE}{path}"
        for attempt in range(retries):
            r = self._session().request(method, url, headers=self._h(), timeout=30, **kwargs)
            if r.status_code == 429:
                wait = 2 ** attempt          # 1 2 4 8 16 s
                print(f"  限流，{wait}s 后重试（{path}）...")
                time.sleep(wait)
                continue
            if not r.ok:
                try:
                    e = r.json()
                    raise RuntimeError(f"{method} {path} HTTP {r.status_code} [{e.get('code')}]: {e.get('msg')}")
                except (ValueError, KeyError):
                    raise RuntimeError(f"{method} {path} HTTP {r.status_code}: {r.text[:200]}")
            d = r.json()
            if d.get("code") != 0:
                raise RuntimeError(f"{method} {path} [{d.get('code')}]: {d.get('msg')}")
            return d.get("data", {})
        raise RuntimeError(f"{method} {path} 重试 {retries} 次后仍然失败")

    def _get(self, path: str, params: dict = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict = None, **kwargs) -> dict:
        return self._request("POST", path, json=body, **kwargs)

    def _patch(self, path: str, body: dict) -> dict:
        return self._request("PATCH", path, json=body)

    def _delete(self, path: str, body: dict) -> dict:
        return self._request("DELETE", path, json=body)

    def update_wiki_title(self, space_id: str, node_token: str, title: str):
        """更新 wiki 页面标题（POST /wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title）"""
        self._post(f"/wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title",
                   {"title": title})

    # ── Wiki ──────────────────────────────────────────────────────────────

    def get_wiki_node(self, node_token: str) -> dict:
        return self._get("/wiki/v2/spaces/get_node", params={"token": node_token})["node"]

    # ── Drive ─────────────────────────────────────────────────────────────

    def get_root_folder_token(self) -> str:
        d = self._get("/drive/explorer/v2/root_folder/meta")
        return d["token"]

    def upload_file(self, file_bytes: bytes, filename: str, folder_token: str) -> str:
        """上传文件到 Drive，返回 file_token"""
        from io import BytesIO
        h = self._h()
        # upload_all 用 multipart
        r = requests.post(
            f"{API_BASE}/drive/v1/files/upload_all",
            headers=h,
            files={"file": (filename, BytesIO(file_bytes), "text/markdown")},
            data={
                "file_name": filename,
                "parent_type": "explorer",
                "parent_node": folder_token,
                "size": str(len(file_bytes)),
            },
            timeout=60,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"上传文件失败 [{d.get('code')}]: {d.get('msg')}")
        return d["data"]["file_token"]

    def delete_drive_file(self, file_token: str, file_type: str = "docx"):
        """删除 Drive 文件（清理临时文档）"""
        try:
            r = requests.delete(
                f"{API_BASE}/drive/v1/files/{file_token}",
                headers=self._h(),
                params={"type": file_type},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"  警告：删除临时文件失败（可手动清理）: {e}")

    # ── Import ────────────────────────────────────────────────────────────

    def import_markdown(self, file_token: str, folder_token: str) -> str:
        """创建导入任务，等待完成，返回新建 docx 的 token"""
        d = self._post("/drive/v1/import_tasks", {
            "file_extension": "md",
            "file_token": file_token,
            "type": "docx",
            "point": {
                "mount_type": 1,       # 1 = Drive
                "mount_key": folder_token,
            },
        })
        ticket = d["ticket"]
        print(f"  导入任务已创建，ticket={ticket}，等待完成...")

        for _ in range(60):
            time.sleep(2)
            status_d = self._get(f"/drive/v1/import_tasks/{ticket}")
            result = status_d.get("result", {})
            job_status = result.get("job_status", -1)
            # job_status=0 + token 存在 = 成功；其他值=进行中；token 缺失且 job_error_msg 非 success = 失败
            if result.get("token"):
                return result["token"]
            if result.get("job_error_msg") not in ("", "success", None) and job_status not in (0, 1, 2):
                raise RuntimeError(f"导入失败: {result.get('job_error_msg', '')}")
        raise RuntimeError("导入任务超时（超过120秒）")

    # ── Docx blocks ───────────────────────────────────────────────────────

    def list_all_blocks(self, doc_id: str) -> dict:
        """获取文档全部 blocks，返回 {block_id: block} 字典"""
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

    def _get_child_count(self, doc_id: str, block_id: str) -> int:
        """查询 block 的当前子节点数"""
        try:
            data = self._get(f"/docx/v1/documents/{doc_id}/blocks/{block_id}")
            # 单块 GET 返回 data = {"block": {...}}，children 在 block 下
            block_obj = data.get("block", data)
            return len(block_obj.get("children", []))
        except Exception:
            return 0

    def delete_page_children(self, doc_id: str, page_id: str, count: int):
        """循环删除子块，直到文档真正为空（处理 API 单次删除数量限制）"""
        if count <= 0:
            return
        BATCH = 500
        prev_actual = -1
        stale_streak = 0
        for _ in range(50):
            actual = self._get_child_count(doc_id, page_id)
            if actual == 0:
                break
            # 检测最终一致性导致计数不变：连续 5 次未变化则认为已清除
            if actual == prev_actual:
                stale_streak += 1
                if stale_streak >= 5:
                    break
            else:
                stale_streak = 0
            prev_actual = actual
            to_delete = min(actual, BATCH)
            try:
                self._delete(
                    f"/docx/v1/documents/{doc_id}/blocks/{page_id}/children/batch_delete",
                    {"start_index": 0, "end_index": to_delete},
                )
            except Exception as e:
                print(f"  警告：批量删除失败: {e}")
                break
            time.sleep(0.3)

    def create_blocks(self, doc_id: str, parent_id: str, blocks: list, index: int = 0) -> list:
        """批量创建子 blocks，返回创建后的 block 列表（含 block_id）"""
        batch_size = 50
        created = []
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i: i + batch_size]
            d = self._post(
                f"/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
                {"children": batch, "index": index + i},
            )
            created.extend(d.get("children", []))
        return created

    def copy_blocks(self, src_doc: str, src_block_ids: list,
                    tgt_doc: str, tgt_parent_id: str,
                    src_map: dict, index: int = 0) -> int:
        """
        把 src_doc 中的一批 block（src_block_ids）递归复制到 tgt_doc 的 tgt_parent_id 下。
        src_map: {block_id: block} —— 整个源文档的 block 字典
        返回实际插入的顶层 block 数量（committed）。

        使用本地 committed 计数器追踪已插入数量，避免依赖 API 查询的最终一致性问题。
        """
        if not src_block_ids:
            return 0

        pending_leaves: list[dict] = []
        committed = 0

        def flush_leaves():
            nonlocal committed
            if not pending_leaves:
                return
            try:
                self.create_blocks(tgt_doc, tgt_parent_id, pending_leaves, index + committed)
                committed += len(pending_leaves)
            except Exception:
                # 批量失败 → 逐块尝试，跳过真正有问题的
                for b in pending_leaves:
                    try:
                        self.create_blocks(tgt_doc, tgt_parent_id, [b], index + committed)
                        committed += 1
                    except Exception as e2:
                        print(f"  警告：跳过 block type={b.get('block_type')}: {str(e2)[:80]}")
            pending_leaves.clear()

        for src_id in src_block_ids:
            src_block = src_map.get(src_id)
            if not src_block:
                continue
            btype = src_block["block_type"]

            if btype == 1:  # page block — 跳过
                continue
            if btype in SKIP_BLOCK_TYPES:  # 图片等跨文档无效的 block
                continue

            if btype in LEAF_BLOCK_TYPES:
                if btype == 3:  # H1（# 标题）→ 用作文档标题，正文中跳过
                    continue
                if src_block.get("children"):
                    # 嵌套列表（bullet/ordered 有子块）：不可批量，单独创建后递归子块
                    flush_leaves()
                    clean = _shift_heading(_clean_block(src_block))
                    try:
                        created = self.create_blocks(tgt_doc, tgt_parent_id, [clean], index + committed)
                        committed += 1
                        if created:
                            self.copy_blocks(src_doc, src_block["children"],
                                             tgt_doc, created[0]["block_id"], src_map, 0)
                    except Exception as e:
                        print(f"  警告：跳过嵌套列表 type={btype}: {str(e)[:80]}")
                else:
                    pending_leaves.append(_shift_heading(_clean_block(src_block)))
            else:
                # 容器块：先 flush 已积累的叶子，再创建容器
                flush_leaves()
                n = self._copy_container(src_doc, src_block, tgt_doc, tgt_parent_id,
                                         src_map, index + committed)
                committed += n

        flush_leaves()
        return committed

    def _copy_container(self, src_doc, src_block, tgt_doc, tgt_parent_id, src_map, tgt_index) -> int:
        """复制一个容器 block（如 table）及其所有子节点到目标位置。
        返回在 tgt_parent_id 下创建的顶层 block 数量。"""
        btype = src_block["block_type"]

        if btype == 31:  # table
            prop = src_block.get("table", {}).get("property", {})
            n_rows = prop.get("row_size", 1)
            n_cols = prop.get("column_size", 1)
            has_header = bool(prop.get("header_row", True))
            print(f"  复制表格 {n_rows}×{n_cols}...")

            src_cell_ids = src_block.get("table", {}).get("cells", [])

            # 创建时最多 9 行（API 限制），超出部分用 insert_table_row 逐行追加
            init_rows = min(n_rows, 9)
            table_prop = {
                "row_size": init_rows,
                "column_size": n_cols,
                "header_row": has_header,
                "header_column": bool(prop.get("header_column", False)),
            }
            if "column_width" in prop:
                table_prop["column_width"] = prop["column_width"]
            try:
                created = self.create_blocks(tgt_doc, tgt_parent_id, [{
                    "block_type": 31,
                    "table": {"cells": [], "property": table_prop},
                }], tgt_index)
            except Exception as e:
                print(f"  警告：表格创建失败（跳过）: {e}")
                return 0
            if not created:
                return 0

            tbl_id = created[0]["block_id"]

            # 追加剩余行
            for row_idx in range(init_rows, n_rows):
                try:
                    self._patch(f"/docx/v1/documents/{tgt_doc}/blocks/{tbl_id}",
                                {"insert_table_row": {"row_index": row_idx}})
                except Exception as e:
                    print(f"  警告：第 {row_idx+1} 行追加失败: {e}")
                    break

            # 重新获取完整 cell 列表（追加行后 cell 已更新）
            tbl_data = self._get(f"/docx/v1/documents/{tgt_doc}/blocks/{tbl_id}")
            tgt_cell_ids = tbl_data.get("block", tbl_data).get("table", {}).get("cells", [])

            for row in range(n_rows):
                for c in range(n_cols):
                    src_flat = row * n_cols + c
                    tgt_flat = row * n_cols + c
                    if src_flat >= len(src_cell_ids) or tgt_flat >= len(tgt_cell_ids):
                        continue
                    src_cell = src_map.get(src_cell_ids[src_flat])
                    tgt_cell_id = tgt_cell_ids[tgt_flat]
                    if not src_cell:
                        continue
                    cell_children = src_cell.get("children", [])
                    if not cell_children:
                        continue
                    # 在 index=0 插入内容（默认空段落被挤到末尾），然后立即删除末尾空段落
                    n_inserted = self.copy_blocks(src_doc, cell_children, tgt_doc, tgt_cell_id, src_map, 0)
                    if n_inserted > 0:
                        try:
                            self._delete(
                                f"/docx/v1/documents/{tgt_doc}/blocks/{tgt_cell_id}/children/batch_delete",
                                {"start_index": n_inserted, "end_index": n_inserted + 1},
                            )
                        except Exception:
                            pass
            return 1

        else:
            # 其他容器：先创建父 block，再递归子节点
            clean = _clean_block(src_block)
            try:
                created = self.create_blocks(tgt_doc, tgt_parent_id, [clean], tgt_index)
            except Exception as e:
                print(f"  警告：容器 block type={btype} 创建失败（跳过）: {e}")
                print(f"  Block: {json.dumps(clean, ensure_ascii=False)[:300]}")
                return 0
            if not created:
                return 0
            tgt_id = created[0]["block_id"]
            children_ids = src_block.get("children", [])
            if children_ids:
                self.copy_blocks(src_doc, children_ids, tgt_doc, tgt_id, src_map, 0)
            return 1


def _clean_block(block: dict) -> dict:
    """去掉源 block 中飞书自动维护的字段，留下可用于创建的内容"""
    skip = {"block_id", "parent_id", "children"}
    return {k: v for k, v in block.items() if k not in skip}


# 标题层级下移映射：(原类型) → (新类型, 原字段名, 新字段名)
_HEADING_SHIFT_MAP = {
    4:  (3,  "heading2", "heading1"),
    5:  (4,  "heading3", "heading2"),
    6:  (5,  "heading4", "heading3"),
    7:  (6,  "heading5", "heading4"),
    8:  (7,  "heading6", "heading5"),
    9:  (8,  "heading7", "heading6"),
    10: (9,  "heading8", "heading7"),
    11: (10, "heading9", "heading8"),
}


def _shift_heading(block: dict) -> dict:
    """标题层级下移一级（H2→H1, H3→H2 等），使 # 成为文档标题、## 成为正文一级标题。"""
    btype = block.get("block_type")
    if btype not in _HEADING_SHIFT_MAP:
        return block
    new_type, old_key, new_key = _HEADING_SHIFT_MAP[btype]
    result = {k: v for k, v in block.items() if k != old_key}
    result["block_type"] = new_type
    if old_key in block:
        result[new_key] = block[old_key]
    return result


# ─────────────────────────── 工具函数 ───────────────────────────

def extract_node_token(url: str) -> str:
    m = re.search(r"/wiki/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"无法从 URL 解析 wiki token: {url}")
    return m.group(1)


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="将 Markdown 文件写入飞书 Wiki 文档（使用官方 Import API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("markdown_file", help="本地 Markdown 文件路径")
    parser.add_argument("feishu_url", help="飞书 Wiki 文档 URL")
    parser.add_argument("--mode", choices=["overwrite", "append"], default="overwrite")
    parser.add_argument("--app-id",     default=os.environ.get("FEISHU_APP_ID"))
    parser.add_argument("--app-secret", default=os.environ.get("FEISHU_APP_SECRET"))
    args = parser.parse_args()

    if not (args.app_id and args.app_secret):
        print("错误: 请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量", file=sys.stderr)
        sys.exit(1)

    md_path = args.markdown_file
    if not os.path.isfile(md_path):
        print(f"错误: 文件不存在: {md_path}", file=sys.stderr)
        sys.exit(1)

    with open(md_path, "rb") as f:
        md_bytes = f.read()
    filename = os.path.basename(md_path)

    node_token = extract_node_token(args.feishu_url)
    print(f"目标 Wiki token: {node_token}")

    client = FeishuClient(app_id=args.app_id, app_secret=args.app_secret)

    # ── 1. 获取目标文档 ID 并更新标题 ──
    print("正在获取目标文档信息...")
    node = client.get_wiki_node(node_token)
    tgt_doc_id = node["obj_token"]
    space_id = node["space_id"]
    print(f"目标文档 ID: {tgt_doc_id}")

    h1_match = re.search(r'^#\s+(.+)$', md_bytes.decode('utf-8', errors='replace'), re.MULTILINE)
    if h1_match:
        title = h1_match.group(1).strip()
        try:
            client.update_wiki_title(space_id, node_token, title)
            print(f"已更新文档标题: {title}")
        except Exception as e:
            print(f"  警告：标题更新失败（{e}），请手动将页面重命名为「{title}」")

    # ── 2. 上传 MD 文件到 Drive ──
    print("正在获取 Drive 根目录...")
    root_folder = client.get_root_folder_token()
    print(f"正在上传 {filename} ({len(md_bytes)} bytes)...")
    file_token = client.upload_file(md_bytes, filename, root_folder)
    print(f"文件上传完成，file_token: {file_token}")

    # ── 3. 导入为 docx ──
    print("正在调用飞书 Import API 转换 Markdown...")
    tmp_doc_token = client.import_markdown(file_token, root_folder)
    print(f"导入完成，临时文档 token: {tmp_doc_token}")

    # ── 4. 获取导入文档的全部 blocks ──
    print("正在读取导入文档的 blocks...")
    src_map = client.list_all_blocks(tmp_doc_token)
    # 找到 page block，取其 children
    src_page = next((b for b in src_map.values() if b["block_type"] == 1), None)
    if not src_page:
        print("错误：导入文档无 page block", file=sys.stderr)
        sys.exit(1)
    src_children = src_page.get("children", [])
    print(f"源文档共 {len(src_map)} 个 block，根节点下 {len(src_children)} 个子块")

    # ── 5. 处理目标文档 ──
    print("正在读取目标文档结构...")
    tgt_map = client.list_all_blocks(tgt_doc_id)
    tgt_page = next((b for b in tgt_map.values() if b["block_type"] == 1), None)
    if not tgt_page:
        print("错误：目标文档无 page block", file=sys.stderr)
        sys.exit(1)
    tgt_page_id = tgt_page["block_id"]
    tgt_child_count = len(tgt_page.get("children", []))

    if args.mode == "overwrite" and tgt_child_count > 0:
        print(f"正在清空目标文档（{tgt_child_count} 个 block）...")
        client.delete_page_children(tgt_doc_id, tgt_page_id, tgt_child_count)
        # 等待并验证文档已真正清空
        time.sleep(0.5)
        remaining = client._get_child_count(tgt_doc_id, tgt_page_id)
        if remaining > 0:
            print(f"  警告：仍有 {remaining} 个旧块未删除，继续尝试清理...")
            client.delete_page_children(tgt_doc_id, tgt_page_id, remaining)
            time.sleep(0.5)
        insert_index = 0
    else:
        insert_index = tgt_child_count if args.mode == "append" else 0

    # ── 6. 递归复制 blocks ──
    print(f"正在复制 {len(src_children)} 个顶层 block 到目标文档...")
    client.copy_blocks(
        src_doc=tmp_doc_token,
        src_block_ids=src_children,
        tgt_doc=tgt_doc_id,
        tgt_parent_id=tgt_page_id,
        src_map=src_map,
        index=insert_index,
    )

    # ── 7. 删除临时文档 ──
    print(f"正在删除临时文档（token={tmp_doc_token}）...")
    client.delete_drive_file(tmp_doc_token, "docx")

    print("✅ 完成！")


if __name__ == "__main__":
    main()
