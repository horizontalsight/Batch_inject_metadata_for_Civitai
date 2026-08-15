#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
civitai_core.py — Civitai 数据导入助手核心逻辑(无界面)

汇集了哈希计算、Civitai by-hash 联网查询、PNG 元数据(AIR)注入等全部逻辑。
图形界面 civitai_gui.py 是唯一入口,所有功能都从这里复用代码。

函数分类:
    哈希:    sha256_hex / autov2
    数据库:  load_db / save_db / query_by_autov2
    PNG:     read_png_chunks / inject_workflow_airs / save_png_with_metadata
    高层:    scan_models / update_db / collect_airs / inject_images / db_stats
"""

import hashlib
import json
import os
import struct
import sys
import time
import urllib.request

from PIL import Image
from PIL.PngImagePlugin import PngInfo

# 常见的模型文件扩展名
MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")

# Civitai by-hash API 请求头(带浏览器 UA 避免 403)
_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}


# --------------------------------------------------------------------------
# 路径辅助
# --------------------------------------------------------------------------
def script_dir():
    # 打包成 exe 后,数据文件(model_db.json / 配置)应放在 exe 旁边,而不是临时解压目录
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_db_path():
    return os.path.join(script_dir(), "model_db.json")


# --------------------------------------------------------------------------
# 哈希计算
# --------------------------------------------------------------------------
def sha256_hex(filepath: str) -> str:
    """计算文件完整 SHA256(64 位十六进制,小写)。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            b = f.read(1 << 20)  # 1MB
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def autov2(sha256: str) -> str:
    """AutoV2 = SHA256 前 10 位,大写(Civitai 网站信息表格式)。"""
    return sha256[:10].upper()


# --------------------------------------------------------------------------
# 数据库(model_db.json)
# --------------------------------------------------------------------------
def load_db(db_path):
    """读取 model_db.json,返回 {AutoV2: entry}。文件不存在/损坏则返回空 dict。"""
    if not os.path.isfile(db_path):
        return {}
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f"[warn] 读取 model_db.json 失败({e}),将重建空数据库。")
        return {}


def save_db(db, db_path):
    """把数据库写回 model_db.json(UTF-8,缩进 2,原子替换)。"""
    tmp = db_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, db_path)
    print(f"[db  ] 数据库已保存: {db_path} ({len(db)} 条)")


def query_by_autov2(autov2):
    """联网调用 Civitai by-hash API 反查 AutoV2。

    成功返回 (entry_dict, None);失败返回 (None, 错误描述)。
    entry_dict 含 AIR / modelName / modelId / modelVersionId / type / baseModel。
    """
    url = f"https://civitai.com/api/v1/model-versions/by-hash/{autov2}"
    try:
        req = urllib.request.Request(url, headers=_API_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    air = d.get("air")
    if not air:
        return None, "返回数据里没有 air 字段"

    model = d.get("model") or {}
    entry = {
        "file": [],
        "modelName": model.get("name", ""),
        "AIR": air,
        "modelId": d.get("modelId"),
        "modelVersionId": d.get("id"),
        "type": model.get("type"),
        "baseModel": d.get("baseModel"),
        "baseModelType": d.get("baseModelType"),
        "queriedAt": None,
        "failed": False,
        "error": None,
    }
    return entry, None


# --------------------------------------------------------------------------
# PNG 读取与写入
# --------------------------------------------------------------------------
def read_png_chunks(path):
    """读取 PNG 的 tEXt/zTXt/iTXt chunk,返回 {keyword: text}。"""
    out = {}
    with open(path, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG: " + path)
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length, ctype = struct.unpack(">I4s", header)
            data = f.read(length)
            crc = f.read(4)
            if ctype in (b"tEXt", b"zTXt"):
                kw, _, txt = data.partition(b"\x00")
                try:
                    out[kw.decode("latin-1")] = txt.decode("utf-8", "replace")
                except Exception:
                    pass
            elif ctype == b"iTXt":
                try:
                    parts = data.split(b"\x00")
                    kw = parts[0].decode("latin-1")
                    if parts[1] == b"\x01":  # compressed
                        import zlib
                        text = zlib.decompress(b"\x78\x9c" + b"\x00".join(parts[5:])).decode("utf-8", "replace")
                    else:
                        text = b"\x00".join(parts[5:]).decode("utf-8", "replace")
                    out[kw] = text
                except Exception:
                    pass
            if ctype == b"IEND":
                break
    return out


def inject_workflow_airs(prompt_text, workflow_text, airs):
    """往 workflow JSON 注入 extra.airs,返回 (new_workflow, new_prompt)。"""
    wf = json.loads(workflow_text)
    extra = wf.get("extra") or {}
    airs_list = extra.get("airs") or []
    for a in airs:
        if a not in airs_list:
            airs_list.append(a)
    extra["airs"] = airs_list
    wf["extra"] = extra
    new_wf = json.dumps(wf, ensure_ascii=False)
    return new_wf, prompt_text


def save_png_with_metadata(src, dst, prompt_text, workflow_text):
    """用 Pillow 重存图片,写入 prompt/workflow 两个 iTXt 块(UTF-8 正确)。"""
    img = Image.open(src)
    img.load()
    pnginfo = PngInfo()
    pnginfo.add_text("prompt", prompt_text)
    pnginfo.add_text("workflow", workflow_text)
    exif = img.info.get("exif")
    if exif:
        pnginfo.add_text("exif", exif.decode("latin-1", "replace") if isinstance(exif, bytes) else str(exif))
    img.save(dst, "PNG", pnginfo=pnginfo)


# --------------------------------------------------------------------------
# 高层:扫描模型文件夹
# --------------------------------------------------------------------------
def find_model_files(folder, recursive=True):
    """递归/非递归查找文件夹下的模型文件,返回绝对路径列表(排序)。"""
    files = []
    if not os.path.isdir(folder):
        return files
    if recursive:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if n.lower().endswith(MODEL_EXTENSIONS):
                    files.append(os.path.join(root, n))
    else:
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            if os.path.isfile(p) and n.lower().endswith(MODEL_EXTENSIONS):
                files.append(p)
    return sorted(files)


def scan_models(folder, recursive=True, log=None, cancel=None, progress=None):
    """扫描文件夹里的模型,计算 SHA256 / AutoV2。返回模型列表。"""
    files = find_model_files(folder, recursive)
    _log(log, f"[扫描] 找到 {len(files)} 个模型文件,开始计算 SHA256 ...")
    models = []
    total = len(files)
    for i, p in enumerate(files, 1):
        if cancel and cancel.is_set():
            _log(log, "[扫描] 已取消。")
            break
        try:
            sha = sha256_hex(p)
            models.append({
                "path": p,
                "file": os.path.basename(p),
                "AutoV2": autov2(sha),
                "SHA256": sha.upper(),
            })
        except Exception as e:
            _log(log, f"[扫描] 跳过 {os.path.basename(p)}: {e}")
        if progress:
            progress(i, total)
    _log(log, f"[扫描] 完成,共识别 {len(models)} 个模型。")
    return models


# --------------------------------------------------------------------------
# 状态/统计辅助
# --------------------------------------------------------------------------
def db_stats(db, autov2_list):
    """统计本次模型列表在数据库里的收录情况。"""
    in_db = 0
    failed = 0
    missing = 0
    for av in autov2_list:
        entry = db.get(av)
        if entry is None:
            missing += 1
        elif entry.get("failed"):
            failed += 1
        elif entry.get("AIR"):
            in_db += 1
    return {"in_db": in_db, "failed": failed, "missing": missing, "to_query": missing + failed}


# --------------------------------------------------------------------------
# 高层:更新数据库
# --------------------------------------------------------------------------
def update_db(models, db_path=None, log=None, cancel=None, progress=None):
    """对模型列表中"数据库缺失或之前失败"的 AutoV2 联网查询,写回并保存。

    返回更新后的数据库 dict。已存在的成功记录不会重复联网。
    """
    db_path = db_path or default_db_path()
    db = load_db(db_path)

    # AutoV2 → 本地文件名列表(合并进已有记录)
    av_files = {}
    for m in models:
        av_files.setdefault(m["AutoV2"], []).append(m["file"])
    changed = False
    for av, files in av_files.items():
        if av in db:
            cur = db[av].setdefault("file", [])
            for f in files:
                if f not in cur:
                    cur.append(f)
                    changed = True

    need = [av for av in av_files if av not in db or db[av].get("failed")]
    _log(log, f"[数据库] 当前 {len(db)} 条记录,本次需联网查询 {len(need)} 个模型。")

    ok = 0
    fail = 0
    total = len(need)
    for i, av in enumerate(need, 1):
        if cancel and cancel.is_set():
            _log(log, "[数据库] 已取消,保存当前进度。")
            break
        entry, err = query_by_autov2(av)
        if entry is None:
            fail += 1
            db[av] = {
                "file": av_files.get(av, []),
                "modelName": "",
                "AIR": None,
                "modelId": None,
                "modelVersionId": None,
                "type": None,
                "baseModel": None,
                "baseModelType": None,
                "queriedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "failed": True,
                "error": err,
            }
            _log(log, f"    [{i}/{total}] 失败 {av}: {err}")
        else:
            ok += 1
            entry["file"] = av_files.get(av, [])
            entry["queriedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            db[av] = entry
            _log(log, f"    [{i}/{total}] OK {av}  {entry['modelName']}")
        if progress:
            progress(i, total)

    save_db(db, db_path)
    _log(log, f"[数据库] 完成:新增 {ok} 条,失败 {fail} 条,已保存 {len(db)} 条记录。")
    return db


# --------------------------------------------------------------------------
# 高层:注入 AIR 到图片
# --------------------------------------------------------------------------
def collect_airs(selected_models, db):
    """按 selected_models 顺序收集 AIR。返回 (airs, missing)。"""
    airs = []
    missing = []
    for m in selected_models:
        entry = db.get(m["AutoV2"])
        if entry and not entry.get("failed") and entry.get("AIR"):
            airs.append(entry["AIR"])
        else:
            missing.append(m["file"])
    return airs, missing


def inject_images(selected_models, input_dir, output_dir, db_path=None,
                  suffix="_comfy", log=None, cancel=None, progress=None):
    """把选定模型(顺序即写入顺序)的 AIR 注入 input_dir 每张 PNG,输出到 output_dir。

    返回 (成功数, 总数)。
    """
    db_path = db_path or default_db_path()
    db = load_db(db_path)
    if not db:
        _log(log, "[错误] 数据库为空,请先在 ② 更新数据库。")
        return 0, 0

    airs, missing = collect_airs(selected_models, db)
    for fname in missing:
        _log(log, f"[警告] {fname} 在数据库里没有可用 AIR,已跳过。")
    if not airs:
        _log(log, "[错误] 没有可写入的 AIR(请先更新数据库)。")
        return 0, 0

    _log(log, f"[AIR] 将按勾选顺序写入 {len(airs)} 个资源引用。")
    os.makedirs(output_dir, exist_ok=True)

    pngs = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".png"))
    if not pngs:
        _log(log, "[警告] 目标文件夹里没有 PNG 图片。")
        return 0, 0
    _log(log, f"[写入] 目标文件夹共 {len(pngs)} 张图片 ...")

    ok = 0
    total = len(pngs)
    for i, fn in enumerate(pngs, 1):
        if cancel and cancel.is_set():
            _log(log, "[写入] 已取消。")
            break
        src = os.path.join(input_dir, fn)
        try:
            chunks = read_png_chunks(src)
            prompt_text = chunks.get("prompt", "")
            workflow_text = chunks.get("workflow", "")
            if not prompt_text:
                _log(log, f"[跳过] {fn}: 没有 prompt 数据。")
            else:
                if not workflow_text:
                    # 没有 workflow chunk 时生成一个最小的,只放 extra.airs
                    workflow_text = json.dumps({"extra": {"airs": []}}, ensure_ascii=False)
                new_wf, new_prompt = inject_workflow_airs(prompt_text, workflow_text, airs)
                base, ext = os.path.splitext(fn)
                dst = os.path.join(output_dir, base + suffix + ext)
                save_png_with_metadata(src, dst, new_prompt, new_wf)
                ok += 1
                _log(log, f"[OK] {fn} -> {os.path.basename(dst)}")
        except Exception as e:
            _log(log, f"[错误] {fn}: {e}")
        if progress:
            progress(i, total)

    _log(log, f"[完成] 成功处理 {ok}/{total} 张。输出目录: {output_dir}")
    return ok, total


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------
def _log(log, msg):
    if log:
        log(msg)
