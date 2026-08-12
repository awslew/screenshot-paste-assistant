# 截图粘贴助手（ScreenshotPasteAssistant）

让 **Win+Shift+S 截图后，在文件夹里 Ctrl+V 就能直接粘贴成图片文件** 的轻量 Windows 后台工具。

A lightweight Windows background tool that lets you **paste a screenshot as an image file with Ctrl+V in any folder**, right after you take it with **Win+Shift+S**.

---

## 痛点 / Why this exists

Win+Shift+S 截图后，剪贴板里只有位图 / PNG 等**图像格式**，没有 Windows 资源管理器需要的「文件类」格式（`CF_HDROP`）。所以你在文件夹里按 Ctrl+V 时，Explorer 没有任何反应。

这个工具在后台监听剪贴板：

- 检测到图片 → 补一个文件类格式（`CF_HDROP` 指向一张时间戳命名的临时 PNG）
- 同时**保留原图像格式**（DIB / DIBV5 / PNG）

之后你在任意文件夹 Ctrl+V / 右键粘贴，Explorer 就会把图片当文件复制进去；Word、画图里粘贴图片也照常。

After Win+Shift+S the clipboard only carries bitmap/PNG image formats and **no file-class format** (`CF_HDROP`), so Explorer ignores Ctrl+V in a folder. This tool watches the clipboard in the background, re-exposes the image with a `CF_HDROP` file-class format, and keeps the original image formats — so pasting into a folder (or Word/Paint) just works.

## 功能 / Features

- 后台常驻，纯监听，**无窗口、无托盘**（可配置托盘）；仅图片触发，复制文本/文件不干扰
- 截图后任意文件夹 Ctrl+V / 右键粘贴 → 出现 `截图_2026-08-03_19-38-38.png`，同秒自动加 `_1` 后缀去重
- 工具启动时也会处理剪贴板上已有的图片（先截图、后启动工具也能粘贴）
- 两种模式：`A` 补粘贴（默认）/ `B` 自动保存到目录
- 配置改完保存约 1 秒自动生效；可选开机自启

- Resides in the background with no window (tray optional); only reacts to images
- Paste as file into any folder; auto `_1` dedup for same-second screenshots
- Handles an image already on the clipboard at startup
- Mode `A` = paste-as-file (default); Mode `B` = auto-save to a folder
- Config hot-reloads ~1s after save; optional autostart

## 安装 / Install

需要 Windows + Python 3.9+（开发于 3.11）。

```bash
cd ScreenshotPasteAssistant
pip install -r requirements.txt
```

Requires Windows + Python 3.9+ (developed on 3.11).

## 运行 / Run

源码运行（无控制台窗口，推荐）：

```bash
pythonw main.py
```

调试时想看到日志，用 `python main.py`（会带控制台）。首次运行会自动生成 `config.json`（可参照 `config.example.json`）。

1. **Win+Shift+S** 截图
2. 打开任意文件夹 → **Ctrl+V** → 出现图片文件

停止：`taskkill /F /IM ScreenshotPasteAssistant.exe`，或在配置里设 `"paused": true` 临时停用。

Run from source (no console): `pythonw main.py`. A `config.json` is auto-created on first run (see `config.example.json`). Then **Win+Shift+S** → open a folder → **Ctrl+V**. Stop with `taskkill /F /IM ScreenshotPasteAssistant.exe` or `"paused": true`.

## 配置 / Configuration

`config.json` 在程序目录下（源码运行 = 项目根；打包后 = exe 目录）。保存后约 1 秒自动生效。

| 键 | 默认 | 说明 |
|---|---|---|
| `filename_format` | `截图_%Y-%m-%d_%H-%M-%S` | 文件名 strftime 模板（不含扩展名） |
| `mode` | `"A"` | `A`=补粘贴（推荐）；`B`=自动保存到 `save_dir`（不重建剪贴板） |
| `save_dir` | `""` | B 模式保存目录；留空 = 自动（当前用户 `图片\Screenshots`） |
| `temp_dir` | `""` | A 模式临时目录；留空 = 程序目录下 `temp`（24h 自动清理） |
| `paused` | `false` | `true` = 暂停（截图不再被处理，Ctrl+V 恢复系统原始行为） |
| `autostart` | `false` | 开机自启标记（可在托盘菜单切换） |
| `show_tray` | `false` | `true` = 显示托盘图标（暂停 / 恢复 / 打开配置 / 自启 / 退出） |

| Key | Default | Description |
|---|---|---|
| `filename_format` | `截图_%Y-%m-%d_%H-%M-%S` | strftime filename template (no extension) |
| `mode` | `"A"` | `A` = paste-as-file (default); `B` = auto-save to `save_dir` |
| `save_dir` | `""` | B-mode save dir; empty = `Pictures\Screenshots` under the current user |
| `temp_dir` | `""` | A-mode temp dir; empty = `<program>\temp` (auto-cleaned after 24h) |
| `paused` | `false` | `true` = pause processing |
| `autostart` | `false` | Run at logon (toggleable in the tray menu) |
| `show_tray` | `false` | `true` = show a tray icon (pause/resume/open config/autostart/quit) |

## 构建 / 自测 / Build & self-test

```bash
# 自测（期望最后一行输出 最终判定: PASS）
python tools/selftest_engine.py

# 剪贴板格式侦查
python tools/capture_clipboard.py [--wait N]

# 打包成独立程序（产物 dist\ScreenshotPasteAssistant\）
build.bat
```

Self-test: `python tools/selftest_engine.py` (expect `最终判定: PASS`). Package with `build.bat` → `dist\ScreenshotPasteAssistant\` (PyInstaller `--noconsole --onedir`).

## 已知取舍 / Known trade-offs

- 重建后剪贴板不再包含 Windows 云剪贴板 / OLE 私有格式 → **Win+V 云剪贴板、剪贴板历史可能不收录**重建内容（Windows 私有格式无法经 `SetClipboardData` 还原；不影响核心目标）。
- 工具对**所有**复制到剪贴板的图片都生效（不只是截图）；不需要时在配置里 `"paused": true`。
- 临时 PNG 采用 COPY 语义（源文件保留），由程序每小时清理 24h 前的文件。

- After rebuilding, Win+V cloud clipboard / clipboard history may not record the content (Windows private OLE formats can't be restored via `SetClipboardData`; the core goal is unaffected).
- The tool enhances **all** images copied to the clipboard, not just screenshots. Set `"paused": true` if not needed.
- Temp PNGs are COPY-semantics and auto-cleaned hourly (files older than 24h).

## 目录结构 / Layout

```
clipboard_engine.py   # 剪贴板监听 + 重建核心（隐藏窗口 + WM_CLIPBOARDUPDATE）
main.py               # 入口：组装 config / 引擎 /（可选）托盘
tray.py               # 托盘（show_tray=true 才启用）
config.py             # config.json 读写
autostart.py          # 开机自启（HKCU\...\Run）
build.bat             # PyInstaller 打包
tools/                # capture_clipboard.py / selftest_engine.py
DESIGN.md             # 设计文档（根因 / 接口契约 / 线程模型）
LICENSE               # MIT
```

## License

MIT — see [LICENSE](LICENSE).
