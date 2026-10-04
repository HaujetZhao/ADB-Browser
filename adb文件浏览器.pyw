"""ADB 文件浏览器：左边设备目录树（懒加载），右边当前目录文件列表。"""
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

ROOT = "/storage/emulated/0"  # 浏览根目录：手机内部存储
NOWIN = subprocess.CREATE_NO_WINDOW  # 防 subprocess 弹 cmd 黑窗（仅 Windows 有效）


def adb(*args):
    """执行一次性 adb 命令（仅设备检查用）。"""
    r = subprocess.run(["adb", *args], capture_output=True, creationflags=NOWIN)
    return r.stdout.decode("utf-8", "replace").replace("\r", "")


def sh_quote(p):
    """路径转成可安全进远端 shell 的单引号形式（路径可能含空格）。"""
    return "'" + p.replace("'", "'\\''") + "'"


class Shell:
    """常驻 adb shell：一条连接反复用，输出以 @EOC@ 行定界。"""

    def __init__(self):
        self.p = subprocess.Popen(["adb", "shell"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  creationflags=NOWIN)

    def run(self, cmd):
        try:
            self.p.stdin.write(f"{cmd}; echo @EOC@$?\n".encode())
            self.p.stdin.flush()
        except OSError:
            self.__init__()  # 设备断开后重连，本次返回空
            return ""
        out = []
        while True:
            line = self.p.stdout.readline()
            if not line:  # 进程已退出
                return ""
            if line.startswith(b"@EOC@"):  # ponytail: 文件名恰为 @EOC@+数字 时会截断，可忽略
                return b"".join(out).decode("utf-8", "replace").replace("\r", "")
            out.append(line)


def parse_ls(text):
    """解析 ls -p 输出 → (目录列表, 文件列表)，目录名以 / 结尾。"""
    dirs, files = [], []
    for line in text.splitlines():
        name = line.strip()
        if not name or name in ("./", "../"):
            continue
        (dirs if name.endswith("/") else files).append(name)
    return dirs, files


def parent_path(p):
    p = p.rstrip("/")
    return p.rsplit("/", 1)[0] or "/"


class App:
    def __init__(self, root):
        self.root = root
        root.title("ADB 文件浏览器")
        root.geometry("900x550")

        # 顶部路径栏
        bar = ttk.Frame(root)
        bar.pack(fill="x")
        ttk.Button(bar, text="↑", width=3, command=self.go_up).pack(side="left", padx=(4, 2), pady=4)
        self.path_var = tk.StringVar(value="/")
        entry = ttk.Entry(bar, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=2)
        entry.bind("<Return>", lambda e: self.navigate(self.path_var.get()))
        ttk.Button(bar, text="前往", command=lambda: self.navigate(self.path_var.get())).pack(side="left", padx=(2, 4))

        # 左右分栏
        pane = ttk.PanedWindow(root, orient="horizontal")
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

        # 右：文件列表
        lf = ttk.Frame(pane)
        self.list = ttk.Treeview(lf, columns=("name",), show="headings")
        self.list.heading("name", text="文件")
        self.list.column("name", anchor="w")
        sb2 = ttk.Scrollbar(lf, command=self.list.yview)
        self.list.configure(yscrollcommand=sb2.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        pane.add(lf, weight=2)
        self.list.tag_configure("dir", foreground="#0066cc")
        self.list.bind("<Double-1>", self.on_list_double)

        # 底部状态栏
        self.status_var = tk.StringVar()
        ttk.Label(root, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x")

        # 设备检查 + 初始化根节点
        n = sum(1 for l in adb("devices").splitlines()[1:] if l.strip().endswith("\tdevice"))
        if n == 0:
            self.status_var.set("未检测到 adb 设备")
            return
        self.shell = Shell()
        self.tree.insert("", "end", iid=ROOT, text="内部存储", open=False)
        self.tree.insert(ROOT, "end", iid="dummy:" + ROOT)  # 占位，撑出展开箭头
        self.navigate(ROOT)

    def status(self, msg):
        self.status_var.set(msg)

    def load_children(self, iid):
        """加载某目录的子目录节点，每个子目录先挂占位节点。"""
        dirs, _ = parse_ls(self.shell.run(f"ls -p -1 {sh_quote(iid)}"))
        for d in dirs:
            child = iid.rstrip("/") + "/" + d.rstrip("/")
            self.tree.insert(iid, "end", iid=child, text=d.rstrip("/"))
            self.tree.insert(child, "end", iid="dummy:" + child)

    def ensure_loaded(self, iid):
        """若节点还挂着占位子节点，替换为真实子目录。"""
        kids = self.tree.get_children(iid)
        if kids and kids[0].startswith("dummy:"):
            self.tree.delete(*kids)
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
        if not sel:
            return
        node = sel[0]
        self.path_var.set(node)
        dirs, files = parse_ls(self.shell.run(f"ls -p -1 {sh_quote(node)}"))
        self.list.delete(*self.list.get_children())
        for name in dirs + files:
            tags = ("dir",) if name.endswith("/") else ()
            self.list.insert("", "end", values=(name,), tags=tags)
        self.status(f"{node}  （{len(dirs)} 个目录，{len(files)} 个文件）")

    def on_list_double(self, _event):
        sel = self.list.selection()
        if not sel:
            return
        name = self.list.item(sel[0], "values")[0]
        if name.endswith("/"):  # 双击目录进入
            self.navigate(self.path_var.get().rstrip("/") + "/" + name.rstrip("/"))

    def go_up(self):
        p = parent_path(self.path_var.get())
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
        self.ensure_loaded(node)  # 程序化 open=True 不触发 <<TreeOpen>>，需主动加载
        self.tree.selection_set(node)
        self.tree.see(node)
        self.tree.item(node, open=True)


if "--test" in sys.argv:  # ponytail: 解析逻辑最小自检
    d, f = parse_ls("etc/\r\nsdcard/\r\n./\r\n../\r\nhello.txt\r\n")
    assert d == ["etc/", "sdcard/"] and f == ["hello.txt"], (d, f)
    assert parent_path("/a/b") == "/a" and parent_path("/a") == "/" and parent_path("/") == "/"
    print("ok")
else:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高 DPI 适配
    root = tk.Tk()
    root.tk.call("tk", "scaling", ctypes.windll.shcore.GetScaleFactorForDevice(0) / 75)
    App(root)
    root.mainloop()
