#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai 数据导入助手 — 图形界面 (tkinter / ttk)

三大功能对应三个标签页:
    ① 本地模型      —— 按本地绝对路径筛选/查询数据库里的模型
    ② 更新数据库    —— 扫描模型路径、增量联网查询 local_config/model_db.json、管理数据库条目
    ③ 写入图片      —— 从模型库勾选若干模型(顺序即写入顺序),把对应 AIR 写进目标文件夹的 PNG

①③ 共享同一个"模型库勾选池":它显示 local_config/model_db.json 里的全部模型,再加上本次扫描
到的模型(含数据库里还没有的)。顶部"类型 / baseModel"下拉可筛选;③ 有"名称"
输入框,可直接输入模型名/文件名快速过滤后勾选。

运行(在项目目录,使用 uv 虚拟环境):
    uv run civitai_gui.py
"""

import json
import os
import queue
import re
import subprocess
import threading
import time
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import civitai_core as core

CONFIG_PATH = os.path.join(core.local_config_dir(), "gui_config.json")
LOG_PATH = os.path.join(core.local_config_dir(), "app.log")

# 常用默认路径(仅当对应路径在本机存在时用于预填)
DEFAULT_MODEL_FOLDER = r"D:\AI tools\ComfyUI-aki-v3\ComfyUI\models"
DEFAULT_IMAGE_FOLDER = r"C:\Users\25707\Pictures\AI stable diffusion\Civitai publish ComfyUI"

# 开发程序版本号(显示在全局工具栏)
APP_VERSION = "0.1.0"


def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _enable_dpi_awareness():
    """Windows 高分屏下让界面更清晰。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Civitai 数据导入助手")
        self.geometry("1100x800")
        self.minsize(940, 680)

        self.cfg = load_config()
        self._reset_detailed_log()
        saved_geo = self.cfg.get("window_geometry")
        if saved_geo:
            try:
                self.geometry(saved_geo)
            except tk.TclError:
                pass
        self.db_path = tk.StringVar(value=self.cfg.get("db_path", core.default_db_path()))
        self.model_folder = tk.StringVar(value=self.cfg.get("model_folder", ""))
        self.query_path = tk.StringVar(value=self.cfg.get("query_path", ""))
        self.image_folder = tk.StringVar(value=self.cfg.get("image_folder", ""))
        self.output_folder = tk.StringVar(value=self.cfg.get("output_folder", ""))
        self.comfyui_path_var = tk.StringVar(value=self.cfg.get("comfyui_model_path", DEFAULT_MODEL_FOLDER))
        self.use_suffix = tk.BooleanVar(value=True)
        self.suffix_var = tk.StringVar(value="_comfy")

        # 筛选
        self.filter_type = tk.StringVar(value="全部")
        self.filter_base = tk.StringVar(value="全部")
        self.filter_search = tk.StringVar(value="")

        self.models = []            # 扫描结果(本次识别到的模型,含 path/file/AutoV2/SHA256)
        self.pool_items = []        # 模型库勾选池(数据库全部模型 ∪ 本次扫描),统一按 AutoV2 标识
        self.selected_avs = []      # 勾选的 AutoV2 列表,顺序即写入顺序

        # 表格排序状态(①②③ 各自独立):(排序列, 是否倒序)。默认按 baseModel 升序。
        self.sort1_col = "base"
        self.sort1_rev = False
        self.sort2_col = "base"
        self.sort2_rev = False
        self.sort3_col = "base"
        self.sort3_rev = False
        self._sortable1 = {"file", "autov2", "status", "name", "type", "base"}
        self._sortable2 = {"file", "autov2", "status", "name", "type", "base", "air"}
        self._sortable3 = {"file", "autov2", "air", "name", "type", "base"}

        self.db = core.load_db(self.db_path.get())
        self.task_queue = queue.Queue()
        self._running = False
        self._cancel = threading.Event()
        self._action_buttons = []

        self._apply_default_folders()
        self._build_style()
        self._build_ui()
        self._rebuild_pool()
        self._startup_self_check()
        self._poll_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 初始化辅助
    # ------------------------------------------------------------------
    def _apply_default_folders(self):
        defaults = {
            "model_folder": self.comfyui_path_var.get().strip() or DEFAULT_MODEL_FOLDER,
            "image_folder": DEFAULT_IMAGE_FOLDER,
        }
        for attr, path in defaults.items():
            var = getattr(self, attr)
            if not var.get().strip() and os.path.isdir(path):
                var.set(path)

    def _build_style(self):
        if os.name == "nt":
            try:
                self.option_add("*Font", ("Microsoft YaHei UI", 9))
            except Exception:
                pass
        try:
            style = ttk.Style(self)
            if "vista" in style.theme_names():
                style.theme_use("vista")
            # 列表行高调大,避免中文字体被上下挤压裁切
            # 卡片头变大
            style.configure(
                "TNotebook",
                tabmargins=(8, 8, 8, 8),
            )
            style.configure(
                "TNotebook.Tab",
                font=("Microsoft YaHei UI", 12, "bold"),
                padding=(15, 9),
            )
            style.configure("Treeview", rowheight=32, font=("Microsoft YaHei UI", 9))
            style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        except Exception:
            pass

    def _style_tree(self, tree):
        """给列表条目加浅灰斑马纹,在视觉上分隔每一条。"""
        try:
            tree.tag_configure("alt", background="#f0f0f0")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self, padding=(10, 8, 10, 8))
        main.pack(fill="both", expand=True)

        # 全局工具栏:ComfyUI 默认模型路径 / 版本号 / 刷新数据库
        global_bar = ttk.Frame(main)
        global_bar.pack(fill="x", pady=(0, 6))

        row1 = ttk.Frame(global_bar)
        row1.pack(fill="x")
        ttk.Label(row1, text="ComfyUI模型路径:").pack(side="left")
        ttk.Entry(row1, textvariable=self.comfyui_path_var, width=44).pack(
            side="left", fill="x", expand=True, padx=4)
        b_comfy = ttk.Button(row1, text="浏览...", command=self._browse_comfyui_path)
        b_comfy.pack(side="left")
        self._action_buttons.append(b_comfy)
        ttk.Label(row1, text=f"版本 v{APP_VERSION}").pack(side="left", padx=(12, 0))

        row2 = ttk.Frame(global_bar)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="刷新数据库:").pack(side="left")
        b_refresh = ttk.Button(row2, text="刷新", command=self._global_refresh)
        b_refresh.pack(side="left", padx=(4, 0))
        self._action_buttons.append(b_refresh)
        ttk.Button(row2, text="打开日志", command=self._open_log_file).pack(
            side="left", padx=(12, 0))

        row3 = ttk.Frame(global_bar)
        row3.pack(fill="x", pady=(4, 0))
        ttk.Label(row3, text="筛选栏:").pack(side="left")
        ttk.Label(row3, text="类型:").pack(side="left", padx=(12, 2))
        self.type_combo_global = ttk.Combobox(
            row3, textvariable=self.filter_type, state="readonly", width=14)
        self.type_combo_global.pack(side="left", padx=(0, 10))
        self.type_combo_global.bind("<<ComboboxSelected>>", self._on_filter_change)
        ttk.Label(row3, text="baseModel:").pack(side="left")
        self.base_combo_global = ttk.Combobox(
            row3, textvariable=self.filter_base, state="readonly", width=18)
        self.base_combo_global.pack(side="left", padx=(2, 10))
        self.base_combo_global.bind("<<ComboboxSelected>>", self._on_filter_change)
        ttk.Label(row3, text="名称:").pack(side="left")
        entry_search = ttk.Entry(row3, textvariable=self.filter_search, width=20)
        entry_search.pack(side="left", padx=(2, 10))
        entry_search.bind("<KeyRelease>", self._on_filter_change)

        nb = ttk.Notebook(main)
        nb.pack(fill="both", expand=True)

        self.tab_scan = ttk.Frame(nb, padding=8)
        self.tab_db = ttk.Frame(nb, padding=8)
        self.tab_inject = ttk.Frame(nb, padding=8)
        nb.add(self.tab_scan, text=" ① 本地模型 ")
        nb.add(self.tab_db, text=" ② 更新数据库 ")
        nb.add(self.tab_inject, text=" ③ 写入图片 ")

        self._build_tab_scan()
        self._build_tab_db()
        self._build_tab_inject()

        # 底部日志 + 状态栏
        log_frame = ttk.LabelFrame(main, text=" 日志 ", padding=4)
        log_frame.pack(fill="both", pady=(8, 4))
        self.log_text = tk.Text(log_frame, height=8, wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        status_frame = ttk.Frame(main)
        status_frame.pack(fill="x", pady=(2, 0))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(status_frame, mode="determinate", maximum=100, value=0)
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.cancel_btn = ttk.Button(status_frame, text="取消", command=self._do_cancel, state="disabled")
        self.cancel_btn.pack(side="right")

    def _build_tab_scan(self):
        f = self.tab_scan
        row0 = ttk.Frame(f)
        row0.pack(fill="x")
        ttk.Label(row0, text="查询路径模型:").pack(side="left")
        ttk.Entry(row0, textvariable=self.query_path).pack(side="left", fill="x", expand=True, padx=6)
        b = ttk.Button(row0, text="浏览...", command=self._browse_query_path)
        b.pack(side="left")
        bfilter = ttk.Button(row0, text="筛选", command=self._on_filter_change)
        bfilter.pack(side="left", padx=(6, 0))
        self._action_buttons += [b, bfilter]
        ttk.Label(row0, text="(留空显示全部,可输入本地绝对路径)").pack(side="left", padx=8)

        cols = ("web", "file", "autov2", "status", "name", "type", "base")
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.tree1 = ttk.Treeview(tree_frame, columns=cols, show="headings", height=13)
        specs = [("web", "网页", 56), ("file", "文件名", 240), ("autov2", "AutoV2", 90),
                 ("status", "状态", 80), ("name", "模型名", 220), ("type", "类型", 90),
                 ("base", "baseModel", 110)]
        for cid, text, w in specs:
            self.tree1.heading(cid, text=text)
            self.tree1.column(cid, width=w, anchor="w")
        self.tree1.column("web", anchor="center")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree1.yview)
        self.tree1.configure(yscrollcommand=sb.set)
        self.tree1.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # 排序:记录列定义,绑定表头点击
        self._cols1 = cols
        self._heading_text1 = {cid: text for cid, text, _w in specs}
        self.tree1.bind("<Button-1>", self._on_tree1_click)
        self.tree1.bind("<Button-3>", self._on_tree1_context_menu)

    def _build_tab_db(self):
        f = self.tab_db
        row1 = ttk.Frame(f)
        row1.pack(fill="x")
        ttk.Label(row1, text="模型路径:").pack(side="left")
        ttk.Entry(row1, textvariable=self.model_folder).pack(side="left", fill="x", expand=True, padx=6)
        b3 = ttk.Button(row1, text="浏览...", command=self._browse_model_folder)
        b3.pack(side="left")
        b4 = ttk.Button(row1, text="开始扫描模型", command=self._do_scan)
        b4.pack(side="left", padx=(6, 0))
        self._action_buttons += [b3, b4]

        self.db_info_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.db_info_var).pack(anchor="w", pady=(8, 4))

        tool = ttk.Frame(f)
        tool.pack(fill="x")
        ttk.Label(tool, text="数据库条目:").pack(side="left")
        b_edit = ttk.Button(tool, text="编辑选中", command=self._edit_db_selected)
        b_edit.pack(side="left", padx=(12, 6))
        b_del = ttk.Button(tool, text="删除选中", command=self._delete_db_selected)
        b_del.pack(side="left")
        self._action_buttons += [b_edit, b_del]

        cols = ("file", "autov2", "name", "air", "type", "base", "status", "op")
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.tree2 = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        specs = [("file", "文件名", 200), ("autov2", "AutoV2", 90), ("name", "模型名", 200),
                 ("air", "AIR", 160), ("type", "类型", 85), ("base", "baseModel", 100),
                 ("status", "状态", 75), ("op", "操作", 90)]
        for cid, text, w in specs:
            self.tree2.heading(cid, text=text)
            self.tree2.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree2.yview)
        self.tree2.configure(yscrollcommand=sb.set)
        self.tree2.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree2.bind("<Button-1>", self._on_tree2_click)
        self.tree2.bind("<Button-3>", self._on_tree2_context_menu)
        self._cols2 = cols
        self._heading_text2 = {cid: text for cid, text, _w in specs}

        ttk.Label(f, text="说明:「开始扫描模型」按文件名登记/补录路径;「增量校验(哈希)」只处理待哈希记录;「全量校验(哈希)」重新计算所有记录;「新增联网查询」只查缺失 AIR。"
                          "数据库条目支持选中后编辑/删除。").pack(anchor="w", pady=(6, 2))

        btn_frame = ttk.Frame(f)
        btn_frame.pack(anchor="e", pady=(6, 0))
        b_full = ttk.Button(btn_frame, text="全量校验（哈希）", command=self._do_full_hash_verify)
        b_full.pack(side="left", padx=(0, 6))
        b_inc = ttk.Button(btn_frame, text="增量校验（哈希）", command=self._do_incremental_hash)
        b_inc.pack(side="left", padx=(0, 6))
        b_query = ttk.Button(btn_frame, text="新增联网查询", command=self._do_query_airs)
        b_query.pack(side="left", padx=(0, 6))
        b_local = ttk.Button(btn_frame, text="更新本地位置", command=self._do_update_local_paths)
        b_local.pack(side="left")
        self._action_buttons += [b_full, b_inc, b_query, b_local]

    def _build_tab_inject(self):
        f = self.tab_inject
        row0 = ttk.Frame(f)
        row0.pack(fill="x")
        ttk.Label(row0, text="目标图片文件夹:").pack(side="left")
        ttk.Entry(row0, textvariable=self.image_folder).pack(side="left", fill="x", expand=True, padx=6)
        b0 = ttk.Button(row0, text="浏览...", command=self._browse_image_folder)
        b0.pack(side="left")
        self._action_buttons.append(b0)

        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=(4, 0))
        ttk.Label(row1, text="输出文件夹:").pack(side="left")
        ttk.Entry(row1, textvariable=self.output_folder).pack(side="left", fill="x", expand=True, padx=6)
        b1 = ttk.Button(row1, text="浏览...", command=self._browse_output_folder)
        b1.pack(side="left")
        self._action_buttons.append(b1)

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row2, text="文件名加后缀", variable=self.use_suffix).pack(side="left")
        ttk.Entry(row2, textvariable=self.suffix_var, width=12).pack(side="left", padx=4)
        ttk.Label(row2, text="(默认 _comfy → 生成 xxx_comfy.png)").pack(side="left", padx=4)

        ttk.Label(f, text="勾选要写入的模型(点击最左侧勾选列切换,勾选顺序即写入顺序)。\n"
                          "PNG 写入 prompt/workflow。"
                  ).pack(anchor="w", pady=(6, 2))

        tool = ttk.Frame(f)
        tool.pack(fill="x")
        for text, cmd in [("全选", self._select_all), ("反选", self._inverse_select),
                          ("清空", self._clear_select), ("上移", lambda: self._move(-1)),
                          ("下移", lambda: self._move(1))]:
            b = ttk.Button(tool, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self._action_buttons.append(b)

        cols = ("web", "check", "file", "autov2", "air", "name", "type", "base")
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.tree3 = ttk.Treeview(tree_frame, columns=cols, show="headings", height=13)
        specs = [("web", "网页", 52), ("check", "✓", 36), ("file", "文件名", 232),
                 ("autov2", "AutoV2", 85), ("air", "AIR", 130), ("name", "模型名", 190),
                 ("type", "类型", 85), ("base", "baseModel", 100)]
        for cid, text, w in specs:
            self.tree3.heading(cid, text=text)
            self.tree3.column(cid, width=w, anchor="w")
        self.tree3.column("web", anchor="center")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree3.yview)
        self.tree3.configure(yscrollcommand=sb.set)
        self.tree3.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree3.bind("<Button-1>", self._on_pool_click3)
        self.tree3.bind("<Button-3>", self._on_tree3_context_menu)
        # 排序:记录列定义(表头点击在 _on_pool_click3 里一并处理)
        self._cols3 = cols
        self._heading_text3 = {cid: text for cid, text, _w in specs}

        btn = ttk.Button(f, text="写入图片", command=self._do_inject)
        btn.pack(anchor="e", pady=(8, 0))
        self._action_buttons.append(btn)

    # ------------------------------------------------------------------
    # 模型库勾选池(数据库全部模型 ∪ 本次扫描)
    # ------------------------------------------------------------------
    def _build_pool(self):
        """重建模型库池。每项一个 AutoV2:
        {AutoV2, file, files, paths, modelName, AIR, type, baseModel, failed, in_scan}
        """
        items = []
        seen = set()
        # 1) 数据库里的全部模型
        for av, entry in self.db.items():
            if not isinstance(entry, dict):
                continue
            seen.add(av)
            files = entry.get("file") or []
            paths = entry.get("paths") or []
            display = (files[0] if files else
                       (os.path.basename(paths[0]) if paths else
                        (entry.get("modelName") or av)))
            items.append({
                "AutoV2": av,
                "file": display,
                "files": files,
                "paths": list(paths),
                "modelName": entry.get("modelName", "") or "",
                "AIR": entry.get("AIR", "") or "",
                "type": entry.get("type") or "",
                "baseModel": entry.get("baseModel") or "",
                "failed": bool(entry.get("failed")),
                "in_scan": False,
            })
        # 2) 本次扫描到、但数据库还没有的模型(未收录)
        for m in self.models:
            av = m["AutoV2"]
            if av in seen:
                for it in items:
                    if it["AutoV2"] == av:
                        it["in_scan"] = True
                        if m["file"] not in it["files"]:
                            it["files"].append(m["file"])
                        if m.get("path") and m["path"] not in it["paths"]:
                            it["paths"].append(m["path"])
                        if it["file"] == av or not it["file"]:
                            it["file"] = m["file"]
                        break
                continue
            items.append({
                "AutoV2": av,
                "file": m["file"],
                "files": [m["file"]],
                "paths": [m["path"]] if m.get("path") else [],
                "modelName": "",
                "AIR": "",
                "type": "",
                "baseModel": "",
                "failed": False,
                "in_scan": True,
            })
        items.sort(key=lambda it: (it["baseModel"] or "", it["type"] or "", it["file"] or ""))
        return items

    def _rebuild_pool(self):
        self.pool_items = self._build_pool()
        self._refresh_filter_options()
        self._refresh_all()

    def _visible_pool(self, path_filter="", search="", sort_col="file", sort_rev=False):
        """按当前筛选条件返回可见的池子列表,并按键值排序。

        path_filter 为本地绝对路径过滤(留空不过滤);search 为模型名/文件名子串过滤(不区分大小写)。
        sort_col 为排序列(映射见 _sort_key),sort_rev 为是否倒序。
        """
        t = self.filter_type.get()
        b = self.filter_base.get()
        q = (search or "").strip().lower()
        p = (path_filter or "").strip()
        items = []
        for it in self.pool_items:
            if p and not any(self._path_matches(path, p) for path in it.get("paths") or []):
                continue
            if t and t != "全部" and it["type"] != t:
                continue
            if b and b != "全部" and it["baseModel"] != b:
                continue
            if q and q not in (it["modelName"] or "").lower() and q not in (it["file"] or "").lower():
                continue
            items.append(it)
        items.sort(key=lambda it: self._sort_key(it, sort_col), reverse=sort_rev)
        return items

    @staticmethod
    def _path_matches(path, folder):
        """判断本地绝对路径是否等于 folder 或位于 folder 目录下(包含子文件夹)。"""
        if not path or not folder:
            return False
        p = os.path.normpath(path).lower()
        f = os.path.normpath(folder).rstrip("\\/").lower()
        if not f:
            return False
        return p == f or p.startswith(f + os.sep)

    def _refresh_filter_options(self):
        types = sorted({it["type"] for it in self.pool_items if it["type"]})
        bases = sorted({it["baseModel"] for it in self.pool_items if it["baseModel"]})
        for combo in (getattr(self, "type_combo_global", None),):
            if combo is not None:
                combo["values"] = ["全部"] + types
                if combo.get() not in combo["values"]:
                    combo.set("全部")
        for combo in (getattr(self, "base_combo_global", None),):
            if combo is not None:
                combo["values"] = ["全部"] + bases
                if combo.get() not in combo["values"]:
                    combo.set("全部")

    def _on_filter_change(self, event=None):
        self._refresh_all()

    # ------------------------------------------------------------------
    # 表格排序(①③ 表头点击,各自独立状态)
    # ------------------------------------------------------------------
    def _sort_key(self, it, col):
        """把表格列 id 映射成排序键(小写,便于按字母序)。"""
        if col == "status":
            return self._item_status(it).lower()
        key = {
            "file": "file", "autov2": "AutoV2", "name": "modelName",
            "type": "type", "base": "baseModel", "air": "AIR",
        }.get(col, "file")
        val = it.get(key)
        return str(val).lower() if val is not None else ""

    def _header_col(self, tree, cols, event):
        """表头点击事件 → 返回列 id;不是表头点击则返回 None。"""
        if tree.identify("region", event.x, event.y) != "heading":
            return None
        cid = tree.identify_column(event.x)
        if not cid.startswith("#"):
            return None
        try:
            idx = int(cid[1:]) - 1
        except ValueError:
            return None
        if 0 <= idx < len(cols):
            return cols[idx]
        return None

    def _toggle_sort(self, which, col):
        """切换某张表的排序列/方向:同列再点 → 升/倒序互换;新列 → 升序。"""
        if which == "tab1":
            sortable = self._sortable1
        elif which == "tab2":
            sortable = self._sortable2
        else:
            sortable = self._sortable3
        if col not in sortable:
            return
        if which == "tab1":
            if self.sort1_col == col:
                self.sort1_rev = not self.sort1_rev
            else:
                self.sort1_col = col
                self.sort1_rev = False
        elif which == "tab2":
            if self.sort2_col == col:
                self.sort2_rev = not self.sort2_rev
            else:
                self.sort2_col = col
                self.sort2_rev = False
        else:
            if self.sort3_col == col:
                self.sort3_rev = not self.sort3_rev
            else:
                self.sort3_col = col
                self.sort3_rev = False

    def _update_headings(self, tree, cols, base_texts, sort_col, sort_rev):
        """在表头文字上标出当前排序列与方向(▲升序 / ▼倒序)。"""
        for col in cols:
            text = base_texts.get(col, "")
            if col == sort_col:
                text += " ▼" if sort_rev else " ▲"
            tree.heading(col, text=text)

    def _on_tree1_click(self, event):
        """卡片①:表头点击 → 排序;网页列(#1)点击 → 打开 Civitai 网页。"""
        tree = self.tree1
        region = tree.identify("region", event.x, event.y)
        if region == "heading":
            col = self._header_col(tree, self._cols1, event)
            if col is not None:
                self._toggle_sort("tab1", col)
                self._refresh_tab1()
            return
        if region not in ("cell", "tree"):
            return
        if tree.identify_column(event.x) != "#1":
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        idx = tree.index(row)
        vis = self._visible1()
        if 0 <= idx < len(vis):
            self._open_model_web(vis[idx])

    def _on_tree2_click(self, event):
        """卡片②:表头点击 → 排序。"""
        tree = self.tree2
        region = tree.identify("region", event.x, event.y)
        if region == "heading":
            col = self._header_col(tree, self._cols2, event)
            if col is not None:
                self._toggle_sort("tab2", col)
                self._refresh_tab2()

    def _on_tree1_context_menu(self, event):
        self._show_row_context_menu(event, self.tree1, self._visible1)

    def _on_tree2_context_menu(self, event):
        self._show_row_context_menu(event, self.tree2, self._visible2)

    def _on_tree3_context_menu(self, event):
        self._show_row_context_menu(event, self.tree3, self._visible3)

    def _show_row_context_menu(self, event, tree, visible_func):
        """表格右键菜单:复制名称 / 在文件资源管理器中打开 / 打开网页链接。"""
        row = tree.identify_row(event.y)
        if not row:
            return
        tree.selection_set(row)
        idx = tree.index(row)
        vis = visible_func()
        if not (0 <= idx < len(vis)):
            return
        item = vis[idx]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="复制名称", command=lambda: self._copy_item_name(item))
        menu.add_command(label="在文件资源管理器中打开", command=lambda: self._open_in_explorer(item))
        menu.add_command(label="打开网页链接", command=lambda: self._open_model_web(item))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_item_name(self, item):
        name = item.get("modelName") or item.get("file") or item.get("AutoV2") or ""
        self.clipboard_clear()
        self.clipboard_append(name)
        self.update_idletasks()
        self._append_log(f"[复制] {name}")

    def _open_in_explorer(self, item):
        paths = item.get("paths") or []
        if not paths:
            entry = self.db.get(item["AutoV2"])
            if entry:
                paths = entry.get("paths") or []

        for p in paths:
            if not p:
                continue
            # 兼容正斜杠 / 反斜杠、重复分隔符等路径写法
            candidates = [p]
            norm = os.path.normpath(p)
            if norm != p:
                candidates.append(norm)
            norm_win = norm.replace("/", "\\")
            if norm_win not in candidates:
                candidates.append(norm_win)

            for cand in candidates:
                if os.path.exists(cand):
                    folder = os.path.dirname(cand)
                    try:
                        if os.name == "nt":
                            os.startfile(folder)
                        else:
                            subprocess.Popen(["xdg-open", folder])
                        return
                    except Exception as e:
                        messagebox.showerror(
                            "错误",
                            f"无法打开文件夹:\n{folder}\n\n详细错误:\n{e}"
                        )
                        return

        messagebox.showinfo(
            "提示",
            "该记录没有可用的本地路径，或路径不存在。\n"
            "请检查 local_config/model_db.json 中该记录的 paths 字段。"
        )

    def _visible1(self):
        """卡片①当前筛选后的可见模型列表(按当前排序),支持全局筛选。"""
        return self._visible_pool(
            path_filter=self.query_path.get(),
            search=self.filter_search.get(),
            sort_col=self.sort1_col, sort_rev=self.sort1_rev,
        )

    def _visible2(self):
        """卡片②当前可见的数据库条目列表(按当前排序),支持全局筛选。"""
        t = self.filter_type.get()
        b = self.filter_base.get()
        q = self.filter_search.get().strip().lower()
        items = []
        for av, entry in self.db.items():
            if not isinstance(entry, dict):
                continue
            files = entry.get("file") or []
            paths = entry.get("paths") or []
            display = (files[0] if files else
                       (os.path.basename(paths[0]) if paths else
                        (entry.get("modelName") or av)))
            it = {
                "AutoV2": av,
                "file": display,
                "files": files,
                "paths": entry.get("paths") or [],
                "modelName": entry.get("modelName", "") or "",
                "AIR": entry.get("AIR", "") or "",
                "type": entry.get("type") or "",
                "baseModel": entry.get("baseModel") or "",
                "failed": bool(entry.get("failed")),
            }
            if t and t != "全部" and it["type"] != t:
                continue
            if b and b != "全部" and it["baseModel"] != b:
                continue
            if q:
                name_hit = q in (it["modelName"] or "").lower()
                file_hit = any(q in f.lower() for f in files)
                if not name_hit and not file_hit:
                    continue
            items.append(it)
        items.sort(key=lambda it: self._sort_key(it, self.sort2_col), reverse=self.sort2_rev)
        return items

    def _open_model_web(self, item):
        """在默认浏览器打开该模型的 Civitai 网页(带版本号)。"""
        entry = self.db.get(item["AutoV2"])
        url = core.model_url(entry) if entry else ""
        if not url:
            messagebox.showinfo("提示", "该模型还没有 Civitai 网页链接(未收录或查询失败)。")
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("错误", f"打开网页失败: {e}")

    def _item_status(self, it):
        av = it["AutoV2"]
        if core.is_pending_key(av):
            return "待哈希"
        if av not in self.db:
            return "未收录"
        entry = self.db[av]
        if entry.get("failed"):
            return "查询失败"
        if entry.get("AIR"):
            return "已收录"
        return "无AIR"

    def _refresh_all(self):
        self._refresh_tab1()
        self._refresh_tab2()
        self._refresh_tab3()
        self._refresh_db_info()

    def _refresh_tab1(self):
        self.tree1.delete(*self.tree1.get_children())
        n = 0
        for it in self._visible1():
            status = self._item_status(it)
            av_disp = "待哈希" if core.is_pending_key(it["AutoV2"]) else it["AutoV2"]
            self.tree1.insert("", "end", values=(
                "🔗", it["file"], av_disp, status,
                it["modelName"], it["type"], it["baseModel"],
            ), tags=("alt",) if n % 2 else ())
            n += 1
        self._update_headings(self.tree1, self._cols1, self._heading_text1,
                              self.sort1_col, self.sort1_rev)
        lbl = getattr(self, "_count_label1", None)
        if lbl:
            lbl.config(text=f"显示 {n} / 模型库 {len(self.pool_items)}")

    def _refresh_tab2(self):
        self.tree2.delete(*self.tree2.get_children())
        n = 0
        for it in self._visible2():
            status = self._item_status(it)
            av_disp = "待哈希" if core.is_pending_key(it["AutoV2"]) else it["AutoV2"]
            air = it["AIR"]
            air_disp = (air[:20] + "…") if len(air) > 20 else air
            self.tree2.insert("", "end", values=(
                it["file"], av_disp, it["modelName"], air_disp,
                it["type"], it["baseModel"], status, "编辑/删除",
            ), tags=("alt",) if n % 2 else ())
            n += 1
        self._update_headings(self.tree2, self._cols2, self._heading_text2,
                              self.sort2_col, self.sort2_rev)
        lbl = getattr(self, "_count_label2", None)
        if lbl:
            lbl.config(text=f"显示 {n} / 数据库 {len(self.db)}")

    def _refresh_tab3(self):
        self.tree3.delete(*self.tree3.get_children())
        n = 0
        for it in self._visible3():
            av = it["AutoV2"]
            if av in self.selected_avs:
                order = self.selected_avs.index(av) + 1
                check = f"☑ {order}"
            else:
                check = "☐"
            av_disp = "待哈希" if core.is_pending_key(av) else av
            air = it["AIR"]
            air_disp = (air[:20] + "…") if len(air) > 20 else air
            self.tree3.insert("", "end", values=(
                "🔗", check, it["file"], av_disp, air_disp,
                it["modelName"], it["type"], it["baseModel"],
            ), tags=("alt",) if n % 2 else ())
            n += 1
        self._update_headings(self.tree3, self._cols3, self._heading_text3,
                              self.sort3_col, self.sort3_rev)
        lbl = getattr(self, "_count_label3", None)
        if lbl:
            lbl.config(text=f"显示 {n} / 模型库 {len(self.pool_items)}")

    def _refresh_db_info(self):
        stats = core.db_stats(self.db, [it["AutoV2"] for it in self.pool_items])
        self.db_info_var.set(
            f"当前数据库共 {len(self.db)} 条记录  |  模型库共 {len(self.pool_items)} 个模型: "
            f"已收录 {stats['in_db']} · 待查询 {stats['to_query']} (其中失败 {stats['failed']})"
        )

    # ------------------------------------------------------------------
    # 后台任务框架
    # ------------------------------------------------------------------
    def _run_task(self, target, on_done=None):
        if self._running:
            messagebox.showinfo("提示", "已有任务在运行,请等待完成或点击「取消」。")
            return
        self._running = True
        self._cancel = threading.Event()
        self.progress.config(maximum=100, value=0)
        self.status_var.set("处理中 ...")
        self.set_busy(True)

        def wrap():
            try:
                result = target()
                self.task_queue.put(("done", result, on_done))
            except Exception as e:
                self.task_queue.put(("error", e, None))

        threading.Thread(target=wrap, daemon=True).start()

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in self._action_buttons:
            try:
                b.config(state=state)
            except Exception:
                pass
        self.cancel_btn.config(state="normal" if busy else "disabled")

    def _do_cancel(self):
        self._cancel.set()
        self._append_log("[操作] 已请求取消,等待当前步骤结束 ...")

    def _poll_queue(self):
        try:
            while True:
                kind, *payload = self.task_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload[0])
                elif kind == "progress":
                    i, total = payload
                    if total:
                        self.progress.config(maximum=total, value=i)
                        self.status_var.set(f"处理中 {i}/{total}")
                elif kind == "done":
                    result, cb = payload
                    self._running = False
                    self.set_busy(False)
                    self.status_var.set("完成")
                    if cb:
                        try:
                            cb(result)
                        except Exception as e:
                            self._append_log(f"[异常] 完成回调出错: {e}")
                elif kind == "error":
                    err = payload[0]
                    self._running = False
                    self.set_busy(False)
                    self.status_var.set("出错")
                    self._append_log(f"[异常] {err}")
                    messagebox.showerror("错误", str(err))
        except queue.Empty:
            pass
        self.after(60, self._poll_queue)

    def _reset_detailed_log(self):
        """每次启动时重置详细日志文件。"""
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动日志已重置\n")
        except Exception:
            pass

    def _write_detailed_log(self, msg):
        """把详细日志写入 local_config/app.log。"""
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def _is_ui_summary(self, msg):
        """判断一条日志是否需要在 UI 中显示一句话概括。"""
        keywords = ("完成", "启动自检", "[操作]", "[错误]", "[警告]", "[刷新]", "[异常]")
        return any(k in msg for k in keywords)

    def _append_log(self, msg):
        # 所有日志都写入详细文件
        self._write_detailed_log(msg)
        # UI 只显示一句话概括
        if self._is_ui_summary(msg):
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")

    def _startup_self_check(self):
        """启动自检:检查数据库完整性,详细写入日志,UI 只显示汇总。"""
        issues = core.check_db_integrity(self.db)
        if not issues:
            self._write_detailed_log("[自检] 数据库完整性检查通过,未发现异常。")
            self._append_log("[自检] 启动自检完成:数据库正常")
            return
        for issue in issues:
            self._write_detailed_log(
                f"[自检][WARN] 记录 {issue['key']} ({issue['name']}) "
                f"{issue['issue']};建议:{issue['suggestion']}"
            )
        self._append_log(
            f"[自检] 启动自检完成:发现 {len(issues)} 条不完整数据,"
            f"详见 local_config/app.log"
        )

    def _progress_cb(self, i, total):
        self.task_queue.put(("progress", i, total))

    # ------------------------------------------------------------------
    # 勾选交互(①③ 共享勾选状态)
    # ------------------------------------------------------------------
    def _on_pool_click3(self, event):
        """卡片③:表头点击 → 排序;网页列(#1)点击 → 打开 Civitai 网页;勾选列(#2)点击 → 切换选中。"""
        tree = self.tree3
        region = tree.identify("region", event.x, event.y)
        if region == "heading":
            col = self._header_col(tree, self._cols3, event)
            if col is not None:
                self._toggle_sort("tab3", col)
                self._refresh_tab3()
            return
        if region not in ("cell", "tree"):
            return
        cid = tree.identify_column(event.x)
        row = tree.identify_row(event.y)
        if not row:
            return
        idx = tree.index(row)
        vis = self._visible3()
        if not (0 <= idx < len(vis)):
            return
        if cid == "#1":  # 网页列
            self._open_model_web(vis[idx])
            return
        if cid != "#2":  # 勾选列
            return
        av = vis[idx]["AutoV2"]
        if av in self.selected_avs:
            self.selected_avs.remove(av)
        else:
            self.selected_avs.append(av)
        self._refresh_all()

    def _visible3(self):
        """卡片③当前筛选后的可见模型列表(按当前排序)。"""
        return self._visible_pool(search=self.filter_search.get(),
                                  sort_col=self.sort3_col, sort_rev=self.sort3_rev)

    def _select_all(self):
        self.selected_avs = [it["AutoV2"] for it in self._visible3()]
        self._refresh_all()

    def _inverse_select(self):
        sel = set(self.selected_avs)
        vis = [it["AutoV2"] for it in self._visible3()]
        new = [av for av in self.selected_avs if av not in vis]
        for av in vis:
            if av not in sel:
                new.append(av)
        self.selected_avs = new
        self._refresh_all()

    def _clear_select(self):
        self.selected_avs = []
        self._refresh_all()

    def _move(self, delta):
        if len(self.selected_avs) < 2:
            return
        sel = self.tree3.selection()
        if not sel:
            return
        idx = self.tree3.index(sel[0])
        vis = self._visible3()
        if not (0 <= idx < len(vis)):
            return
        av = vis[idx]["AutoV2"]
        if av not in self.selected_avs:
            return
        pos = self.selected_avs.index(av)
        newpos = pos + delta
        if not (0 <= newpos < len(self.selected_avs)):
            return
        self.selected_avs[pos], self.selected_avs[newpos] = \
            self.selected_avs[newpos], self.selected_avs[pos]
        self._refresh_all()
        items = self.tree3.get_children()
        if 0 <= idx < len(items):
            self.tree3.selection_set(items[idx])

    # ------------------------------------------------------------------
    # ① 本地模型查询 / ② 扫描
    # ------------------------------------------------------------------
    def _browse_query_path(self):
        d = filedialog.askdirectory(initialdir=self.query_path.get() or os.path.expanduser("~"))
        if d:
            self.query_path.set(d)
            self._refresh_tab1()

    def _browse_model_folder(self):
        d = filedialog.askdirectory(initialdir=self.model_folder.get() or os.path.expanduser("~"))
        if d:
            self.model_folder.set(d)

    def _browse_comfyui_path(self):
        d = filedialog.askdirectory(initialdir=self.comfyui_path_var.get() or os.path.expanduser("~"))
        if d:
            self.comfyui_path_var.set(d)
            self.cfg["comfyui_model_path"] = d
            save_config(self.cfg)

    def _open_log_file(self):
        """打开详细日志文件 local_config/app.log。"""
        try:
            os.startfile(LOG_PATH)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开日志文件:\n{e}")

    def _global_refresh(self):
        """刷新数据库,并重置排序/筛选/勾选状态,尽量恢复成刚打开程序的样子。"""
        self.db = core.load_db(self.db_path.get())
        self.models = []
        self.selected_avs = []
        self.filter_type.set("全部")
        self.filter_base.set("全部")
        self.filter_search.set("")
        self.sort1_col = "base"
        self.sort1_rev = False
        self.sort2_col = "base"
        self.sort2_rev = False
        self.sort3_col = "base"
        self.sort3_rev = False
        self.query_path.set(self.cfg.get("query_path", ""))
        self.model_folder.set(self.cfg.get("model_folder", ""))
        self._rebuild_pool()
        self._append_log("[刷新] 已重新加载数据库,排序/筛选/勾选已重置。")

    def _do_scan(self):
        folder = self.model_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("提示", "请先选择一个有效的模型文件夹。")
            return
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)
        self._append_log(f"[操作] 开始扫描模型文件夹(按文件名登记,不计算哈希): {folder}")

        def target():
            return core.scan_and_register_folder(
                folder, db_path=db_path,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(db):
            if db is None:
                return
            self.db = db
            self.models = []
            self.cfg["model_folder"] = folder
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("扫描完成:已按文件名登记本地模型")

        self._run_task(target, on_done)

    # ------------------------------------------------------------------
    # ② 更新数据库
    # ------------------------------------------------------------------
    def _do_full_hash_verify(self):
        """全量校验(哈希):重新计算所有有 paths 的记录,并校验主键是否一致。"""
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)

        def target():
            return core.compute_hashes_from_db(
                db_path=db_path, full=True,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(db):
            if db is None:
                self.status_var.set("全量校验:未执行")
                return
            self.db = db
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("全量校验完成")

        self._run_task(target, on_done)

    def _do_incremental_hash(self):
        """增量校验(哈希):只处理 pending 临时记录,已哈希的记录跳过。"""
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)

        def target():
            return core.compute_hashes_from_db(
                db_path=db_path, full=False,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(db):
            if db is None:
                self.status_var.set("增量校验:未执行")
                return
            self.db = db
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("增量校验完成")

        self._run_task(target, on_done)

    def _do_query_airs(self):
        """新增联网查询:遍历数据库,找到有 AutoV2 但没有 AIR 的记录联网查询。"""
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)

        def target():
            return core.query_missing_airs(
                db_path=db_path,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(db):
            if db is None:
                self.status_var.set("联网查询:未执行")
                return
            self.db = db
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("联网查询完成")

        self._run_task(target, on_done)

    def _do_update_local_paths(self):
        """更新本地位置:从数据库遍历已有 paths,校验路径/文件名,不读取文本框。"""
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)

        def target():
            return core.verify_local_paths(
                db_path=db_path,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(db):
            if db is None:
                self.status_var.set("本地位置更新:未执行")
                return
            self.db = db
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("本地位置更新完成")

        self._run_task(target, on_done)

    def _selected_db_av(self):
        """返回卡片②当前选中行对应的 AutoV2;未选中则提示并返回 None。"""
        sel = self.tree2.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在数据库条目表格中选中一行。")
            return None
        idx = self.tree2.index(sel[0])
        vis = self._visible2()
        if not (0 <= idx < len(vis)):
            return None
        return vis[idx]["AutoV2"]

    def _edit_db_selected(self):
        av = self._selected_db_av()
        if not av or av not in self.db:
            return
        entry = self.db[av]

        dlg = tk.Toplevel(self)
        dlg.title(f"编辑数据库记录 - {av}")
        dlg.geometry("720x620")
        saved_dlg_geo = self.cfg.get("edit_dialog_geometry")
        if saved_dlg_geo:
            try:
                m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", saved_dlg_geo)
                if m:
                    w = int(m.group(1))
                    h = int(m.group(2))
                    x = int(m.group(3))
                    y = int(m.group(4))
                    sw = self.winfo_screenwidth()
                    sh = self.winfo_screenheight()
                    w = min(w, max(600, sw - 80))
                    h = min(h, max(500, sh - 120))
                    x = max(0, min(x, max(0, sw - w - 20)))
                    y = max(0, min(y, max(0, sh - h - 60)))
                    dlg.geometry(f"{w}x{h}+{x}+{y}")
                else:
                    dlg.geometry(saved_dlg_geo)
            except tk.TclError:
                pass
        dlg.transient(self)
        dlg.grab_set()

        tip_frame = ttk.Frame(dlg, padding=(10, 8, 10, 0))
        tip_frame.pack(fill="x")
        ttk.Label(tip_frame, text="字段说明:", font=("Microsoft YaHei UI", 9, "bold"))\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tip_fields = [
            ("file:", "本地文件名列表"),
            ("paths:", "本地绝对路径列表"),
            ("modelName:", "模型名"),
            ("AIR:", "Civitai 资源引用"),
            ("modelId:", "模型 ID"),
            ("modelVersionId:", "模型版本 ID"),
            ("type:", "类型(Checkpoint / LORA / ...)"),
            ("baseModel:", "基础模型"),
            ("baseModelType:", "基础模型类型"),
            ("queriedAt:", "查询时间"),
            ("failed:", "是否查询失败"),
            ("error:", "错误信息"),
            ("url:", "Civitai 网页链接"),
        ]
        for i, (key, desc) in enumerate(tip_fields, start=1):
            ttk.Label(tip_frame, text=key).grid(
                row=i, column=0, sticky="e", padx=(0, 18), pady=2)
            ttk.Label(tip_frame, text=desc).grid(
                row=i, column=1, sticky="w", pady=2)

        text = tk.Text(dlg, wrap="none", undo=True)
        text.insert("1.0", json.dumps(entry, ensure_ascii=False, indent=2))

        def remember_dialog_geometry():
            self.cfg["edit_dialog_geometry"] = dlg.geometry()
            save_config(self.cfg)

        def close_dialog():
            remember_dialog_geometry()
            dlg.destroy()

        def save():
            try:
                new_entry = json.loads(text.get("1.0", "end-1c"))
            except Exception as e:
                messagebox.showerror("错误", f"JSON 格式不正确,无法保存:\n{e}")
                return
            if not isinstance(new_entry, dict):
                messagebox.showerror("错误", "保存内容必须是 JSON 对象。")
                return
            ok, errors = core.validate_db_entry(new_entry)
            if not ok:
                messagebox.showerror("数据库数据不合法", "\n".join(errors))
                return
            self.db[av] = new_entry
            db_path = self.db_path.get().strip() or core.default_db_path()
            self.db_path.set(db_path)
            core.save_db(self.db, db_path)
            self._rebuild_pool()
            self._append_log(f"[数据库] 已编辑记录 {av}")
            remember_dialog_geometry()
            dlg.destroy()

        # 按钮放在文本框上方,确保一定可见
        btn_frame = ttk.Frame(dlg, padding=(10, 6, 10, 0))
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="保存修改", command=save).pack(side="left")
        ttk.Button(btn_frame, text="取消修改", command=close_dialog).pack(side="left", padx=8)
        dlg.protocol("WM_DELETE_WINDOW", close_dialog)

        text.pack(fill="both", expand=True, padx=10, pady=4)

    def _delete_db_selected(self):
        av = self._selected_db_av()
        if not av or av not in self.db:
            return
        name = (self.db[av].get("modelName") or "") or av
        if not messagebox.askyesno("确认删除", f"确定删除数据库记录 {av} 吗?\n\n{name}"):
            return
        del self.db[av]
        if av in self.selected_avs:
            self.selected_avs.remove(av)
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)
        core.save_db(self.db, db_path)
        self._rebuild_pool()
        self._append_log(f"[数据库] 已删除记录 {av}")

    # ------------------------------------------------------------------
    # ③ 写入图片
    # ------------------------------------------------------------------
    def _browse_image_folder(self):
        d = filedialog.askdirectory(initialdir=self.image_folder.get() or os.path.expanduser("~"))
        if d:
            self.image_folder.set(d)
            if not self.output_folder.get().strip():
                self.output_folder.set(os.path.join(d, "_comfy"))

    def _browse_output_folder(self):
        d = filedialog.askdirectory(initialdir=self.output_folder.get() or os.path.expanduser("~"))
        if d:
            self.output_folder.set(d)

    def _do_inject(self):
        if not self.selected_avs:
            messagebox.showwarning("提示", "请先在下方的模型库勾选池里勾选要写入的模型。")
            return
        input_dir = self.image_folder.get().strip()
        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showwarning("提示", "请先选择有效的目标图片文件夹。")
            return
        output_dir = self.output_folder.get().strip()
        if not output_dir:
            output_dir = os.path.join(input_dir, "_comfy")
            self.output_folder.set(output_dir)
        suffix = self.suffix_var.get().strip() or "_comfy"
        if not self.use_suffix.get():
            suffix = ""

        by_av = {it["AutoV2"]: it for it in self.pool_items}
        selected = [by_av[av] for av in self.selected_avs if av in by_av]
        self._append_log(f"[操作] 目标文件夹:{input_dir} | 输出:{output_dir} | 勾选模型 {len(selected)} 个")

        def target():
            try:
                entries = os.listdir(input_dir)
            except OSError as e:
                self._append_log(f"[错误] 无法读取目标文件夹: {e}")
                return {}
            has_png = any(f.lower().endswith(".png") for f in entries)
            if not has_png:
                self._append_log("[警告] 目标文件夹里没有 PNG 图片。")
                return {}
            return {"PNG": core.inject_images(
                selected, input_dir, output_dir,
                db_path=self.db_path.get(), suffix=suffix,
                log=self._append_log, cancel=self._cancel,
                progress=self._progress_cb)}

        def on_done(results):
            self.cfg.update({"image_folder": input_dir, "output_folder": output_dir})
            save_config(self.cfg)
            if results:
                detail = " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in results.items())
                self.status_var.set(f"写入完成:{detail}")
            else:
                self.status_var.set("写入完成:无文件可处理")

        self._run_task(target, on_done)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def _on_close(self):
        self.cfg.update({
            "model_folder": self.model_folder.get(),
            "query_path": self.query_path.get(),
            "image_folder": self.image_folder.get(),
            "output_folder": self.output_folder.get(),
            "comfyui_model_path": self.comfyui_path_var.get(),
            "db_path": self.db_path.get(),
            "window_geometry": self.geometry(),
        })
        save_config(self.cfg)
        self.destroy()


def main():
    _enable_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
