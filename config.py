"""配置：~/.adb-browser.toml，记录窗口大小、列宽、收藏路径。"""
import copy
import os
import tomllib

import tomli_w

PATH = os.path.join(os.path.expanduser("~"), ".adb-browser.toml")

DEFAULTS = {
    "window": {"width": 1000, "height": 640},
    "tree": {"width": 0},  # 0 = 用布局默认宽度
    "columns": {"name": 0, "size": 110, "mtime": 140},  # 0 = 用代码里的默认宽度
    "favorites": [],
    "colors": {},  # 完整路径 → 颜色名（PALETTE 的键）
}


def load():
    """读配置并与默认值合并；文件不存在/损坏时返回全默认。"""
    merged = copy.deepcopy(DEFAULTS)
    try:
        with open(PATH, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return merged
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        elif k in merged:
            merged[k] = v
    return merged


def save(cfg):
    with open(PATH, "wb") as f:
        tomli_w.dump(cfg, f)
