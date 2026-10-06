"""右：文件列表面板——渲染/排序/过滤、双击打开、右键文件操作。"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import adb, fs
from core.ui import MARK_COLOR, ROOT, SEL_BG, SEL_FG


class FileListMixin:
    def _build_list(self, right):
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
        self.list = ttk.Treeview(lw, columns=("name", "size", "mtime"), show="headings",
                                 selectmode="extended")
        for col, text in (("name", "文件"), ("size", "大小"), ("mtime", "修改时间")):
            self.list.heading(col, text=text,
                              command=lambda c=col: self.set_sort(c))
        self.list.column("name", anchor="w")  # 只有文件列吃掉多余空间
        self.list.column("size", width=110, anchor="e", stretch=False)
        self.list.column("mtime", width=140, anchor="center", stretch=False)
        sb2 = ttk.Scrollbar(lw, command=self.list.yview)
        self.list.configure(yscrollcommand=sb2.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        right.add(ff, weight=1)  # 多余空间全给文件列表（队列为 weight=0 恒高）

        # 行上的前景色标签互斥（见 on_sel_change / render_list），这里的配置顺序不再敏感
        self.list.tag_configure("dir", foreground="#0066cc")
        self.list.tag_configure("mark", foreground=MARK_COLOR)
        self.list.tag_configure("seltxt", foreground=SEL_FG, background=SEL_BG)
        self.list.tag_configure("selbg", background=SEL_BG)
        self.list.bind("<Double-1>", self.on_list_double)
        self.list.bind("<Button-3>", self.on_list_menu)
        self.list.bind("<Button-1>", self.on_list_click, add="+")

        # 右键菜单上下两段：上=对选中项的操作，下=对当前所在文件夹的操作
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="拉取到电脑…", command=self.pull_selected)
        m.add_command(label="重命名…", command=self.rename_selected)
        m.add_command(label="删除", command=self.delete_selected)
        self.mark_idx = m.index("end") + 1  # 「标注」项的动态文字下标
        m.add_command(label="标注", command=self.toggle_mark)
        m.add_separator()
        m.add_command(label="推送文件到当前目录…", command=self.push_files)
        m.add_command(label="新建文件夹…", command=self.make_dir)
        self.menu = m

    # ---------- 渲染 ----------

    def set_sort(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, False
        self.render_list()

    def render_list(self):
        """按表头排序 + 过滤词（子串或通配符）重画右侧列表；目录始终排前面。"""
        self._update_headings()
        key = {"name": lambda e: e[0].lower(),
               "size": lambda e: e[2],
               "mtime": lambda e: e[3]}[self.sort_col]
        dirs = sorted((e for e in self.entries if e[1]), key=key, reverse=self.sort_desc)
        files = sorted((e for e in self.entries if not e[1]), key=key, reverse=self.sort_desc)
        kw = self.filter_var.get()
        marked = self.cfg["marked"]
        self.list.delete(*self.list.get_children())
        for name, is_dir, size, mtime, nlink in dirs + files:
            if kw and not fs.match_filter(name, kw):
                continue
            counts = self.dir_counts.get(self.path_var.get().rstrip("/"), {})
            meta = (f"{counts.get(name, nlink - 2)} 项" if is_dir else fs.human_size(size))
            # 标注行不带 dir 标签：每行只留一个前景色标签，避免 ttk 标签冲突
            if self.remote(name) in marked:
                tags = ["mark"]
            else:
                tags = ["dir"] if is_dir else []
            self.list.insert("", "end", values=(name, meta, mtime.replace("-", "/")),
                             tags=tags)

    def _update_headings(self):
        for col, text in (("name", "文件"), ("size", "大小"), ("mtime", "修改时间")):
            arrow = (" ▼" if self.sort_desc else " ▲") if col == self.sort_col else ""
            self.list.heading(col, text=text + arrow)

    def reload_current(self):
        """文件操作后强制刷新当前目录（树一层 + 列表）。"""
        sel = self.tree.selection()
        node = sel[0] if sel and not sel[0].startswith("dummy:") else ROOT
        self.dir_counts.pop(node, None)  # 内容变过，计数缓存失效
        self.load_children(node)
        self.entries = fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(node)}")[0])
        self.render_list()
        self.status(f"已刷新 {node}")

    def _load_counts(self, node):
        """统计 node 各子目录条目总数（子目录 + 文件）。

        有缓存直接返回；否则后台线程跑一趟 find 深度 2（单进程，比逐目录 ls 快得多），
        完成后经消息队列刷新 UI。立即返回 {}，期间列表先用链接数兜底。
        """
        cached = self.dir_counts.get(node)
        if cached is not None:
            return cached
        if node in self.counting:
            return {}
        self.counting.add(node)

        def work():
            out = adb.adb("shell", "find", node, "-mindepth", "1", "-maxdepth", "2",
                          serial=self.serial)
            counts = {}
            for line in out.splitlines():
                parts = line[len(node) + 1:].split("/")
                # 深度 2 的行 = 子目录里的条目；隐藏文件与 ls 不带 -a 的行为对齐，不计入
                if len(parts) == 2 and not any(p.startswith(".") for p in parts):
                    counts[parts[0]] = counts.get(parts[0], 0) + 1
            self.counting.discard(node)
            self.q.put((None, ("counts", (node, counts))))

        threading.Thread(target=work, daemon=True).start()
        return {}

    # ---------- 交互 ----------

    def on_list_double(self, _event):
        sel = self.list.selection()
        if not sel:
            return
        name = self.list.item(sel[0], "values")[0]
        # 设色行不带 dir 标签（避免前景色冲突），是否目录改查数据
        if any(e[0] == name and e[1] for e in self.entries):
            self.navigate(self.path_var.get().rstrip("/") + "/" + name)
        else:
            self.open_remote(name)

    def open_remote(self, name):
        """拉到临时目录（总是重拉，避免打开过期缓存）后用系统关联程序打开。"""
        self.status(f"正在拉取 {name}…")
        self.root.update_idletasks()
        dst, code = self.pull_to_temp(name, refresh=True)
        if code != 0:
            self.status(f"拉取失败：{name}")
            return
        try:
            os.startfile(dst)
            self.status(f"已打开 {name}")
        except OSError as e:  # 该类型没有关联程序
            self.status(f"打不开：{e}")

    def on_list_click(self, event):
        if self.list.identify_region(event.x, event.y) == "nothing":  # 空白处点击取消选择
            self.list.selection_remove(*self.list.selection())

    def on_list_menu(self, event):
        iid = self.list.identify_row(event.y)
        if iid and iid not in self.list.selection():
            self.list.selection_set(iid)
        self.menu.entryconfigure(
            self.mark_idx, label=self.mark_label([self.remote(n) for n in self.sel_names()]))
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---------- 基础 ----------

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
            if self.shell_run(f"rm -rf {adb.sh_quote(self.remote(name))}", f"已删除 {name}"):
                self._move_mark(self.remote(name), None)
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
        if self.shell_run(f"mv {adb.sh_quote(self.remote(old))} {adb.sh_quote(self.remote(new))}",
                          f"已重命名为 {new}"):
            self._move_mark(self.remote(old), self.remote(new))
        self.reload_current()

    def make_dir(self):
        name = simpledialog.askstring("新建文件夹", "文件夹名：", parent=self.root)
        if not name:
            return
        self.shell_run(f"mkdir -p {adb.sh_quote(self.remote(name))}", f"已创建 {name}")
        self.reload_current()
