"""ADB 文件浏览器入口：高 DPI 适配后启动界面（有 tkinterdnd2 则启用拖拽）。"""
import ctypes

import adb
from ui import App

ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高 DPI 适配，须在创建窗口前

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except ImportError:
    import tkinter as tk
    root = tk.Tk()

root.tk.call("tk", "scaling", ctypes.windll.shcore.GetScaleFactorForDevice(0) / 75)
App(root)
root.mainloop()
