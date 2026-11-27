import logging
from PySide6.QtCore import QObject, Signal
from pynput import keyboard

logger = logging.getLogger("HotkeyManager")


class GlobalHotkeyManager(QObject):
    start_scan_triggered = Signal()
    stop_scan_triggered = Signal()

    def __init__(self):
        super().__init__()
        self.listener = keyboard.GlobalHotKeys({
            "<ctrl>+1": self._on_start,
            "<ctrl>+2": self._on_stop,
        })

    def start(self):
        self.listener.start()
        logger.info("Đã đăng ký Global Hotkey: Ctrl+1 (Bắt đầu quét), Ctrl+2 (Dừng quét).")

    def stop(self):
        self.listener.stop()

    def _on_start(self):
        self.start_scan_triggered.emit()

    def _on_stop(self):
        self.stop_scan_triggered.emit()
