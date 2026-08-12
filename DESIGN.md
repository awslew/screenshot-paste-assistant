# 截图粘贴助手（ScreenshotPasteAssistant）设计文档

> 架构决策版（Architecture Decision Record）。日期：2026-08-03。
> 目的：让 **Win+Shift+S 截图后，在文件夹里 Ctrl+V 能直接粘贴成图片文件**。
> 本文档是开发 A/B 并行开发的唯一契约。**双方只读本文档**，不互相改对方文件。

---

## 1. 根因分析（基于本机真实捕获）

Team Lead 已在本机用 `tools/capture_clipboard.py` 实测捕获真实 Win+Shift+S 剪贴板格式面：

| 格式 | 类型 | 大小 | Explorer 是否认 |
|---|---|---|---|
| CF_BITMAP | 句柄 | - | ✗（纯图像格式） |
| CF_DIB | 字节 | ~3.9MB | ✗ |
| CF_DIBV5 | 字节 | ~3.9MB | ✗ |
| "PNG" | 字节 | ~128KB | ✗ |
| "DataObject" / "Ole Private Data" | OLE 私有 | - | ✗ |
| "CanUploadToCloudClipboard" / "CanIncludeInClipboardHistory" | 云/历史标志 | - | ✗ |
| **CF_HDROP** | 文件列表 | 无 | **Explorer 唯一认的"粘贴成文件"格式** |
| **FileGroupDescriptorW/F + FileContents** | 虚拟文件描述 | 无 | Explorer 也认（虚拟文件方案） |

**结论**：Windows 资源管理器的"粘贴"（Ctrl+V）只对**文件类格式**（CF_HDROP 或 虚拟文件描述）有反应。截图剪贴板里只有位图/PNG/OLE 云格式 → 文件夹 Ctrl+V 无反应。这是 Windows 已知行为，不是用户操作问题。

**解决思路**：剪贴板出现图片时，后台补一个"文件类格式"进去（CF_HDROP 指向一张真实 PNG），其余图像格式原样保留，让 Explorer 把图当文件复制。

---

## 2. GitHub 调研结论（全部经 GitHub API 2026-08-03 实测，走代理）

### 2.1 检索执行情况

- GitHub 仓库搜索：`paste image as file` / `clipboard image save file` / `clipboard to file` / `screenshot auto save folder` / `win shift s screenshot paste file` / `clipboard watcher python image` / `CF_FILEDESCRIPTOR python` / `win32clipboard virtual file` / `IDataObject virtual file` / `clipboard drag image python` / `virtual file clipboard python windows`（共 11 条，走本机代理）。
- 中文搜索：`截图 粘贴 文件夹` / `剪贴板图片保存`（`ws.js` 国内引擎）。

### 2.2 候选项目真实数据表

| 名称 | stars | 语言 | 许可证 | 是否直接可复用 | 为什么 |
|---|---|---|---|---|---|
| **HubertBiyo/cc-clip** | 0 | PowerShell | MIT | **最佳参考（不可直接复用）** | 与本方案机制1完全一致：`AddClipboardFormatListener` + 存临时 PNG + `SetFileDropList`(CF_HDROP) + 保留位图 + `ignoreNext` 防自触发。但它是 PowerShell 脚本，非 Python，且面向"终端粘贴路径"。**它证明了机制1在生产脚本里有效**。 |
| frook1/claude-code-screenshot-paste | 0 | PowerShell | MIT | 参考 | `SetText(路径)+SetImage(位图)`，粘贴给终端是"路径文本"，Explorer 场景下不成立（无 CF_HDROP）。轮询式监听。 |
| citizenll/clipboard-image-watcher | 21 | C# | MIT | 参考 | 监听 + 存临时 PNG + 用**路径文本**替换剪贴板。面向 Claude Code 终端粘贴，非 Explorer 文件粘贴；需 .NET9 运行时。 |
| tomzorz/PasteHere | 113 | C# | MIT | 参考 | 右键"粘贴为文件"，直接写盘到当前目录后 `SHOpenFolderAndSelectItems` 选中。**不监听、不重建剪贴板**，交互形态不同（需先装进右键菜单）；2020 年停更。 |
| alexyan0431/imgclip | 22 | Rust | MIT | 不可复用 | 跨平台 CLI（Rust 生态），非 Windows 托盘常驻，无剪贴板重建。 |
| nichind/screenclip-autosave | 2 | Python | 无 | 弱参考 | Python 但功能=自动保存到 Pictures/Screenshots，无托盘/无重建，未验证。 |
| avsiel/SnipSave | 2 | C# | 无 | 弱参考 | 同构于 screenclip-autosave。 |
| ShareX/ShareX | 38,945 | C# | GPL-3.0 | 不可复用 | 接管截图流程的大而全工具，不符"只补粘贴"轻量定位；GPL 传染。 |
| microsoft/PowerToys | 137,427 | C | MIT | 不可复用 | 不做贴图，巨型仓库。 |
| Snipaste | — | — | 闭源免费 | 不可复用 | 无 GitHub 仓库；可作**验收参照标准**（它能粘贴进文件夹）。 |

### 2.3 关键技术结论

- **机制2（虚拟文件 CF_FILEDESCRIPTORW + CF_FILECONTENTS + 自定义 IDataObject）的 Python 实现：GitHub 检索全部为 0 有效结果**（`CF_FILEDESCRIPTOR python`、`win32clipboard virtual file`、`IDataObject virtual file`、`virtual file clipboard python windows`、`DragDropLib python` 均无命中）。没有被验证、可直接抄的 Python 完整实现。
- 机制1 的等效做法在多项目（cc-clip / clipboard-image-watcher / PasteHere）被使用，且**本机 POC 已真机验证通过**。

### 2.4 结论

**自研**。没有现成可直接复用的 Python 实现，但机制1 的技术路线已被多个开源项目 + 本机 POC 双重验证，自研风险低、工作量小（核心 ~100 行）。参照标准：Snipaste 的"粘贴进文件夹"行为。

---

## 3. 最终技术方案

### 3.1 方案对比与决策

| 维度 | 机制1：CF_HDROP 临时文件法 | 机制2：虚拟文件法（IDataObject + CF_FILEDESCRIPTORW/FILECONTENTS） |
|---|---|---|
| 实现复杂度 | ~30 行核心，纯 `win32clipboard` + `ctypes` | 需自定义 COM `IDataObject`（pythoncom/comtypes 手写 vtable） |
| 依赖 | pywin32 + PIL（已装） | comtypes（**未安装**）+ 手写 COM，调试成本极高 |
| GitHub 现成 Python 实现 | 有思路可参考（cc-clip 等效，但非 Python） | **检索 0 结果，无可靠实现可抄** |
| 可靠性 | 本机 5/5 真机 Explorer 粘贴成功 | 未验证；COM 接口细碎，易踩坑 |
| 磁盘副作用 | 临时文件真实存在，需清理（COPY 语义，源文件保留） | 无（文件在内存中延迟渲染） |
| 语义 | "粘贴=把临时 PNG 复制进目标文件夹"，文件名/内容与截图一致 | 粘贴时由 shell 按需取字节，效果相同 |

**决策：机制1（CF_HDROP 临时文件法）。** 理由：
1. 代码简单优先、个人工具不过度设计（决策原则）。
2. GitHub 上**没有**被验证、可直接抄的机制2 Python 完整实现 → 机制2 的评估标准不满足。
3. 机制1 已在本机真机验证（POC 5 次 Explorer 真实粘贴全部成功），零未知风险。
4. "粘贴实为文件复制"的语义在 Explorer 场景下与用户期望完全一致（用户本来就是要"粘贴成文件"）。
5. 机制1 完整满足全部 5 条验收标准（见 §8）。

### 3.2 工作模式

- **A 模式（补粘贴，默认）**：剪贴板出现图片 → 后台重建剪贴板，补 CF_HDROP（指向一张时间戳命名的临时 PNG），保留 DIB/DIBV5/PNG 图像格式 + Preferred DropEffect=COPY。用户随后在任何文件夹 Ctrl+V，Explorer 就把该 PNG 复制进当前文件夹；Word/画图照常贴图（图像格式被保留，POC 已验证）。
- **B 模式（自动保存降级）**：不重建剪贴板，直接把图片 PNG 存到 `save_dir`（默认=当前用户 `图片\Screenshots`，`save_dir` 留空自动解析）。作为 A 模式的降级/替代。

### 3.3 A 模式核心流程（`clipboard_engine.py`）

```
WM_CLIPBOARDUPDATE 到达（隐藏窗口注册 AddClipboardFormatListener 后，任何剪贴板变化都会收到）
  ├─ 重建进行中（_rebuilding=True，Empty/Set 触发的同步重入）? → 忽略（防自触发）
  ├─ 剪贴板无图片（无 DIB/DIBV5/PNG/常见图格式）? → 忽略（仅图片触发）
  ├─ 剪贴板已含 CF_HDROP? → 忽略（重建后的异步回声 / 已是文件类，别叠加工）
  └─ 否则执行重建：
      1) _rebuilding=True
      2) OpenClipboard（复用 tools/capture_clipboard.py 的 _open_clipboard_retry，多进程占用时重试）
      3) 读原始字节：CF_DIBV5 / CF_DIB / "PNG"（能读到的都读）
      4) 生成文件名：filename_format.strftime → temp_dir/截图_YYYY-MM-DD_HH-MM-SS.png
         （同秒重名 → 追加 _1/_2；POC 验证临时文件名即粘贴后文件名）
      5) EmptyClipboard
      6) SetClipboardData 依次：DIBV5(有则) / DIB(有则) / "PNG" / CF_HDROP(临时路径) / "Preferred DropEffect"=1(COPY)
      7) CloseClipboard
      8) finally: _rebuilding=False
```

> **2026-08-03 修订（验收修复）**：早期用 `_suppress_until = now+2s` 时间窗防自触发，验收发现它会吞掉 2s 内快速连截的第二张（DESIGN §8 验收项2 不满足）。改为 `_rebuilding` 标志拦"重建进行中的同步重入"，异步回声靠"已有 CF_HDROP"拦——两者之外的无 HDROP 更新一律视为用户真实新操作并重建。快速连截/重复复制均正常。

### 3.4 关键实现要点

- **CF_HDROP 字节构造**（POC 已验证）：
  ```python
  # DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; } = 20 字节
  header = struct.pack("<IiiII", 20, 0, 0, 0, 1)   # pFiles=20, pt=(0,0), fNC=0, fWide=1(Unicode)
  data = header + path.encode("utf-16le") + b"\x00\x00" + b"\x00\x00"
  ```
- **CF_HDROP 读回验证**：pywin32 `GetClipboardData(CF_HDROP)` 返回 None（句柄类型），须用 `ctypes` 调 `shell32.DragQueryFileW`（不是 user32）。POC 已验证。
- **系统自动合成 CF_BITMAP/CF_DIBV5**：只显式 Set CF_DIB 时，Windows 会自动补 CF_BITMAP 与 DIBV5 → Word/画图/浏览器拿图无障碍。
- **`Preferred DropEffect`** = `struct.pack("<I", 1)`（DROPEFFECT_COPY）。用 COPY 而非 MOVE：临时文件保留，便于复用/清理。
- **自触发防护（双闸，2026-08-03 起替代旧 2s 时间窗）**：
  1. `_rebuilding` 布尔标志：重建开始（EmptyClipboard 前）置 True、`finally` 置 False——拦 EmptyClipboard/SetClipboardData 可能触发的**同步重入**；
  2. "剪贴板已含 CF_HDROP 则跳过"——重建完成后的**异步回声**到达时剪贴板必然已带 CF_HDROP，见之即跳过，同时天然放过"用户复制文件"的场景。
  3. 剪贴板操作全部在主线程消息回调内同步完成，天然串行，无并发竞争。
  两者之外，任何无 HDROP 的图片剪贴板更新 = 用户真实新操作 → 一律放行重建（快速连截/重复复制都正常）。
- **临时文件清理**：启动时 + 每 1 小时，删除 `temp_dir` 中 24h 前创建的 *.png。
- **已知取舍**：重建后不再含 `DataObject / Ole Private Data / CanUploadToCloudClipboard / CanIncludeInClipboardHistory`（OLE 私有数据无法经 SetClipboardData 还原）。影响：Win+V 云剪贴板/历史可能不收录重建内容。可接受。

---

## 4. 模块划分与文件所有权（铁律，并行开发避免冲突）

| 开发 | 独占文件 | 说明 |
|---|---|---|
| **开发 A** | `clipboard_engine.py`、`main.py`、`poc/`、`tools/` | 剪贴板监听+重建核心、入口组装、POC（已存在 `tools/capture_clipboard.py`） |
| **开发 B** | `tray.py`、`config.py`、`autostart.py`、`config.json`、`requirements.txt`、`build.bat` | 托盘、配置读写、开机自启、打包脚本 |
| 双方 | `DESIGN.md` | **只读**，不修改 |

- `main.py` 由开发 A 组装三方（唯一耦合点）：
  ```python
  from config import load_config
  from tray import TrayApp
  from clipboard_engine import ClipboardWatcher
  ```
- 依赖方向：`main.py` → config / tray / clipboard_engine；`tray.py` 不 import clipboard_engine，只通过回调松耦合。
- **任何一方不得修改对方文件**；接口有异议 → 回这里改 DESIGN.md 再同步。

---

## 5. 接口契约（A/B 各自按此实现，互不阻塞）

### 5.1 `config.py`（开发 B）
```python
def load_config() -> dict          # 读 config.json；不存在则写默认值并返回
def save_config(cfg: dict) -> None # 原子写回 config.json（tmp+rename）
```
`config.json` 键（默认值明确）：

| 键 | 默认值 | 含义 |
|---|---|---|
| `filename_format` | `"截图_%Y-%m-%d_%H-%M-%S"` | 保存文件名 strftime 模板（不含扩展名） |
| `mode` | `"A"` | `"A"`=补粘贴（默认）；`"B"`=自动保存降级 |
| `autostart` | `false` | 是否开机自启 |
| `save_dir` | `""`（空=自动） | B 模式保存目录；空则用 `~/Pictures/Screenshots` |
| `temp_dir` | `""`（空=自动） | A 模式临时目录；空则用 `exe_dir/temp` |
| `paused` | `false` | 初始暂停状态 |

### 5.2 `tray.py`（开发 B）
```python
def TrayApp(pause_cb, resume_cb, exit_cb, config: dict, on_reconfig):
    # pystray 托盘图标；菜单：状态(灰显) / 暂停 / 恢复 / 打开配置 / 退出
    # pause_cb / resume_cb：切换引擎暂停（线程安全）
    # exit_cb：优雅退出（通知主循环收尾）
    # on_reconfig(cfg)：配置改动后回调引擎刷新
```
- 用 `pystray.Icon`；无自定义图标时在程序内生成一个简单 PNG 作为图标。
- 线程模型：`icon.run_detached()`（pystray 自带后台线程），菜单回调只做**线程安全通信**（见 §6），不直接碰剪贴板。

### 5.3 `clipboard_engine.py`（开发 A）
```python
def ClipboardWatcher(handler):
    # 内部：隐藏窗口 + AddClipboardFormatListener；收到 WM_CLIPBOARDUPDATE 时
    #   先做 _suppress / 图片检测 / 已有CF_HDROP 三道闸，通过则调 handler(...)
    # handler 签名：handler(clipboard_snapshot: dict, is_image: bool)
    def start(self): ...   # 注册窗口并进入/接管消息循环
    def stop(self):  ...
    # 内部维护 _suppress_until 时间戳 + _rebuild_image() 重建核心
```
- `handler` 由 `main.py` 传入，负责读取原格式并调用引擎的 `_rebuild_image()`；引擎负责监听与防自触发。
- 重建逻辑从 `poc/paste_as_file_poc.py` 的 `rebuild_clipboard` 演进而来。

### 5.4 `autostart.py`（开发 B）
- `set_autostart(enable: bool)`：写/删 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 项（值 = exe 完整路径）。
- 打包后读 `sys.executable`；源码运行读 `sys.argv[0]`（或跳过）。

---

## 6. 线程模型（关键，A/B 都按此实现）

```
主线程（消息循环）：
  创建隐藏窗口 win32gui.CreateWindow → AddClipboardFormatListener(hwnd)
  → win32gui.PumpMessages()（main.py 持有）
  收到 WM_CLIPBOARDUPDATE → 在消息回调内【同步】完成剪贴板重建（操作耗时毫秒级）
  剪贴板操作全部在主线程 → 无并发竞争

托盘线程（pystray icon.run_detached）：
  菜单回调（暂停/恢复/退出/改配置）通过线程安全通道与主线程通信：
    - 暂停/恢复 → 共享 threading.Event（引擎检测，或 main 循环定时查询）
    - 退出 → 向隐藏窗口 PostMessage(WM_APP+1)（主循环收到即退出）
    - 改配置 → 向隐藏窗口 PostMessage(WM_APP+2)，主循环在回调里读 config 并刷新引擎
  （原则：托盘线程【绝不】直接 OpenClipboard / 碰引擎内部状态）

自触发防护（2026-08-03 修订）：
  _rebuilding 标志（重建进行中置 True，finally 复位）→ 拦 Empty/Set 的同步重入
  + "剪贴板已含 CF_HDROP 则跳过" → 拦重建完成后的异步回声 + 放过真实文件复制
  （主线程串行 + 双闸 = 无死循环；无 HDROP 的更新一律视为用户新操作，快速连截不丢）

OpenClipboard 占用：复用 tools/capture_clipboard.py 的 _open_clipboard_retry
  （枚举验证真打开，失败重试最多 30 次、间隔 50ms）
```

---

## 7. 功能清单映射

| # | 功能 | 实现模块 | 说明 |
|---|---|---|---|
| 1 | 后台常驻监听（**仅图片触发**） | `clipboard_engine.py` (A) + `main.py` (A) | 隐藏窗口 + AddClipboardFormatListener；三道闸过滤非图片/自触发/已有文件格式 |
| 2 | 检测截图→重建→用户 Ctrl+V 存当前文件夹 + 时间戳去重 | `clipboard_engine.py` (A) | A 模式：补 CF_HDROP + 保留图像格式；文件名 `filename_format`（秒级）+ 同秒重名加序号 |
| 3 | 托盘：状态 / 暂停 / 退出 / 改保存名格式 | `tray.py` (B) | pystray；`paused` 持久化到 config.json |
| 4 | 开机自启（HKCU\...\Run） | `autostart.py` (B) + `tray.py` (B) | 托盘菜单"开机自启"开关 |
| 5 | config.json 可配（文件名格式、A/B 切换） | `config.py` (B) + `config.json` (B) | `load_config/save_config`；A/B 模式切换在引擎侧生效 |

---

## 8. 验收标准对照表

| # | 验收标准 | 验证方式 |
|---|---|---|
| 1 | 后台常驻监听，仅图片触发（复制文本/文件不触发重建） | 手动：跑工具→复制一段文本→复制一个文件→枚举剪贴板应无新增 CF_HDROP；再按 Win+Shift+S 截图→应出现 CF_HDROP |
| 2 | 截图后 Ctrl+V 能在文件夹里存成图片文件，文件名=配置模板+时间戳，去重 | 手动（真机）：按 Win+Shift+S → 打开任意文件夹 → Ctrl+V → 出现 `截图_2026-08-03_18-37-15.png`；同秒截两张→出现 `_1` 后缀；对比文件字节与剪贴板 PNG 一致。**POC 已预验证此机制（5/5 成功）** |
| 3 | 托盘可用：状态显示/暂停/恢复/退出/改文件名格式 | 手动：托盘图标→暂停（截图不再重建）→恢复→改格式（config.json 生效）→退出进程干净结束 |
| 4 | 开机自启生效 | 手动：托盘开自启→重启→工具自动在托盘；注册表 `HKCU\...\Run` 有项；关闭自启→项被删 |
| 5 | config.json 全键可配，A/B 模式切换生效 | 手动/脚本：改 `mode="B"` + `save_dir` → 截图后自动存到 save_dir 且不重建剪贴板；`mode="A"` → 恢复补粘贴 |
| (补充) | Word/画图仍能正常贴图 | 手动：截图→重建后→Word 里 Ctrl+V 应插入图片（图像格式被保留） |

---

## 9. 打包（开发 B 交付 `build.bat`）

- 推荐 **PyInstaller `--noconsole --onedir`**（onefile 启动慢、杀软误报多；个人工具 onedir 更稳）。产物 `dist/ScreenshotPasteAssistant/`。
- 命令（`build.bat`）：
  ```bat
  pyinstaller --noconsole --onedir --name ScreenshotPasteAssistant ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageGrab ^
    --hidden-import win32timezone ^
    --collect-all pystray ^
    main.py
  ```
- 要点：pystray 图标/后台线程、pywin32 的 `win32timezone`、PIL 的 ImageGrab 常被漏掉 → 必须显式 hidden-import。pystray 若带资源图标，用 `--collect-all pystray` 兜底。
- 依赖写进 `requirements.txt`：`pywin32`、`Pillow`、`pystray`、`pyautogui`（仅验收脚本用，可注释）。
- `temp_dir` 默认值在打包后解析为 exe 所在目录的 `temp/`（用 `os.path.dirname(sys.executable)`）。

---

## 10. 开发任务清单

### 10.1 开发 A 任务清单

| # | 任务 | 依赖 | 验收点 |
|---|---|---|---|
| A1 | 从 `poc/paste_as_file_poc.py` 提炼 `clipboard_engine.py`：`ClipboardWatcher(handler)`，隐藏窗口 + AddClipboardFormatListener + WM_CLIPBOARDUPDATE，三道闸（_suppress 时间戳 / 图片检测 / 已有 CF_HDROP 跳过），`start()/stop()` | 无 | 独立测试：复制文本→无回调；放入图片→handler 被调；重建后不再自触发 |
| A2 | 引擎重建核心 `_rebuild_image()`：读原 DIBV5/DIB/PNG → 生成临时 PNG（`filename_format` + 去重）→ 单会话重建 5 格式（含 CF_HDROP 构造）→ `_suppress_until=now+2s` | A1 | 复用 POC 读回验证（ctypes DragQueryFile）；PowerShell `GetFileDropList` 路径正确 |
| A3 | 临时文件清理：启动时 + 每小时删除 temp_dir 中 24h 前的 *.png | A2 | 造旧文件→重启→被清理 |
| A4 | `main.py`：读 `config.py` 的 `load_config()` → 建隐藏窗口 + `ClipboardWatcher` → `TrayApp`（传 pause/resume/exit/on_reconfig 回调）→ `win32gui.PumpMessages()`；WM_APP+1/2 消息处理（退出/刷新配置） | A1/A2 + B1/B2 完成接口 | 整体能跑；托盘菜单操作生效 |
| A5 | A/B 模式分派：`mode=="B"` 时截图→直接存 `save_dir`，不重建 | A2 + B1 | 见验收#5 |
| A6 | 自测脚本：`tools/selftest_engine.py`（枚举剪贴板+触发一次重建+读回断言+快速连截回归） | A2 | 一键自测 PASS |

### 10.2 开发 B 任务清单

| # | 任务 | 依赖 | 验收点 |
|---|---|---|---|
| B1 | `config.py`：`load_config()/save_config()`，键与默认值按 §5.1 | 无 | 首次运行生成 config.json；缺键补默认；原子写 |
| B2 | `tray.py`：`TrayApp(...)` 按 §5.2；菜单：状态/暂停/恢复/退出/打开配置/开机自启开关；图标程序内生成 | B1 | 托盘图标显示；暂停/恢复改 config `paused` 并回调引擎 |
| B3 | `autostart.py`：HKCU Run 写删 | 无 | 见验收#4 |
| B4 | `requirements.txt`、`build.bat`（§9） | 全部 | PyInstaller 打包成功；双击 exe 托盘出现、截图粘贴可用 |
| B5 | 与 A 联调：`main.py` 组装三模块跑通（A4 的联调部分） | A4 + B1/B2 | 全功能手动验收清单走一遍 |

### 10.3 联调里程碑

1. **M1（无托盘）**：A1/A2/A5 + 临时 main（无托盘）→ 截图→文件夹 Ctrl+V 出文件。
2. **M2（全功能）**：A4 + B1/B2/B3 → 托盘、自启、配置、退出齐活。
3. **M3（交付）**：B4 打包 → 双击 exe 全流程验收（§8 五条 + Word 贴图）。

---

## 附：已确认事实与引用

- 本机 Win+Shift+S 真实格式面：`tools/capture_clipboard.py` 输出（CF_BITMAP/CF_DIB/CF_DIBV5/"PNG"/DataObject/Ole Private Data/云标志，无 CF_HDROP 无虚拟文件描述）。
- POC 实证：`poc/POC_RESULT.md`；脚本 `poc/paste_as_file_poc.py`、`poc/test_rebuild_realpath.py`。
- 开源参照：HubertBiyo/cc-clip（机制1 等效，PowerShell/MIT）、citizenll/clipboard-image-watcher（C#/MIT）、tomzorz/PasteHere（C#/MIT）、alexyan0431/imgclip（Rust/MIT）、ShareX(GPL-3.0)、PowerToys(MIT)、Snipaste(闭源)。
