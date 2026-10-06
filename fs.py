"""纯函数：ls 输出解析、路径处理、大小格式化。不依赖 adb 与 UI。自检：python fs.py"""
import fnmatch
import re
import sys


def parse_ls(text):
    """解析 ls -pl 输出 → [(名字, 是否目录, 大小说明)]，目录在前、名字排序。

    toybox ls -l 字段：权限 链接数 属主 属组 大小 日期 时间 名字。
    ponytail: 目录的"条目数"取 ls 大小字段，依赖设备 ls 的语义（sdcard/FUSE 下即条目数），
    个别系统会显示成块大小，到时再调。
    """
    entries = []
    for line in text.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8 or parts[0][0] not in "dl-":
            continue
        perms, nlink, size, name = parts[0], parts[1], parts[4], parts[7].rstrip("/")
        if perms.startswith("d"):
            meta = f"{int(nlink) - 2} 项"
        else:
            meta = human_size(int(size))
        entries.append((name, perms.startswith("d"), meta))
    entries.sort(key=lambda e: (not e[1], e[0].lower()))
    return entries


def human_size(n):
    """字节数 → 人类可读大小。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


_SUMMARY = re.compile(r"([\d.]+ [KMGT]?B)/s \((\d+) bytes in ([\d.]+)s\)")


def transfer_summary(line):
    """从 adb 摘要行提取精简信息 "大小，速度，耗时"，无则 None。"""
    m = _SUMMARY.search(line)
    if not m:
        return None
    return f"{human_size(int(m.group(2)))}，{m.group(1)}/s，{m.group(3)}s"


def parent_path(p):
    p = p.rstrip("/")
    return p.rsplit("/", 1)[0] or "/"


def match_filter(name, kw):
    """过滤：含 * ? [ 时按通配符，否则子串匹配；均忽略大小写。"""
    name = name.lower()
    kw = kw.lower()
    if any(c in kw for c in "*?["):
        return fnmatch.fnmatchcase(name, kw)
    return kw in name


if __name__ == "__main__":
    text = ("drwxrwx--x 7 root sdcard_rw 4096 2026-10-04 22:54 DCIM\r\n"
            "-rw-rw---- 1 root sdcard_rw 15360 2026-10-04 22:54 a b.txt\r\n"
            "total 128\r\n")
    es = parse_ls(text)
    assert es == [("DCIM", True, "5 项"), ("a b.txt", False, "15.0 KB")], es
    assert human_size(0) == "0 B" and human_size(15360) == "15.0 KB" and human_size(3 * 1024**3) == "3.0 GB"
    assert transfer_summary("1 file pulled, 0 skipped. 12.4 MB/s (12345678 bytes in 0.945s)") == "11.8 MB，12.4 MB/s，0.945s"
    assert transfer_summary("no summary here") is None
    assert parent_path("/a/b") == "/a" and parent_path("/a") == "/" and parent_path("/") == "/"
    assert match_filter("Abc.txt", "abc") and match_filter("Abc.txt", "*.TXT") and not match_filter("Abc.txt", "xyz")
    assert match_filter("a1.txt", "a?.txt") and not match_filter("ab.txt", "a[0-9].txt")
    print("ok")
