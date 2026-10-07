# ADB 文件浏览器

浏览 Android 设备文件（通过 adb）：左边目录树懒加载，右边文件列表（含大小），支持拉取/推送/删除/重命名/新建文件夹，带传输进度队列、文件名过滤（通配符）、多设备切换、拖拽收发文件。

## 运行

adb 不需要另装：仓库 `bin/` 里自带 [adb-chinese](https://github.com/gws0920/adb-chinese) 编译的 adb.exe（修复了 Windows 下官方 adb 的中文路径问题），程序只认它，PATH 里的官方 adb 不受影响。首次连接设备记得在手机上授权。

```
uv run main.pyw
```

### 打包

```
uv run pyinstaller --noconfirm adb-browser.spec
```

产出 `dist/adb-browser/`（目录版、无控制台，整个目录拷走即可运行）。打包配置都在 [adb-browser.spec](adb-browser.spec)：`--collect-all tkinterdnd2`（拖拽后端的 tcl 扩展）、`--add-data` 带入窗口图标与 `bin/` 的 adb，excludes 砍掉用不到的 Pillow 编解码器（AVIF/色彩管理/WebP）与 ssl/_hashlib（应用无网络、无加密）。改打包选项直接编辑 spec 文件。

## 操作

| 操作 | 方式 |
|---|---|
| 进入目录 | 双击 / 树展开 / 列表上回车 |
| 打开文件 | 双击文件（拉到临时目录后用系统关联程序打开） |
| 上级 | ↑ 按钮 / Backspace |
| 刷新 | F5 |
| 重命名 | F2 / 右键 |
| 删除 | Delete / 右键（目录递归删除，有确认） |
| 全选 | Ctrl+A |
| 排序 | 点击表头（文件/大小/修改时间），再点一次反向；目录始终在前 |
| 调列宽 | 拖动表头分隔线（文件列表与传输队列都有） |
| 收藏路径 | 路径栏 ★ 按钮：收藏当前路径、点条目跳转、删除收藏（存 `~/.adb-browser.json`） |
| 过滤 | 直接打字（自动聚焦过滤框）/ Esc 清空；含 `* ? [` 时按通配符，否则子串，均忽略大小写 |
| 拉取 / 推送 | 右键菜单，或**拖入**文件到列表（拖出暂未开放） |
| 切换设备 | 左上设备下拉框（⟳ 刷新在线设备） |

列表有大小列（文件为人类可读大小，目录为子条目数）和修改时间列。传输完成后状态列显示 adb 摘要：文件数、平均速度、总大小、耗时。

## 结构

```
main.pyw              入口（高 DPI 适配、日志、拖拽后端）
core/
  adb.py              命令层：常驻 shell、设备列表、pull/push 传输
  fs.py               纯函数：ls 解析、路径/大小处理、过滤匹配
  config.py           配置读写（~/.adb-browser.toml）
  ui/
    app.py            App 组装 + 设备/导航/历史/选中态配色
    tree.py           左树：懒加载、选择联动、右键节点操作
    filelist.py       右列表：渲染/排序/过滤、双击打开、文件操作
    transfer.py       传输队列、拖拽、临时目录拉取
    keys.py           全局快捷键与鼠标侧键
    favorites.py      收藏路径与颜色标记
```

自检：`uv run python core/fs.py && uv run python core/adb.py`，输出 `ok` 即通过。
