from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class AnswerOverlayHUD(QWidget):
    """Hiển thị đáp án mờ nhẹ ở góc màn hình, xuyên chuột và không chiếm focus cửa sổ thi."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel("")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(QFont("Segoe UI", 13, QFont.Bold))
        # Nền trong suốt rất mờ, chữ xám nhạt hòa vào màn hình
        self.label.setStyleSheet("""
            QLabel {
                background-color: rgba(25, 28, 34, 65);
                color: rgba(235, 240, 245, 115);
                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 8px;
                padding: 6px 14px;
            }
        """)
        layout.addWidget(self.label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_answer(self, labels: list[str], auto_hide_sec: int = 25):
        if not labels:
            return
        ans_text = ", ".join(sorted(set(lbl.upper() for lbl in labels)))
        self.label.setText(f"Đáp án: {ans_text}")
        self.adjustSize()

        # Đặt ở góc dưới bên phải màn hình chính (ngay phía trên thanh Taskbar)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 24
        y = screen.bottom() - self.height() - 20
        self.move(x, y)

        self.show()
        if auto_hide_sec > 0:
            self._hide_timer.start(auto_hide_sec * 1000)
