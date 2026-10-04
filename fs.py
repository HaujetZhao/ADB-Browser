"""纯函数：ls 输出解析、路径处理。不依赖 adb 与 UI。自检：python fs.py"""
import sys


def parse_ls(text):
    """解析 ls -p 输出 → (目录列表, 文件列表)，目录名以 / 结尾。"""
    dirs, files = [], []
    for line in text.splitlines():
        name = line.strip()
        if not name or name in ("./", "../"):
            continue
        (dirs if name.endswith("/") else files).append(name)
    return dirs, files


def parent_path(p):
    p = p.rstrip("/")
    return p.rsplit("/", 1)[0] or "/"


if __name__ == "__main__":
    d, f = parse_ls("etc/\r\nsdcard/\r\n./\r\n../\r\nhello.txt\r\n")
    assert d == ["etc/", "sdcard/"] and f == ["hello.txt"], (d, f)
    assert parent_path("/a/b") == "/a" and parent_path("/a") == "/" and parent_path("/") == "/"
    print("ok")
