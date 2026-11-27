import sys
import ctypes
from PySide6.QtWidgets import QApplication, QMessageBox
from src.config_manager import ConfigManager
from src.ui_main import MainWindow
from src.hotkey_manager import GlobalHotkeyManager


def ensure_single_instance() -> bool:
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\QuizHelperAI_SingleInstance_Mutex")
    last_error = ctypes.windll.kernel32.GetLastError()
    return last_error != 183


def main():
    if not ensure_single_instance():
        app = QApplication(sys.argv)
        QMessageBox.warning(None, "QuizHelper AI", "Ứng dụng QuizHelper AI đang chạy (kiểm tra khay hệ thống).")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cfg_mgr = ConfigManager()
    window = MainWindow(cfg_mgr)

    hotkeys = GlobalHotkeyManager()
    hotkeys.start_scan_triggered.connect(window.start_scanning)
    hotkeys.stop_scan_triggered.connect(window.stop_scanning)
    hotkeys.start()

    window.show()
    exit_code = app.exec()
    hotkeys.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
