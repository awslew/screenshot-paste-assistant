# -*- coding: utf-8 -*-
"""
config.py —— 配置读写模块（开发 B，任务 B1）

职责：
  - load_config()：读 config.json，不存在则生成默认配置并返回；缺键补默认。
  - save_config()：原子写回 config.json（先写临时文件，再 os.replace）。
  - resolve_temp_dir()：解析 A 模式临时目录（temp_dir 为空时自动取 exe/项目 目录/temp）。

config.json 位置：
  - 源码运行：与 config.py 同目录（项目根目录）。
  - PyInstaller 打包后：exe 所在目录（便携，配置跟着程序走）。

键与默认值严格按 DESIGN.md §5.1。
"""

import json
import os
import sys

# 默认配置（严格按 DESIGN §5.1）
DEFAULTS = {
    "filename_format": "截图_%Y-%m-%d_%H-%M-%S",
    "mode": "A",                    # "A"=补粘贴（默认）；"B"=自动保存降级
    "autostart": False,             # 是否开机自启
    "save_dir": "",                 # B 模式保存目录；空 = 自动（当前用户 图片\Screenshots）
    "temp_dir": "",                 # A 模式临时目录；空 = 自动
    "paused": False,                # 初始暂停状态
    "show_tray": False,             # 是否显示托盘图标（默认隐藏，后台纯跑）
}


def _app_dir():
    """config.json 所在目录：打包后 = exe 目录；源码运行时 = 项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    """config.json 的完整路径。"""
    return os.path.join(_app_dir(), "config.json")


def load_config():
    """读 config.json；不存在则写默认值并返回；缺键补默认。

    返回 dict（DEFAULTS 的拷贝 + 用户已保存的值）。
    JSON 损坏/不可读时不覆盖用户文件，返回默认值。
    """
    path = config_path()
    if not os.path.exists(path):
        cfg = dict(DEFAULTS)
        _write_atomic(path, cfg)
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except (json.JSONDecodeError, OSError):
        # 文件损坏或不可读：返回默认值，不覆盖用户文件（避免毁掉用户编辑内容）
        return dict(DEFAULTS)

    # 缺键补默认；用户已有的键原样保留
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    """原子写回 config.json：先写 .tmp 再 os.replace（避免写一半损坏）。"""
    if not isinstance(cfg, dict):
        raise TypeError("cfg 必须是 dict")
    path = config_path()
    _write_atomic(path, cfg)


def _write_atomic(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resolve_temp_dir(cfg=None):
    """解析 A 模式的临时目录（temp_dir 空 = 自动）。

    - temp_dir 非空：直接用该值。
    - 空：源码运行时 = 项目根目录/temp；打包后 = exe 目录/temp。
    """
    if cfg is None:
        cfg = load_config()
    td = cfg.get("temp_dir") or ""
    if td:
        return td
    return os.path.join(_app_dir(), "temp")


if __name__ == "__main__":
    # 自测：python config.py
    c = load_config()
    print("config_path:", config_path())
    print("load:", c)
    c["paused"] = True
    save_config(c)
    print("reload:", load_config())
    print("resolve_temp_dir:", resolve_temp_dir())
