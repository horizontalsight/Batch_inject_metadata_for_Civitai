# Civitai 数据导入助手

> ⚠️ **开发中**:本项目仍处于开发阶段,界面与功能可能会调整,可能存在 Bug,请谨慎使用。

把 ComfyUI 生成的图片批量上传到 [Civitai](https://civitai.com) 时,自动识别图片里用到的模型 / LoRA,并把对应的 Civitai 资源引用(AIR)写进 PNG 元数据。上传后 Civitai 会自动显示模型资源卡片,免去逐张手动填写的麻烦。

## 功能

图形界面(GUI,基于 tkinter / ttk)包含三个标签页:

1. **① 扫描识别模型** — 选择一个模型文件夹,递归识别其中的模型(SHA256 / AutoV2 哈希),并对照数据库显示收录状态。
2. **② 更新数据库** — 增量联网调用 Civitai by-hash API,把「未收录 / 之前查询失败」的模型信息(AIR / 模型名 / ID)写入本地数据库 `model_db.json`;已收录的不重复查询。
3. **③ 写入图片** — 从模型库勾选若干模型(勾选顺序即写入顺序),把对应 AIR 写进目标文件夹里每张 PNG 的 `workflow` 元数据,输出 `_comfy` 后缀副本。

模型库支持按「类型 / baseModel」筛选,并可输入「模型名 / 文件名」快速搜索;①③ 两个页面共享同一个模型库勾选池。

## 原理

Civitai 对 ComfyUI 有原生解析器,会读取 PNG 内嵌的 `prompt` + `workflow` 两个 chunk。向 workflow JSON 的 `extra.airs` 数组塞入模型 AIR 后,上传时 Civitai 会自动把主模型和所有 LoRA 显示为资源卡片。AIR 来自 Civitai by-hash API(`/api/v1/model-versions/by-hash/{AutoV2}`)的返回结果。

## 安装与运行

需要 Python 3.10+ 与 [uv](https://docs.astral.sh/uv/):

```bash
cd civitai_batch_metadata
uv sync                 # 创建 .venv 并安装依赖(如 pillow)
uv run civitai_gui.py   # 启动图形界面
```

Windows 下也可直接双击 `run_gui.bat`。

## 打包为 exe

双击 `build.bat`,产物为单文件 `dist/CivitaiDataAssistant.exe`(数据文件会放在 exe 旁边)。

## 目录结构

```
civitai_gui.py    图形界面(唯一入口)
civitai_core.py   核心逻辑(哈希 / 数据库 / PNG 注入),全部逻辑集中于此
pyproject.toml    uv 项目配置
uv.lock           依赖锁定文件
run_gui.bat       启动脚本(Windows)
build.bat         打包脚本(Windows, PyInstaller)
```

## 数据文件

- `model_db.json` — 本地查询缓存(按 AutoV2 索引)。**不随仓库提交**,首次使用请先「① 扫描模型」再「② 更新数据库」自动重建。
- `gui_config.json` — 保存本机路径等运行配置,同样不提交。
