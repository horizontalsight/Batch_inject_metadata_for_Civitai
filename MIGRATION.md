# Migration / 迁移说明

This repository is archived and no longer maintained.

The project has moved to:

- Repository: <https://github.com/horizontalsight/horizontalSight-custom-nodes>
- Component: `civitai_db/`
- Web panel: `/db/`

## 功能对应

| 旧功能 | 新位置 |
|---|---|
| 本地模型库、扫描、哈希、查询 | `civitai_db/core.py`、`civitai_db/service.py` |
| 旧 GUI 三个页签 | `/db/` Web 面板 |
| PNG 批量注入 AIR | `civitai_db/inject.py`、`CivitaiInjectImages` 节点 |
| Civitai 提示词修复 | `civitai_db/inject.py` |
| 旧脚本/CLI 入口 | 新面板或对应 ComfyUI 节点 |

旧代码和 Git 历史保留作参考；新功能以 `horizontalSight-custom-nodes` 为准。