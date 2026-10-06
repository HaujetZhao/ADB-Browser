"""界面子包：App 由各面板 Mixin 组装而成（见 app.py）。"""
import os
import tempfile

ROOT = "/storage/emulated/0"  # 浏览根目录：手机内部存储

TEMP_BASE = os.path.join(tempfile.gettempdir(), "adb-browser")  # 拖出/打开用的暂存目录

PALETTE = {"红": "#e53935", "橙": "#fb8c00", "黄": "#d4b106", "绿": "#43a047",
           "青": "#00acc1", "蓝": "#1e88e5", "紫": "#8e24aa", "灰": "#757575"}  # 标记用色
