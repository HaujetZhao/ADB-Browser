"""界面子包：App 由各面板 Mixin 组装而成（见 app.py）。"""
import os
import tempfile

ROOT = "/storage/emulated/0"  # 浏览根目录：手机内部存储

TEMP_BASE = os.path.join(tempfile.gettempdir(), "adb-browser")  # 拖出/打开用的暂存目录

THEME = "flatly"  # ttkbootstrap 主题（浅色）
