"""tkinter 界面：设备栏、目录树、文件列表（大小/过滤/快捷键）、传输队列、右键操作、拖拽。"""
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import adb
import fs

ROOT = "/storage/emulated/0"  # 浏览根目录：手机内部存储

TEMP_BASE = os.path.join(tempfile.gettempdir(), "adb-browser")  # 拖出暂存目录

# 焦点在这些控件里时不劫持按键（路径栏 / 过滤框 / 设备框）
ENTRY_CLASSES = ("TEntry", "TCombobox", "Text", "Spinbox")


class App:
    def __init__(self, root):
        self.root = root
        root.title("ADB 文件浏览器")
        root.geometry("1000x640")

        self.serial = None
        self.shell = None
        self.entries = []         # 当前目录 [(名字, 是否目录, 大小说明)]
        self.q = queue.Queue()    # 传输线程 → UI 线程
        self.transfer_kinds = {}  # 队列 iid → "pull"/"push"，完成后决定是否刷新列表

        self._build_top()
        self._build_panes()
        self._build_status()
        self._bind_keys()
        self._setup_dnd()
        root.after(200, self._poll_queue)
        self.refresh_devices()

    # ---------- 界面搭建 ----------

    def _build_top(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x")
        self.device_var = tk.StringVar()
        self.combo = ttk.Combobox(bar, textvariable=self.device_var,
                                  state="readonly", width=18)
        self.combo.pack(side="left", padx=(4, 2), pady=4)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.switch_device())
        ttk.Button(bar, text="⟳", width=3, command=self.refresh_devices).pack(side="left", padx=2)
        ttk.Button(bar, text="↑", width=3, command=self.go_up).pack(side="left", padx=(10, 2))
        self.path_var = tk.StringVar(value="/")
        entry = ttk.Entry(bar, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=2)
        entry.bind("<Return>", lambda e: self.navigate(self.path_var.get()))
        ttk.Button(bar, text="前往", command=lambda: self.navigate(self.path_var.get())).pack(side="left", padx=(2, 4))

    def _build_panes(self):
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # 左：目录树（iid 直接用完整路径）
        tf = ttk.Frame(pane)
        self.tree = ttk.Treeview(tf, show="tree")
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pane.add(tf, weight=1)
        # Tk 9 的指示器点击不发 <<TreeOpen>>，改在 Button-1 里识别箭头点击
        self.tree.bind("<Button-1>", self.on_tree_click, add="+")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # 右：过滤栏 + 文件列表 + 传输队列
        rf = ttk.Frame(pane)
        right = ttk.PanedWindow(rf, orient="vertical")
        right.pack(fill="both", expand=True)
        pane.add(rf, weight=2)

        ff = ttk.Frame(right)
        ftop = ttk.Frame(ff)
        ftop.pack(fill="x")
        ttk.Label(ftop, text="过滤:").pack(side="left", padx=(4, 2))
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(ftop, textvariable=self.filter_var)
        self.filter_entry.pack(side="left", fill="x", expand=True, pady=2)
        self.filter_entry.bind("<KeyRelease>", lambda e: self.render_list())
        lw = ttk.Frame(ff)
        lw.pack(fill="both", expand=True)
        self.list = ttk.Treeview(lw, columns=("name", "size"), show="headings",
                                 selectmode="extended")
        self.list.heading("name", text="文件")
        self.list.heading("size", text="大小")
        self.list.column("name", anchor="w")
        self.list.column("size", width=110, anchor="e")
        sb2 = ttk.Scrollbar(lw, command=self.list.yview)
        self.list.configure(yscrollcommand=sb2.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        right.add(ff, weight=3)

        qf = ttk.Frame(right)
        qw = ttk.Frame(qf)
        qw.pack(fill="both", expand=True)
        self.queue = ttk.Treeview(qw, columns=("name", "status"), show="headings", height=4)
        self.queue.heading("name", text="传输")
        self.queue.heading("status", text="状态")
        self.queue.column("name", anchor="w")
        self.queue.column("status", width=360, anchor="w")
        sb3 = ttk.Scrollbar(qw, command=self.queue.yview)
        self.queue.configure(yscrollcommand=sb3.set)
        self.queue.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")
        right.add(qf, weight=1)

        self.list.tag_configure("dir", foreground="#0066cc")
        self.list.bind("<Double-1>", self.on_list_double)
        self.list.bind("<Button-3>", self.on_list_menu)
        self.list.bind("<Button-1>", self.on_list_click, add="+")

        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="拉取到电脑…", command=self.pull_selected)
        m.add_command(label="重命名…", command=self.rename_selected)
        m.add_command(label="删除", command=self.delete_selected)
        m.add_separator()
        m.add_command(label="推送文件到当前目录…", command=self.push_files)
        m.add_command(label="新建文件夹…", command=self.make_dir)
        self.menu = m

    def _build_status(self):
        self.status_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x")

    def status(self, msg):
        self.status_var.set(msg)

    # ---------- 快捷键 ----------

    def _bind_keys(self):
        self.root.bind("<F5>", lambda e: self.reload_current())
        self.root.bind("<F2>", lambda e: self.rename_selected())
        self.root.bind("<BackSpace>", self.on_backspace)
        self.root.bind("<Escape>", self.on_escape)
        self.root.bind("<Key>", self.on_global_key)  # 任意可打印字符 → 聚焦过滤框
        self.list.bind("<Delete>", lambda e: self.delete_selected())
        self.list.bind("<Control-a>", self.on_select_all)
        self.list.bind("<Return>", self.on_list_double)

    def _in_entry(self, widget):
        return widget.winfo_class() in ENTRY_CLASSES

    def on_global_key(self, event):
        """在树/列表等非输入控件里直接打字：字符落进过滤框并聚焦。"""
        if self._in_entry(event.widget):
            return
        ch = event.char
        if ch and ch.isprintable() and not event.state & 0x0004:  # 排除 Ctrl 组合
            self.filter_var.set(ch)
            self.filter_entry.focus_set()
            self.filter_entry.icursor("end")
            return "break"

    def on_backspace(self, event):
        if not self._in_entry(event.widget):
            self.go_up()

    def on_escape(self, event):
        if not self._in_entry(event.widget):
            self.filter_var.set("")
            self.render_list()
            self.list.focus_set()

    def on_select_all(self, _event):
        self.list.selection_set(self.list.get_children())
        return "break"

    # ---------- 拖拽 ----------

    def _setup_dnd(self):
        if not hasattr(self.root, "drop_target_register"):
            return  # 未装 tkinterdnd2：拖拽不可用，右键推送/拉取照常
        self.list.drop_target_register("DND_Files")
        self.list.dnd_bind("<<Drop>>", self.on_drop_files)
        self.list.drag_source_register("DND_Files")
        self.list.dnd_bind("<<DragInitCmd>>", self.on_drag_init)

    def on_drop_files(self, event):
        # Tcl 列表字符串 → 路径元组；自己拖出的临时副本不能又 push 回去
        for f in self.root.tk.splitlist(event.data):
            if not f.lower().startswith(TEMP_BASE.lower()):
                self.push_one(f)

    def push_one(self, local):
        # 必须显式拼好远端文件路径：Windows adb.exe 在"目标为目录自动拼文件名"时
        # 用非 Unicode 方式处理本地名，中文文件名会坏掉（报 Is a directory）
        self.start_transfer("push", local, self.remote(os.path.basename(local)))

    def on_drag_init(self, event):
        # 拖出仅在条目行上发起；标题行、列分隔符（全高可拖调宽）、空白处一律取消
        x = event.x_root - self.list.winfo_rootx()
        y = event.y_root - self.list.winfo_rooty()
        if self.list.identify_region(x, y) != "cell" or self._near_col_edge(x) \
                or not self.sel_names():
            return "refuse_drop"  # tkdnd 约定：返回它则不启动拖拽
        # ponytail: 拖出 = 先同步拉到临时目录再交给资源管理器，大文件会卡界面；
        # 要不卡的得换虚拟文件方案（CFSTR_FILEDESCRIPTOR），不值得
        base = os.path.join(TEMP_BASE,
                            (self.serial or "dev").replace(":", "_"))  # 冒号在 Windows 路径非法
        os.makedirs(base, exist_ok=True)
        paths = []
        for name in self.sel_names():
            dst = os.path.join(base, name.rstrip("/"))
            if not os.path.exists(dst):  # 已拉过直接复用
                adb.transfer("pull", self.serial, self.remote(name), dst, lambda l: None)
            paths.append(dst)
        return ("copy", "DND_Files", paths)

    def _near_col_edge(self, x):
        """x 是否落在列分隔符（±6px）附近。"""
        edge = 0
        for c in ("name", "size"):
            edge += self.list.column(c, "width")
            if abs(x - edge) <= 6:
                return True
        return False

    # ---------- 设备 ----------

    def refresh_devices(self):
        devs = adb.list_devices()
        self.combo["values"] = devs
        if not devs:
            self.status("未检测到 adb 设备")
            return
        if self.device_var.get() not in devs:
            self.device_var.set(devs[0])
            self.switch_device()
        else:
            self.status(f"{len(devs)} 台设备在线")

    def switch_device(self):
        self.serial = self.device_var.get()
        self.shell = adb.Shell(self.serial)
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid=ROOT, text="内部存储", open=False)
        self.tree.insert(ROOT, "end", iid="dummy:" + ROOT)  # 占位，撑出展开箭头
        self.navigate(ROOT)

    # ---------- 目录树 ----------

    def load_children(self, iid):
        """加载某目录的子目录节点（先清掉旧子节点），每个子目录先挂占位。"""
        self.tree.delete(*self.tree.get_children(iid))
        for name, is_dir, meta in fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(iid)}")[0]):
            if not is_dir:
                continue
            child = iid.rstrip("/") + "/" + name
            self.tree.insert(iid, "end", iid=child, text=f"{name} ({meta.removesuffix(' 项')})")
            self.tree.insert(child, "end", iid="dummy:" + child)

    def ensure_loaded(self, iid):
        """若节点还挂着占位子节点，替换为真实子目录。"""
        kids = self.tree.get_children(iid)
        if kids and kids[0].startswith("dummy:"):
            self.load_children(iid)

    def on_tree_click(self, event):
        if self.tree.identify_element(event.x, event.y) != "Treeitem.indicator":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            # after_idle：等 ttk 先完成开/合切换，再补加载子节点
            self.root.after_idle(self.ensure_loaded, iid)

    def on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel or sel[0].startswith("dummy:"):
            return
        node = sel[0]
        self.path_var.set(node)
        self.entries = fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(node)}")[0])
        self.filter_var.set("")
        self.render_list()
        dirs = sum(1 for e in self.entries if e[1])
        self.status(f"{node}  （{dirs} 个目录，{len(self.entries) - dirs} 个文件）")

    def go_up(self):
        p = fs.parent_path(self.path_var.get())
        self.navigate(p if p.startswith(ROOT) else ROOT)

    def navigate(self, path):
        """跳转到 path（必须在 ROOT 下）：逐级确保树节点已加载，最后选中它。"""
        path = "/" + path.strip("/")
        if path != ROOT and not path.startswith(ROOT + "/"):
            self.status(f"超出根目录范围：{path}")
            return
        node = ROOT
        for part in path[len(ROOT) + 1:].split("/"):
            if not part:
                continue
            parent, node = node, node.rstrip("/") + "/" + part
            self.ensure_loaded(parent)
            if not self.tree.exists(node):
                self.status(f"路径不存在：{path}")
                return
        self.ensure_loaded(node)  # 程序化 open=True 不触发指示器点击，需主动加载
        self.tree.selection_set(node)
        self.tree.see(node)
        self.tree.item(node, open=True)

    # ---------- 文件列表 ----------

    def render_list(self):
        """按过滤词（子串或通配符）重画右侧列表。"""
        kw = self.filter_var.get()
        self.list.delete(*self.list.get_children())
        for name, is_dir, meta in self.entries:
            if kw and not fs.match_filter(name, kw):
                continue
            self.list.insert("", "end", values=(name, meta),
                             tags=("dir",) if is_dir else ())

    def reload_current(self):
        """文件操作后强制刷新当前目录（树一层 + 列表）。"""
        sel = self.tree.selection()
        node = sel[0] if sel and not sel[0].startswith("dummy:") else ROOT
        self.load_children(node)
        self.entries = fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(node)}")[0])
        self.render_list()
        self.status(f"已刷新 {node}")

    def on_list_double(self, _event):
        sel = self.list.selection()
        if not sel:
            return
        name = self.list.item(sel[0], "values")[0]
        if "dir" in self.list.item(sel[0], "tags"):  # 双击目录进入
            self.navigate(self.path_var.get().rstrip("/") + "/" + name)

    def on_list_click(self, event):
        if self.list.identify_region(event.x, event.y) == "nothing":  # 空白处点击取消选择
            self.list.selection_remove(*self.list.selection())

    def on_list_menu(self, event):
        iid = self.list.identify_row(event.y)
        if iid and iid not in self.list.selection():
            self.list.selection_set(iid)
        self.menu.tk_popup(event.x_root, event.y_root)

    def remote(self, name):
        """当前目录下某条目的完整远端路径。"""
        return self.path_var.get().rstrip("/") + "/" + name

    def sel_names(self):
        return [self.list.item(i, "values")[0] for i in self.list.selection()]

    def shell_run(self, cmd, ok_msg):
        out, code = self.shell.run(cmd)
        if code != 0:
            self.status(f"失败：{out.strip()[:80]}")
            return False
        self.status(ok_msg)
        return True

    # ---------- 文件操作 ----------

    def pull_selected(self):
        names = self.sel_names()
        dst = filedialog.askdirectory(title="拉取到哪个目录")
        if not dst or not names:
            return
        for name in names:
            self.start_transfer("pull", self.remote(name), dst)

    def push_files(self):
        files = filedialog.askopenfilenames(title="推送哪些文件")
        if not files:
            return
        for f in files:
            self.push_one(f)

    def delete_selected(self):
        names = self.sel_names()
        if not names or not messagebox.askyesno(
                "删除", f"删除 {len(names)} 项？目录将递归删除。"):
            return
        for name in names:
            self.shell_run(f"rm -rf {adb.sh_quote(self.remote(name))}", f"已删除 {name}")
        self.reload_current()

    def rename_selected(self):
        sel = self.sel_names()
        if len(sel) != 1:
            self.status("重命名需要恰好选中一项")
            return
        old = sel[0]
        new = simpledialog.askstring("重命名", "新名字：",
                                     initialvalue=old.rstrip("/"), parent=self.root)
        if not new or new == old.rstrip("/"):
            return
        self.shell_run(f"mv {adb.sh_quote(self.remote(old))} {adb.sh_quote(self.remote(new))}",
                       f"已重命名为 {new}")
        self.reload_current()

    def make_dir(self):
        name = simpledialog.askstring("新建文件夹", "文件夹名：", parent=self.root)
        if not name:
            return
        self.shell_run(f"mkdir -p {adb.sh_quote(self.remote(name))}", f"已创建 {name}")
        self.reload_current()

    # ---------- 传输队列 ----------

    def start_transfer(self, kind, src, dst):
        iid = self.queue.insert("", "end", values=(os.path.basename(src), "传输中…"))
        self.transfer_kinds[iid] = kind

        def work():
            last = [""]

            def on_line(line):
                last[0] = line.strip()
                pct = adb.parse_progress(line)
                if pct is not None:
                    self.q.put((iid, ("pct", pct)))

            code = adb.transfer(kind, self.serial, src, dst, on_line)
            if code == 0:
                s = fs.transfer_summary(last[0])  # "11.8 MB，12.4 MB/s，0.945s"
                final = f"完成：{s}" if s else "完成"
            else:
                final = f"失败：{last[0][:60]}"
            self.q.put((iid, ("final", final)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_queue(self):
        while True:
            try:
                iid, (kind, val) = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "pct":
                self.queue.set(iid, "status", f"{val}%")
                continue
            self.queue.set(iid, "status", val)
            if val.startswith("完成") and self.transfer_kinds.get(iid) == "push":
                self.reload_current()
        self.root.after(200, self._poll_queue)
