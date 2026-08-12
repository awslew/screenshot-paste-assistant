"""剪贴板引擎：后台监听剪贴板，截图出现时补 CF_HDROP，让"文件夹 Ctrl+V 直接粘贴成图片文件"。

设计来源：DESIGN.md §3.3 / §5.3 / §6；核心重建逻辑从 poc/paste_as_file_poc.py 提炼（POC 已验证 5/5）。
仅依赖 pywin32 / Pillow，不 import config.py（与开发B 解耦，配置由 main.py 传入）。

职责：
  - 隐藏窗口 + AddClipboardFormatListener，收到 WM_CLIPBOARDUPDATE(0x031D)
  - 三道闸过滤：_rebuilding 同步闸（重建进行中）/ 无图片 / 已有 CF_HDROP
  - 通过后调 handler(snapshot, is_image)（main.py 的 handler 负责 A/B 模式分派）
  - rebuild()：读原 DIBV5/DIB/PNG 字节 → 写临时 PNG → EmptyClipboard → 依次 Set
      DIBV5 / DIB / "PNG" / CF_HDROP / "Preferred DropEffect"=COPY
  - 临时文件清理：启动时 + 每小时删 temp_dir 中 24h 前的 *.png

自触发防护（无 2s 时间窗，快速连截不受影响）：
  - 重建进行中的同步重入（Empty/Set 触发）→ _rebuilding 标志拦截
  - 重建完成后的异步回声 → 剪贴板必带 CF_HDROP，已有 HDROP 闸拦截
  - 其余任何无 HDROP 的剪贴板更新 = 用户真实新操作 → 一律放行

线程模型（DESIGN §6）：窗口、监听、重建全部在主线程消息回调内同步完成；
托盘线程只通过 PostMessage(WM_APP+1/2) 与本窗口通信，绝不直接碰剪贴板。
"""
import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes

import pywintypes
import win32api
import win32clipboard
import win32con
import win32gui

# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────
WM_CLIPBOARDUPDATE = 0x031D  # AddClipboardFormatListener 后剪贴板变化通知
WM_APP = 0x8000               # 托盘→主线程 自定义消息基址（WM_APP+1 退出 / +2 改配置）
_CLEANUP_TIMER_ID = 1
_CLEANUP_INTERVAL_MS = 3600 * 1000   # 每小时清理
_TEMP_FILE_MAX_AGE = 24 * 3600       # 24 小时前的临时 PNG 删除
DROPEFFECT_COPY = 1                  # Preferred DropEffect = COPY
_CLASS_NAME = "ScreenshotPasteAssistant_ClipboardWatcher"

# ─────────────────────────────────────────────────────────────
# user32 缺失的少量 API（pywin32 312 未导出，用 ctypes 直调）
# ─────────────────────────────────────────────────────────────
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_AddClipboardFormatListener = _user32.AddClipboardFormatListener
_AddClipboardFormatListener.argtypes = [wintypes.HWND]
_AddClipboardFormatListener.restype = wintypes.BOOL

_RemoveClipboardFormatListener = _user32.RemoveClipboardFormatListener
_RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
_RemoveClipboardFormatListener.restype = wintypes.BOOL

_SetTimer = _user32.SetTimer
_SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
_SetTimer.restype = wintypes.UINT

_KillTimer = _user32.KillTimer
_KillTimer.argtypes = [wintypes.HWND, wintypes.UINT]
_KillTimer.restype = wintypes.BOOL

# ─────────────────────────────────────────────────────────────
# 剪贴板基础操作（模式同 tools/capture_clipboard.py 的 _open_clipboard_retry）
# ─────────────────────────────────────────────────────────────
def _open_clipboard_retry(tries=30, delay=0.05):
    """OpenClipboard 可能因其他进程占用而失败（pywin32 部分版本失败时不抛异常），
    用 EnumClipboardFormats 验证是否真的打开，失败则重试。"""
    for _ in range(tries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EnumClipboardFormats(0)  # 验证打开成功
            except pywintypes.error:
                win32clipboard.CloseClipboard()
                time.sleep(delay)
                continue
            return True
        except Exception:
            time.sleep(delay)
    return False


def _get_bytes(fmt):
    """读某格式字节（须在剪贴板已打开时调用）；句柄/非字节类返回 None。"""
    try:
        d = win32clipboard.GetClipboardData(fmt)
        return d if isinstance(d, bytes) else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# 图片判定 / CF_HDROP 构造 / DIB→PNG 转换
# ─────────────────────────────────────────────────────────────
# 常见"已注册图片格式"名（见 tools/capture_clipboard.py WELL_KNOWN_NAMES）
_IMAGE_FORMAT_NAMES = {
    "PNG", "JFIF", "GIF", "JPG", "JPEG", "TIFF", "WEBP",
    "image/png", "image/jpeg", "image/bmp", "image/gif", "image/tiff",
    "Snipping Tool image", "System.Drawing.Bitmap", "NBitmap",
    "DeviceIndependentBitmap",
}


def _has_image(fmt_ids, names):
    """剪贴板是否含位图类图片（格式 id 面 + 注册名面双查）。"""
    if (win32con.CF_DIB in fmt_ids or win32con.CF_DIBV5 in fmt_ids
            or win32con.CF_BITMAP in fmt_ids):
        return True
    return bool(_IMAGE_FORMAT_NAMES & names)


def build_cf_hdrop(file_path):
    """构造 CF_HDROP 字节：DROPFILES 结构 + UTF-16 路径 + 双空终止（POC 已验证）。

    DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; } = 20 字节
    pFiles = 20（文件列表相对结构头的偏移），fWide = 1（Unicode）。
    """
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    return header + file_path.encode("utf-16le") + b"\x00\x00" + b"\x00\x00"


def _dib_to_png(dib_bytes):
    """CF_DIB / CF_DIBV5 字节（BITMAPINFOHEADER + 像素）→ PNG 字节；失败返回 None。"""
    try:
        import io
        from PIL import Image

        if not dib_bytes or len(dib_bytes) < 40:
            return None
        hdr_size = struct.unpack_from("<I", dib_bytes, 0)[0]
        if hdr_size not in (40, 124):          # BITMAPINFOHEADER / BITMAPV5HEADER
            return None
        width = struct.unpack_from("<i", dib_bytes, 4)[0]
        height = struct.unpack_from("<i", dib_bytes, 8)[0]
        bpp = struct.unpack_from("<H", dib_bytes, 14)[0]
        if width <= 0 or height == 0 or bpp not in (24, 32):
            return None
        top_down = height < 0                  # 负高度 = 自顶向下（DIBV5 常见）
        height = abs(height)
        row_bytes = ((width * bpp + 31) // 32) * 4   # 每行 4 字节对齐
        data = dib_bytes[hdr_size:]
        if len(data) < row_bytes * height:
            return None
        mode = "RGBA" if bpp == 32 else "RGB"
        raw = "BGRA" if bpp == 32 else "BGR"
        img = Image.frombuffer(
            mode, (width, height), data,
            "raw", raw, row_bytes, 1 if top_down else -1,
        )
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


def _default_temp_dir():
    """temp_dir 为空时的默认：exe 目录/temp（源码运行=脚本目录/temp）。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(base, "temp")


# ─────────────────────────────────────────────────────────────
# 模块级 WndProc（pywin32 窗口过程用静态函数 + hwnd→实例 映射最稳）
# ─────────────────────────────────────────────────────────────
_WATCHERS = {}
_class_atom = None


def _global_wnd_proc(hwnd, msg, wparam, lparam):
    watcher = _WATCHERS.get(hwnd)
    if watcher is not None:
        return watcher._wnd_proc(hwnd, msg, wparam, lparam)
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


class ClipboardWatcher:
    """剪贴板监听 + 重建引擎。

    用法：
      engine = ClipboardWatcher(handler, on_message=on_message)
      engine.set_settings(cfg)   # filename_format / temp_dir / mode / paused
      engine.start()             # 建隐藏窗口 + AddClipboardFormatListener（主线程）
      win32gui.PumpMessages()    # 主消息循环（main.py 持有）
      engine.stop()

    handler(snapshot: dict, is_image: bool)：三道闸全过时调用，由 main.py 负责
      A/B 模式分派（A→engine.rebuild()；B→engine.save_snapshot_to_dir(...)）。
    on_message(hwnd, msg, wparam, lparam)：WM_APP+1/WM_APP+2 转发给 main.py。
    """

    def __init__(self, handler=None, on_message=None):
        self._handler = handler
        self._on_message = on_message
        self._hwnd = None
        self._paused = False
        self._mode = "A"
        self._filename_format = "截图_%Y-%m-%d_%H-%M-%S"
        self._temp_dir = _default_temp_dir()
        self._save_dir = ""                 # B 模式保存目录；空 = 自动（保存时解析）
        self._rebuilding = False              # 重建进行中（拦 Empty/Set 的同步重入）
        self._last_output_path = None         # 最近一次写出的临时/保存 PNG 路径

    # ── 对外属性 ──
    @property
    def hwnd(self):
        return self._hwnd

    @property
    def last_output_path(self):
        return self._last_output_path

    # ── 配置 ──
    def set_settings(self, cfg):
        if not isinstance(cfg, dict):
            return
        if cfg.get("filename_format"):
            self._filename_format = cfg["filename_format"]
        if "temp_dir" in cfg:
            td = cfg.get("temp_dir") or ""
            self._temp_dir = td.strip() or _default_temp_dir()
        if "mode" in cfg:
            self._mode = cfg["mode"]
        if "paused" in cfg:
            self._paused = bool(cfg["paused"])
        if "save_dir" in cfg:
            self._save_dir = cfg["save_dir"]

    def set_paused(self, paused):
        self._paused = bool(paused)

    # ── 生命周期 ──
    def start(self):
        """创建隐藏窗口 + AddClipboardFormatListener + 每小时清理定时器。
        必须在持有消息循环的主线程调用。返回 hwnd。"""
        global _class_atom
        if self._hwnd is not None:
            return self._hwnd
        hinst = win32api.GetModuleHandle(None)
        if _class_atom is None:
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = _CLASS_NAME
            wc.lpfnWndProc = _global_wnd_proc
            _class_atom = win32gui.RegisterClass(wc)
        hwnd = win32gui.CreateWindow(
            _CLASS_NAME, "ScreenshotPasteAssistant",
            0, 0, 0, 0, 0,   # 隐藏窗口，不显示
            0, 0, hinst, None,
        )
        if not hwnd:
            raise RuntimeError("CreateWindow 失败")
        _WATCHERS[hwnd] = self
        self._hwnd = hwnd
        if not _AddClipboardFormatListener(hwnd):
            err = ctypes.get_last_error()
            _WATCHERS.pop(hwnd, None)
            self._hwnd = None
            win32gui.DestroyWindow(hwnd)
            raise RuntimeError(f"AddClipboardFormatListener 失败 (err={err})")
        _SetTimer(hwnd, _CLEANUP_TIMER_ID, _CLEANUP_INTERVAL_MS, None)
        self._cleanup_old_files()   # 启动时清理一次
        print(f"[engine] 剪贴板监听启动 hwnd={hwnd:#x} temp_dir={self._temp_dir}")
        return hwnd

    def stop(self):
        hwnd = self._hwnd
        self._hwnd = None
        if not hwnd:
            return
        _WATCHERS.pop(hwnd, None)
        try:
            _RemoveClipboardFormatListener(hwnd)
        except Exception:
            pass
        try:
            _KillTimer(hwnd, _CLEANUP_TIMER_ID)
        except Exception:
            pass
        try:
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass

    # ── 窗口过程 ──
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            self._on_clipboard_update()
            return 0
        if msg == win32con.WM_TIMER:
            if wparam == _CLEANUP_TIMER_ID:
                self._cleanup_old_files()
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        if msg >= WM_APP:   # 托盘 → 主线程：退出(WM_APP+1) / 改配置(WM_APP+2)
            if self._on_message:
                try:
                    self._on_message(hwnd, msg, wparam, lparam)
                except Exception as e:
                    print(f"[engine] on_message 回调异常: {e}")
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    # ── 三道闸 + handler 通知 ──
    def _on_clipboard_update(self):
        """WM_CLIPBOARDUPDATE 入口 → 复用 _handle_update。"""
        self._handle_update()

    def _handle_update(self):
        """三道闸全过才调 handler。

        闸0：_rebuilding（重建进行中的同步重入）；闸1：无图片（仅图片触发）；
        闸2：已有 CF_HDROP（异步回声=自己重建的产物，别叠加工）。
        不用 2s 时间窗：快速连截（内容不同）都会放行重建。
        """
        if self._paused:
            return
        if self._rebuilding:                              # 闸0：重建进行中
            return
        snapshot = self._read_snapshot()
        if snapshot is None:
            return
        if not snapshot["has_image"]:                     # 闸1：仅图片触发
            return
        if snapshot["has_hdrop"]:                         # 闸2：已有文件类，别叠加工
            return
        if self._handler:
            try:
                self._handler(snapshot, True)
            except Exception as e:
                print(f"[engine] handler 回调异常: {e}")

    def process_current_clipboard(self):
        """启动时立即处理剪贴板上已存在的图片（用户先截图、后启动工具也有效）。
        与监听走同一套三道闸；剪贴板空/无图/已有文件类都会安全跳过。"""
        try:
            self._handle_update()
        except Exception as e:
            print(f"[engine] process_current_clipboard 异常: {e}")

    def _read_snapshot(self):
        """打开剪贴板 → 枚举格式 → 判断图片/HDROP → 读 DIBV5/DIB/PNG 字节到内存。"""
        if not _open_clipboard_retry():
            return None
        try:
            formats = []
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                try:
                    name = win32clipboard.GetClipboardFormatName(fmt) or ""
                except Exception:
                    name = ""
                formats.append((fmt, name))
            fmt_ids = {f for f, _ in formats}
            names = {n for _, n in formats if n}
            png_fmt = win32clipboard.RegisterClipboardFormat("PNG")
            return {
                "formats": formats,
                "has_image": _has_image(fmt_ids, names),
                "has_hdrop": win32con.CF_HDROP in fmt_ids,
                "png": _get_bytes(png_fmt),
                "dibv5": _get_bytes(win32con.CF_DIBV5),
                "dib": _get_bytes(win32con.CF_DIB),
            }
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    # ── 重建核心（模式 A）──
    def _rebuild_image(self):
        """DESIGN.md §5.3 约定的方法名（等价于 rebuild()）。"""
        return self.rebuild()

    def rebuild(self):
        """读当前剪贴板原图字节 → 写临时 PNG → 重建多格式（补 CF_HDROP）。
        返回 True/False。在 handler 里被 main.py 调用于模式 A。

        自触发防护：_rebuilding 标志拦 Empty/Set 触发的同步重入；重建完成后
        剪贴板必带 CF_HDROP，异步回声由 _on_clipboard_update 闸2（has_hdrop）拦截。
        不再使用 2s 时间窗，快速连截/重复复制均正常处理。"""
        self._rebuilding = True
        try:
            if not _open_clipboard_retry():
                return False
            png_fmt = win32clipboard.RegisterClipboardFormat("PNG")
            dibv5 = _get_bytes(win32con.CF_DIBV5)
            dib = _get_bytes(win32con.CF_DIB)
            png = _get_bytes(png_fmt)
            file_png = png or _dib_to_png(dibv5 or dib)   # 落盘/Set 用同一份字节
            if not file_png:
                print("[engine] 剪贴板无可用图片字节，跳过重建")
                return False
            png_path = self._write_temp_png(file_png)     # 临时文件名即粘贴后文件名
            win32clipboard.EmptyClipboard()
            if dibv5:
                win32clipboard.SetClipboardData(win32con.CF_DIBV5, dibv5)
            if dib:
                win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
            win32clipboard.SetClipboardData(png_fmt, file_png)
            win32clipboard.SetClipboardData(win32con.CF_HDROP, build_cf_hdrop(png_path))
            drop_fmt = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
            win32clipboard.SetClipboardData(drop_fmt, struct.pack("<I", DROPEFFECT_COPY))
            self._last_output_path = png_path
            print(f"[engine] 剪贴板重建完成 → {png_path}")
            return True
        finally:
            self._rebuilding = False
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    # ── 保存到目录（模式 B）──
    def save_snapshot_to_dir(self, snapshot, directory):
        """把快照里的图片存成 PNG 到指定目录（不重建剪贴板）。返回路径或 None。

        directory 为空时自动用当前用户的 图片\Screenshots 目录。"""
        if not directory:
            directory = os.path.join(
                os.path.expanduser("~"), "Pictures", "Screenshots")
        png = snapshot.get("png")
        if not png:
            png = _dib_to_png(snapshot.get("dibv5") or snapshot.get("dib"))
        if not png:
            print("[engine] 快照无可用图片字节，无法保存")
            return None
        try:
            os.makedirs(directory, exist_ok=True)
            path = self._make_filename(directory)
            with open(path, "wb") as f:
                f.write(png)
            self._last_output_path = path
            print(f"[engine] 图片已保存（B模式）→ {path}")
            return path
        except OSError as e:
            print(f"[engine] 保存失败: {e}")
            return None

    # ── 文件名 / 临时文件 ──
    def _make_filename(self, directory):
        """filename_format strftime 命名；同秒重名追加 _1/_2。"""
        base = time.strftime(self._filename_format) if self._filename_format \
            else "截图_%Y-%m-%d_%H-%M-%S"
        path = os.path.join(directory, base + ".png")
        n = 1
        while os.path.exists(path):
            path = os.path.join(directory, f"{base}_{n}.png")
            n += 1
        return path

    def _write_temp_png(self, png_bytes):
        os.makedirs(self._temp_dir, exist_ok=True)
        path = self._make_filename(self._temp_dir)
        with open(path, "wb") as f:
            f.write(png_bytes)
        return path

    def _cleanup_old_files(self):
        """删除 temp_dir 中 24h 前的 *.png（启动时 + 每小时）。"""
        try:
            if not os.path.isdir(self._temp_dir):
                return
            cutoff = time.time() - _TEMP_FILE_MAX_AGE
            for name in os.listdir(self._temp_dir):
                if not name.lower().endswith(".png"):
                    continue
                path = os.path.join(self._temp_dir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        print(f"[engine] 清理过期临时文件: {path}")
                except OSError:
                    pass
        except Exception as e:
            print(f"[engine] 临时文件清理异常: {e}")
