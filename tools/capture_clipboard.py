"""捕获当前剪贴板全部格式，判断截图后 Explorer 为什么不认。

用法:
  python capture_clipboard.py            # 立即读当前剪贴板
  python capture_clipboard.py --wait 45  # 等待剪贴板出现图片(用户按Win+Shift+S)，最多等45秒
"""
import sys, time, os

import pywintypes
import win32clipboard
from win32clipboard import (CF_HDROP, CF_BITMAP, CF_DIB, CF_DIBV5,
                            CF_UNICODETEXT, CF_TEXT, CF_ENHMETAFILE, CF_METAFILEPICT)


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

# 常见"已注册格式"的规范名（这些是剪贴板格式名，不是数值）
WELL_KNOWN_NAMES = {
    "PNG", "JFIF", "GIF", "JPG", "TIFF", "WEBP", "HTML Format", "Rich Text Format",
    "FileGroupDescriptorW", "FileGroupDescriptor", "FileContents", "FileNames",
    "Preferred DropEffect", "Shell IDList Array", "DataObjectAttributes",
    "FileNameW", "FileName", "Snipping Tool image", "System.Drawing.Bitmap",
    "DeviceIndependentBitmap", "image/png", "image/bmp", "image/jpeg",
    "CFSTR_HTML", "NBitmap", "Text", "System.String",
}


def enum_all():
    """枚举剪贴板全部格式，返回 {fmt_id: {'name':..., 'kind':..., 'len':...}}"""
    if not _open_clipboard_retry():
        return {}
    out = {}
    try:
        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            name = ""
            try:
                name = win32clipboard.GetClipboardFormatName(fmt) or ""
            except Exception:
                name = ""
            # 尝试读数据，判断类型/大小
            kind, size = "?", -1
            try:
                data = win32clipboard.GetClipboardData(fmt)
                if isinstance(data, bytes):
                    kind, size = "bytes", len(data)
                elif isinstance(data, (int,)):
                    kind, size = "handle", data  # HBITMAP/HENHMETAFILE 等句柄
                elif isinstance(data, str):
                    kind, size = "str", len(data)
                else:
                    kind, size = type(data).__name__, -1
            except Exception as e:
                kind = f"ERR:{type(e).__name__}"
            out[fmt] = {"name": name, "kind": kind, "size": size}
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    return out


def describe(fmt_map):
    lines = []
    for fid in sorted(fmt_map):
        info = fmt_map[fid]
        name = info["name"]
        kind = info["kind"]
        size = info["size"]
        note = []
        if name:
            note.append(name)
        if kind == "handle":
            note.append(f"(handle={size:#x})")
        elif kind == "bytes":
            note.append(f"(bytes={size})")
        elif kind == "str":
            note.append(f"(str={size})")
        else:
            note.append(f"({kind})")
        lines.append(f"  fmt#{fid:<4} {'|'.join(note)}")
    return "\n".join(lines)


def classify(fmt_map):
    """按组归类"""
    names = {v["name"] for v in fmt_map.values() if v["name"]}
    fids = set(fmt_map.keys())
    groups = {
        "CF_HDROP(文件列表)": bool(CF_HDROP in fids),
        "CF_BITMAP": bool(CF_BITMAP in fids),
        "CF_DIB": bool(CF_DIB in fids),
        "CF_DIBV5": bool(CF_DIBV5 in fids),
        "CF_ENHMETAFILE": bool(CF_ENHMETAFILE in fids),
        "虚拟文件描述(FileGroupDescriptorW/F)": bool(
            {"FileGroupDescriptorW", "FileGroupDescriptor"} & names),
        "FileContents": bool({"FileContents"} & names),
        "Preferred DropEffect": bool({"Preferred DropEffect"} & names),
        "PNG": bool({"PNG"} & names),
        "Snipping Tool image": bool({"Snipping Tool image"} & names),
    }
    return groups


def has_image(fmt_map):
    """判断剪贴板是否含位图类图片"""
    names = {v["name"] for v in fmt_map.values() if v["name"]}
    return any([
        bool(CF_DIB in fmt_map or CF_DIBV5 in fmt_map or CF_BITMAP in fmt_map),
        bool({"PNG", "JFIF", "GIF", "JPG", "TIFF", "WEBP", "image/png", "image/jpeg",
              "image/bmp", "Snipping Tool image", "System.Drawing.Bitmap"} & names),
    ])


def wait_for_image(timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = enum_all()
        if has_image(m):
            return m
        time.sleep(0.3)
    return None


def main():
    wait = 0
    if "--wait" in sys.argv:
        wait = int(sys.argv[sys.argv.index("--wait") + 1])

    if wait:
        print(f"[capture] 等待剪贴板出现图片（最长 {wait}s）… 现在请按 Win+Shift+S 截一张图")
        m = wait_for_image(wait)
        if m is None:
            print("[capture] 超时：未捕获到图片。")
            sys.exit(2)
    else:
        m = enum_all()

    print("=== 剪贴板格式清单 ===")
    print(describe(m))
    print("\n=== 归类判断 ===")
    for k, v in classify(m).items():
        print(f"  {'✅' if v else '❌'} {k}")
    if not has_image(m):
        print("\n[结论] 剪贴板当前无图片。")

    # 尝试存一份 PNG 原始字节，方便查看
    names = {v["name"]: fid for fid, v in m.items() if v["name"]}
    for fmt_name in ("PNG", "image/png", "Snipping Tool image"):
        fid = names.get(fmt_name)
        if not fid:
            continue
        try:
            if not _open_clipboard_retry():
                print(f"[warn] 打开剪贴板失败，跳过 {fmt_name}")
                continue
            try:
                data = win32clipboard.GetClipboardData(fid)
                if isinstance(data, bytes):
                    os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
                    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "captured.png")
                    with open(p, "wb") as f:
                        f.write(data)
                    print(f"\n[已保存] 格式 '{fmt_name}' 的原始字节 → {p} ({len(data)} bytes)")
                    break
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        except Exception as e:
            print(f"[warn] 读 {fmt_name} 失败: {e}")


if __name__ == "__main__":
    main()
