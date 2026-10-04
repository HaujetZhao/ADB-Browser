"""ADB 文件浏览器入口：高 DPI 适配后启动界面。"""
import ctypes
import tkinter as tk

from ui import App

ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高 DPI 适配
root = tk.Tk()
root.tk.call("tk", "scaling", ctypes.windll.shcore.GetScaleFactorForDevice(0) / 75)
App(root)
root.mainloop()
