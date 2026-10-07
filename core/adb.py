"""adb 命令层：一次性命令、设备列表、常驻 shell、pull/push 传输。自检：python adb.py"""
import os
import re
import subprocess
import sys
import threading

NOWIN = subprocess.CREATE_NO_WINDOW  # 防 subprocess 弹 cmd 黑窗（仅 Windows 有效）

# 用 bin/ 里的 adb-chinese（修了 Windows 中文路径），不用 PATH 里的官方 adb
if getattr(sys, "frozen", False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADB = os.path.join(_BASE, "bin", "adb.exe")

_PCT = re.compile(r"\[\s*(\d+)%\]")


def adb(*args, serial=None):
    """执行一次性 adb 命令，返回 stdout 文本。"""
    r = subprocess.run([ADB, *(["-s", serial] if serial else []), *args],
                       capture_output=True, creationflags=NOWIN)
    return r.stdout.decode("utf-8", "replace").replace("\r", "")


def list_devices():
    """在线设备序列号列表。"""
    return [l.split("\t")[0]
            for l in adb("devices").splitlines()[1:]
            if l.strip().endswith("\tdevice")]


def sh_quote(p):
    """路径转成可安全进远端 shell 的单引号形式（路径可能含空格）。"""
    return "'" + p.replace("'", "'\\''") + "'"


def parse_progress(line):
    """从 adb 传输输出行提取百分比整数，无则 None。"""
    m = _PCT.search(line)
    return int(m.group(1)) if m else None


class Shell:
    """常驻 adb shell：一条连接反复用，输出以 @EOC@ 行定界。run() → (输出, 退出码)。"""

    def __init__(self, serial=None):
        self.cmd = [ADB, *(["-s", serial] if serial else []), "shell"]
        self.lock = threading.Lock()
        self._start()

    def _start(self):
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  creationflags=NOWIN)

    def run(self, cmd):
        # UI 线程与传输线程共用这条连接：锁住整次问答，防输出串台（代价：期间另一方等待）
        with self.lock:
            return self._run(cmd)

    def _run(self, cmd):
        try:
            self.p.stdin.write(f"{cmd}; echo @EOC@$?\n".encode())
            self.p.stdin.flush()
        except OSError:
            self._start()  # 设备断开后重连，本次返回空
            return "", -1
        out = []
        while True:
            line = self.p.stdout.readline()
            if not line:  # 进程已退出
                self._start()
                return b"".join(out).decode("utf-8", "replace").replace("\r", ""), -1
            out.append(line)
            if line.startswith(b"@EOC@"):  # ponytail: 文件名恰为 @EOC@+数字 时会截断，可忽略
                out.pop()  # 定界行不给调用方
                text = b"".join(out).decode("utf-8", "replace").replace("\r", "")
                return text, int(line[5:].strip() or -1)


def transfer(kind, serial, src, dst, on_line):
    """跑 adb pull/push（宿主机子进程，不走常驻 shell）。阻塞；每行输出回调 on_line；返回退出码。"""
    p = subprocess.Popen([ADB, *(["-s", serial] if serial else []), kind, src, dst],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=NOWIN)
    for raw in p.stdout:
        on_line(raw.decode("utf-8", "replace"))
    p.wait()
    return p.returncode


if __name__ == "__main__":
    assert parse_progress("[  42%] /sdcard/x") == 42
    assert parse_progress("1 file pulled, 0 skipped.") is None
    assert sh_quote("a b'c") == "'a b'\\''c'"
    print("ok")
