"""收藏路径与标注（都持久化在 config.toml）。"""
import tkinter as tk

from core import config


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

    def add_favorite(self):
        p = self.path_var.get()
        if p not in self.favorites:
            self.favorites.append(p)
            self.save_favorites()

    def remove_favorite(self):
        p = self.path_var.get()
        self.favorites.remove(p)
        self.save_favorites()
        self.status(f"已移除收藏：{p}")

    # ---------- 标注 ----------

    def mark_label(self, paths):
        """菜单项文字按状态显示：全部已标注 → 「取消标注」，否则 → 「标注」。"""
        marked = self.cfg["marked"]
        return "取消标注" if paths and all(p in marked for p in paths) else "标注"

    def _move_mark(self, old, new):
        """路径改名/删除后同步挪动、清理标注。"""
        marked = self.cfg["marked"]
        if old in marked:
            if new:
                marked[marked.index(old)] = new
            else:
                marked.remove(old)

    def toggle_mark(self):
        """给列表选中项（可为多个）标注/取消标注。混合选中时：有未标注的则全部标注。"""
        marked = self.cfg["marked"]
        paths = [self.remote(n) for n in self.sel_names()]
        if paths and all(p in marked for p in paths):
            for p in paths:
                marked.remove(p)
            self.status(f"已取消标注 {len(paths)} 项")
        else:
            for p in paths:
                if p not in marked:
                    marked.append(p)
            self.status(f"已标注 {len(paths)} 项")
        config.save(self.cfg)
        self.render_list()

    def toggle_node_mark(self):
        """给树选中节点标注/取消标注。"""
        p = self.sel_node()
        marked = self.cfg["marked"]
        if p in marked:
            marked.remove(p)
            self.status(f"已取消标注 {p}")
        else:
            marked.append(p)
            self.status(f"已标注 {p}")
        config.save(self.cfg)
        sel = p in self.tree.selection()
        if p in marked:
            tags = ["mark"] + (["selbg"] if sel else [])
        else:
            tags = ["seltxt"] if sel else []
        self.tree.item(p, tags=tags)
