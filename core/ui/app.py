"""App 组装：各面板 Mixin 合成完整界面，本模块只留跨面板的协调逻辑。"""
import logging
import queue
import tkinter as tk
from tkinter import ttk

from core import adb, config, fs
from core.ui import ROOT
from core.ui.favorites import FavoritesMixin
from core.ui.filelist import FileListMixin
from core.ui.keys import KeyMixin
from core.ui.transfer import TransferMixin
from core.ui.tree import TreeMixin


class App(TreeMixin, FileListMixin, TransferMixin, KeyMixin, FavoritesMixin):
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
        self._build_tree(pane)

        rf = ttk.Frame(pane)
        right = ttk.PanedWindow(rf, orient="vertical")
        right.pack(fill="both", expand=True)
        pane.add(rf, weight=1)
        self._build_list(right)
        self._build_queue(right)

    def _setup_sel_tags(self):
        """选中态配色：未设色行走标准反白（seltxt），设色行保前景只加深蓝底（selbg）。

        样式 map 是全局的，会把标记色一起盖掉，故禁用 map，
        改在 <<TreeviewSelect>> 里对选中变化的行差量重挂标签。
        标签本体在各面板 _build_* 里按优先级创建（先配置者赢）。
        """
        ttk.Style().map("Treeview", foreground=[], background=[])
        self.prev_sel = {}  # 控件 → 上次选中集
        for w in (self.list, self.tree):
            w.bind("<<TreeviewSelect>>", self.on_sel_change, add="+")

    def on_sel_change(self, event):
        w = event.widget
        key = str(w)
        cur = set(w.selection())
        for iid in self.prev_sel.get(key, set()) ^ cur:
            if not w.exists(iid):
                continue
            base = [t for t in w.item(iid, "tags") or () if t not in ("seltxt", "selbg", "dir")]
            # 每行只留一个定义前景色的标签，杜绝 ttk 标签冲突：
            # 未标注选中 → seltxt（标准反白，dir 被换下）；已标注 → mark 保前景，选中叠 selbg 补底
            if iid in cur:
                marked = "mark" in base
                base = (["mark", "selbg"] if marked else ["seltxt"])
            w.item(iid, tags=base)
        self.prev_sel[key] = cur

    def _build_status(self):
        self.status_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x")

    def status(self, msg):
        self.status_var.set(msg)

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

    # ---------- 导航与历史 ----------

    def go_up(self):
        old = self.path_var.get().rstrip("/")
        p = fs.parent_path(old)
        child = old[len(p) + 1:] if old != p else None
        self.navigate(p if p.startswith(ROOT) else ROOT, restore=child)

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
            target = self.history[pos]
            # 后退到上级时恢复刚才出来的子目录；前进时目标本身就是那个子目录
            leaving = self.history[self.hist_pos]
            under = leaving if delta < 0 else target
            child = under[len(target):].lstrip("/") if under.startswith(target + "/") else None
            if child and "/" in child:
                child = None  # 只恢复直接子目录
            self.hist_pos = pos
            self.navigate(target, restore=child)  # 联动的 on_tree_select 会因路径相同跳过入栈

    def navigate(self, path, restore=None):
        """跳转到 path（必须在 ROOT 下）：逐级确保树节点已加载，最后选中它。

        restore：跳转完成后要恢复选中的列表项名（回到上级时即刚才所在的子目录）。
        """
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
        if restore:
            def _restore():
                iid = next((i for i in self.list.get_children()
                            if self.list.item(i, "values")[0] == restore), None)
                logging.info("恢复选中 %r → %s", restore, iid)
                if iid:
                    self.list.selection_set(iid)
                    self.list.see(iid)
            self.root.after_idle(_restore)  # 等列表渲染完成后执行
