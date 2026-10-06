"""tkinter 界面：设备栏、目录树、文件列表（大小/时间/排序/过滤）、传输队列、右键操作、拖拽。"""
import logging
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import adb
import config
import fs

ROOT = "/storage/emulated/0"  # 浏览根目录：手机内部存储

TEMP_BASE = os.path.join(tempfile.gettempdir(), "adb-browser")  # 拖出暂存目录

PALETTE = {"红": "#e53935", "橙": "#fb8c00", "黄": "#d4b106", "绿": "#43a047",
           "青": "#00acc1", "蓝": "#1e88e5", "紫": "#8e24aa", "灰": "#757575"}  # 标记用色

# 焦点在这些控件里时不劫持按键（路径栏 / 过滤框 / 设备框）
ENTRY_CLASSES = ("TEntry", "TCombobox", "Text", "Spinbox")


class App:
    def __init__(self, root):
        self.root = root
        root.title("ADB 文件浏览器")
        self.cfg = config.load()
        root.geometry(f'{self.cfg["window"]["width"]}x{self.cfg["window"]["height"]}')

        self.serial = None
        self.shell = None
        self.entries = []         # 当前目录 [(名字, 是否目录, 大小, 修改时间, 条目数)]
        self.q = queue.Queue()    # 传输线程 → UI 线程
        self.transfer_kinds = {}  # 队列 iid → "pull"/"push"，完成后决定是否刷新列表
        self.sort_col = "name"    # 列表排序：表头可点，再点一次反向
        self.sort_desc = False
        self.favorites = self.cfg["favorites"]
        self.history = []         # 浏览过的路径，鼠标侧键/X1 X2 后退前进
        self.hist_pos = -1

        self._build_top()
        self._build_panes()
        self._setup_sel_tags()
        self._build_status()
        self._bind_keys()
        self._setup_dnd()
        for c, w in self.cfg["columns"].items():
            if w:
                self.list.column(c, width=w)
        if self.cfg["tree"]["width"]:
            # Treeview 没有 width 选项，用 #0 列宽撑出请求宽度，布局时自然生效
            self.tree.column("#0", width=self.cfg["tree"]["width"])
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._poll_queue)
        self.refresh_devices()

    # ---------- 配置 ----------

    def save_config(self):
        self.cfg["window"] = {"width": self.root.winfo_width(),
                              "height": self.root.winfo_height()}
        self.cfg["tree"] = {"width": self.tree.column("#0", "width")}  # 存列宽，避免滚动条宽度的往返漂移
        self.cfg["columns"] = {c: self.list.column(c, "width")
                               for c in ("name", "size", "mtime")}
        config.save(self.cfg)

    def on_close(self):
        self.save_config()
        self.root.destroy()

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
        self.star = ttk.Button(bar, text="★", width=3, command=self.show_favorites)
        self.star.pack(side="left", padx=(0, 4))

    def _build_panes(self):
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.pane = pane
        pane.pack(fill="both", expand=True)

        # 左：目录树（iid 直接用完整路径）；weight=0：窗口变宽时树宽保持不变
        tf = ttk.Frame(pane)
        self.tree_frame = tf
        self.tree = ttk.Treeview(tf, show="tree")
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pane.add(tf, weight=0)
        # Tk 9 的指示器点击不发 <<TreeOpen>>，改在 Button-1 里识别箭头点击
        self.tree.bind("<Button-1>", self.on_tree_click, add="+")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # 右：过滤栏 + 文件列表 + 传输队列
        rf = ttk.Frame(pane)
        right = ttk.PanedWindow(rf, orient="vertical")
        right.pack(fill="both", expand=True)
        pane.add(rf, weight=1)

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
        for cname, hexv in PALETTE.items():
            self.list.tag_configure(f"c_{cname}", foreground=hexv)
            self.tree.tag_configure(f"c_{cname}", foreground=hexv)
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
        m.add_cascade(label="标记颜色", menu=self._color_menu(m, self.set_color))
        self.menu = m

        # 树的右键菜单（节点都是目录）
        self.tree.bind("<Button-3>", self.on_tree_menu)
        tm = tk.Menu(self.root, tearoff=0)
        tm.add_command(label="拉取到电脑…", command=self.pull_node)
        tm.add_command(label="★ 收藏此路径", command=lambda: self.add_favorite(self.tree.selection()[0]))
        tm.add_command(label="新建文件夹…", command=self.make_dir_in_node)
        tm.add_command(label="重命名…", command=self.rename_node)
        tm.add_command(label="删除", command=self.delete_node)
        tm.add_cascade(label="标记颜色", menu=self._color_menu(tm, self.set_node_color))
        self.tmenu = tm

    def _color_menu(self, parent, setter):
        cm = tk.Menu(parent, tearoff=0)
        for cname in PALETTE:
            cm.add_command(label=f"● {cname}",
                           command=lambda n=cname: setter(n),
                           foreground=PALETTE[cname])
        cm.add_separator()
        cm.add_command(label="✕ 清除标记", command=lambda: setter(None))
        return cm

    def _setup_sel_tags(self):
        """选中态只给未设色行换标准配色；设色行保前景、只加选中底色。

        样式 map 是全局的，会把标记色一起盖掉，故禁用 map，
        改在 <<TreeviewSelect>> 里对选中变化的行差量重挂标签。
        """
        style = ttk.Style()
        sel_bg = style.lookup("Treeview", "background", ["selected"]) or "#0078d7"
        sel_fg = style.lookup("Treeview", "foreground", ["selected"]) or "#ffffff"
        style.map("Treeview", foreground=[], background=[])
        self.prev_sel = {}  # 控件 → 上次选中集
        for w in (self.list, self.tree):
            w.tag_configure("seltxt", foreground=sel_fg, background=sel_bg)
            w.tag_configure("selbg", background=sel_bg)
            w.bind("<<TreeviewSelect>>", self.on_sel_change, add="+")

    def on_sel_change(self, event):
        w = event.widget
        key = str(w)
        cur = set(w.selection())
        for iid in self.prev_sel.get(key, set()) ^ cur:
            if not w.exists(iid):
                continue
            base = [t for t in w.item(iid, "tags") or () if t not in ("seltxt", "selbg")]
            if iid in cur:
                colored = any(t.startswith("c_") for t in base)
                if colored:  # 标记色保前景，只补选中底色
                    base += ["selbg"]
                else:  # 未设色走标准选中态；Tk 标签同选项靠前优先，seltxt 须排在 dir 前
                    base = ["seltxt"] + base
            w.item(iid, tags=base)
        self.prev_sel[key] = cur

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
        # 鼠标侧键 X1/X2：Tk 9.0.4 下 <Button-8/9> 精确模式收不到（实测），
        # 但 <ButtonPress> 能收到 num=8/9，故统一在 catch-all 里按 num 分发
        self.root.bind("<ButtonPress>", self.on_button_press, add="+")

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

    def on_button_press(self, event):
        """统一处理鼠标按下：记录非左键；侧键 X1/X2（num=8/9）后退/前进。"""
        if event.num != 1:
            logging.info("ButtonPress num=%s 控件=%s", event.num, event.widget.winfo_class())
        if event.num == 8:
            self.go_hist(-1)
        elif event.num == 9:
            self.go_hist(1)

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
        # 临时禁用拖出：恢复时取消下面两行注释
        # self.list.drag_source_register("DND_Files")
        # self.list.dnd_bind("<<DragInitCmd>>", self.on_drag_init)

    def on_drop_files(self, event):
        # Tcl 列表字符串 → 路径元组；自己拖出的临时副本不能又 push 回去
        for f in self.root.tk.splitlist(event.data):
            if not f.lower().startswith(TEMP_BASE.lower()):
                self.push_one(f)

    def push_one(self, local):
        # 必须显式拼好远端文件路径：Windows adb.exe 在"目标为目录自动拼文件名"时
        # 用非 Unicode 方式处理本地名，中文文件名会坏掉（报 Is a directory）
        self.start_transfer("push", local, self.remote(os.path.basename(local)))

    def pull_to_temp(self, name, refresh=False):
        """拉到本机临时目录（拖出/双击打开共用），返回 (本地路径, 退出码)。"""
        base = os.path.join(TEMP_BASE,
                            (self.serial or "dev").replace(":", "_"))  # 冒号在 Windows 路径非法
        dst = os.path.join(base, name.rstrip("/"))
        if refresh or not os.path.exists(dst):  # 已拉过直接复用
            os.makedirs(base, exist_ok=True)
            code = adb.transfer("pull", self.serial, self.remote(name), dst, lambda l: None)
        else:
            code = 0
        return dst, code

    def on_drag_init(self, event):
        # 拖出仅在条目行上发起；标题行、列分隔符（全高可拖调宽）、空白处一律取消
        x = event.x_root - self.list.winfo_rootx()
        y = event.y_root - self.list.winfo_rooty()
        if self.list.identify_region(x, y) != "cell" or self._near_col_edge(x) \
                or not self.sel_names():
            return "refuse_drop"  # tkdnd 约定：返回它则不启动拖拽
        # ponytail: 拖出 = 先同步拉到临时目录再交给资源管理器，大文件会卡界面；
        # 要不卡的得换虚拟文件方案（CFSTR_FILEDESCRIPTOR），不值得
        paths = [self.pull_to_temp(name)[0] for name in self.sel_names()]
        return ("copy", "DND_Files", paths)

    def _near_col_edge(self, x):
        """x 是否落在列分隔符（±6px）附近。"""
        edge = 0
        for c in ("name", "size", "mtime"):
            edge += self.list.column(c, "width")
            if abs(x - edge) <= 6:
                return True
        return False

    # ---------- 收藏路径 ----------

    def save_favorites(self):
        config.save(self.cfg)

    def show_favorites(self):
        cur = self.path_var.get()
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="★ 收藏当前路径", command=self.add_favorite,
                      state="normal" if cur not in self.favorites else "disabled")
        if self.favorites:
            m.add_separator()
            for p in self.favorites:
                m.add_command(label=p, command=lambda p=p: self.navigate(p))
        m.add_separator()
        m.add_command(label="✕ 删除当前路径的收藏", command=self.remove_favorite,
                      state="normal" if cur in self.favorites else "disabled")
        m.post(self.star.winfo_rootx(), self.star.winfo_rooty() + self.star.winfo_height())

    def add_favorite(self, path=None):
        p = path or self.path_var.get()
        if p not in self.favorites:
            self.favorites.append(p)
            self.save_favorites()

    def remove_favorite(self):
        p = self.path_var.get()
        self.favorites.remove(p)
        self.save_favorites()
        self.status(f"已移除收藏：{p}")

    # ---------- 颜色标记 ----------

    def _move_color(self, old, new):
        """路径改名后同步挪动颜色记录。"""
        colors = self.cfg["colors"]
        if old in colors:
            if new:
                colors[new] = colors.pop(old)
            else:
                del colors[old]

    def set_color(self, color):
        """给列表选中项（可为多个）设置/清除颜色标记。"""
        colors = self.cfg["colors"]
        for name in self.sel_names():
            p = self.remote(name)
            if color:
                colors[p] = color
            else:
                colors.pop(p, None)
        config.save(self.cfg)
        self.render_list()

    def set_node_color(self, color):
        """给树选中节点设置/清除颜色标记。"""
        p = self.sel_node()
        if color:
            self.cfg["colors"][p] = color
        else:
            self.cfg["colors"].pop(p, None)
        config.save(self.cfg)
        self.tree.item(p, tags=(["c_" + color] if color else []))
        self.status(("已标记 " if color else "已清除标记 ") + p)

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
        """加载某目录的子目录节点（先清旧子节点）。

        ls -pl 自带的链接数就是子目录数：为 0 的空目录不挂占位——没占位就没有展开箭头。
        """
        self.tree.delete(*self.tree.get_children(iid))
        for name, is_dir, _, _, nlink in fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(iid)}")[0]):
            if not is_dir:
                continue
            n = nlink - 2
            child = iid.rstrip("/") + "/" + name
            c = self.cfg["colors"].get(child)
            self.tree.insert(iid, "end", iid=child, text=f"{name} ({n})",
                             tags=(["c_" + c] if c else []))
            if n > 0:  # 空目录不挂占位，不出箭头
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
        self._push_hist(node)
        self.path_var.set(node)
        self.entries = fs.parse_ls(self.shell.run(f"ls -pl {adb.sh_quote(node)}")[0])
        self.filter_var.set("")
        self.render_list()
        dirs = sum(1 for e in self.entries if e[1])
        self.status(f"{node}  （{dirs} 个目录，{len(self.entries) - dirs} 个文件）")

    def go_up(self):
        p = fs.parent_path(self.path_var.get())
        self.navigate(p if p.startswith(ROOT) else ROOT)

    def _push_hist(self, path):
        cur = self.history[self.hist_pos] if self.history else None
        if path == cur:
            return
        del self.history[self.hist_pos + 1:]
        self.history.append(path)
        self.hist_pos = len(self.history) - 1

    def go_hist(self, delta):
        pos = self.hist_pos + delta
        logging.info("go_hist(%+d): pos %d/%d", delta, pos, len(self.history) - 1)
        if 0 <= pos < len(self.history):
            self.hist_pos = pos
            self.navigate(self.history[pos])  # 联动的 on_tree_select 会因路径相同跳过入栈

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
        colors = self.cfg["colors"]
        self.list.delete(*self.list.get_children())
        for name, is_dir, size, mtime, nlink in dirs + files:
            if kw and not fs.match_filter(name, kw):
                continue
            meta = f"{nlink - 2} 项" if is_dir else fs.human_size(size)
            c = colors.get(self.remote(name))
            tags = (["c_" + c] if c else []) + (["dir"] if is_dir else [])  # 色标在前，覆盖目录蓝
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
            if self.shell_run(f"rm -rf {adb.sh_quote(self.remote(name))}", f"已删除 {name}"):
                self._move_color(self.remote(name), None)
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
            self._move_color(self.remote(old), self.remote(new))
        self.reload_current()

    def make_dir(self):
        name = simpledialog.askstring("新建文件夹", "文件夹名：", parent=self.root)
        if not name:
            return
        self.shell_run(f"mkdir -p {adb.sh_quote(self.remote(name))}", f"已创建 {name}")
        self.reload_current()

    # ---------- 树节点操作 ----------

    def sel_node(self):
        sel = self.tree.selection()
        return sel[0] if sel and not sel[0].startswith("dummy:") else None

    def on_tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and not iid.startswith("dummy:"):
            self.tree.selection_set(iid)  # 先选中，联动右侧列表
            self.tmenu.tk_popup(event.x_root, event.y_root)

    def refresh_node(self, parent):
        """节点增删改名后：重载父级树一层并导航过去（联动刷新列表）。"""
        self.load_children(parent)
        self.navigate(parent)

    def pull_node(self):
        dst = filedialog.askdirectory(title="拉取到哪个目录")
        if dst:
            self.start_transfer("pull", self.sel_node(), dst)

    def make_dir_in_node(self):
        parent = self.sel_node()
        name = simpledialog.askstring("新建文件夹", "文件夹名：", parent=self.root)
        if not name:
            return
        self.shell_run(f"mkdir -p {adb.sh_quote(parent + '/' + name)}", f"已创建 {name}")
        self.refresh_node(parent)

    def rename_node(self):
        path = self.sel_node()
        old = path.rsplit("/", 1)[-1]
        new = simpledialog.askstring("重命名", "新名字：", initialvalue=old, parent=self.root)
        if not new or new == old:
            return
        parent = fs.parent_path(path)
        target = parent + "/" + new
        if self.shell_run(f"mv {adb.sh_quote(path)} {adb.sh_quote(target)}", f"已重命名为 {new}"):
            self._move_color(path, target)
        self.refresh_node(parent)

    def delete_node(self):
        path = self.sel_node()
        if not messagebox.askyesno("删除", f"删除 {path}？目录将递归删除。"):
            return
        parent = fs.parent_path(path)
        if self.shell_run(f"rm -rf {adb.sh_quote(path)}", f"已删除 {path}"):
            self._move_color(path, None)
            self.refresh_node(parent)

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
