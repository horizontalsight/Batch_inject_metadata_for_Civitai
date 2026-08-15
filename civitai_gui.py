#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai 数据导入助手 — 图形界面 (tkinter / ttk)

三大功能对应三个标签页:
    ① 扫描识别模型  —— 选一个模型文件夹,自动识别里面的模型(SHA256 / AutoV2)
    ② 更新数据库    —— 增量联网查询 model_db.json 中未收录/失败的模型
    ③ 写入图片      —— 从模型库勾选若干模型(顺序即写入顺序),把对应 AIR 写进目标文件夹的 PNG

①③ 共享同一个"模型库勾选池":它显示 model_db.json 里的全部模型,再加上本次扫描
到的模型(含数据库里还没有的)。顶部"类型 / baseModel"下拉可筛选;① 有"仅显示
本次扫描"开关;③ 有"名称"输入框,可直接输入模型名/文件名快速过滤后勾选。

运行(在项目目录,使用 uv 虚拟环境):
    uv run civitai_gui.py
"""

import json
import os
import queue
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import civitai_core as core

CONFIG_PATH = os.path.join(core.script_dir(), "gui_config.json")

# 常用默认路径(仅当对应路径在本机存在时用于预填)
DEFAULT_MODEL_FOLDER = r"D:\AI tools\ComfyUI-aki-v3\ComfyUI\models"
DEFAULT_IMAGE_FOLDER = r"C:\Users\25707\Pictures\AI stable diffusion\Civitai publish ComfyUI"


def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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
        self.db_path = tk.StringVar(value=self.cfg.get("db_path", core.default_db_path()))
        self.model_folder = tk.StringVar(value=self.cfg.get("model_folder", ""))
        self.image_folder = tk.StringVar(value=self.cfg.get("image_folder", ""))
        self.output_folder = tk.StringVar(value=self.cfg.get("output_folder", ""))
        self.recursive = tk.BooleanVar(value=True)
        self.use_suffix = tk.BooleanVar(value=True)
        self.suffix_var = tk.StringVar(value="_comfy")

        # 筛选
        self.filter_type = tk.StringVar(value="全部")
        self.filter_base = tk.StringVar(value="全部")
        self.filter_search = tk.StringVar(value="")
        self.show_scan_only = tk.BooleanVar(value=False)

        self.models = []            # 扫描结果(本次识别到的模型,含 path/file/AutoV2/SHA256)
        self.pool_items = []        # 模型库勾选池(数据库全部模型 ∪ 本次扫描),统一按 AutoV2 标识
        self.selected_avs = []      # 勾选的 AutoV2 列表,顺序即写入顺序

        self.db = core.load_db(self.db_path.get())
        self.task_queue = queue.Queue()
        self._running = False
        self._cancel = threading.Event()
        self._action_buttons = []

        self._apply_default_folders()
        self._build_style()
        self._build_ui()
        self._rebuild_pool()
        self._poll_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 初始化辅助
    # ------------------------------------------------------------------
    def _apply_default_folders(self):
        defaults = {
            "model_folder": DEFAULT_MODEL_FOLDER,
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
            style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 9))
            style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self, padding=(10, 8, 10, 8))
        main.pack(fill="both", expand=True)

        nb = ttk.Notebook(main)
        nb.pack(fill="both", expand=True)

        self.tab_scan = ttk.Frame(nb, padding=8)
        self.tab_db = ttk.Frame(nb, padding=8)
        self.tab_inject = ttk.Frame(nb, padding=8)
        nb.add(self.tab_scan, text=" ① 扫描识别模型 ")
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

    def _build_filter_bar(self, parent, with_scan_switch=False, with_search=False,
                          count_label_attr=None):
        """公共筛选行:类型 / baseModel 下拉,可选名称搜索框 / 仅显示本次扫描开关。"""
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(6, 0))

        ttk.Label(bar, text="类型:").pack(side="left")
        combo_type = ttk.Combobox(bar, textvariable=self.filter_type, state="readonly", width=14)
        combo_type.pack(side="left", padx=(2, 10))
        combo_type.bind("<<ComboboxSelected>>", self._on_filter_change)

        ttk.Label(bar, text="baseModel:").pack(side="left")
        combo_base = ttk.Combobox(bar, textvariable=self.filter_base, state="readonly", width=18)
        combo_base.pack(side="left", padx=(2, 10))
        combo_base.bind("<<ComboboxSelected>>", self._on_filter_change)

        if with_search:
            ttk.Label(bar, text="名称:").pack(side="left")
            entry = ttk.Entry(bar, textvariable=self.filter_search, width=20)
            entry.pack(side="left", padx=(2, 10))
            entry.bind("<KeyRelease>", self._on_filter_change)

        if with_scan_switch:
            ttk.Checkbutton(bar, text="仅显示本次扫描", variable=self.show_scan_only,
                            command=self._on_filter_change).pack(side="left", padx=(6, 0))

        lbl = ttk.Label(bar, text="显示 - / 模型库 -")
        lbl.pack(side="right")
        if count_label_attr:
            setattr(self, count_label_attr, lbl)
        return combo_type, combo_base

    def _build_tab_scan(self):
        f = self.tab_scan
        row0 = ttk.Frame(f)
        row0.pack(fill="x")
        ttk.Label(row0, text="模型文件夹:").pack(side="left")
        ttk.Entry(row0, textvariable=self.model_folder).pack(side="left", fill="x", expand=True, padx=6)
        b = ttk.Button(row0, text="浏览...", command=self._browse_model_folder)
        b.pack(side="left")
        self._action_buttons.append(b)
        bscan = ttk.Button(row0, text="开始扫描", command=self._do_scan)
        bscan.pack(side="left", padx=(6, 0))
        self._action_buttons.append(bscan)

        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row1, text="包含子文件夹", variable=self.recursive).pack(side="left")

        # 筛选行(含"仅显示本次扫描")
        ctype, cbase = self._build_filter_bar(f, with_scan_switch=True,
                                              count_label_attr="_count_label1")
        self.type_combo1 = ctype
        self.base_combo1 = cbase

        cols = ("file", "autov2", "status", "name", "type", "base")
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.tree1 = ttk.Treeview(tree_frame, columns=cols, show="headings", height=13)
        specs = [("file", "文件名", 320), ("autov2", "AutoV2", 100),
                 ("status", "状态", 90), ("name", "模型名", 280), ("type", "类型", 100),
                 ("base", "baseModel", 120)]
        for cid, text, w in specs:
            self.tree1.heading(cid, text=text)
            self.tree1.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree1.yview)
        self.tree1.configure(yscrollcommand=sb.set)
        self.tree1.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _build_tab_db(self):
        f = self.tab_db
        row0 = ttk.Frame(f)
        row0.pack(fill="x")
        ttk.Label(row0, text="数据库文件:").pack(side="left")
        ttk.Entry(row0, textvariable=self.db_path).pack(side="left", fill="x", expand=True, padx=6)
        b1 = ttk.Button(row0, text="浏览...", command=self._browse_db)
        b1.pack(side="left")
        b2 = ttk.Button(row0, text="重新加载", command=self._reload_db)
        b2.pack(side="left", padx=(4, 0))
        self._action_buttons += [b1, b2]

        self.db_info_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.db_info_var).pack(anchor="w", pady=(12, 6))

        info = ttk.LabelFrame(f, text=" 说明 ", padding=8)
        info.pack(fill="both", expand=True)
        ttk.Label(info, justify="left", wraplength=820, text=(
            "点击「更新数据库」会对模型库中「未收录」或「之前查询失败」的模型"
            "联网调用 Civitai by-hash API,反查 AIR / 模型名 / 模型 ID,结果写入 model_db.json。\n"
            "已经收录成功的模型不会重复联网;失败的记录(failed=True)下次运行会自动重试。\n"
            "联网需要能访问 civitai.com(如开启代理请保持系统代理可用)。"
        )).pack(anchor="w")

        btn = ttk.Button(f, text="更新数据库", command=self._do_update_db)
        btn.pack(anchor="e", pady=(10, 0))
        self._action_buttons.append(btn)

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

        # 筛选行(含名称搜索框)
        ctype, cbase = self._build_filter_bar(f, with_search=True,
                                              count_label_attr="_count_label3")
        self.type_combo3 = ctype
        self.base_combo3 = cbase

        ttk.Label(f, text="勾选要写入的模型(点击最左侧勾选列切换,勾选顺序即写入顺序):").pack(anchor="w", pady=(6, 2))

        tool = ttk.Frame(f)
        tool.pack(fill="x")
        for text, cmd in [("全选", self._select_all), ("反选", self._inverse_select),
                          ("清空", self._clear_select), ("上移", lambda: self._move(-1)),
                          ("下移", lambda: self._move(1))]:
            b = ttk.Button(tool, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self._action_buttons.append(b)

        cols = ("check", "file", "autov2", "air", "name", "type", "base")
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.tree3 = ttk.Treeview(tree_frame, columns=cols, show="headings", height=13)
        specs = [("check", "✓", 52), ("file", "文件名", 270), ("autov2", "AutoV2", 100),
                 ("air", "AIR", 300), ("name", "模型名", 200), ("type", "类型", 90),
                 ("base", "baseModel", 100)]
        for cid, text, w in specs:
            self.tree3.heading(cid, text=text)
            self.tree3.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree3.yview)
        self.tree3.configure(yscrollcommand=sb.set)
        self.tree3.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree3.bind("<Button-1>", self._on_pool_click3)

        btn = ttk.Button(f, text="写入图片", command=self._do_inject)
        btn.pack(anchor="e", pady=(8, 0))
        self._action_buttons.append(btn)

    # ------------------------------------------------------------------
    # 模型库勾选池(数据库全部模型 ∪ 本次扫描)
    # ------------------------------------------------------------------
    def _build_pool(self):
        """重建模型库池。每项一个 AutoV2:
        {AutoV2, file, files, modelName, AIR, type, baseModel, failed, in_scan}
        """
        items = []
        seen = set()
        # 1) 数据库里的全部模型
        for av, entry in self.db.items():
            if not isinstance(entry, dict):
                continue
            seen.add(av)
            files = entry.get("file") or []
            display = files[0] if files else av
            items.append({
                "AutoV2": av,
                "file": display,
                "files": files,
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
                        if it["file"] == av or not it["file"]:
                            it["file"] = m["file"]
                        break
                continue
            items.append({
                "AutoV2": av,
                "file": m["file"],
                "files": [m["file"]],
                "modelName": "",
                "AIR": "",
                "type": "",
                "baseModel": "",
                "failed": False,
                "in_scan": True,
            })
        items.sort(key=lambda it: (it["type"] or "", it["baseModel"] or "", it["file"] or ""))
        return items

    def _rebuild_pool(self):
        self.pool_items = self._build_pool()
        self._refresh_filter_options()
        self._refresh_all()

    def _visible_pool(self, scan_filter=False, search=""):
        """按当前筛选条件返回可见的池子(生成器)。

        scan_filter=True 时应用"仅显示本次扫描";search 为模型名/文件名子串过滤(不区分大小写)。
        """
        t = self.filter_type.get()
        b = self.filter_base.get()
        q = (search or "").strip().lower()
        for it in self.pool_items:
            if scan_filter and self.show_scan_only.get() and not it["in_scan"]:
                continue
            if t and t != "全部" and it["type"] != t:
                continue
            if b and b != "全部" and it["baseModel"] != b:
                continue
            if q and q not in (it["modelName"] or "").lower() and q not in (it["file"] or "").lower():
                continue
            yield it

    def _refresh_filter_options(self):
        types = sorted({it["type"] for it in self.pool_items if it["type"]})
        bases = sorted({it["baseModel"] for it in self.pool_items if it["baseModel"]})
        for combo in (getattr(self, "type_combo1", None), getattr(self, "type_combo3", None)):
            if combo is not None:
                combo["values"] = ["全部"] + types
                if combo.get() not in combo["values"]:
                    combo.set("全部")
        for combo in (getattr(self, "base_combo1", None), getattr(self, "base_combo3", None)):
            if combo is not None:
                combo["values"] = ["全部"] + bases
                if combo.get() not in combo["values"]:
                    combo.set("全部")

    def _on_filter_change(self, event=None):
        self._refresh_all()

    def _item_status(self, it):
        if it["AutoV2"] not in self.db:
            return "未收录"
        entry = self.db[it["AutoV2"]]
        if entry.get("failed"):
            return "查询失败"
        if entry.get("AIR"):
            return "已收录"
        return "无AIR"

    def _refresh_all(self):
        self._refresh_tab1()
        self._refresh_tab3()
        self._refresh_db_info()

    def _refresh_tab1(self):
        self.tree1.delete(*self.tree1.get_children())
        n = 0
        for it in self._visible_pool(scan_filter=True):
            status = self._item_status(it)
            self.tree1.insert("", "end", values=(
                it["file"], it["AutoV2"], status,
                it["modelName"], it["type"], it["baseModel"],
            ))
            n += 1
        lbl = getattr(self, "_count_label1", None)
        if lbl:
            lbl.config(text=f"显示 {n} / 模型库 {len(self.pool_items)}")

    def _refresh_tab3(self):
        self.tree3.delete(*self.tree3.get_children())
        n = 0
        for it in self._visible_pool(search=self.filter_search.get()):
            av = it["AutoV2"]
            if av in self.selected_avs:
                order = self.selected_avs.index(av) + 1
                check = f"☑ {order}"
            else:
                check = "☐"
            self.tree3.insert("", "end", values=(
                check, it["file"], av, it["AIR"],
                it["modelName"], it["type"], it["baseModel"],
            ))
            n += 1
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

    def _append_log(self, msg):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _progress_cb(self, i, total):
        self.task_queue.put(("progress", i, total))

    # ------------------------------------------------------------------
    # 勾选交互(①③ 共享勾选状态)
    # ------------------------------------------------------------------
    def _on_pool_click3(self, event):
        """卡片③勾选列点击:切换该行模型的选中状态。"""
        tree = self.tree3
        region = tree.identify("region", event.x, event.y)
        if region not in ("cell", "tree"):
            return
        if tree.identify_column(event.x) != "#1":
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        idx = tree.index(row)
        vis = self._visible3()
        if not (0 <= idx < len(vis)):
            return
        av = vis[idx]["AutoV2"]
        if av in self.selected_avs:
            self.selected_avs.remove(av)
        else:
            self.selected_avs.append(av)
        self._refresh_all()

    def _visible3(self):
        """卡片③当前筛选后的可见模型列表。"""
        return list(self._visible_pool(search=self.filter_search.get()))

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
    # ① 扫描
    # ------------------------------------------------------------------
    def _browse_model_folder(self):
        d = filedialog.askdirectory(initialdir=self.model_folder.get() or os.path.expanduser("~"))
        if d:
            self.model_folder.set(d)

    def _do_scan(self):
        folder = self.model_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("提示", "请先选择一个有效的模型文件夹。")
            return
        self._append_log(f"[操作] 开始扫描模型文件夹: {folder}")

        def target():
            return core.scan_models(
                folder,
                recursive=self.recursive.get(),
                log=self._append_log,
                cancel=self._cancel,
                progress=self._progress_cb,
            )

        def on_done(scanned):
            self.models = scanned
            # 默认把本次扫描到的模型勾选上,并切到"仅显示本次扫描"便于查看
            self.selected_avs = [m["AutoV2"] for m in scanned]
            self.show_scan_only.set(True)
            self.cfg["model_folder"] = folder
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set(f"扫描完成:共识别 {len(scanned)} 个模型")

        self._run_task(target, on_done)

    # ------------------------------------------------------------------
    # ② 更新数据库
    # ------------------------------------------------------------------
    def _browse_db(self):
        p = filedialog.askopenfilename(
            title="选择 model_db.json",
            initialdir=os.path.dirname(self.db_path.get()) or ".",
            filetypes=[("JSON 文件", "*.json")],
        )
        if p:
            self.db_path.set(p)
            self._reload_db()

    def _reload_db(self):
        self.db = core.load_db(self.db_path.get())
        self._rebuild_pool()
        self._append_log(f"[数据库] 已重新加载: {self.db_path.get()} (共 {len(self.db)} 条)")

    def _update_db_models(self):
        """构造传给 core.update_db 的模型清单。

        只包含两类:本次扫描到的(可能要新查询)、数据库里之前失败的(重试)。
        不传已成功的数据库记录;文件名为空时用 AutoV2 占位的情况也排除,
        避免把 AutoV2 当文件名写进数据库。
        """
        items = []
        seen = set()
        for m in self.models:
            if m["AutoV2"] not in seen:
                items.append({"AutoV2": m["AutoV2"], "file": m["file"]})
                seen.add(m["AutoV2"])
        for av, entry in self.db.items():
            if av in seen or not isinstance(entry, dict):
                continue
            if entry.get("failed"):
                files = [f for f in (entry.get("file") or []) if f]
                if files:
                    items.append({"AutoV2": av, "file": files[0]})
                    seen.add(av)
        return items

    def _do_update_db(self):
        if not self.pool_items:
            messagebox.showwarning("提示", "模型库为空。请先在 ① 扫描识别模型 里扫描,或在 ② 加载数据库。")
            return
        db_path = self.db_path.get().strip() or core.default_db_path()
        self.db_path.set(db_path)

        def target():
            return core.update_db(self._update_db_models(), db_path=db_path,
                                  log=self._append_log, cancel=self._cancel,
                                  progress=self._progress_cb)

        def on_done(db):
            self.db = db
            self.cfg["db_path"] = db_path
            save_config(self.cfg)
            self._rebuild_pool()
            self.status_var.set("数据库更新完成")

        self._run_task(target, on_done)

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
        self._append_log(f"[操作] 目标图片:{input_dir} | 输出:{output_dir} | 勾选模型 {len(selected)} 个")

        def target():
            return core.inject_images(selected, input_dir, output_dir,
                                      db_path=self.db_path.get(), suffix=suffix,
                                      log=self._append_log, cancel=self._cancel,
                                      progress=self._progress_cb)

        def on_done(result):
            ok, total = result
            self.cfg.update({"image_folder": input_dir, "output_folder": output_dir})
            save_config(self.cfg)
            self.status_var.set(f"写入完成:{ok}/{total} 张")

        self._run_task(target, on_done)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def _on_close(self):
        self.cfg.update({
            "model_folder": self.model_folder.get(),
            "image_folder": self.image_folder.get(),
            "output_folder": self.output_folder.get(),
            "db_path": self.db_path.get(),
        })
        save_config(self.cfg)
        self.destroy()


def main():
    _enable_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
