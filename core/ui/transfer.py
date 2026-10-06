"""传输队列面板——后台线程传输、进度轮询、拖拽收发、临时目录拉取。"""
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

from core import adb, fs
from core.ui import TEMP_BASE


class TransferMixin:
    def _build_queue(self, right):
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
        right.add(qf, weight=0)  # 恒定高度：窗口变高时空间全给上方文件列表

    # ---------- 队列 ----------

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

    # ---------- 临时目录 ----------

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
