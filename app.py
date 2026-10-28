# -*- coding: utf-8 -*-
"""
WuwaAutoChest Compact
---------------------
Local auto-clicker for Wuwa Tappo.

- No countdown / no timer.
- Reads the cumulative click count and chest-ready state directly from
  Wuwa Tappo's WebView2 accessibility tree (UI Automation).
- Global control hotkeys use F1/F2/F3, F8, F10 and F11.
- The simulated output key is selectable F13-F24 (keys above F12) and is sent
  through SendInput; no mouse movement is involved in the auto-press loop.
- Chest positions are manual: click a "定位" button, then left-click the chest.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QObject, QEvent
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontDatabase, QGuiApplication, QIcon, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QMenu, QComboBox,
    QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QToolButton,
    QVBoxLayout, QWidget, QTextBrowser,
)

try:
    import comtypes
    import comtypes.client as comtypes_client
    from comtypes.gen import UIAutomationClient as UIA_RAW
except Exception:  # pragma: no cover
    comtypes = None
    comtypes_client = None
    UIA_RAW = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_NAME = "WuwaAutoChest"
APP_TITLE = "WuwaTappo 小助手"
APP_VERSION = "1.2.0"
AUTHOR = "让你破费的泪"
BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "app.log"
DEFAULT_BG = ASSET_DIR / "bg_default.png"
MASCOT = ASSET_DIR / "mascot.png"
MASCOT_FEMALE = ASSET_DIR / "mascot_female.png"
MASCOT_MALE = ASSET_DIR / "mascot_male.png"
CHEST_GOLD = ASSET_DIR / "chest_gold.png"
ICON_PATH = ASSET_DIR / "icon.ico"
SIGRIKA_ICON = ASSET_DIR / "icon_sigrika.png"
UIA_BRIDGE = ASSET_DIR / "uia_bridge.ps1"
STATS_PATH = CONFIG_DIR / "stats.json"

# Wuwa Tappo: requires 1000 cumulative clicks before a chest can open.
CHEST_MIN_TOTAL_CLICKS = 1000
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F10 = 0x79
VK_F11 = 0x7A

# Keys above F12.  These virtual-key codes are not present on normal keyboards.
VK_F13 = 0x7C
VK_F21 = 0x84
VK_F22 = 0x85
VK_F23 = 0x86
VK_F24 = 0x87
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
HOTKEY_CHEST_1 = VK_F6
HOTKEY_CHEST_2 = VK_F7
HOTKEY_TOGGLE = VK_F8
HOTKEY_STOP_ONLY = VK_F3
CONTROL_KEYS = {VK_F1, VK_F2, VK_F3, VK_F8, VK_F10, VK_F11}
SIM_KEYS = list(range(VK_F13, VK_F24 + 1))
HOTKEY_NAMES = {VK_F1: "F1", VK_F2: "F2", VK_F3: "F3", VK_F8: "F8",
                VK_F10: "F10", VK_F11: "F11"}
SIM_KEY_NAMES = {vk: f"F{vk - VK_F13 + 13}" for vk in SIM_KEYS}

# ---------------------------------------------------------------------------
# Windows API
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowW.restype = wintypes.HWND
user32.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
winmm.timeBeginPeriod.argtypes = (wintypes.UINT,)
winmm.timeEndPeriod.argtypes = (wintypes.UINT,)
ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
ctypes.windll.kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)


def enable_dpi() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def virtual_screen_rect() -> tuple[int, int, int, int]:
    return (
        int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))),
        max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))),
    )


def send_key(vk: int, hold_ms: float = 8.0) -> bool:
    """Inject a real keyboard key (F13-F24) without touching the mouse."""
    scan = int(user32.MapVirtualKeyW(int(vk), 0))
    down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(int(vk), scan, 0, 0, 0))
    up = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(int(vk), scan, KEYEVENTF_KEYUP, 0, 0))
    if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT)) != 1:
        return False
    if hold_ms > 0:
        time.sleep(max(0.0, min(50.0, hold_ms)) / 1000.0)
    return user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 1


def send_click(x: int, y: int, hold_ms: float = 1.5) -> bool:
    vx, vy, vw, vh = virtual_screen_rect()
    x = max(vx, min(vx + vw - 1, int(x)))
    y = max(vy, min(vy + vh - 1, int(y)))
    ax = int(round((x - vx) * 65535.0 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535.0 / max(1, vh - 1)))
    move = INPUT(INPUT_MOUSE, MOUSEINPUT(ax, ay, 0,
                 MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0))
    down = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0))
    up = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0))
    if user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(INPUT)) != 1:
        return False
    if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT)) != 1:
        return False
    if hold_ms > 0:
        time.sleep(max(0.0, min(20.0, hold_ms)) / 1000.0)
    return user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 1


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_if_needed() -> None:
    if is_admin() or "--no-elevate" in sys.argv:
        return
    if "--screenshot" in sys.argv or "--uia-test" in sys.argv:
        return
    try:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, "--elevated"
        else:
            exe, params = sys.executable, f'"{Path(__file__).resolve()}" --elevated'
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(BASE_DIR), 1)
        if ret > 32:
            sys.exit(0)
        ctypes.windll.user32.MessageBoxW(
            None,
            "本程序需要管理员权限才能读取 Wuwa Tappo 并注入按键。\n"
            "请在 UAC 提示中选择“是”后重试。",
            "需要管理员权限",
            0x10,
        )
    except Exception:
        pass
    sys.exit(1)


def save_config(cfg: "AppConfig") -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Keyboard hotkeys: F1/F2/F3, F8, F10, F11; F13-F24 for simulated output
# ---------------------------------------------------------------------------
class KeyboardHotkeys(QObject):
    pressed = Signal(int)
    statusChanged = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hook = None
        self._thread_id = 0
        self._thread: Optional[threading.Thread] = None
        self._proc = HOOKPROC(self._callback)
        self._running = False
        self._down: set[int] = set()
        self._combo_down: set[int] = set()
        self.direct_stop = None

    def start(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="KeyboardHotkeys", daemon=True)
        self._thread.start()
        for _ in range(30):
            if self._hook:
                self.statusChanged.emit(True, "F1–F3 / F8 控制键已就绪")
                return True
            time.sleep(0.02)
        self._running = False
        self.statusChanged.emit(False, "热键注册失败")
        return False

    def _loop(self):
        self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
        try:
            hmod = ctypes.windll.kernel32.GetModuleHandleW(None)
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
            if not self._hook:
                self.statusChanged.emit(False, f"热键注册失败 {ctypes.get_last_error()}")
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.statusChanged.emit(False, "热键监听异常")
        finally:
            if self._hook:
                try:
                    user32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
                self._hook = None
            self._running = False

    def _callback(self, n_code, w_param, l_param):
        handled = False
        try:
            if n_code >= 0:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(info.vkCode)
                wp = int(w_param)
                if wp in (WM_KEYDOWN, WM_SYSKEYDOWN) and vk in CONTROL_KEYS:
                    handled = True
                    if vk not in self._down:
                        self._down.add(vk)
                        if vk in (VK_F3, VK_F8) and self.direct_stop:
                            try:
                                self.direct_stop(vk)
                            except Exception:
                                pass
                        self.pressed.emit(vk)
                elif wp in (WM_KEYUP, WM_SYSKEYUP) and vk in CONTROL_KEYS:
                    handled = True
                    self._down.discard(vk)
        except Exception:
            pass
        # Swallow only our control keys; F6/F7/F9 stay untouched for Wuwa Tappo.
        if handled:
            return 1
        return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def stop(self):
        self._running = False
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None


# ---------------------------------------------------------------------------
# UIA watcher: total clicks + chest states
# ---------------------------------------------------------------------------
class UiaWatcher(QObject):
    stateChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hwnd = 0
        self._bridge_proc = None
        self._invoke_q: queue.Queue = queue.Queue()

    def request_invoke(self, side: str) -> None:
        self._invoke_q.put("left" if side == "left" else "right")

    def start(self):
        if comtypes_client is None or UIA_RAW is None:
            self.stateChanged.emit({"ok": False, "reason": "UIA 组件不可用"})
            return
        self._stop.clear()
        # Direct UIA Invoke needs the in-process COM reader.  The PowerShell
        # bridge is read-only and is only a fallback for a non-admin frozen run.
        use_bridge = (
            getattr(sys, "frozen", False)
            and not is_admin()
            and UIA_BRIDGE.exists()
        )
        target = self._run_bridge if use_bridge else self._run
        self._thread = threading.Thread(target=target, name="UiaWatcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._bridge_proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
            self._bridge_proc = None

    def reset(self):
        # Force the next read to re-resolve the window and the COM root.
        self._hwnd = 0
        self._raw_uia = None

    # -- Win32 window discovery -------------------------------------------
    @staticmethod
    def _child_webview(parent: int) -> int:
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                if buf.value == "Chrome_WidgetWin_1":
                    tbuf = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(hwnd, tbuf, 256)
                    if tbuf.value == "Wuwa Tappo":
                        found.append(int(hwnd))
                        return False
            except Exception:
                pass
            return True

        user32.EnumChildWindows(parent, cb, 0)
        return found[0] if found else 0

    def _find_hwnd(self) -> int:
        if self._hwnd and user32.IsWindow(self._hwnd):
            return self._hwnd
        candidates = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value.strip() == "Wuwa Tappo":
                    candidates.append(int(hwnd))
            except Exception:
                pass
            return True

        user32.EnumWindows(cb, 0)
        for top in candidates:
            child = self._child_webview(top)
            if child:
                self._hwnd = child
                return child
        # Fallback for the usual install.
        top = int(user32.FindWindowW(None, "Wuwa Tappo") or 0)
        if top:
            child = self._child_webview(top)
            if child:
                self._hwnd = child
                return child
        return 0

    # -- UIA reading -------------------------------------------------------
    def _read_raw(self, hwnd: int) -> Optional[dict]:
        """Read via the native IUIAutomation COM interface.

        This is more reliable inside a frozen executable than the higher-level
        uiautomation wrapper, which can silently return an incomplete control
        view.
        """
        if comtypes_client is None or UIA_RAW is None:
            return None
        if getattr(self, "_raw_uia", None) is None:
            self._raw_uia = comtypes_client.CreateObject(
                UIA_RAW.CUIAutomation, interface=UIA_RAW.IUIAutomation
            )
        uia = self._raw_uia
        try:
            elem = uia.ElementFromHandle(hwnd)
        except Exception:
            # WebView2 / Wuwatappo may have restarted; rebuild the COM root once.
            self._raw_uia = None
            uia = comtypes_client.CreateObject(
                UIA_RAW.CUIAutomation, interface=UIA_RAW.IUIAutomation
            )
            self._raw_uia = uia
            elem = uia.ElementFromHandle(hwnd)
        if not elem:
            return None
        arr = elem.FindAll(4, uia.CreateTrueCondition())
        total = None
        slots = []
        reward = None
        debug = []
        for i in range(arr.Length):
            el = arr.GetElement(i)
            try:
                cls = el.CurrentClassName or ""
                name = el.CurrentName or ""
                low = cls.lower()
                if "chest-reward-popup" in low or "chest-drop-motion" in low:
                    m = re.search(r"chest-reward-popup--(green|blue|purple|gold)", low)
                    if m:
                        reward = m.group(1)
                    else:
                        for level, color in (("4", "gold"), ("3", "purple"), ("2", "blue"), ("1", "green")):
                            if f"level-{level}" in low:
                                reward = color
                                break
                if len(debug) < 40:
                    debug.append([int(el.CurrentControlType), cls, name])
                if name.strip().replace(",", "").isdigit():
                    val = int(name.strip().replace(",", ""))
                    if 0 <= val < 1_000_000_000:
                        total = val if total is None else max(total, val)
                if cls in ("reward-countdown", "reward-btn"):
                    rect = el.CurrentBoundingRectangle
                    ready = cls == "reward-btn" and bool(el.CurrentIsEnabled)
                    slots.append((int(rect.left), ready, el))
            except Exception:
                continue
        slots.sort(key=lambda x: x[0])
        left = {"ready": False}
        right = {"ready": False}
        if slots:
            left = {"ready": bool(slots[0][1])}
        if len(slots) > 1:
            right = {"ready": bool(slots[1][1])}

        invoked = None
        requested = []
        while True:
            try:
                requested.append(self._invoke_q.get_nowait())
            except queue.Empty:
                break
        for side in requested:
            idx = 0 if side == "left" else 1
            if len(slots) <= idx or not slots[idx][1]:
                continue
            try:
                pattern = slots[idx][2].GetCurrentPattern(10000)
                invoke = pattern.QueryInterface(UIA_RAW.IUIAutomationInvokePattern)
                invoke.Invoke()
                invoked = side
            except Exception:
                continue
        return {"ok": True, "total": total, "left": left, "right": right, "hwnd": hwnd,
                "reward": reward, "invoked": invoked, "debug": debug}

    def _read(self) -> dict:
        hwnd = self._find_hwnd()
        if not hwnd:
            return {"ok": False, "reason": "未找到 Wuwa Tappo"}
        try:
            raw = self._read_raw(hwnd)
            if raw is not None:
                return raw
        except Exception as e:
            return {"ok": False, "reason": f"UIA 读取失败: {e}"}
        return {"ok": False, "reason": "UIA 无数据"}

        total = None
        left = {"ready": False}
        right = {"ready": False}
        for c, _depth in items:
            try:
                cls = c.ClassName or ""
                name = c.Name or ""
                if cls == "reward-countdown":
                    parent = c.GetParentControl()
                    pcls = parent.ClassName or "" if parent else ""
                    if "--left" in pcls:
                        left = {"ready": False}
                    elif "--right" in pcls:
                        right = {"ready": False}
                elif cls == "reward-btn":
                    parent = c.GetParentControl()
                    pcls = parent.ClassName or "" if parent else ""
                    if "--left" in pcls:
                        left = {"ready": True}
                    elif "--right" in pcls:
                        right = {"ready": True}
                elif c.ControlTypeName == "TextControl" and name.strip().isdigit():
                    val = int(name.strip())
                    if 0 <= val < 1_000_000_000:
                        total = val if total is None else max(total, val)
                elif name.strip().isdigit():
                    # Some providers expose the pet info text with a different
                    # control type; accept any numeric element as a fallback.
                    val = int(name.strip())
                    if 0 <= val < 1_000_000_000:
                        total = val if total is None else max(total, val)
            except Exception:
                continue
        return {
            "ok": True,
            "total": total,
            "left": left,
            "right": right,
            "hwnd": hwnd,
        }

    def _run_bridge(self):
        """Frozen-build UIA reader using the tested PowerShell bridge."""
        try:
            flags = 0x08000000  # CREATE_NO_WINDOW
            p = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UIA_BRIDGE)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags,
            )
            self._bridge_proc = p
            if p.stdout is None:
                self._run()
                return
            for line in p.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    st = json.loads(line)
                except Exception:
                    continue
                self.stateChanged.emit(st)
            try:
                p.terminate()
            except Exception:
                pass
        except Exception:
            # If PowerShell is unavailable, fall back to the in-process COM reader.
            if not self._stop.is_set():
                self._run()
        finally:
            self._bridge_proc = None

    def _run(self):
        try:
            if comtypes is not None:
                comtypes.CoInitialize()
            while not self._stop.is_set():
                try:
                    st = self._read()
                except Exception as e:
                    st = {"ok": False, "reason": str(e)}
                self.stateChanged.emit(st)
                self._stop.wait(0.35)
        except Exception:
            self.stateChanged.emit({"ok": False, "reason": "UIA 线程异常"})
        finally:
            try:
                if comtypes is not None:
                    comtypes.CoUninitialize()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Click engine
# ---------------------------------------------------------------------------
@dataclass
class EngineConfig:
    vk: int
    cps: float
    hold_ms: float
    jitter: bool


class ClickEngine(QObject):
    stateChanged = Signal(dict)
    finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cfg: EngineConfig):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(cfg,), name="ClickEngine", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self, cfg: EngineConfig):
        winmm.timeBeginPeriod(1)
        reason = "已停止"
        start = time.perf_counter()
        next_click = start
        count = 0
        times: deque[float] = deque(maxlen=512)
        last_emit = 0.0
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                if now >= next_click:
                    if not send_key(cfg.vk, cfg.hold_ms):
                        reason = "按键注入失败，请尝试管理员权限"
                        break
                    count += 1
                    times.append(time.perf_counter())
                    period = 1.0 / max(0.5, cfg.cps)
                    if cfg.jitter:
                        period *= random.uniform(0.88, 1.12)
                    next_click += period
                    if next_click < time.perf_counter() - period:
                        next_click = time.perf_counter() + period
                now = time.perf_counter()
                if now - last_emit >= 0.1:
                    last_emit = now
                    self.stateChanged.emit({
                        "count": count, "actual": self._actual(times),
                        "running": True,
                    })
                wait = min(next_click, time.perf_counter() + 0.05) - time.perf_counter()
                if wait > 0.0035:
                    self._stop.wait(wait - 0.0025)
                elif wait > 0:
                    end = time.perf_counter() + wait
                    while time.perf_counter() < end and not self._stop.is_set():
                        pass
        except Exception:
            reason = "运行异常"
            write_log("engine exception:\n" + traceback.format_exc())
        finally:
            try:
                winmm.timeEndPeriod(1)
            except Exception:
                pass
            self.stateChanged.emit({"count": count, "actual": 0.0, "running": False})
            self.finished.emit(reason)

    @staticmethod
    def _actual(times: deque[float]) -> float:
        if len(times) < 2:
            return 0.0
        now = time.perf_counter()
        recent = [t for t in times if now - t <= 2.0]
        if len(recent) < 2:
            return 0.0
        return (len(recent) - 1) / max(1e-6, recent[-1] - recent[0])


def write_log(message: str):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class AppConfig:
    sim_key: int = VK_F24
    chest_point: Optional[list[int]] = None
    chest_point2: Optional[list[int]] = None
    cps: float = 12.0
    hold_ms: float = 8.0
    jitter: bool = False
    auto_chest: bool = False
    bg_path: str = ""
    disclaimer_version: int = 0
    gender: str = "female"
    desktop_only: bool = False
    guide_seen: bool = False
    app_count_interval: int = 10
    dialogue_interval: int = 120
    simple_guard: bool = False
    simple_bubble_seconds: int = 5
    guard_topmost: bool = True


def load_config() -> AppConfig:
    cfg = AppConfig()
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
    except Exception:
        pass
    return cfg


@dataclass
class Stats:
    software_presses: int = 0
    green: int = 0
    blue: int = 0
    purple: int = 0
    gold: int = 0
    chests: int = 0
    achievements: list = None
    first_seen: str = ""
    last_seen: str = ""
    clue_coins: int = 0
    clue_awarded: int = 0
    hints: list = None
    pet_clicks: int = 0

    def __post_init__(self):
        if self.achievements is None:
            self.achievements = []
        if self.hints is None:
            self.hints = []


def load_stats() -> Stats:
    st = Stats()
    try:
        if STATS_PATH.exists():
            raw = json.loads(STATS_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(st, k):
                    setattr(st, k, v)
    except Exception:
        pass
    if not st.first_seen:
        st.first_seen = time.strftime("%Y-%m-%d %H:%M:%S")
    st.last_seen = time.strftime("%Y-%m-%d %H:%M:%S")
    return st


def save_stats(st: Stats) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        st.last_seen = time.strftime("%Y-%m-%d %H:%M:%S")
        STATS_PATH.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


ROVER_CLICK_LINES = [
    "嗯，我在。需要我做什么？",
    "别急，先把节奏稳住。",
    "我会看着的。你专心做你的事。",
    "今天的风很安静，适合慢慢来。",
    "又见面了。旅途还顺利吗？",
    "累的话就停一会儿，按键不会跑。",
    "你的手速，比声骸还快一点。",
    "我在听。",
    "这样的节奏……还不错。",
    "如果遇到麻烦，记得叫我。",
    "别把什么事都一个人扛着。",
    "再点下去，我可要记进旅途日志了。",
]

ROVER_RAPID_LINES = [
    "你还有重要的事要做吧？……不过，再陪你一会儿也行。",
    "先深呼吸。键盘又不会消失。",
    "我知道你在，但也不用这么频繁地确认。",
    "再点，我就要怀疑你今天是不是没有别的安排了。",
    "好，好，我在。慢慢说。",
]


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    desc: str
    color: str
    stars: int
    check: object
    hidden: bool = False
    hint: str = ""


ACHIEVEMENTS = [
    Achievement("first", "初次触碰", "完成第 1 次模拟按键", "#8e5e43", 1, lambda s: s.software_presses >= 1),
    Achievement("press100", "指尖热身", "本软件累计 100 次按键", "#6fbba9", 1, lambda s: s.software_presses >= 100),
    Achievement("press1000", "键盘旅人", "本软件累计 1,000 次按键", "#5b9bd5", 2, lambda s: s.software_presses >= 1000),
    Achievement("press5000", "连击达人", "本软件累计 5,000 次按键", "#9b7bd4", 3, lambda s: s.software_presses >= 5000),
    Achievement("press10000", "万次相伴", "本软件累计 10,000 次按键", "#d48a2e", 4, lambda s: s.software_presses >= 10000),
    Achievement("press50000", "指尖风暴", "本软件累计 50,000 次按键", "#d45b4e", 5, lambda s: s.software_presses >= 50000),

    Achievement("green1", "第一片绿叶", "获得 1 个绿色品质（90%）", "#6f9b63", 1, lambda s: s.green >= 1),
    Achievement("green10", "绿意渐浓", "获得 10 个绿色品质", "#6f9b63", 2, lambda s: s.green >= 10),
    Achievement("green100", "绿野成行", "获得 100 个绿色品质", "#6f9b63", 3, lambda s: s.green >= 100),
    Achievement("green500", "森林的低语", "获得 500 个绿色品质", "#6f9b63", 4, lambda s: s.green >= 500),

    Achievement("blue1", "一抹蓝光", "获得 1 个蓝色品质（9.5%）", "#4a90d9", 1, lambda s: s.blue >= 1),
    Achievement("blue5", "蓝色回声", "获得 5 个蓝色品质", "#4a90d9", 2, lambda s: s.blue >= 5),
    Achievement("blue20", "深海来客", "获得 20 个蓝色品质", "#4a90d9", 3, lambda s: s.blue >= 20),
    Achievement("blue50", "潮汐收藏家", "获得 50 个蓝色品质", "#4a90d9", 4, lambda s: s.blue >= 50),

    Achievement("purple1", "紫色的第一眼", "获得 1 个紫色品质（0.49%）", "#9b6bd4", 4, lambda s: s.purple >= 1),
    Achievement("purple3", "紫电三次", "获得 3 个紫色品质", "#9b6bd4", 5, lambda s: s.purple >= 3),

    Achievement("gold1", "金色传说", "获得 1 个金色品质（0.01%）", "#d4a017", 5, lambda s: s.gold >= 1),

    Achievement("chest10", "宝箱旅人", "累计开启 10 个宝箱", "#6fbba9", 2, lambda s: s.chests >= 10),
    Achievement("chest100", "宝箱猎人", "累计开启 100 个宝箱", "#d48a2e", 4, lambda s: s.chests >= 100),
    Achievement("chest500", "宝箱传说", "累计开启 500 个宝箱", "#d45b4e", 5, lambda s: s.chests >= 500),

    Achievement("pet_once", "摸摸猫猫", "在值守界面点击漂泊者 1 次", "#6fbba9", 2,
                lambda s: False, True, "试试在桌宠或主界面的漂泊者身上点一下。"),
    Achievement("pet_three", "猫猫哈气", "10 秒内点击漂泊者 3 次", "#d48a2e", 3,
                lambda s: False, True, "短时间内连续点三次——她/他不会真的生气，只会提醒你还有重要的事。"),
]


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def load_fonts():
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "segoeui.ttf"):
        p = windir / "Fonts" / name
        try:
            if p.exists():
                QFontDatabase.addApplicationFont(str(p))
        except Exception:
            pass


def add_shadow(w: QWidget, blur=24, alpha=35, dy=6):
    e = QGraphicsDropShadowEffect(w)
    e.setBlurRadius(blur)
    e.setOffset(0, dy)
    e.setColor(QColor(80, 52, 36, alpha))
    w.setGraphicsEffect(e)


class Switch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 1.0 if self.isChecked() else 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.toggled.connect(lambda _: self._timer.start())

    def _tick(self):
        target = 1.0 if self.isChecked() else 0.0
        self._pos += (target - self._pos) * 0.35
        if abs(target - self._pos) < 0.02:
            self._pos = target
            self._timer.stop()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e98b7d") if self._pos > 0.5 else QColor("#d9cbb8"))
        p.drawRoundedRect(QRectF(1, 2, 38, 18), 9, 9)
        p.setBrush(QColor("#fff"))
        p.setPen(QPen(QColor(0, 0, 0, 18), 1))
        p.drawEllipse(QPointF(10 + self._pos * 20, 11), 7, 7)


class Card(QFrame):
    def __init__(self, parent=None, name="card"):
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WA_StyledBackground, True)


class Pill(QLabel):
    def __init__(self, text, tone="cream", parent=None):
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(24)


class ClickablePill(Pill):
    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
        else:
            super().mousePressEvent(e)


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
        else:
            super().mousePressEvent(e)


class Shell(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm: Optional[QPixmap] = None
        self._scaled: Optional[QPixmap] = None
        self._custom = False

    def set_background(self, path: str) -> bool:
        if not path:
            return False
        pm = QPixmap(path)
        if pm.isNull():
            return False
        self._pm = pm
        try:
            self._custom = Path(path).resolve() != DEFAULT_BG.resolve()
        except Exception:
            self._custom = True
        self._scaled = None
        self.update()
        return True

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        p.setClipPath(path)
        if self._pm:
            if self._scaled is None or self._scaled.size() != self.size():
                self._scaled = self._pm.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm = self._scaled
            sx = max(0, (pm.width() - self.width()) // 2)
            sy = max(0, (pm.height() - self.height()) // 2)
            p.drawPixmap(0, 0, pm, sx, sy, self.width(), self.height())
        else:
            p.fillRect(self.rect(), QColor("#fdf6e8"))
        p.fillPath(path, QColor(253, 248, 238, 185 if self._custom else 150))
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(255, 255, 255, 35))
        grad.setColorAt(1, QColor(232, 139, 124, 20))
        p.fillPath(path, grad)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 190), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


# ---------------------------------------------------------------------------
# Point picker
# ---------------------------------------------------------------------------
class PointPicker(QWidget):
    picked = Signal(int, int)
    cancelled = Signal()

    def __init__(self, title: str, hint: str):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self._title = title
        self._hint = hint
        union = QRect()
        for s in QGuiApplication.screens():
            union = union.united(s.geometry())
        self.setGeometry(union)
        self._mouse = QCursor.pos()
        self._tip = QLabel(self)
        self._tip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tip.setText(
            f"<div style='font-size:16px;font-weight:700;color:#fff8ec'>{title}</div>"
            f"<div style='font-size:11px;color:#f4e6d2;margin-top:3px'>{hint}</div>"
            f"<div style='font-size:10px;color:#e8cdb0;margin-top:2px'>左键确认 · 右键取消</div>"
        )
        self._tip.setStyleSheet(
            "background:rgba(52,36,28,225);border:1px solid rgba(255,255,255,55);"
            "border-radius:12px;padding:10px 15px;"
        )
        self._tip.adjustSize()
        self._move_tip()

    def _move_tip(self):
        x = self._mouse.x() - self.geometry().x() + 24
        y = self._mouse.y() - self.geometry().y() + 24
        x = max(10, min(self.width() - self._tip.width() - 10, x))
        y = max(10, min(self.height() - self._tip.height() - 10, y))
        self._tip.move(x, y)

    def mouseMoveEvent(self, e):
        self._mouse = QCursor.pos()
        self._move_tip()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            x, y = get_cursor_pos()
            self.picked.emit(x, y)
            self.close()
        elif e.button() == Qt.RightButton:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        local = QPoint(self._mouse.x() - self.geometry().x(), self._mouse.y() - self.geometry().y())
        p.fillRect(self.rect(), QColor(30, 20, 14, 45))
        p.setPen(QPen(QColor(255, 207, 151, 210), 1, Qt.DashLine))
        p.drawLine(0, local.y(), self.width(), local.y())
        p.drawLine(local.x(), 0, local.x(), self.height())
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawEllipse(local, 30, 30)
        p.setPen(QPen(QColor("#e98b7d"), 2))
        p.drawEllipse(local, 16, 16)
        p.setPen(QPen(QColor("#fff8ec"), 2))
        p.drawLine(local + QPoint(-6, 0), local + QPoint(6, 0))
        p.drawLine(local + QPoint(0, -6), local + QPoint(0, 6))
        coord = f"({self._mouse.x()}, {self._mouse.y()})"
        p.setFont(QFont("Consolas", 9))
        fm = p.fontMetrics()
        box = QRect(local.x() + 38, local.y() + 38, fm.horizontalAdvance(coord) + 14, 23)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(52, 36, 28, 210))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor("#ffe9cf"))
        p.drawText(box, Qt.AlignCenter, coord)


class ManualPointDialog(QDialog):
    def __init__(self, parent, title, point):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(380, 190)
        self._result = None
        self.setStyleSheet(
            "QDialog{background:#fffaf0;} QLabel{color:#5b4032;}"
            "QSpinBox{background:#fff;border:1px solid #e3d4c1;border-radius:7px;padding:5px;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(9)
        lay.addWidget(QLabel(title))
        row = QHBoxLayout()
        row.addWidget(QLabel("X"))
        self.sx = QSpinBox()
        self.sx.setRange(-20000, 30000)
        self.sx.setValue(point[0] if point else 0)
        row.addWidget(self.sx, 1)
        row.addWidget(QLabel("Y"))
        self.sy = QSpinBox()
        self.sy.setRange(-20000, 30000)
        self.sy.setValue(point[1] if point else 0)
        row.addWidget(self.sy, 1)
        lay.addLayout(row)
        note = QLabel("推荐用“定位”按钮：全屏准星出现后，左键点击目标。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#96745f;font-size:11px;")
        lay.addWidget(note)
        btns = QHBoxLayout()
        cur = QPushButton("填入当前鼠标位置")
        cur.setObjectName("ghost")
        cur.clicked.connect(lambda: (self.sx.setValue(get_cursor_pos()[0]), self.sy.setValue(get_cursor_pos()[1])))
        btns.addWidget(cur)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("确定")
        ok.setObjectName("primary")
        ok.clicked.connect(self._ok)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _ok(self):
        self._result = (int(self.sx.value()), int(self.sy.value()))
        self.accept()

    def point(self):
        return self._result


# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------
DISCLAIMER_HTML = """
<style>
body{color:#5b4032;font-size:12px;line-height:1.8;font-family:'Microsoft YaHei UI','Microsoft YaHei';}
h1{font-size:20px;color:#51372a;margin:0 0 4px 0;} h2{font-size:14px;color:#8e5e43;margin:14px 0 4px 0;}
.lead{background:#fff3df;border-left:4px solid #e98b7d;padding:9px 12px;border-radius:7px;}
.warn{background:#fff0ec;border-left:4px solid #d45b4e;padding:9px 12px;border-radius:7px;}
li{margin:3px 0;} .muted{color:#96745f;font-size:11px;}
</style>
<h1>免责声明与使用风险告知</h1>
<p class="muted">WuwaTappo 小助手 v1.2.0　·　作者：让你破费的泪</p>
<p class="lead"><b>此应用由 ai 制作，使用请自行确定安全性，制作人对此不负责。</b><br>
本软件不是库洛游戏、《鸣潮》或 Wuwa Tappo 官方产品，也未获得官方授权或背书。</p>

<h2>一、AI 生成与安全性</h2>
<p>本软件由人工智能辅助生成，可能存在错误、遗漏、兼容性问题或未预见行为。请只从可信来源获取可执行文件，运行前使用安全软件扫描，并先在低风险环境测试。因第三方重新打包、修改或植入代码造成的后果，制作人无法负责。</p>

<h2>二、账号与规则风险</h2>
<p>连点、自动化、宏或类似操作可能违反游戏/桌面宠物的用户协议、活动规则或社区规范，可能导致点击数据异常、奖励取消、活动资格限制、警告、临时封禁或永久封禁。不同账号、区服、版本的风控策略不同，即使本次未触发限制，也不代表后续安全。</p>

<h2>三、设备与操作风险</h2>
<ul>
<li>连点会持续发送模拟按键，可能误触游戏技能、宏或其他快捷键。</li>
<li>高频输入可能触发安全软件、输入法、远程控制或反作弊拦截，造成卡顿或输入失效。</li>
<li>以管理员权限运行时拥有更高系统权限，请勿同时运行来源不明的程序。</li>
</ul>

<h2>四、功能与结果不作保证</h2>
<p>本软件不保证连点速率在任何机器上精确达到设定值，不保证宝箱一定可开、奖励一定到账。宝箱位置需要手动定位；分辨率、缩放、窗口移动后需要重新定位。点击数读取依赖 Wuwa Tappo 的 UI 自动化接口，应用更新后可能失效。</p>

<h2>五、无担保与责任限制</h2>
<p>本软件按“现状”提供，不附带任何明示或默示担保。在适用法律允许的最大范围内，制作人不对因使用或无法使用本软件造成的任何直接、间接、附带、特殊或后果性损失负责，包括账号损失、虚拟财产损失、数据丢失、设备损坏、活动中断或第三方索赔。</p>

<h2>六、数据与隐私</h2>
<p>本软件默认仅在本地运行，不读取游戏账号密码，不连接游戏服务器，不上传点击记录、截图或个人信息。配置与日志保存在 %APPDATA%\\WuwaAutoChest。更换底图时选择的图片只保存在本地路径。</p>

<h2>七、素材版权</h2>
<p>界面中的角色、宝箱、纹理等素材版权归库洛游戏及相关权利人所有，仅用于本机个人学习与界面展示，不得商业分发、二次售卖或冒充官方产品。</p>

<h2>八、使用前检查</h2>
<ul>
<li>关闭重要文档与不希望被误点的窗口。</li>
<li>先用 F8 启动低速率试跑，确认 F3 能立即停止。</li>
<li>控制键为 F1/F2/F3、F8、F10、F11；连发键可选 F13–F24，不会影响正常打字。</li>
<li>宝箱位置要准确放在按钮中心；窗口移动后重新定位。</li>
</ul>

<h2>九、接受条款</h2>
<p class="warn"><b>点击“我已阅读并知悉”表示你已理解并自愿承担全部风险；如不同意，请关闭并删除本软件。</b></p>
<p class="muted">作者：让你破费的泪　·　此应用由 ai 制作，使用请自行确定安全性，制作人对此不负责。</p>
"""


class InfoDialog(QDialog):
    def __init__(self, parent, title, html):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 560)
        self.setMinimumSize(620, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        t = QLabel(title)
        t.setStyleSheet("font-size:17px;font-weight:700;color:#51372a;")
        lay.addWidget(t)
        b = QTextBrowser()
        b.setHtml(html)
        b.setOpenExternalLinks(False)
        lay.addWidget(b, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("知道了")
        btn.setObjectName("primary")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)


GUIDE_HTML = """
<style>
body{color:#5b4032;font-size:12px;line-height:1.8;font-family:'Microsoft YaHei UI';}
h2{font-size:14px;color:#8e5e43;margin:12px 0 4px 0;} li{margin:3px 0;}
.key{background:#fff8ec;border:1px solid #e3d4c1;border-radius:5px;padding:1px 6px;font-family:Consolas;font-weight:700;}
.warn{background:#fff3e8;border-left:3px solid #e0a06b;padding:5px 8px;border-radius:4px;}
</style>
<h2>这个软件能做什么</h2>
<p>打开 Wuwa Tappo 后，小助手可以自动连续按键、在宝箱就绪时打开宝箱，并在桌面上显示漂泊者值守窗口。它不修改游戏文件，也不读取账号密码。</p>

<h2>第一次使用</h2>
<ol>
<li>先把整个压缩包解压到一个文件夹，保留 <b>_internal</b> 文件夹，不要只复制 exe。</li>
<li>如果 Windows 提示“Windows 已保护你的电脑”：点“更多信息”，再点“仍要运行”。</li>
<li>双击 <b>WuwaTappo 小助手.exe</b>，在 UAC 弹窗里选择“是”。</li>
<li>打开 Wuwa Tappo 并登录，让桌面上的 Wuwa Tappo 桌宠显示出来。</li>
<li>回到小助手，模拟键保持默认 <b>F24</b>，按 <b>F8</b> 开始；按 <b>F3</b> 停止。</li>
</ol>

<h2>快捷键</h2>
<ul>
<li><span class="key">F8</span> 开始 / 停止自动连发</li>
<li><span class="key">F3</span> 只停止</li>
<li><span class="key">F1</span> 开箱一　<span class="key">F2</span> 开箱二</li>
<li><span class="key">F10</span> 主面板 / 桌宠切换</li>
<li><span class="key">F11</span> 自动开箱开 / 关</li>
</ul>
<p>连发键是 F13–F24，默认 F24。这些键不会输入文字，也不会占用常用打字键。</p>

<h2>自动开箱</h2>
<p>“自动开箱”默认关闭。按 <b>F11</b> 或打开主界面的“自动开箱 (F11)”开关后，才会处理已经就绪的宝箱。</p>
<p>不需要手动定位宝箱；程序会自己找到宝箱按钮。一次只开一个宝箱，等奖励弹层结束后再处理另一个。</p>

<h2>桌面值守</h2>
<p>主界面可以打开“启用桌面值守”和“简洁版值守”。</p>
<ul>
<li>打开选项本身不会立刻收起主面板；按 <b>F8</b> 开始连发，或打开自动开箱后，才会进入值守。</li>
<li>普通版有可滚动对话；简洁版只显示漂泊者，有消息时弹出气泡。</li>
<li>按 <b>F10</b>、双击角色或右键角色，可以回到主面板。</li>
<li>“值守窗口置顶”默认开启；觉得挡住其他窗口时可以关闭。</li>
<li>值守窗口默认在屏幕右下角，角色图案本身可以按住拖动。</li>
</ul>

<h2>设置</h2>
<p>“更多 → 设置”可以调整：</p>
<ul>
<li>当前应用计数刷新间隔（默认 10 秒）；</li>
<li>点击数据语句间隔（默认 120 秒）；</li>
<li>简洁气泡显示时长（默认 5 秒）。</li>
</ul>

<h2>成就与线索币</h2>
<p>成就会根据累计按键、宝箱品质和宝箱数量自动解锁。每累计 10,000 次按键获得 1 枚线索币，可解锁一个隐藏成就提示。在“更多 → 成就”查看。</p>

<h2>注意事项</h2>
<ul>
<li>本软件不是官方产品，自动化操作有账号风险，请自行判断是否使用。</li>
<li>请保留 <b>_internal</b> 文件夹，否则程序无法启动。</li>
<li>如果“当前应用”显示“—”，请先允许 UAC，并确认 Wuwa Tappo 正在运行。</li>
<li>不要按 F9/F7 操作本软件的桌宠；这两个键属于 Wuwa Tappo 自己的功能，可能改变它的桌宠状态。</li>
</ul>
"""

FAQ_HTML = """
<style>
body{color:#5b4032;font-size:12px;line-height:1.8;font-family:'Microsoft YaHei UI';}
h2{font-size:13px;color:#8e5e43;margin:12px 0 3px 0;} .q{font-weight:700;color:#6a4a37;}
</style>

<h2>Windows 提示“Windows 已保护你的电脑”怎么办？</h2>
<p>这是 Windows SmartScreen 对未签名程序的提示。点“更多信息”，再点“仍要运行”即可。</p>
<p>如果无法点击，请确认文件是从本压缩包解压出来的，并且没有被杀毒软件隔离。</p>

<h2>启动时出现 UAC 管理员提示，正常吗？</h2>
<p>正常。小助手需要管理员权限才能稳定读取 Wuwa Tappo 并发送按键。请选择“是”；选择“否”程序会退出。</p>

<h2>按 F8 后没有反应？</h2>
<p>先看主界面右下角状态是否为“运行中”，确认模拟键是 F24 或 F13–F24 中的其他键，再确认 Wuwa Tappo 正在运行。</p>

<h2>宝箱不自动开？</h2>
<p>确认主界面的“自动开箱 (F11)”已打开，并且按了 F8 开始连发。自动开箱默认是关闭的。</p>

<h2>“当前应用”显示“—”或数字不动？</h2>
<p>请允许 UAC，确认 Wuwa Tappo 没有最小化到不可见状态。如果刚刚重启过 Wuwa Tappo，等几秒会自动重连并刷新。</p>

<h2>桌宠或简洁值守不见了？</h2>
<p>按 <b>F10</b> 回到主面板，再在“桌面值守”卡片里重新进入。也可以看主界面是否还显示“运行中”。</p>

<h2>值守窗口挡住了其他程序？</h2>
<p>在“桌面值守”卡片关闭“值守窗口置顶”，或将角色拖到其他位置。</p>

<h2>F1/F2/F3 和别的软件冲突？</h2>
<p>本软件只在运行时监听这些键。可以先停止连发，再使用其他软件；如果仍有冲突，请关闭本软件。</p>

<h2>怎么退出小助手？</h2>
<p>主面板右上角“×”。如果只看到桌宠，按 <b>F10</b> 回到面板，或右键桌宠 →“退出”。</p>

<h2>数据保存在哪里？怎么重置？</h2>
<p>在 <b>%APPDATA%\\WuwaAutoChest</b> 下的 config.json、stats.json 和 app.log。删除整个 WuwaAutoChest 文件夹即可重置设置、统计和成就。</p>

<h2>为什么桌宠上的 F9/F7 不能用？</h2>
<p>本软件的快捷键是 F1/F2/F3、F8、F10、F11。F9/F7 留给 Wuwa Tappo 自己，避免两个程序同时响应同一个键。</p>
"""


class AchievementsDialog(QDialog):
    def __init__(self, parent, stats: Stats):
        super().__init__(parent)
        self.main = parent
        self.stats = stats
        self.setWindowTitle("成就")
        self.resize(720, 600)
        self.setMinimumSize(620, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        t = QLabel("成就 · 只属于这台电脑的记录")
        t.setStyleSheet("font-size:17px;font-weight:700;color:#51372a;")
        lay.addWidget(t)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_link)
        self.browser.setHtml(self._html(stats))
        lay.addWidget(self.browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _on_link(self, url):
        if url.scheme() == "hint":
            key = url.path().lstrip("/")
            if self.main and self.main.unlock_hint(key):
                self.browser.setHtml(self._html(self.main.stats))

    @staticmethod
    def _stars(n: int) -> str:
        return "★" * n + "☆" * (5 - n)

    @classmethod
    def _html(cls, stats: Stats) -> str:
        done = set(stats.achievements)
        total = len(ACHIEVEMENTS)
        got = sum(1 for a in ACHIEVEMENTS if a.key in done)
        stars = sum(a.stars for a in ACHIEVEMENTS if a.key in done)
        max_stars = sum(a.stars for a in ACHIEVEMENTS)
        rows = [
            "<style>body{color:#5b4032;font-size:12px;line-height:1.7;font-family:'Microsoft YaHei UI';}"
            "h2{font-size:14px;color:#8e5e43;} .locked{color:#b9a99a;} "
            ".gold{color:#c38b00;font-weight:800;text-shadow:0 0 8px #ffd700;}"
            ".purple{color:#8a5bc4;font-weight:700;} .blue{color:#3f7fc4;font-weight:700;}"
            ".green{color:#5d8f55;font-weight:700;}"
            ".ach{margin:5px 0;padding:6px 9px;border-left:3px solid #e98b7d;background:#fff8ec;border-radius:5px;}"
            ".hint{color:#a46952;font-size:11px;} a{color:#b06b52;text-decoration:none;}"
            "</style>",
            f"<h2>★ 星数 {stars} / {max_stars}　◆ 线索币 {stats.clue_coins}</h2>",
            f"<p>已获得 {got} / {total} 个成就　本软件累计按键：<b>{stats.software_presses:,}</b> 次<br>"
            f"宝箱：<b>{stats.chests}</b> 个　绿 {stats.green} / 蓝 {stats.blue} / 紫 {stats.purple} / 金 {stats.gold}</p>",
        ]
        for a in ACHIEVEMENTS:
            unlocked = a.key in done
            if a.hidden and not unlocked:
                hint = a.hint if a.key in stats.hints else ""
                if hint:
                    hint_html = f"<div class='hint'>线索：{hint}</div>"
                else:
                    hint_html = (
                        f"<div class='hint'><a href='hint:{a.key}'>消耗 1 线索币查看提示</a>"
                        f"（当前 {stats.clue_coins} 枚）</div>"
                    )
                rows.append(
                    f"<div class='ach locked'><span style='color:#b9a99a;font-weight:700'>？？？</span>"
                    f"<br><span style='color:#b9a99a'>隐藏成就</span>"
                    f"<br><span style='color:#b9a99a'>？？？</span>{hint_html}</div>"
                )
                continue
            color = a.color if unlocked else "#b9a99a"
            name = a.name
            desc = a.desc if unlocked else "未解锁"
            star_html = cls._stars(a.stars)
            cls_name = "ach" if unlocked else "ach locked"
            mark = "★" if unlocked else "·"
            rows.append(
                f"<div class='{cls_name}'><span style='color:{color};font-weight:700'>{mark} {name}</span> "
                f"<span style='color:{color}'>{star_html}</span>"
                f"<br><span style='color:{color}'>{desc}</span></div>"
            )
        if got == total:
            rows.append("<p class='gold'>★ 全成就达成。这个桌面，已经被你敲出了传说。 ★</p>")
        return "".join(rows)


class DisclaimerDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("免责声明")
        self.resize(720, 560)
        self.setMinimumSize(620, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        t = QLabel("免责声明 · 使用风险告知")
        t.setStyleSheet("font-size:17px;font-weight:700;color:#51372a;")
        lay.addWidget(t)
        from PySide6.QtWidgets import QTextBrowser
        b = QTextBrowser()
        b.setHtml(DISCLAIMER_HTML)
        b.setOpenExternalLinks(True)
        lay.addWidget(b, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton("不同意")
        no.setObjectName("ghost")
        no.clicked.connect(self.reject)
        row.addWidget(no)
        yes = QPushButton("我已阅读并知悉")
        yes.setObjectName("primary")
        yes.clicked.connect(self.accept)
        row.addWidget(yes)
        lay.addLayout(row)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
APP_QSS = """
* { outline:none; }
QWidget { font-family:'Microsoft YaHei UI','Microsoft YaHei','Segoe UI'; color:#5b4032; font-size:11px; }
QFrame#card { background:rgba(255,251,243,222); border:1px solid rgba(255,255,255,195); border-radius:16px; }
QFrame#clickCard { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(255,249,237,238),stop:1 rgba(255,233,218,225)); border:1px solid rgba(255,255,255,220); border-radius:18px; }
QFrame#chestCard { background:rgba(255,250,242,225); border:1px solid rgba(255,255,255,200); border-radius:16px; }
QFrame#hotkeyCard { background:rgba(255,245,233,220); border:1px solid rgba(255,255,255,200); border-radius:16px; }
QLabel#title { font-size:18px; font-weight:800; color:#4d3326; }
QLabel#sub { font-size:10px; color:#9a7a63; }
QLabel#cardTitle { font-size:12px; font-weight:700; color:#5a3d2d; }
QLabel#muted { color:#9a7a63; font-size:10px; }
QLabel#bigCount { font-size:40px; font-weight:800; color:#4d3326; }
QLabel#metric { font-size:14px; font-weight:700; color:#6a4a37; }
QLabel#key { background:#fff8ec; border:1px solid #e3d4c1; border-radius:5px; padding:2px 6px; font-family:Consolas; font-size:10px; font-weight:700; color:#8e5e43; }
QLabel#pill { border-radius:12px; padding:2px 9px; font-size:10px; background:rgba(255,255,255,155); border:1px solid rgba(255,255,255,190); color:#7c5a45; }
QLabel#pill[tone="live"] { background:rgba(233,139,125,235); color:white; }
QLabel#pill[tone="ok"] { background:rgba(120,198,179,225); color:white; }
QLabel#pill[tone="warn"] { background:rgba(241,190,115,230); color:#5a3d2d; }
QPushButton { border:none; border-radius:9px; padding:6px 11px; font-weight:600; background:#f4e8d7; color:#6c4b37; }
QPushButton:hover { background:#f8efe3; }
QPushButton#primary { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ef9b8a,stop:1 #e58072); color:white; padding:8px 14px; }
QPushButton#primary:disabled { background:#e5d5c4; color:#b9a08c; }
QPushButton#danger { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #e36558,stop:1 #cf4d42); color:white; padding:8px 14px; }
QPushButton#danger:disabled { background:#ecd3ca; color:#bda092; }
QPushButton#ghost { background:rgba(255,255,255,135); border:1px solid rgba(214,193,172,170); padding:4px 8px; font-weight:500; }
QPushButton#ghost:hover { background:rgba(255,255,255,210); }
QPushButton#link { background:transparent; color:#a46952; padding:1px 3px; font-weight:600; text-decoration:underline; }
QToolButton#more { background:rgba(255,255,255,155); border:1px solid rgba(214,193,172,150); border-radius:10px; padding:4px 12px; color:#6c4b37; font-weight:700; }
QMenu { background:#fffaf2; border:1px solid #eadbc8; border-radius:8px; padding:5px; }
QMenu::item { padding:7px 22px 7px 12px; border-radius:6px; }
QMenu::item:selected { background:#f7e7d7; color:#8e5e43; }
QSlider::groove:horizontal { height:6px; border-radius:3px; background:#eadfce; }
QSlider::sub-page:horizontal { background:#e98b7d; border-radius:3px; }
QSlider::handle:horizontal { width:16px; height:16px; margin:-5px 0; border-radius:8px; background:white; border:2px solid #e98b7d; }
QSpinBox, QDoubleSpinBox { background:rgba(255,255,255,190); border:1px solid #e3d4c1; border-radius:7px; padding:4px 6px; min-height:22px; }
QComboBox { background:rgba(255,255,255,190); border:1px solid #e3d4c1; border-radius:7px; padding:4px 8px; min-height:22px; }
QComboBox::drop-down { border:none; width:18px; }
QComboBox QAbstractItemView { background:#fffaf2; border:1px solid #e3d4c1; selection-background-color:#f7e7d7; selection-color:#5b4032; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width:0; height:0; border:none; }
QPlainTextEdit { background:rgba(255,255,255,125); border:1px solid rgba(234,219,200,160); border-radius:9px; padding:5px; font-size:10px; }
QScrollBar:vertical { background:transparent; width:7px; }
QScrollBar::handle:vertical { background:#dcc9b5; border-radius:3px; min-height:20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QTextBrowser { background:#fffdf7; border:1px solid #eadbc8; border-radius:9px; padding:6px; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent, cfg: AppConfig):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedSize(430, 300)
        self.setStyleSheet("QDialog{background:#fffaf0;} QLabel{color:#5b4032;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        lay.addWidget(QLabel("值守与刷新设置"))
        grid = QGridLayout()
        grid.setVerticalSpacing(9)
        grid.addWidget(QLabel("当前应用计数刷新"), 0, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 60)
        self.count_spin.setSuffix(" 秒")
        self.count_spin.setValue(int(cfg.app_count_interval))
        grid.addWidget(self.count_spin, 0, 1)
        grid.addWidget(QLabel("点击数据语句间隔"), 1, 0)
        self.talk_spin = QSpinBox()
        self.talk_spin.setRange(15, 600)
        self.talk_spin.setSuffix(" 秒")
        self.talk_spin.setValue(int(cfg.dialogue_interval))
        grid.addWidget(self.talk_spin, 1, 1)
        grid.addWidget(QLabel("简洁气泡显示"), 2, 0)
        self.bubble_spin = QSpinBox()
        self.bubble_spin.setRange(1, 15)
        self.bubble_spin.setSuffix(" 秒")
        self.bubble_spin.setValue(int(cfg.simple_bubble_seconds))
        grid.addWidget(self.bubble_spin, 2, 1)
        lay.addLayout(grid)
        note = QLabel("点击数据语句只影响普通计数台词；品质、成就、开始/停止不受影响。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)

    def values(self):
        return (
            int(self.count_spin.value()),
            int(self.talk_spin.value()),
            int(self.bubble_spin.value()),
        )


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        # Auto-open is deliberately session-scoped: every launch starts with it
        # off, so it only fires after the user presses F11 / the switch.
        self.cfg.auto_chest = False
        self.stats = load_stats()
        self.engine = ClickEngine(self)
        self.hotkeys = KeyboardHotkeys(self)
        self.uia = UiaWatcher(self)
        self._drag = None
        self._picked = ""
        self._overlay = None
        self._total = None
        self._ready_latch = {"left": False, "right": False}
        self._last_state = {}
        self._opened = 0
        self._toggle_stop_flag = False
        self._session_base = int(self.stats.software_presses)
        self._last_stats_save = 0.0
        self._last_reward = None
        self._last_reward_at = 0.0
        self._dialogue_history = []
        self.pet_window = None
        self.simple_window = None
        self._last_app_refresh = 0.0
        self._pending_app_total = None
        self._uia_lost = False
        self._last_count_say = 0.0
        self._pet_click_times = []
        self._pet_reply_last = ""
        self._auto_busy = False
        self._auto_busy_until = 0.0
        self._ui_built = False

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(900, 600)
        self.setMinimumSize(840, 560)
        scr = QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.left() + 24, g.top() + max(16, (g.height() - 600) // 2))

        self._build()
        self._apply()
        self._connect()
        self._check_achievements()
        self._update_clue_coins()
        self.hotkeys.start()
        self.uia.start()
        self._guard_timer = QTimer(self)
        self._guard_timer.setInterval(2500)
        self._guard_timer.timeout.connect(self._ensure_guard_visible)
        self._guard_timer.start()
        if self.cfg.disclaimer_version < 1 and "--screenshot" not in sys.argv:
            QTimer.singleShot(600, self._first_disclaimer)
        elif not self.cfg.guide_seen and "--screenshot" not in sys.argv and "--uia-test" not in sys.argv:
            QTimer.singleShot(900, self._first_onboarding)
        self._log("就绪。F8 开始/停止；F3 停止；F10 切换桌面值守。")
        self._say("F8 一按，我就开始敲键；F10 可以随时把我叫回来。", "#7c5a45")

    # ------------------------------------------------------------------ UI
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.shell = Shell(self)
        add_shadow(self.shell, 28, 38, 7)
        outer.addWidget(self.shell)

        root = QVBoxLayout(self.shell)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        # title bar
        tb = QHBoxLayout()
        tb.setContentsMargins(6, 0, 2, 0)
        ico = QLabel()
        if SIGRIKA_ICON.exists():
            pm = QPixmap(str(SIGRIKA_ICON))
            if not pm.isNull():
                ico.setPixmap(pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        tb.addWidget(ico)
        wtitle = QLabel("WuwaTappo 小助手")
        wtitle.setStyleSheet("font-size:10px;font-weight:700;color:#8e5e43;")
        tb.addWidget(wtitle)
        tb.addStretch(1)
        self.perm = Pill("普通权限", "warn")
        tb.addWidget(self.perm)
        for text, slot in (("—", self.showMinimized), ("×", self.close)):
            b = QToolButton()
            b.setText(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QToolButton{background:transparent;border:none;color:#8e5e43;font-size:14px;padding:0 7px;border-radius:6px;}"
                            "QToolButton:hover{background:rgba(255,255,255,150);}")
            b.clicked.connect(slot)
            tb.addWidget(b)
        root.addLayout(tb)

        # header
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        hv = QVBoxLayout()
        hv.setSpacing(0)
        title = QLabel("WuwaTappo 小助手")
        title.setObjectName("title")
        sub = QLabel("西格莉卡主题 · 模拟按键连发 · 宝箱内部开启")
        sub.setObjectName("sub")
        hv.addWidget(title)
        hv.addWidget(sub)
        head.addLayout(hv)
        head.addStretch(1)
        self.link_pill = Pill("正在连接 Wuwa Tappo…", "warn")
        head.addWidget(self.link_pill)
        self.state_pill = Pill("待命", "cream")
        head.addWidget(self.state_pill)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setContentsMargins(4, 0, 4, 0)
        body.setSpacing(9)

        # left
        left = QVBoxLayout()
        left.setSpacing(9)
        left.addWidget(self._dialogue_card(), 1)
        left.addWidget(self._hotkey_card(), 0)
        body.addLayout(left, 30)

        # center
        center = QVBoxLayout()
        center.setSpacing(9)
        center.addWidget(self._click_card(), 0)
        center.addWidget(self._control_card(), 1)
        body.addLayout(center, 42)

        # right
        right = QVBoxLayout()
        right.setSpacing(9)
        right.addWidget(self._mode_card(), 0)
        right.addWidget(self._chest_card(), 0)
        right.addWidget(self._option_card(), 0)
        right.addWidget(self._status_card(), 1)
        body.addLayout(right, 30)

        root.addLayout(body, 1)

        # footer
        foot = QHBoxLayout()
        foot.setContentsMargins(8, 0, 6, 0)
        hint = QLabel(f"作者：{AUTHOR}　·　F8 开始/停止　F3 停止　F10 面板　F11 自动开箱")
        hint.setObjectName("muted")
        foot.addWidget(hint)
        foot.addStretch(1)
        self.more = QToolButton()
        self.more.setObjectName("more")
        self.more.setText("更多")
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.more.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        a0 = menu.addAction("设置")
        a0.triggered.connect(self._settings)
        a1 = menu.addAction("使用前看我")
        a1.triggered.connect(self._guide)
        a2 = menu.addAction("常见问题")
        a2.triggered.connect(self._faq)
        a3 = menu.addAction("成就")
        a3.triggered.connect(self._achievements)
        menu.addSeparator()
        a4 = menu.addAction("免责声明")
        a4.triggered.connect(self._disclaimer)
        a5 = menu.addAction("更换底图")
        a5.triggered.connect(self._change_bg)
        self.more.setMenu(menu)
        foot.addWidget(self.more)
        root.addLayout(foot)

    def _dialogue_card(self):
        c = Card(name="card")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 9, 12, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(self._card_title("桌面值守"))
        top.addStretch(1)
        self.pet_status = Pill("待命", "cream")
        top.addWidget(self.pet_status)
        lay.addLayout(top)

        gender_row = QHBoxLayout()
        gender_row.setSpacing(5)
        gender_row.addWidget(QLabel("角色"))
        self.gender_combo = QComboBox()
        self.gender_combo.addItem("女漂泊者", "female")
        self.gender_combo.addItem("男漂泊者", "male")
        idx = self.gender_combo.findData(self.cfg.gender)
        self.gender_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.gender_combo.setFixedWidth(104)
        gender_row.addWidget(self.gender_combo)
        gender_row.addStretch(1)
        lay.addLayout(gender_row)

        self.dialog_view = QTextBrowser()
        self.dialog_view.setOpenExternalLinks(False)
        self.dialog_view.setStyleSheet(
            "QTextBrowser{background:rgba(255,255,255,145);border:1px solid rgba(234,219,200,150);"
            "border-radius:10px;padding:5px;font-size:10px;color:#6c4b37;}"
        )
        self.dialog_view.document().setDefaultStyleSheet(
            ".bubble{background:#fff8ec;border-radius:8px;padding:5px 7px;margin:3px 0;}"
            ".name{color:#b07a5c;font-size:9px;font-weight:700;}"
            ".gold{color:#c38b00;font-weight:800;font-size:12px;}"
            ".purple{color:#8a5bc4;font-weight:700;}"
            ".blue{color:#3f7fc4;font-weight:700;}"
            ".achievement{color:#b57a3e;font-weight:700;}"
        )
        lay.addWidget(self.dialog_view, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        self.pet_name = QLabel("漂泊者·女")
        self.pet_name.setObjectName("muted")
        bottom.addWidget(self.pet_name)
        bottom.addStretch(1)
        self.pet = ClickableLabel()
        self.pet.setFixedSize(82, 82)
        self.pet.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.pet.setCursor(Qt.PointingHandCursor)
        self.pet.clicked.connect(self.on_pet_click)
        bottom.addWidget(self.pet)
        lay.addLayout(bottom)
        self._update_character_assets()
        return c

    def _character_path(self) -> Path:
        return MASCOT_MALE if self.cfg.gender == "male" else MASCOT_FEMALE

    def _update_character_assets(self):
        path = self._character_path()
        pm = QPixmap(str(path)) if path.exists() else QPixmap(str(MASCOT))
        if hasattr(self, "pet") and not pm.isNull():
            self.pet.setPixmap(pm.scaled(82, 82, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if hasattr(self, "pet_name"):
            self.pet_name.setText("漂泊者·男" if self.cfg.gender == "male" else "漂泊者·女")
        if self.pet_window is not None:
            self.pet_window.set_character(self.cfg.gender)
        if self.simple_window is not None:
            self.simple_window.set_character(self.cfg.gender)

    def _hotkey_card(self):
        c = Card(name="hotkeyCard")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)
        lay.addWidget(self._card_title("操作热键"))
        rows = [
            ("F8", "开始 / 停止"),
            ("F3", "只停止"),
            ("F1", "开箱一"),
            ("F2", "开箱二"),
            ("F10", "面板 / 桌宠"),
            ("F11", "自动开箱"),
        ]
        for key, text in rows:
            r = QHBoxLayout()
            r.setSpacing(6)
            k = QLabel(key)
            k.setObjectName("key")
            k.setFixedWidth(42)
            k.setAlignment(Qt.AlignCenter)
            r.addWidget(k)
            r.addWidget(QLabel(text))
            r.addStretch(1)
            lay.addLayout(r)
        self.hotkey_status = QLabel("F1–F3 / F8 控制键监听中")
        self.hotkey_status.setObjectName("muted")
        lay.addWidget(self.hotkey_status)
        return c

    def _click_card(self):
        c = Card(name="clickCard")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(16, 10, 16, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(self._card_title("操作计数"))
        top.addStretch(1)
        self.actual = QLabel("实际 0.0 次/秒")
        self.actual.setObjectName("muted")
        top.addWidget(self.actual)
        lay.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(12)
        for key, name in (("session", "本次"), ("software", "本软件累计"), ("app", "当前应用")):
            box = QVBoxLayout()
            box.setSpacing(0)
            val = QLabel("0" if key != "app" else "—")
            val.setStyleSheet("font-size:24px;font-weight:800;color:#4d3326;")
            box.addWidget(val)
            lab = QLabel(name)
            lab.setObjectName("muted")
            box.addWidget(lab)
            row.addLayout(box, 1)
            setattr(self, f"{key}_value", val)
        lay.addLayout(row)

        self.app_hint = QLabel("正在读取 Wuwa Tappo…")
        self.app_hint.setObjectName("muted")
        lay.addWidget(self.app_hint)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.open_label = QLabel("已开箱 0")
        self.open_label.setObjectName("muted")
        bottom.addWidget(self.open_label)
        lay.addLayout(bottom)
        return c

    def _control_card(self):
        c = Card(name="card")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        lay.addWidget(self._card_title("模拟按键连发"))
        run = QHBoxLayout()
        run.setSpacing(8)
        self.start_btn = QPushButton("开始（F8）")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(34)
        self.stop_btn = QPushButton("停止 (F3)")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.setEnabled(False)
        run.addWidget(self.start_btn, 2)
        run.addWidget(self.stop_btn, 1)
        lay.addLayout(run)

        cps = QHBoxLayout()
        cps.setSpacing(6)
        cps.addWidget(QLabel("速率"))
        self.cps_slider = QSlider(Qt.Horizontal)
        self.cps_slider.setRange(1, 50)
        self.cps_slider.setValue(int(self.cfg.cps))
        cps.addWidget(self.cps_slider, 1)
        self.cps_spin = QDoubleSpinBox()
        self.cps_spin.setRange(1, 50)
        self.cps_spin.setDecimals(1)
        self.cps_spin.setSuffix(" 次/秒")
        self.cps_spin.setFixedWidth(92)
        self.cps_spin.setValue(float(self.cfg.cps))
        cps.addWidget(self.cps_spin)
        lay.addLayout(cps)

        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        key_row.addWidget(QLabel("模拟键"))
        self.key_combo = QComboBox()
        for vk in SIM_KEYS:
            self.key_combo.addItem(SIM_KEY_NAMES[vk], vk)
        idx = self.key_combo.findData(int(self.cfg.sim_key))
        self.key_combo.setCurrentIndex(idx if idx >= 0 else len(SIM_KEYS) - 1)
        self.key_combo.setFixedWidth(92)
        key_row.addWidget(self.key_combo)
        key_row.addWidget(QLabel("F13–F24 不在普通键盘上"))
        key_row.addStretch(1)
        lay.addLayout(key_row)

        adv = QHBoxLayout()
        adv.setSpacing(7)
        adv.addWidget(QLabel("按住"))
        self.hold = QDoubleSpinBox()
        self.hold.setRange(1, 30)
        self.hold.setSingleStep(1)
        self.hold.setDecimals(1)
        self.hold.setSuffix(" ms")
        self.hold.setValue(float(self.cfg.hold_ms))
        self.hold.setFixedWidth(76)
        adv.addWidget(self.hold)
        self.jitter = Switch()
        self.jitter.setChecked(self.cfg.jitter)
        adv.addWidget(self.jitter)
        adv.addWidget(QLabel("自然抖动"))
        adv.addStretch(1)
        lay.addLayout(adv)
        self.note = QLabel("建议 8–20 次/秒；F13–F24 不会输入文字，也不会占用常用键。")
        self.note.setObjectName("muted")
        lay.addWidget(self.note)
        return c

    def _mode_card(self):
        c = Card(name="card")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        row = QHBoxLayout()
        row.addWidget(QLabel("启用桌面值守"))
        row.addStretch(1)
        self.desktop_switch = Switch()
        self.desktop_switch.setChecked(self.cfg.desktop_only)
        row.addWidget(self.desktop_switch)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("简洁版值守"))
        row2.addStretch(1)
        self.simple_guard_switch = Switch()
        self.simple_guard_switch.setChecked(self.cfg.simple_guard)
        row2.addWidget(self.simple_guard_switch)
        lay.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("值守窗口置顶"))
        row3.addStretch(1)
        self.topmost_switch = Switch()
        self.topmost_switch.setChecked(self.cfg.guard_topmost)
        row3.addWidget(self.topmost_switch)
        lay.addLayout(row3)
        note = QLabel("开始连击或开启自动开箱后进入；F10 可打开面板")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        return c

    def _chest_card(self):
        c = Card(name="chestCard")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(self._card_title("宝箱"))
        top.addStretch(1)
        top.addWidget(self._pill("内部开启", "cream"))
        lay.addLayout(top)
        self.chest_labels = {}
        for idx, key in enumerate(("left", "right"), start=1):
            row = QHBoxLayout()
            row.setSpacing(4)
            name = QLabel(f"宝箱一" if idx == 1 else "宝箱二")
            name.setFixedWidth(38)
            row.addWidget(name)
            lab = QLabel("检测中…")
            lab.setStyleSheet("font-weight:700;color:#8e5e43;font-size:10px;")
            row.addWidget(lab, 1)
            self.chest_labels[key] = lab
            b2 = QPushButton("开箱")
            b2.setObjectName("ghost")
            b2.clicked.connect(lambda _=False, k=key: self._open_chest(k, manual=True))
            b2.setFixedWidth(48)
            row.addWidget(b2)
            lay.addLayout(row)
        lay.addWidget(QLabel("F1 开箱一 · F2 开箱二 · 不点鼠标"))
        return c

    def _option_card(self):
        c = Card(name="card")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("自动开箱（F11）"))
        row.addStretch(1)
        self.auto_chest = Switch()
        self.auto_chest.setChecked(self.cfg.auto_chest)
        row.addWidget(self.auto_chest)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.bg_btn = QPushButton("恢复默认底图")
        self.bg_btn.setObjectName("link")
        self.bg_btn.clicked.connect(self._restore_bg)
        row2.addWidget(self.bg_btn)
        row2.addStretch(1)
        self.admin_btn = QPushButton("管理员重启")
        self.admin_btn.setObjectName("link")
        self.admin_btn.clicked.connect(self._admin)
        row2.addWidget(self.admin_btn)
        lay.addLayout(row2)
        return c

    def _status_card(self):
        c = Card(name="card")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(self._card_title("状态"))
        top.addStretch(1)
        clr = QPushButton("清空")
        clr.setObjectName("link")
        clr.clicked.connect(lambda: self.log.clear())
        top.addWidget(clr)
        lay.addLayout(top)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(80)
        self.log.setMinimumHeight(55)
        self.log.setMaximumHeight(105)
        self.log.setPlaceholderText("运行状态…")
        lay.addWidget(self.log, 1)
        return c

    @staticmethod
    def _card_title(text):
        lab = QLabel(text)
        lab.setObjectName("cardTitle")
        return lab

    @staticmethod
    def _pill(text, tone):
        return Pill(text, tone)

    # ------------------------------------------------------------------ config
    def _apply(self):
        if not self.cfg.bg_path or not self.shell.set_background(self.cfg.bg_path):
            self.shell.set_background(str(DEFAULT_BG))
        self._refresh_points()
        self.cps_spin.setValue(float(self.cfg.cps))
        self.hold.setValue(float(self.cfg.hold_ms))
        self.jitter.setChecked(self.cfg.jitter)
        self.auto_chest.setChecked(self.cfg.auto_chest)
        self.desktop_switch.setChecked(self.cfg.desktop_only)
        self.simple_guard_switch.setChecked(self.cfg.simple_guard)
        self.topmost_switch.setChecked(self.cfg.guard_topmost)
        self.gender_combo.setCurrentIndex(max(0, self.gender_combo.findData(self.cfg.gender)))
        self._update_character_assets()
        self.software_value.setText(f"{self.stats.software_presses:,}")
        self.open_label.setText(f"已开箱 {self.stats.chests}")
        self.app_value.setText("—")
        if is_admin():
            self.perm.setText("管理员")
            self.perm.setProperty("tone", "ok")
            self.perm.style().unpolish(self.perm)
            self.perm.style().polish(self.perm)

    def _save(self, *args):
        self.cfg.cps = float(self.cps_spin.value())
        self.cfg.hold_ms = float(self.hold.value())
        self.cfg.jitter = self.jitter.isChecked()
        self.cfg.auto_chest = self.auto_chest.isChecked()
        try:
            self.cfg.sim_key = int(self.key_combo.currentData())
        except Exception:
            self.cfg.sim_key = VK_F24
        self.cfg.bg_path = getattr(self.shell, "_bg_path", "") if getattr(self.shell, "_custom", False) else ""
        self.cfg.desktop_only = self.desktop_switch.isChecked()
        self.cfg.simple_guard = self.simple_guard_switch.isChecked()
        self.cfg.guard_topmost = self.topmost_switch.isChecked()
        self.cfg.gender = self.gender_combo.currentData() or "female"
        save_config(self.cfg)

    def _on_gender_changed(self, *args):
        self.cfg.gender = self.gender_combo.currentData() or "female"
        save_config(self.cfg)
        self._update_character_assets()
        self._say("换我上场。无论男漂泊者还是女漂泊者，都会盯着倒计时。", "#7c5a45")

    def _on_desktop_switch(self, checked: bool):
        self.cfg.desktop_only = bool(checked)
        save_config(self.cfg)
        # Turning the option on does NOT enter immediately.  It is entered when
        # auto-click starts or auto-open is switched on.
        if not checked and not self.isVisible():
            self.leave_desktop_mode()

    def _on_simple_guard(self, checked: bool):
        self.cfg.simple_guard = bool(checked)
        save_config(self.cfg)
        if self.cfg.desktop_only and not self.isVisible():
            self.enter_desktop_mode()

    def _on_topmost(self, checked: bool):
        self.cfg.guard_topmost = bool(checked)
        save_config(self.cfg)
        for w in (self.pet_window, self.simple_window):
            if w is not None:
                w.set_always_on_top(bool(checked))

    def _on_auto_chest(self, checked: bool):
        self.cfg.auto_chest = bool(checked)
        save_config(self.cfg)
        if checked and self.cfg.desktop_only and self.isVisible():
            self.enter_desktop_mode()
        self._log("自动开箱：" + ("开" if checked else "关"))

    def enter_desktop_mode(self):
        if self.cfg.simple_guard:
            if self.pet_window is not None:
                self.pet_window.hide()
            if self.simple_window is None:
                self.simple_window = SimpleGuardWindow(self)
            self.simple_window.set_character(self.cfg.gender)
            self.simple_window.show()
            self.simple_window.raise_()
        else:
            if self.simple_window is not None:
                self.simple_window.hide()
            if self.pet_window is None:
                self.pet_window = DesktopPetWindow(self)
            self.pet_window.set_character(self.cfg.gender)
            self.pet_window.set_history(self._dialogue_history)
            self.pet_window.show()
            self.pet_window.raise_()
        self.hide()

    def _ensure_guard_visible(self):
        if not self.cfg.desktop_only or self.isVisible():
            return
        if self.cfg.simple_guard:
            if self.simple_window is None:
                self.simple_window = SimpleGuardWindow(self)
            if not self.simple_window.isVisible():
                self.simple_window.show()
        else:
            if self.pet_window is None:
                self.pet_window = DesktopPetWindow(self)
            if not self.pet_window.isVisible():
                self.pet_window.show()

    def leave_desktop_mode(self):
        if self.pet_window is not None:
            self.pet_window.hide()
        if self.simple_window is not None:
            self.simple_window.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_panel(self):
        if self.isVisible():
            self.enter_desktop_mode()
        else:
            self.leave_desktop_mode()

    def _connect(self):
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(lambda: self.stop("界面停止"))
        self.cps_slider.valueChanged.connect(lambda v: self.cps_spin.setValue(float(v)))
        self.cps_spin.valueChanged.connect(lambda v: self.cps_slider.setValue(int(round(v))))
        self.cps_spin.valueChanged.connect(self._save)
        self.hold.valueChanged.connect(self._save)
        self.jitter.toggled.connect(self._save)
        self.auto_chest.toggled.connect(self._on_auto_chest)
        self.key_combo.currentIndexChanged.connect(self._save)
        self.gender_combo.currentIndexChanged.connect(self._on_gender_changed)
        self.desktop_switch.toggled.connect(self._on_desktop_switch)
        self.simple_guard_switch.toggled.connect(self._on_simple_guard)
        self.topmost_switch.toggled.connect(self._on_topmost)
        self.engine.stateChanged.connect(self._on_engine)
        self.engine.finished.connect(self._on_finished)
        self.hotkeys.pressed.connect(self._on_hotkey)
        self.hotkeys.statusChanged.connect(lambda ok, text: self.hotkey_status.setText(text))
        self.hotkeys.direct_stop = self._direct_stop
        self.uia.stateChanged.connect(self._on_uia)

    def _refresh_points(self):
        for key in ("left", "right"):
            if key in self.chest_labels:
                self.chest_labels[key].setText("检测中…")

    # ------------------------------------------------------------------ picking
    def _pick(self, kind):
        if self.engine.running:
            QMessageBox.information(self, "请先停止", "自动运行中不能定位。")
            return
        titles = {
            "chest1": ("定位宝箱一", "把准星移到第一个宝箱按钮中心。"),
            "chest2": ("定位宝箱二", "把准星移到第二个宝箱按钮中心。"),
        }
        t, h = titles[kind]
        self._picked = kind
        self._overlay = PointPicker(t, h)
        self._overlay.picked.connect(self._picked_point)
        self._overlay.cancelled.connect(self._pick_cancelled)
        self.hide()
        self._overlay.show()
        self._overlay.raise_()
        self._overlay.activateWindow()

    def _pick_cancelled(self):
        self._log("已取消定位")
        self.show()
        self.raise_()
        self.activateWindow()

    def _picked_point(self, x, y):
        kind = self._picked
        if kind == "chest1":
            self.cfg.chest_point = [x, y]
        elif kind == "chest2":
            self.cfg.chest_point2 = [x, y]
        save_config(self.cfg)
        self._refresh_points()
        self._log(f"已定位：({x}, {y})")
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ hotkeys
    def _direct_stop(self, vk):
        # Called in the low-level keyboard hook thread.  Only touch thread-safe state.
        if vk == VK_F3 and self.engine.running:
            self.engine.stop()
        elif vk == VK_F8 and self.engine.running:
            self._toggle_stop_flag = True
            self.engine.stop()

    def _on_hotkey(self, vk):
        if vk == VK_F8:
            if self._toggle_stop_flag:
                self._toggle_stop_flag = False
                self._on_idle()
                self._log("停止：F8")
            elif self.engine.running:
                self.stop("F8")
            else:
                self.start()
        elif vk == VK_F3:
            if self.engine.running:
                self.stop("F3")
        elif vk == VK_F1:
            self._open_chest("left", manual=True)
        elif vk == VK_F2:
            self._open_chest("right", manual=True)
        elif vk == VK_F10:
            self.toggle_panel()
        elif vk == VK_F11:
            self.auto_chest.setChecked(not self.auto_chest.isChecked())
            self._log("自动开箱：" + ("开" if self.auto_chest.isChecked() else "关"))

    # ------------------------------------------------------------------ engine
    def start(self):
        if self.engine.running:
            return
        cfg = EngineConfig(
            vk=int(self.key_combo.currentData()),
            cps=float(self.cps_spin.value()), hold_ms=float(self.hold.value()),
            jitter=self.jitter.isChecked(),
        )
        self._save()
        self._session_base = int(self.stats.software_presses)
        self._last_count_say = time.time()
        self.session_value.setText("0")
        if self.cfg.desktop_only and self.isVisible():
            self.enter_desktop_mode()
        self.software_value.setText(f"{self.stats.software_presses:,}")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.state_pill.setText("运行中")
        self.state_pill.setProperty("tone", "live")
        self.state_pill.style().unpolish(self.state_pill)
        self.state_pill.style().polish(self.state_pill)
        self.pet_status.setText("连发中")
        self.pet_status.setProperty("tone", "live")
        self.pet_status.style().unpolish(self.pet_status)
        self.pet_status.style().polish(self.pet_status)
        self._say(
            f"{SIM_KEY_NAMES.get(cfg.vk, cfg.vk)} 已接管。目标 {cfg.cps:.0f} 次/秒，"
            f"本软件累计 {self.stats.software_presses:,} 次；本次重新从 0 开始。",
            "#7c5a45",
        )
        self._log(f"开始按键：{cfg.cps:.1f} 次/秒 · 模拟键 {SIM_KEY_NAMES.get(cfg.vk, cfg.vk)}")
        self.engine.start(cfg)

    def stop(self, reason="停止"):
        if self.engine.running:
            self.engine.stop()
            self.stop_btn.setEnabled(False)
            save_stats(self.stats)
            self._log(f"停止：{reason}")

    def _on_engine(self, st):
        self._last_state = st
        count = int(st.get("count", 0))
        self.session_value.setText(f"{count:,}")
        software_total = self._session_base + count
        self.stats.software_presses = max(self.stats.software_presses, software_total)
        self.software_value.setText(f"{self.stats.software_presses:,}")
        self.actual.setText(f"实际 {float(st.get('actual', 0)):.1f} 次/秒")
        if self._total is None:
            self.app_value.setText("—")
        now = time.time()
        if now - self._last_stats_save >= 2.0:
            self._last_stats_save = now
            save_stats(self.stats)
        self._update_clue_coins()
        if now - self._last_count_say >= max(15, int(self.cfg.dialogue_interval)) and count > 0:
            self._last_count_say = now
            self._say(f"{self._snapshot_text()}。{self._fun_line(count)}", "#7c5a45")
        self._check_achievements()
        if not st.get("running", True):
            self._on_idle()

    @staticmethod
    def _fun_line(n: int) -> str:
        if n < 500:
            return "手指还热着，继续。"
        if n < 2000:
            return "节奏稳住，宝箱在看着我们。"
        if n < 8000:
            return "这台键盘今天算是加班了。"
        return "别停，欧气正在路上。"

    def _snapshot_text(self) -> str:
        session = int(self._last_state.get("count", 0))
        current = self.app_value.text()
        current = "读取受限" if current in ("—", "") else f"{current} 次"
        return (
            f"本次 {session:,} 次 · 本软件累计 {self.stats.software_presses:,} 次 · "
            f"当前应用 {current}"
        )

    def _on_finished(self, reason):
        self._on_idle()
        save_stats(self.stats)
        self._log(f"已停止：{reason}")
        self._say(f"先歇一下。{self._snapshot_text()}。成绩已经存好。", "#7c5a45")

    def _on_idle(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.state_pill.setText("待命")
        self.state_pill.setProperty("tone", "cream")
        self.state_pill.style().unpolish(self.state_pill)
        self.state_pill.style().polish(self.state_pill)
        self.pet_status.setText("待命")
        self.pet_status.setProperty("tone", "cream")
        self.pet_status.style().unpolish(self.pet_status)
        self.pet_status.style().polish(self.pet_status)
        self.actual.setText("实际 0.0 次/秒")
        self._last_app_refresh = 0.0
        QTimer.singleShot(50, self._ensure_guard_visible)

    # ------------------------------------------------------------------ UIA
    def _on_uia(self, st: dict):
        if not st.get("ok"):
            self._uia_lost = True
            self._total = None
            self.app_value.setText("—")
            self.link_pill.setText("未连接 Wuwa Tappo")
            self.link_pill.setProperty("tone", "warn")
            self.link_pill.style().unpolish(self.link_pill)
            self.link_pill.style().polish(self.link_pill)
            self.app_hint.setText(st.get("reason", "无法读取"))
            return
        if self._uia_lost:
            self._uia_lost = False
            self._last_app_refresh = 0.0
        self.link_pill.setText("已连接 Wuwa Tappo")
        self.link_pill.setProperty("tone", "ok")
        total = st.get("total")
        if total is None:
            self._total = None
            self._uia_lost = True
            self.link_pill.setText("已连接 · 数据受限")
            self.link_pill.setProperty("tone", "warn")
            self.app_hint.setText("账号数据受限；可管理员运行或使用源码版")
            self.app_value.setText("—")
        self.link_pill.style().unpolish(self.link_pill)
        self.link_pill.style().polish(self.link_pill)
        if isinstance(total, int):
            self._total = total
            self._pending_app_total = total
            self.app_hint.setText("当前应用计数（每 %d 秒刷新）" % int(self.cfg.app_count_interval))
            if time.time() - self._last_app_refresh >= max(1, int(self.cfg.app_count_interval)):
                self._last_app_refresh = time.time()
                self.app_value.setText(f"{total:,}")
        elif self._pending_app_total is not None:
            if time.time() - self._last_app_refresh >= max(1, int(self.cfg.app_count_interval)):
                self._last_app_refresh = time.time()
                self.app_value.setText(f"{self._pending_app_total:,}")

        reward = st.get("reward")
        now = time.time()
        if reward:
            if reward != self._last_reward or now - self._last_reward_at > 2.5:
                self._last_reward = reward
                self._last_reward_at = now
                self._handle_reward(reward)
            else:
                self._last_reward_at = now
        elif now - self._last_reward_at > 2.5:
            self._last_reward = None

        for side in ("left", "right"):
            ready = bool(st.get(side, {}).get("ready"))
            if side in self.chest_labels:
                self.chest_labels[side].setText("就绪" if ready else "冷却中")
        if st.get("invoked"):
            self._log(f"已通过 UI Automation 内部开箱：{'宝箱一' if st['invoked'] == 'left' else '宝箱二'}")

        if not self.auto_chest.isChecked():
            return
        if self.cfg.disclaimer_version < 1 and "--screenshot" not in sys.argv:
            return
        now2 = time.time()
        if self._auto_busy:
            if reward:
                self._auto_busy_until = max(self._auto_busy_until, now2 + 2.0)
                return
            if now2 < self._auto_busy_until:
                return
            self._auto_busy = False
        for side in ("left", "right"):
            ready = bool(st.get(side, {}).get("ready"))
            if not ready:
                self._ready_latch[side] = False
                continue
            if self._ready_latch[side]:
                continue
            if isinstance(total, int) and total < CHEST_MIN_TOTAL_CLICKS:
                continue
            self._ready_latch[side] = True
            self._auto_busy = True
            self._auto_busy_until = now2 + 4.0
            QTimer.singleShot(300, lambda s=side: self._auto_open(s))
            # One chest at a time: opening both within a second can wedge
            # Wuwa Tappo's toast/input layer.
            break

    def _handle_reward(self, quality: str):
        self.stats.chests += 1
        if quality == "green":
            self.stats.green += 1
            self._say("本次宝箱产出了绿色品质。普通的收获，也是旅途的一部分。", "#5d8f55")
        elif quality == "blue":
            self.stats.blue += 1
            self._say("Wow！本次宝箱产出了蓝色品质！蓝光一闪，今天的运气正在慢慢升温。", "#3f7fc4", "font-weight:600;")
        elif quality == "purple":
            self.stats.purple += 1
            self._say("Wow！本次宝箱产出了紫色品质！紫色光晕铺开，连空气都认真了。这一箱，很值。", "#8a5bc4", "font-weight:700;")
        elif quality == "gold":
            self.stats.gold += 1
            self._say(
                "Wow！本次宝箱产出了金色品质！★ 金色光芒炸开，整个桌面都亮了——这就是传说中的欧气吗？ ★",
                "#c38b00",
                "font-size:13px;font-weight:800;",
            )
        save_stats(self.stats)
        self.open_label.setText(f"已开箱 {self.stats.chests}")
        self._check_achievements()

    def _check_achievements(self):
        changed = False
        for a in ACHIEVEMENTS:
            if a.key in self.stats.achievements:
                continue
            try:
                ok = bool(a.check(self.stats))
            except Exception:
                ok = False
            if ok:
                self.stats.achievements.append(a.key)
                changed = True
                self._say(
                    f"成就解锁 · {a.name} —— {a.desc}",
                    a.color,
                    "font-weight:800;",
                )
        if changed:
            self._achievements_dirty = True
            save_stats(self.stats)

    def _auto_open(self, side):
        if self.auto_chest.isChecked():
            self._open_chest(side, manual=False)

    def _open_chest(self, side, manual=False):
        name = "宝箱一" if side == "left" else "宝箱二"
        self._ready_latch[side] = True
        self.uia.request_invoke(side)
        self._log(f"请求内部打开{name}")
        QTimer.singleShot(7000, lambda s=side: self._unlatch(s))

    def _unlatch(self, side):
        # If the invoke failed and the ready button is still there, allow a retry.
        self._ready_latch[side] = False

    # ------------------------------------------------------------------ dialogs
    def _say(self, text: str, color: str = "#6c4b37", extra: str = ""):
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        html = (
            "<div class='bubble'><span class='name'>漂泊者</span><br>"
            f"<span style='color:{color};{extra}'>{safe}</span></div>"
        )
        self._dialogue_history.append(html)
        if len(self._dialogue_history) > 80:
            self._dialogue_history = self._dialogue_history[-80:]
        self.dialog_view.append(html)
        self.dialog_view.verticalScrollBar().setValue(
            self.dialog_view.verticalScrollBar().maximum()
        )
        if self.pet_window is not None:
            self.pet_window.append_dialogue(html)
        if self.simple_window is not None:
            self.simple_window.show_message(html, self.cfg.simple_bubble_seconds)

    def on_pet_click(self):
        now = time.time()
        self.stats.pet_clicks += 1
        self._pet_click_times = [t for t in self._pet_click_times if now - t <= 10.0]
        self._pet_click_times.append(now)
        if len(self._pet_click_times) >= 3:
            self._pet_click_times = []
            line = random.choice(ROVER_RAPID_LINES)
            self._say(line, "#b57a3e", "font-weight:700;")
            self._unlock_hidden("pet_three")
        else:
            line = random.choice([x for x in ROVER_CLICK_LINES if x != self._pet_reply_last] or ROVER_CLICK_LINES)
            self._pet_reply_last = line
            self._say(line, "#7c5a45")
            self._unlock_hidden("pet_once")
        save_stats(self.stats)

    def _unlock_hidden(self, key: str):
        if key in self.stats.achievements:
            return
        a = next((x for x in ACHIEVEMENTS if x.key == key), None)
        if not a:
            return
        self.stats.achievements.append(key)
        save_stats(self.stats)
        self._say(f"隐藏成就解锁 · {a.name} —— {a.desc}", a.color, "font-weight:800;")

    def unlock_hint(self, key: str) -> bool:
        if key in self.stats.hints:
            return True
        if self.stats.clue_coins <= 0:
            QMessageBox.information(self, "线索币不足", "每累计 10,000 次按键可获得 1 枚线索币。")
            return False
        self.stats.clue_coins -= 1
        self.stats.hints.append(key)
        save_stats(self.stats)
        self._say("线索已经展开。要不要再点一下漂泊者试试？", "#b57a3e")
        return True

    def _update_clue_coins(self):
        earned = int(self.stats.software_presses) // 10000
        if earned > int(self.stats.clue_awarded):
            diff = earned - int(self.stats.clue_awarded)
            self.stats.clue_coins += diff
            self.stats.clue_awarded = earned
            save_stats(self.stats)
            self._say(f"获得 {diff} 枚线索币。隐藏成就的提示可以解锁了。", "#b57a3e", "font-weight:700;")

    def _log(self, text):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")
        write_log(text)

    def _settings(self):
        d = SettingsDialog(self, self.cfg)
        if d.exec() == QDialog.Accepted:
            self.cfg.app_count_interval, self.cfg.dialogue_interval, self.cfg.simple_bubble_seconds = d.values()
            save_config(self.cfg)
            self.app_hint.setText("当前应用计数（每 %d 秒刷新）" % self.cfg.app_count_interval)
            self._log("设置已保存")

    def _guide(self):
        self.cfg.guide_seen = True
        save_config(self.cfg)
        InfoDialog(self, "使用前看我", GUIDE_HTML).exec()

    def _faq(self):
        InfoDialog(self, "常见问题", FAQ_HTML).exec()

    def _achievements(self):
        AchievementsDialog(self, self.stats).exec()

    def _disclaimer(self):
        d = DisclaimerDialog(self)
        if d.exec() == QDialog.Accepted:
            self.cfg.disclaimer_version = 1
            save_config(self.cfg)

    def _first_disclaimer(self):
        d = DisclaimerDialog(self)
        if d.exec() == QDialog.Accepted:
            self.cfg.disclaimer_version = 1
            save_config(self.cfg)
            if not self.cfg.guide_seen:
                QTimer.singleShot(250, self._first_onboarding)

    def _first_onboarding(self):
        if self.cfg.guide_seen:
            return
        box = QMessageBox(self)
        box.setWindowTitle("第一次使用提示")
        box.setTextFormat(Qt.RichText)
        box.setText(
            "<b>欢迎使用 WuwaTappo 小助手。</b><br><br>"
            "建议先看主界面右下角 <b>更多</b> 里的内容：<br>"
            "· <b>使用前看我</b>：解压、Windows 提示、快捷键和第一次操作<br>"
            "· <b>常见问题</b>：宝箱、计数、桌宠、设置等常见问题<br>"
            "· <b>设置</b>：当前应用刷新、点击台词、气泡时长<br>"
            "· <b>成就</b>：星级、线索币与隐藏成就"
        )
        open_btn = box.addButton("打开使用前看我", QMessageBox.AcceptRole)
        box.addButton("稍后自己看", QMessageBox.RejectRole)
        box.exec()
        self.cfg.guide_seen = True
        save_config(self.cfg)
        if box.clickedButton() is open_btn:
            self._guide()

    def _change_bg(self):
        exts = "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff);;所有文件 (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择新的底图", str(Path.home() / "Pictures"), exts)
        if not path:
            return
        if not self.shell.set_background(path):
            QMessageBox.warning(self, "无法加载", "无法识别该图片，或文件已损坏。")
            return
        self.cfg.bg_path = path
        save_config(self.cfg)
        self._log("底图已更换")

    def _restore_bg(self):
        self.shell.set_background(str(DEFAULT_BG))
        self.cfg.bg_path = ""
        save_config(self.cfg)
        self._log("已恢复默认底图")

    def _admin(self):
        if is_admin():
            QMessageBox.information(self, "管理员", "当前已经是管理员权限。")
            return
        try:
            if getattr(sys, "frozen", False):
                exe, params = sys.executable, ""
            else:
                exe, params = sys.executable, f'"{Path(__file__).resolve()}"'
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(BASE_DIR), 1)
            if ret > 32:
                self.close()
            else:
                self._log("管理员重启被取消")
        except Exception:
            self._log("管理员重启失败")

    # ------------------------------------------------------------------ window
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 48:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def closeEvent(self, e):
        if self.engine.running:
            self.engine.stop()
            if self.engine._thread:
                self.engine._thread.join(timeout=1.0)
        self.hotkeys.stop()
        self.uia.stop()
        self._save()
        save_stats(self.stats)
        if self.pet_window is not None:
            self.pet_window.hide()
            self.pet_window.deleteLater()
            self.pet_window = None
        if self.simple_window is not None:
            self.simple_window.hide()
            self.simple_window.deleteLater()
            self.simple_window = None
        e.accept()


class SimpleGuardWindow(QWidget):
    """Very small desktop guard: only Rover + a 5s message bubble."""

    def __init__(self, main: MainWindow):
        super().__init__(None)
        self.main = main
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if main.cfg.guard_topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(200, 240)
        self._drag = None
        self._pet_drag = None
        self._pet_moved = False
        self._gender = main.cfg.gender
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._hide_bubble)

        # Absolute positions: the bubble overlays the top area, so showing or
        # hiding it never moves the character or the hint.
        self.bubble = QLabel(self)
        self.bubble.setWordWrap(True)
        self.bubble.setGeometry(4, 4, 192, 52)
        self.bubble.setStyleSheet(
            "background:rgba(255,251,243,242);border:1px solid rgba(234,219,200,190);"
            "border-radius:10px;padding:5px 7px;color:#6c4b37;font-size:10px;"
        )
        self.bubble.hide()

        self.pet = ClickableLabel(self)
        self.pet.setGeometry(25, 56, 150, 150)
        self.pet.setAlignment(Qt.AlignCenter)
        self.pet.setCursor(Qt.PointingHandCursor)
        self.pet.clicked.connect(self.main.on_pet_click)
        self.pet.installEventFilter(self)
        self.hint = QLabel("F8 开始 · F3 停止\nF1/F2 开箱 · F10 面板 · F11 自动", self)
        self.hint.setGeometry(2, 204, 196, 32)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(
            "color:#6c4b37;font-size:9px;background:rgba(255,250,242,232);"
            "border:1px solid rgba(180,150,120,180);border-radius:6px;padding:2px;"
        )
        eff = QGraphicsOpacityEffect(self.hint)
        eff.setOpacity(0.82)
        self.hint.setGraphicsEffect(eff)
        self.set_character(self._gender)
        self._place()

    def _place(self):
        scr = QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - self.width() - 24, g.bottom() - self.height() - 22)

    def set_always_on_top(self, on: bool):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        if self.isVisible():
            self.show()

    def set_character(self, gender: str):
        self._gender = gender
        path = MASCOT_MALE if gender == "male" else MASCOT_FEMALE
        pm = QPixmap(str(path)) if path.exists() else QPixmap(str(MASCOT))
        if not pm.isNull():
            self.pet.setPixmap(pm.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def show_message(self, html: str, seconds: int):
        if not self.isVisible():
            return
        self.bubble.setText(html)
        self.bubble.show()
        self.bubble.raise_()
        self._timer.start(max(1, int(seconds)) * 1000)

    def _hide_bubble(self):
        self.bubble.hide()

    def eventFilter(self, obj, ev):
        if obj is self.pet:
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._pet_drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._pet_moved = False
                return True
            if ev.type() == QEvent.MouseMove and self._pet_drag is not None and ev.buttons() & Qt.LeftButton:
                self.move(ev.globalPosition().toPoint() - self._pet_drag)
                self._pet_moved = True
                return True
            if ev.type() == QEvent.MouseButtonRelease and self._pet_drag is not None:
                if not self._pet_moved:
                    self.main.on_pet_click()
                self._pet_drag = None
                return True
        return super().eventFilter(obj, ev)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 14, 14)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.main.leave_desktop_mode()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        a = menu.addAction("打开主面板")
        a.triggered.connect(self.main.leave_desktop_mode)
        b = menu.addAction("退出")
        b.triggered.connect(self.main.close)
        menu.exec(e.globalPos())


class DesktopPetWindow(QWidget):
    """Only the desktop mascot + scrollable dialogue, no control panel."""

    def __init__(self, main: MainWindow):
        super().__init__(None)
        self.main = main
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if main.cfg.guard_topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(300, 440)
        self._drag = None
        self._pet_drag = None
        self._pet_moved = False
        self._gender = main.cfg.gender

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.title = QLabel("桌面值守")
        self.title.setStyleSheet("font-size:12px;font-weight:700;color:#6a4a37;")
        top.addWidget(self.title)
        top.addStretch(1)
        hint = QLabel("F10 面板")
        hint.setStyleSheet("font-size:9px;color:#a58873;")
        top.addWidget(hint)
        lay.addLayout(top)

        self.dialog = QTextBrowser()
        self.dialog.setStyleSheet(
            "QTextBrowser{background:rgba(255,251,243,238);border:1px solid rgba(234,219,200,190);"
            "border-radius:10px;padding:4px;font-size:10px;color:#6c4b37;}"
        )
        self.dialog.document().setDefaultStyleSheet(
            ".bubble{background:#fff8ec;border-radius:8px;padding:4px 6px;margin:3px 0;}"
            ".name{color:#b07a5c;font-size:9px;font-weight:700;}"
        )
        lay.addWidget(self.dialog, 1)

        bottom = QHBoxLayout()
        self.name = QLabel("漂泊者·女")
        self.name.setStyleSheet("font-size:10px;color:#8e5e43;font-weight:700;")
        bottom.addWidget(self.name)
        bottom.addStretch(1)
        self.pet = ClickableLabel()
        self.pet.setFixedSize(150, 150)
        self.pet.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.pet.setCursor(Qt.PointingHandCursor)
        self.pet.clicked.connect(self.main.on_pet_click)
        self.pet.installEventFilter(self)
        bottom.addWidget(self.pet)
        lay.addLayout(bottom)

        low = QLabel("F8 开始 · F3 停止\nF1/F2 开箱 · F10 面板 · F11 自动")
        low.setAlignment(Qt.AlignCenter)
        low.setStyleSheet(
            "font-size:9px;color:#6c4b37;background:rgba(255,250,242,232);"
            "border:1px solid rgba(180,150,120,180);border-radius:6px;padding:2px;"
        )
        eff = QGraphicsOpacityEffect(low)
        eff.setOpacity(0.82)
        low.setGraphicsEffect(eff)
        lay.addWidget(low)

        self.set_character(self._gender)
        self._place()

    def _place(self):
        scr = QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - self.width() - 24, g.bottom() - self.height() - 22)

    def set_always_on_top(self, on: bool):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        if self.isVisible():
            self.show()

    def set_character(self, gender: str):
        self._gender = gender
        path = MASCOT_MALE if gender == "male" else MASCOT_FEMALE
        pm = QPixmap(str(path)) if path.exists() else QPixmap(str(MASCOT))
        if not pm.isNull():
            self.pet.setPixmap(pm.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.name.setText("漂泊者·男" if gender == "male" else "漂泊者·女")

    def set_history(self, history):
        self.dialog.setHtml("".join(history[-40:]))
        self.dialog.verticalScrollBar().setValue(self.dialog.verticalScrollBar().maximum())

    def append_dialogue(self, html: str):
        self.dialog.append(html)
        self.dialog.verticalScrollBar().setValue(self.dialog.verticalScrollBar().maximum())

    def eventFilter(self, obj, ev):
        if obj is self.pet:
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._pet_drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._pet_moved = False
                return True
            if ev.type() == QEvent.MouseMove and self._pet_drag is not None and ev.buttons() & Qt.LeftButton:
                self.move(ev.globalPosition().toPoint() - self._pet_drag)
                self._pet_moved = True
                return True
            if ev.type() == QEvent.MouseButtonRelease and self._pet_drag is not None:
                if not self._pet_moved:
                    self.main.on_pet_click()
                self._pet_drag = None
                return True
        return super().eventFilter(obj, ev)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 16, 16)
        p.fillPath(path, QColor(253, 248, 238, 235))
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.main.leave_desktop_mode()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        a = menu.addAction("打开主面板")
        a.triggered.connect(self.main.leave_desktop_mode)
        b = menu.addAction("退出")
        b.triggered.connect(self.main.close)
        menu.exec(e.globalPos())

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def set_app_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WuwaAutoChest.Local.1")
    except Exception:
        pass


def main():
    elevate_if_needed()
    enable_dpi()
    set_app_id()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    load_fonts()
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(APP_QSS)
    app.setApplicationName(APP_NAME)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    w = MainWindow()
    w.show()
    if "--uia-test" in sys.argv:
        import json as _json
        results = []
        w.uia.stateChanged.connect(lambda st: results.append(st))
        out = Path(os.environ.get("WUWA_UIA_TEST_OUT", str(BASE_DIR / "uia_test.json")))
        def finish():
            try:
                out.write_text(_json.dumps(results[-1] if results else {"ok": False}, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            app.quit()
        QTimer.singleShot(3500, finish)
    if "--screenshot" in sys.argv:
        out = Path(os.environ.get("WUWA_SCREENSHOT_PATH", str(BASE_DIR / "ui_preview.png")))
        def grab():
            w.grab().save(str(out), "PNG")
            QTimer.singleShot(100, app.quit)
        QTimer.singleShot(1200, grab)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

