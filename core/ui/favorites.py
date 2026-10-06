"""收藏路径与颜色标记（都持久化在 config.toml）。"""
import tkinter as tk

from core import config
from core.ui import PALETTE


class FavoritesMixin:
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

    def _color_menu(self, parent, setter):
        cm = tk.Menu(parent, tearoff=0)
        for cname in PALETTE:
            cm.add_command(label=f"● {cname}",
                           command=lambda n=cname: setter(n),
                           foreground=PALETTE[cname])
        cm.add_separator()
        cm.add_command(label="✕ 清除标记", command=lambda: setter(None))
        return cm

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
