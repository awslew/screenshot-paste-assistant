"""引擎自测：一键验证 clipboard_engine.py 的三道闸 + 重建 + A/B 模式 + 快速连截回归。

流程（DESIGN.md §10.1 A6 + 联调回归）：
  1. 启动真实 ClipboardWatcher（隐藏窗口 + AddClipboardFormatListener + 主消息循环）
  2. 注入图A → 轮询等重建完成（HDROP 出现）→ 断言 CF_HDROP + 临时 PNG + 路径正确
  3. 快速连截回归：A 重建完成后 0.3s 内注入内容不同的图B → 断言 B 也被重建
     （不被 2s 时间窗吞掉；引擎已改为 _rebuilding + has_hdrop 双保险，无时间窗）
  4. 复制一段文本 → 断言未被重建（无 CF_HDROP/无图）
  5. 切 B 模式 → 注入图片 → 断言存到 save_dir 且不重建
  6. 打印 PASS / FAIL

用法：python tools/selftest_engine.py        （在项目根目录跑）
"""
import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

import win32clipboard
import win32con
import win32gui
from PIL import Image

# 让 `python tools/selftest_engine.py` 能 import 项目根的 clipboard_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipboard_engine import ClipboardWatcher  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(HERE, "_selftest_temp")
SAVE_DIR = os.path.join(HERE, "_selftest_saved")
WM_APP = 0x8000

# 全局可变配置（模拟 main.py 的 cfg，worker 线程切换 B 模式用）
CFG = {"mode": "A", "save_dir": SAVE_DIR}


# ─────────────────────────────────────────────────────────────
# 剪贴板辅助
# ─────────────────────────────────────────────────────────────
def _open_cb_retry(tries=30, delay=0.05):
    for _ in range(tries):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EnumClipboardFormats(0)
            return True
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            time.sleep(delay)
    return False


def _close_cb():
    try:
        win32clipboard.CloseClipboard()
    except Exception:
        pass


def enum_names():
    """枚举剪贴板格式 → {fmt_id: name}"""
    if not _open_cb_retry():
        return {}
    out = {}
    try:
        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            try:
                out[fmt] = win32clipboard.GetClipboardFormatName(fmt) or ""
            except Exception:
                out[fmt] = ""
    finally:
        _close_cb()
    return out


def set_image_clipboard(base_color=120):
    """放一张测试图（CF_DIB + 'PNG'，模拟 Win+Shift+S 的核心格式面）。
    base_color 不同 → 图片内容不同（快速连截回归用）。返回 PNG 字节。"""
    img = Image.new("RGB", (320, 200))
    px = img.load()
    for y in range(200):
        for x in range(320):
            px[x, y] = (x * 255 // 320, y * 255 // 200, base_color)
    pb = io.BytesIO()
    img.save(pb, "PNG")
    png = pb.getvalue()
    bb = io.BytesIO()
    img.save(bb, "BMP")
    dib = bb.getvalue()[14:]
    if not _open_cb_retry():
        raise RuntimeError("OpenClipboard 失败")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        win32clipboard.SetClipboardData(
            win32clipboard.RegisterClipboardFormat("PNG"), png)
    finally:
        _close_cb()
    return png


def set_text_clipboard(text):
    if not _open_cb_retry():
        raise RuntimeError("OpenClipboard 失败")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        _close_cb()


def read_hdrop_paths():
    """ctypes 原生读 CF_HDROP 路径（pywin32 对 HDROP 句柄返回 None，须用 DragQueryFile）"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.CloseClipboard.restype = wintypes.BOOL
    DQF = shell32.DragQueryFileW
    DQF.restype = wintypes.UINT
    DQF.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    paths = []
    if user32.OpenClipboard(None):
        try:
            h = user32.GetClipboardData(win32con.CF_HDROP)
            if h:
                n = DQF(h, 0xFFFFFFFF, None, 0)
                for i in range(n):
                    ln = DQF(h, i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    DQF(h, i, buf, ln + 1)
                    paths.append(buf.value)
        finally:
            user32.CloseClipboard()
    return paths


def clear_dir(path):
    os.makedirs(path, exist_ok=True)
    for name in os.listdir(path):
        try:
            os.remove(os.path.join(path, name))
        except OSError:
            pass


def list_pngs(path):
    if not os.path.isdir(path):
        return []
    return [f for f in os.listdir(path) if f.lower().endswith(".png")]


# ─────────────────────────────────────────────────────────────
# 引擎回调（复刻 main.py 的 A/B 分派）
# ─────────────────────────────────────────────────────────────
engine = None
RESULTS = {}


def on_image(snapshot, is_image):
    if not is_image:
        return
    if CFG["mode"] == "B":
        engine.save_snapshot_to_dir(snapshot, CFG["save_dir"])
    else:
        engine.rebuild()


def on_message(hwnd, msg, wparam, lparam):
    if msg == WM_APP + 1:      # 退出
        win32gui.PostQuitMessage(0)


def wait_until(cond, timeout=4.0, interval=0.1):
    """轮询直到 cond() 为真（cond 内可开剪贴板，失败自动重试）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# ─────────────────────────────────────────────────────────────
# 场景线程（模拟用户操作，最后 Post WM_APP+1 退出主循环）
# ─────────────────────────────────────────────────────────────
def scenario():
    try:
        clear_dir(TEMP_DIR)
        clear_dir(SAVE_DIR)
        time.sleep(0.4)   # 等主消息循环就绪

        # ── 阶段1：注入图A → 轮询等重建（HDROP 出现）→ 断言 ──
        print("[selftest] 阶段1：注入测试图A…")
        set_image_clipboard(base_color=120)
        ok1 = wait_until(lambda: win32con.CF_HDROP in enum_names())
        names = enum_names()
        hdrop_ok = win32con.CF_HDROP in names
        png_ok = "PNG" in names.values()
        temp_pngs = list_pngs(TEMP_DIR)
        temp_file_ok = len(temp_pngs) > 0
        paths = read_hdrop_paths()
        path_ok = (len(paths) == 1 and os.path.exists(paths[0])
                   and paths[0] == engine.last_output_path
                   and os.path.dirname(paths[0]).lower() == TEMP_DIR.lower())
        RESULTS["image_trigger"] = ok1 and hdrop_ok and png_ok and temp_file_ok and path_ok
        print(f"  轮询等到重建完成:  {'PASS' if ok1 else 'FAIL'}")
        print(f"  CF_HDROP 出现:     {'PASS' if hdrop_ok else 'FAIL'}")
        print(f"  PNG 保留:          {'PASS' if png_ok else 'FAIL'}")
        print(f"  临时文件生成:      {temp_pngs} {'PASS' if temp_file_ok else 'FAIL'}")
        print(f"  HDROP→路径正确:    {paths} {'PASS' if path_ok else 'FAIL'}")

        # ── 阶段2（回归）：快速连截 —— A 重建完成后 0.3s 内注入内容不同的图B ──
        print("[selftest] 阶段2：快速连截回归（A重建完成后0.3s内注入图B）…")
        time.sleep(0.3)
        png_b = set_image_clipboard(base_color=240)   # 内容与A不同
        ok2 = wait_until(lambda: len(list_pngs(TEMP_DIR)) >= 2)
        names_b = enum_names()
        hdrop_b = win32con.CF_HDROP in names_b
        # B 的内容确实被写入最新临时文件（而非被 2s 时间窗吞掉）
        content_b = False
        last = engine.last_output_path
        if last and os.path.exists(last) and os.path.dirname(last).lower() == TEMP_DIR.lower():
            try:
                with open(last, "rb") as f:
                    content_b = f.read() == png_b
            except Exception:
                content_b = False
        RESULTS["rapid_double"] = ok2 and hdrop_b and content_b
        print(f"  图B也被重建出HDROP: {'PASS' if (ok2 and hdrop_b) else 'FAIL'}")
        print(f"  图B内容真实写入:   {'PASS' if content_b else 'FAIL'}  "
              f"临时文件数={len(list_pngs(TEMP_DIR))}")

        # ── 阶段3：复制文本 → 断言不重建 ──
        print("[selftest] 阶段3：复制一段文本…")
        set_text_clipboard("ScreenshotPasteAssistant selftest text")
        time.sleep(1.0)
        names2 = enum_names()
        text_names = set(names2.values())
        no_rebuild = not (
            win32con.CF_HDROP in names2
            or win32con.CF_DIB in names2
            or win32con.CF_DIBV5 in names2
            or "PNG" in text_names
        )
        RESULTS["text_no_trigger"] = no_rebuild
        print(f"  文本复制未被重建(无CF_HDROP/无图): "
              f"{'PASS' if no_rebuild else 'FAIL'}  格式面={sorted(names2.values())}")

        # ── 阶段4（附）：B 模式 → 放图 → 断言存盘且不重建 ──
        print("[selftest] 阶段4：切换 B 模式，再放一张图…")
        CFG["mode"] = "B"
        engine.set_settings({"mode": "B"})
        set_image_clipboard(base_color=60)
        time.sleep(1.0)
        saved_pngs = list_pngs(SAVE_DIR)
        saved_ok = (len(saved_pngs) > 0
                    and os.path.exists(engine.last_output_path)
                    and os.path.dirname(engine.last_output_path).lower() == SAVE_DIR.lower())
        names3 = enum_names()
        no_rebuild_b = win32con.CF_HDROP not in names3
        RESULTS["mode_b_saved"] = saved_ok
        RESULTS["mode_b_no_rebuild"] = no_rebuild_b
        CFG["mode"] = "A"
        engine.set_settings({"mode": "A"})
        print(f"  B模式存盘: {saved_pngs} {'PASS' if saved_ok else 'FAIL'}")
        print(f"  B模式未重建(无CF_HDROP): {'PASS' if no_rebuild_b else 'FAIL'}")

    except Exception as e:
        RESULTS["exception"] = repr(e)
        print(f"[selftest] 场景异常: {e!r}")
    finally:
        win32gui.PostMessage(engine.hwnd, WM_APP + 1, 0, 0)


# ─────────────────────────────────────────────────────────────
def main():
    global engine
    print("=== ScreenshotPasteAssistant 引擎自测 (selftest_engine) ===")
    engine = ClipboardWatcher(handler=on_image, on_message=on_message)
    engine.set_settings({
        "mode": "A",
        "temp_dir": TEMP_DIR,
        "save_dir": SAVE_DIR,
        "filename_format": "截图_%Y-%m-%d_%H-%M-%S",
    })
    try:
        engine.start()
    except Exception as e:
        print(f"[FAIL] 引擎启动失败: {e}")
        return 1
    print(f"[selftest] 引擎已启动 hwnd={engine.hwnd:#x}，监听剪贴板…\n")

    threading.Thread(target=scenario, daemon=True).start()

    def watchdog():
        time.sleep(30)   # 兜底：任何卡死 30s 后强制退出
        win32gui.PostMessage(engine.hwnd, WM_APP + 1, 0, 0)
    threading.Thread(target=watchdog, daemon=True).start()

    win32gui.PumpMessages()
    engine.stop()

    print("\n=== 结果汇总 ===")
    if "exception" in RESULTS:
        print(f"  [FAIL] 场景异常: {RESULTS['exception']}")
        return 1
    required = ["image_trigger", "rapid_double", "text_no_trigger",
                "mode_b_saved", "mode_b_no_rebuild"]
    ok = True
    for k in required:
        v = RESULTS.get(k)
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
        ok = ok and bool(v)
    print("=" * 40)
    print(f"最终判定: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
