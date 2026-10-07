"""ADB 文件浏览器入口：高 DPI 适配后启动界面（有 tkinterdnd2 则启用拖拽）。"""
import ctypes
import logging
import os
import sys

from core.ui.app import App

# 日志写在脚本（打包后为 exe）同目录 latest.log，每次启动覆盖，方便无控制台的 .pyw 排查问题
base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
logging.basicConfig(filename=os.path.join(base, "latest.log"),
                    filemode="w", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
sys.excepthook = lambda t, v, b: logging.exception("未捕获异常", exc_info=(t, v, b))

ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高 DPI 适配，须在创建窗口前

# 窗口/任务栏图标：打包时经 --add-data 带入，运行时在解压目录取
icon = os.path.join(sys._MEIPASS if getattr(sys, "frozen", False) else ".", "assets", "icons", "adb-browser.ico")

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except ImportError:
    import tkinter as tk
    root = tk.Tk()
root.withdraw()  # 建 UI 期间藏住窗口，避免先闪出默认小窗再变大；须在 iconbitmap 等会触发映射的调用前

root.report_callback_exception = lambda t, v, b: logging.exception("Tk 回调异常", exc_info=(t, v, b))
root.iconbitmap(icon)
root.tk.call("tk", "scaling", ctypes.windll.shcore.GetScaleFactorForDevice(0) / 75)
logging.info("启动: python %s / tk %s / %s",
             sys.version.split()[0], root.tk.call("info", "patchlevel"), sys.executable)
App(root)
root.update_idletasks()
root.deiconify()
root.mainloop()
