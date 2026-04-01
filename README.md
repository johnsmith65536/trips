# Trips Workspace

按“旅行项目”而不是按“文件类型”组织内容。

## 目录约定

- `trips/<trip-slug>/docs/`: 行程文档、签证材料、攻略源文件
- `trips/<trip-slug>/visuals/`: HTML 可视化页面
- `trips/<trip-slug>/exports/`: PDF、KML 等导出产物
- `scripts/feishu/`: 飞书同步与调试脚本

## 当前项目

- `trips/australia-2026-mayday/`
- `trips/qinggan-2026-mayday/`

## 使用建议

- 新建行程时，先复制一个 trip 目录骨架，再放内容
- 尽量使用稳定英文文件名，正文内容保持中文即可
- 飞书相关密钥只通过环境变量传入，不写入脚本
- 若使用 `lark-cli` 同步飞书，可运行 `scripts/feishu/overwrite_wiki_from_md.sh <markdown文件> <飞书URL>`，默认会把飞书标题设为 Markdown 文件名（不含 `.md`）
- 青甘行程的一键同步脚本是 `scripts/feishu/sync_feishu.sh`
