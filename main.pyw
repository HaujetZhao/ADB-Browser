"""ADB 文件浏览器入口：高 DPI 适配后启动界面（有 tkinterdnd2 则启用拖拽）。"""
import ctypes
import logging
import os
import sys

import adb
from ui import App

# 日志写在脚本同目录 latest.log，每次启动覆盖，方便无控制台的 .pyw 排查问题
logging.basicConfig(filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest.log"),
                    filemode="w", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
sys.excepthook = lambda t, v, b: logging.exception("未捕获异常", exc_info=(t, v, b))

ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高 DPI 适配，须在创建窗口前

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except ImportError:
    import tkinter as tk
    root = tk.Tk()

root.report_callback_exception = lambda t, v, b: logging.exception("Tk 回调异常", exc_info=(t, v, b))
root.tk.call("tk", "scaling", ctypes.windll.shcore.GetScaleFactorForDevice(0) / 75)
logging.info("启动: python %s / tk %s / %s",
             sys.version.split()[0], root.tk.call("info", "patchlevel"), sys.executable)
App(root)
root.mainloop()
