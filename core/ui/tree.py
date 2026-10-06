"""左：目录树面板——懒加载、选择联动列表、右键节点操作。"""
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import adb, fs
from core.ui import PALETTE


class TreeMixin:
    def _build_tree(self, pane):
        # 目录树（iid 直接用完整路径）；weight=0：窗口变宽时树宽保持不变
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
        self.tree.bind("<Double-1>", self.on_tree_double, add="+")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        for cname, hexv in PALETTE.items():
            self.tree.tag_configure(f"c_{cname}", foreground=hexv)

        # 右键菜单（节点都是目录）
        self.tree.bind("<Button-3>", self.on_tree_menu)
        tm = tk.Menu(self.root, tearoff=0)
        tm.add_command(label="拉取到电脑…", command=self.pull_node)
        tm.add_command(label="★ 收藏此路径", command=lambda: self.add_favorite(self.tree.selection()[0]))
        tm.add_command(label="新建文件夹…", command=self.make_dir_in_node)
        tm.add_command(label="重命名…", command=self.rename_node)
        tm.add_command(label="删除", command=self.delete_node)
        tm.add_cascade(label="标记颜色", menu=self._color_menu(tm, self.set_node_color))
        self.tmenu = tm

    # ---------- 加载与导航 ----------

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

    def on_tree_double(self, event):
        # 双击文字也会走默认类绑定展开节点，但不经过指示器检测，占位空行没被替换，这里补加载
        iid = self.tree.identify_row(event.y)
        if iid:
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

    # ---------- 节点操作 ----------

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
