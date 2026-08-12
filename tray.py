# -*- coding: utf-8 -*-
"""
tray.py —— 托盘模块（开发 B，任务 B2）

职责：
  - TrayApp：pystray 托盘图标（图标程序内用 PIL 生成：蓝底 + "SC"）。
  - 菜单：状态(灰显，显示模式 A/B + 是否暂停) / 暂停 / 恢复 /
          打开配置 / 开机自启(复选框) / 退出。
  - 线程模型：icon.run_detached() 跑在 pystray 自带后台线程；
             菜单回调只做线程安全的事（设标志、PostMessage 由 main 负责）。
  - 绝不 import clipboard_engine，与引擎完全通过回调松耦合。

回调约定（由 main.py 提供）：
  pause_cb()            —— 引擎暂停（内部设线程标志，线程安全）
  resume_cb()           —— 引擎恢复（线程安全）
  exit_cb()             —— 优雅退出（main.py 负责 PostMessage 主窗口收尾）
  on_reconfig(cfg)      —— config.json 被改动后通知主线程刷新引擎
"""

import os
import threading

import pystray
from PIL import Image, ImageDraw, ImageFont

import autostart
from config import config_path, load_config, save_config

# 配置文件轮询间隔（秒）：用户在编辑器保存 config.json 后约 1 秒内生效
_CONFIG_POLL_INTERVAL = 1.0


class TrayApp:
    """托盘应用。构造即启动（run_detached + 配置监控线程），stop() 可干净退出。"""

    def __init__(self, pause_cb, resume_cb, exit_cb, config, on_reconfig):
        self.pause_cb = pause_cb
        self.resume_cb = resume_cb
        self.exit_cb = exit_cb
        self.config = config          # 与 main 共享的配置 dict（本类会同步写回 config.json）
        self.on_reconfig = on_reconfig

        self._stop_watcher = threading.Event()
        self._last_mtime = self._current_mtime()

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: self._status_text(),
                lambda icon, item: None,     # 灰显状态项，无动作
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("暂停", self._on_pause),
            pystray.MenuItem("恢复", self._on_resume),
            pystray.MenuItem("打开配置", self._on_open_config),
            pystray.MenuItem(
                "开机自启",
                self._on_toggle_autostart,
                checked=lambda item: bool(self.config.get("autostart")),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_exit),
        )

        self.icon = pystray.Icon(
            "ScreenshotPasteAssistant",
            self._make_icon(),
            "截图粘贴助手",
            menu,
        )
        self.icon.run_detached()

        # 配置监控线程：用户手动编辑 config.json 保存后自动触发 on_reconfig
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
        self._watcher.start()

    # ---------- 图标 ----------

    def _make_icon(self):
        """程序内生成托盘图标：蓝色方块上写 'SC'。"""
        img = Image.new("RGBA", (64, 64), (30, 100, 220, 255))
        draw = ImageDraw.Draw(img)
        text = "SC"
        try:
            font = ImageFont.truetype("arial.ttf", 26)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((64 - w) / 2 - bbox[0], (64 - h) / 2 - bbox[1]),
            text,
            fill="white",
            font=font,
        )
        return img

    # ---------- 菜单回调（全部线程安全，绝不碰剪贴板/引擎内部） ----------

    def _status_text(self):
        mode = self.config.get("mode", "A")
        state = "已暂停" if self.config.get("paused") else "运行中"
        return f"模式 {mode} · {state}"

    def _on_pause(self, icon, item):
        # 同步 paused 到 config.json，再回调引擎暂停
        self.config["paused"] = True
        save_config(self.config)
        self.pause_cb()

    def _on_resume(self, icon, item):
        self.config["paused"] = False
        save_config(self.config)
        self.resume_cb()

    def _on_open_config(self, icon, item):
        # 打开 config.json 让用户手动编辑；保存后由监控线程自动 on_reconfig
        os.startfile(config_path())

    def _on_toggle_autostart(self, icon, item):
        enable = not bool(self.config.get("autostart"))
        self.config["autostart"] = enable
        autostart.set_autostart(enable)
        save_config(self.config)

    def _on_exit(self, icon, item):
        self._stop_watcher.set()
        self.icon.stop()
        self.exit_cb()

    # ---------- 配置监控 ----------

    def _current_mtime(self):
        try:
            return os.path.getmtime(config_path())
        except OSError:
            return 0.0

    def _watch_loop(self):
        while not self._stop_watcher.wait(_CONFIG_POLL_INTERVAL):
            m = self._current_mtime()
            if m != 0.0 and m != self._last_mtime:
                self._last_mtime = m
                try:
                    cfg = load_config()
                except Exception:
                    # 文件可能正被编辑器半写状态，等下一轮
                    continue
                self.config.update(cfg)      # 状态/自启复选框跟随新配置
                self.on_reconfig(cfg)

    # ---------- 对外接口 ----------

    def stop(self):
        """停止托盘图标与配置监控（幂等）。"""
        self._stop_watcher.set()
        try:
            self.icon.stop()
        except Exception:
            pass
