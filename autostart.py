# -*- coding: utf-8 -*-
"""
autostart.py —— 开机自启模块（开发 B，任务 B3）

set_autostart(enable)：写/删
  HKCU\Software\Microsoft\Windows\CurrentVersion\Run 下的项 ScreenshotPasteAssistant。

启动命令值：
  - 打包后（PyInstaller frozen）：sys.executable（exe 完整路径，带引号）。
  - 源码运行：pythonw.exe + main.py 绝对路径（pythonw 不弹黑色控制台）。

本模块只依赖标准库（winreg），不依赖 pywin32。
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ScreenshotPasteAssistant"


def _autostart_command():
    """返回写入注册表的启动命令（带引号）。"""
    if getattr(sys, "frozen", False):
        # 打包后：exe 完整路径
        return f'"{sys.executable}"'
    # 源码模式：pythonw.exe 不弹控制台
    python_exe = sys.executable
    pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = python_exe
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{pythonw}" "{main_py}"'


def set_autostart(enable):
    """enable=True 写入注册表自启项；False 删除（不存在则忽略）。"""
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enable:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, _autostart_command()
            )
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def get_autostart():
    """查询当前是否已启用自启（供验证/同步用）。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    # 自测：python autostart.py
    print("命令值：", _autostart_command())
    set_autostart(True)
    print("启用后 get_autostart() =", get_autostart())
    set_autostart(False)
    print("删除后 get_autostart() =", get_autostart())
