"""全局快捷键与鼠标侧键。"""
import logging

# 焦点在这些控件里时不劫持按键（路径栏 / 过滤框 / 设备框）
ENTRY_CLASSES = ("TEntry", "TCombobox", "Text", "Spinbox")


class KeyMixin:
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
