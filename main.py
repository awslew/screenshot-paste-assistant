"""ScreenshotPasteAssistant 入口：组装 config + clipboard_engine + tray。

线程模型（DESIGN.md §6）：
  主线程：engine.start() 建隐藏窗口 + AddClipboardFormatListener → win32gui.PumpMessages()
  托盘线程（pystray run_detached）：只通过 PostMessage(WM_APP+1/2) 与主线程通信，
    绝不直接碰剪贴板 / 引擎内部状态。

消息：
  WM_APP+1 = 退出（托盘 exit_cb 触发）→ PostQuitMessage 结束主循环
  WM_APP+2 = 改配置（托盘 on_reconfig 触发）→ 重读 config.json → engine.set_settings

依赖契约：config.py / tray.py 归开发B。本文件只在它们存在时使用；
开发B 未交付时以内置默认 + 无托盘方式运行（对应 DESIGN.md §10.3 M1 里程碑）。

健壮性：
  - pystray run_detached 线程非 daemon，主循环结束后用 os._exit() 强制退出进程
    （正常托盘退出路径会先 icon.stop() 再 exit_cb，不受影响）。
  - 打包(--noconsole)后 stdout/stderr 重定向到 exe 目录/app.log；未捕获异常
    写 [FATAL] 到日志并 os._exit(1)。
"""
import os
import sys
import traceback

import win32con
import win32gui

from clipboard_engine import ClipboardWatcher

WM_APP = 0x8000

# ── config（开发B交付后自动走 config.py）───────────────────
try:
    from config import load_config, save_config
    HAVE_CONFIG = True
except ImportError:
    HAVE_CONFIG = False

    def load_config():
        # 与 DESIGN.md §5.1 相同的默认值（config.py 就绪后此兜底不再使用）
        return {
            "filename_format": "截图_%Y-%m-%d_%H-%M-%S",
            "mode": "A",
            "autostart": False,
            "save_dir": "",
            "temp_dir": "",
            "paused": False,
        }

    def save_config(cfg):
        pass

# ── tray（开发B交付后自动使用）─────────────────────────────
try:
    from tray import TrayApp
    HAVE_TRAY = True
except ImportError:
    HAVE_TRAY = False

# 全局配置（可变 dict，on_message 里原地更新，避免闭包重绑定问题）
CFG = {}


def on_image(snapshot, is_image):
    """引擎三道闸全过 → 图片事件。A/B 模式分派。"""
    if not is_image:
        return
    mode = CFG.get("mode", "A")
    if mode == "B":
        # save_dir 为空 → 引擎自动用 图片\Screenshots
        engine.save_snapshot_to_dir(snapshot, CFG.get("save_dir"))
    else:
        engine.rebuild()


def on_message(hwnd, msg, wparam, lparam):
    """托盘线程 → 主线程 的自定义消息。"""
    if msg == WM_APP + 1:            # 退出
        print("[main] 收到退出消息，主循环结束")
        win32gui.PostQuitMessage(0)
    elif msg == WM_APP + 2:          # 改配置
        try:
            new_cfg = load_config()
        except Exception as e:
            print(f"[main] 重读配置失败: {e}")
            return
        CFG.clear()
        CFG.update(new_cfg)
        engine.set_settings(CFG)
        print(f"[main] 配置已刷新: mode={CFG.get('mode')} "
              f"filename_format={CFG.get('filename_format')}")


def _setup_file_logging():
    """打包(--noconsole)后 stdout/stderr 不可见 → 重定向到 exe 目录/app.log。
    源码运行保留控制台，不重定向。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                "app.log")
        f = open(log_path, "a", encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f
        print(f"[main] 日志写入: {log_path}")
    except Exception:
        pass


def main():
    _setup_file_logging()
    global engine
    exit_code = 0
    try:
        engine = ClipboardWatcher(handler=on_image, on_message=on_message)

        CFG.clear()
        CFG.update(load_config())
        engine.set_settings(CFG)

        # 预创建 B 模式保存目录（顺手，省得每次 handler 里判断）
        if CFG.get("mode") == "B" and CFG.get("save_dir"):
            try:
                os.makedirs(CFG["save_dir"], exist_ok=True)
            except OSError as e:
                print(f"[main] 无法创建 save_dir: {e}")

        try:
            engine.start()
        except Exception as e:
            print(f"[main] 引擎启动失败: {e}")
            exit_code = 1
        else:
            # 启动时立即处理已存在于剪贴板的图片（先截图、后启动工具也能粘贴）
            try:
                engine.process_current_clipboard()
            except Exception as e:
                print(f"[main] 处理现有剪贴板失败: {e}")

            # 托盘按需开启：show_tray=true 才显示图标（默认隐藏，后台纯跑）
            if CFG.get("show_tray") and HAVE_TRAY:
                try:
                    TrayApp(
                        pause_cb=lambda: engine.set_paused(True),
                        resume_cb=lambda: engine.set_paused(False),
                        exit_cb=lambda: win32gui.PostMessage(
                            engine.hwnd, WM_APP + 1, 0, 0),
                        config=CFG,
                        on_reconfig=lambda new_cfg: win32gui.PostMessage(
                            engine.hwnd, WM_APP + 2, 0, 0),
                    )
                    print("[main] 托盘已启动")
                except Exception as e:
                    print(f"[main] 托盘启动失败（继续无托盘运行）: {e}")
            else:
                print("[main] 后台无托盘模式（show_tray=false）。"
                      "控制走 config.json，保存后约 1s 自动生效。")

            print(f"[main] 启动完成。mode={CFG.get('mode')}  "
                  f"filename_format={CFG.get('filename_format')}")
            print("[main] 现在 Win+Shift+S 截图后，在文件夹里 Ctrl+V 即可粘贴成图片文件。")

            try:
                win32gui.PumpMessages()
            except KeyboardInterrupt:
                print("[main] Ctrl+C 退出")
            except Exception:
                exit_code = 1
                traceback.print_exc()
            finally:
                try:
                    engine.stop()
                except Exception as e:
                    print(f"[main] 引擎停止异常: {e}")
                print("[main] 已退出。")
    except BaseException:
        # 未捕获异常 → [FATAL] 写日志（含 traceback），强退
        exit_code = 1
        try:
            print(f"[FATAL] {traceback.format_exc()}", file=sys.stderr)
        except Exception:
            pass
    # 绕过 pystray run_detached 的非 daemon 线程，强制退出进程
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(exit_code)


if __name__ == "__main__":
    sys.exit(main())
