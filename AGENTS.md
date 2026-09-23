# AGENTS.md

面向在此仓库工作的 coding agent。**只写"看代码不容易知道"的约束**。

## 这是什么

一个 **Windows 剪贴板后台守护进程**：让 Win+Shift+S 截的图能在资源管理器里 **Ctrl+V 粘贴成文件**。

**核心机制**（改代码前必须理解）：
Win+Shift+S 只在剪贴板放位图/PNG 等**图像格式**，而 Explorer 粘贴文件需要
**文件类格式 `CF_HDROP`**。本工具在后台监听剪贴板更新，检测到图片时：
① 写一张时间戳命名的临时 PNG；② 给剪贴板**补一个 `CF_HDROP`**；
③ **保留**原有 DIB / DIBV5 / PNG 格式（否则 Word / 画图里就贴不出图了）。

## 常用命令

```bash
pythonw main.py                      # 跑（无控制台窗口，推荐）
python main.py                       # 跑（带控制台日志，调试用）
python tools/selftest_engine.py      # 自测：最后一行必须是「最终判定: PASS」
python tools/capture_clipboard.py --wait N   # 剪贴板格式侦查（看当前有哪些格式）
build.bat                            # PyInstaller 打包 → dist\ScreenshotPasteAssistant\
```

环境：**Windows + Python 3.9+**（开发于 3.11）。非 Windows 平台不适用（依赖 `win32clipboard` /
`win32gui` 与 `WM_CLIPBOARDUPDATE`）。

## 改动纪律（重要）

1. **`clipboard_engine.py` 是核心，改动必须跑自测**（`tools/selftest_engine.py`）。
   剪贴板是全局共享资源，写坏了会影响整个系统而不只是本程序。
2. **保留原图像格式是硬要求**：只补 `CF_HDROP` 而不保留 DIB/DIBV5/PNG，会让 Word、
   画图等程序粘贴失败。改重建逻辑时不要"顺手清理"旧格式。
3. **已知取舍不要"修"**：重建后 Windows 私有 OLE 格式无法还原，所以 Win+V 云剪贴板/
   历史可能不收录 —— 这是**有意为之**（`SetClipboardData` 无法还原私有格式）。
   见 README 的「已知取舍」。
4. **隐藏窗口 + 消息循环**：引擎靠一个隐藏窗口接收 `WM_CLIPBOARDUPDATE` 消息，
   不是轮询。涉及线程模型/退出顺序的改动先读 `DESIGN.md`（含根因、接口契约、线程模型）。
5. **`config.json` 是运行时生成、已 gitignore**；改配置结构要同步 `config.example.json`
   和 README 的配置表（中英两张表都要改）。
6. **`autostart.py` 直接运行是自测**（会启用→检查→**禁用**自启），不是 CLI 开关。
   别在文档里承诺 `autostart.py unregister` 这类参数。
7. **不要提交 `temp/`、`poc/`、`tools/captured.png`**——它们可能含真实截图（已 gitignore）。

## 目录速览

| 文件 | 职责 |
|---|---|
| `clipboard_engine.py` | 剪贴板监听 + 格式重建（隐藏窗口 + `WM_CLIPBOARDUPDATE`） |
| `main.py` | 入口：组装 config / 引擎 /（可选）托盘 |
| `tray.py` | 托盘图标（`show_tray: true` 才启用） |
| `config.py` | `config.json` 读写与热加载 |
| `autostart.py` | 开机自启（写 `HKCU\...\Run`）；直接运行是自测 |
| `tools/` | `selftest_engine.py`（自测）、`capture_clipboard.py`（格式侦查） |
| `DESIGN.md` | 设计文档：根因 / 接口契约 / 线程模型 |

## 不要做的事

- 不要为"让 Win+V 历史也能用"去尝试还原 OLE 私有格式（技术上不可行，属已知取舍）
- 不要把工具改成只处理"截图来源"的图片（Windows 剪贴板不携带该来源标记）
- 不要在没有 Windows 环境的 CI 上跑自测（它依赖真实剪贴板与 win32 API）
