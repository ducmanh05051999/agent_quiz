import os
import shutil
import logging
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QIcon, QAction, QPixmap, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QLineEdit, QListWidget, QFileDialog,
    QMessageBox, QSpinBox, QCheckBox, QTextEdit, QGroupBox,
    QFormLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QSystemTrayIcon, QMenu, QApplication
)

from src.config_manager import ConfigManager
from src.document_processor import SUPPORTED_EXTENSIONS
from src.rag_engine import RAGEngine
from src.ocr_scanner import OCRScanner
from src.openai_client import QuizOpenAIClient
from src.auto_clicker import AutoClicker

logger = logging.getLogger("MainUI")


class IndexWorker(QThread):
    progress = Signal(str)
    finished_sync = Signal(bool, str)

    def __init__(self, rag: RAGEngine, api_key: str):
        super().__init__()
        self.rag = rag
        self.api_key = api_key

    def run(self):
        try:
            msg = self.rag.sync_documents(self.api_key, progress_callback=self.progress.emit)
            self.finished_sync.emit(True, msg)
        except Exception as e:
            self.finished_sync.emit(False, str(e))


class ScanWorker(QThread):
    status_changed = Signal(str)
    result_ready = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, cfg_mgr: ConfigManager, rag: RAGEngine, scanner: OCRScanner):
        super().__init__()
        self.cfg_mgr = cfg_mgr
        self.rag = rag
        self.scanner = scanner
        self._running = True
        self.last_hash = ""

    def stop(self):
        self._running = False

    def run(self):
        self.status_changed.emit("Đang quét màn hình trình duyệt...")
        while self._running:
            try:
                api_key = self.cfg_mgr.get_api_key()
                if not api_key:
                    self.error_occurred.emit("Chưa cấu hình OpenAI API Key. Đã dừng quét.")
                    break

                quiz = self.scanner.scan_screen()
                if quiz and quiz.content_hash != self.last_hash:
                    self.last_hash = quiz.content_hash
                    self.status_changed.emit("Phát hiện câu hỏi mới! Đang tra cứu tài liệu & phân tích AI...")

                    chunks = self.rag.search(
                        quiz.question, api_key=api_key, top_k=self.cfg_mgr.config.top_k_chunks
                    )
                    client = QuizOpenAIClient(
                        api_key=api_key,
                        model=self.cfg_mgr.config.model,
                        max_tokens=self.cfg_mgr.config.max_tokens_per_request,
                    )
                    opts_payload = [{"label": o.label, "text": o.text} for o in quiz.options]
                    ai_res = client.analyze_quiz(
                        question=quiz.question,
                        options=opts_payload,
                        question_type=quiz.question_type,
                        retrieved_chunks=chunks,
                    )

                    clicked_labels = []
                    if (
                        self.cfg_mgr.config.auto_select
                        and ai_res.evidence_status == "SUFFICIENT_EVIDENCE"
                        and ai_res.confidence >= self.cfg_mgr.config.confidence_threshold
                        and ai_res.selected_options
                    ):
                        clicked_labels = AutoClicker.select_options(ai_res.selected_options, quiz.options)

                    self.result_ready.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "question": quiz.question,
                        "options": opts_payload,
                        "selected": ai_res.selected_options,
                        "clicked": clicked_labels,
                        "confidence": ai_res.confidence,
                        "status": ai_res.evidence_status,
                        "explanation": ai_res.explanation,
                        "source": f"{ai_res.source_document} ({ai_res.source_location})",
                    })
                    self.status_changed.emit("Đang theo dõi màn hình (Chờ câu hỏi tiếp theo)...")

            except Exception as e:
                logger.error(f"Lỗi trong vòng lặp quét: {e}")
                self.error_occurred.emit(str(e))

            steps = int(self.cfg_mgr.config.scan_interval_sec * 10)
            for _ in range(max(1, steps)):
                if not self._running:
                    break
                self.msleep(100)

        self.status_changed.emit("Đã dừng quét.")


class MainWindow(QMainWindow):
    def __init__(self, cfg_mgr: ConfigManager):
        super().__init__()
        self.cfg_mgr = cfg_mgr
        self.rag = RAGEngine(
            docs_dir=cfg_mgr.docs_dir,
            data_dir=cfg_mgr.data_dir,
            embedding_model=cfg_mgr.config.embedding_model,
        )
        self.scanner = OCRScanner(
            tesseract_dir=cfg_mgr.tesseract_dir,
            lang=cfg_mgr.config.language_ocr,
        )
        self.scan_worker = None
        self.index_worker = None

        self.setWindowTitle("QuizHelper AI - Trợ lý Trắc nghiệm Portable")
        self.resize(860, 640)
        self._setup_icon()
        self._setup_ui()
        self._setup_tray()
        self.refresh_document_list()

    def _setup_icon(self):
        pix = QPixmap(32, 32)
        pix.fill(QColor("#2563EB"))
        self.app_icon = QIcon(pix)
        self.setWindowIcon(self.app_icon)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        self.btn_start = QPushButton("▶ Bắt đầu quét (Ctrl+1)")
        self.btn_start.setStyleSheet("background-color: #16A34A; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_start.clicked.connect(self.start_scanning)

        self.btn_stop = QPushButton("⏹ Dừng quét (Ctrl+2)")
        self.btn_stop.setStyleSheet("background-color: #DC2626; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scanning)

        self.chk_auto_select = QCheckBox("Tự động chọn đáp án (Auto Select)")
        self.chk_auto_select.setChecked(self.cfg_mgr.config.auto_select)
        self.chk_auto_select.stateChanged.connect(self._on_auto_select_toggled)

        top_bar.addWidget(self.btn_start)
        top_bar.addWidget(self.btn_stop)
        top_bar.addStretch()
        top_bar.addWidget(self.chk_auto_select)
        main_layout.addLayout(top_bar)

        self.lbl_status = QLabel("Trạng thái: Sẵn sàng")
        self.lbl_status.setStyleSheet("color: #1D4ED8; font-weight: bold; padding: 4px;")
        main_layout.addWidget(self.lbl_status)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._build_live_tab()
        self._build_docs_tab()
        self._build_history_tab()
        self._build_settings_tab()

    def _build_live_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        grp_q = QGroupBox("Câu hỏi nhận diện từ màn hình")
        lq = QVBoxLayout(grp_q)
        self.txt_question = QTextEdit()
        self.txt_question.setReadOnly(True)
        self.txt_question.setMaximumHeight(110)
        lq.addWidget(self.txt_question)
        layout.addWidget(grp_q)

        grp_ans = QGroupBox("Phân tích & Đáp án AI đề xuất")
        la = QVBoxLayout(grp_ans)
        self.lbl_answer = QLabel("Đáp án đề xuất: Chưa có")
        self.lbl_answer.setStyleSheet("font-size: 15px; font-weight: bold; color: #059669;")
        self.lbl_confidence = QLabel("Độ tin cậy: -- | Trạng thái: --")
        self.lbl_source = QLabel("Nguồn tài liệu: --")
        self.txt_explanation = QTextEdit()
        self.txt_explanation.setReadOnly(True)

        la.addWidget(self.lbl_answer)
        la.addWidget(self.lbl_confidence)
        la.addWidget(self.lbl_source)
        la.addWidget(QLabel("Giải thích chi tiết:"))
        la.addWidget(self.txt_explanation)
        layout.addWidget(grp_ans)

        self.tabs.addTab(tab, "Quét & Kết quả")

    def _build_docs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        btn_row = QHBoxLayout()
        btn_import = QPushButton("➕ Import tài liệu")
        btn_import.clicked.connect(self.import_documents)

        btn_delete = QPushButton("🗑 Xóa tài liệu đã chọn")
        btn_delete.clicked.connect(self.delete_selected_document)

        btn_open_dir = QPushButton("📂 Mở thư mục tài liệu")
        btn_open_dir.clicked.connect(lambda: os.startfile(str(self.cfg_mgr.docs_dir)))

        btn_reindex = QPushButton("🔄 Cập nhật chỉ mục RAG")
        btn_reindex.clicked.connect(self.trigger_reindex)

        btn_row.addWidget(btn_import)
        btn_row.addWidget(btn_delete)
        btn_row.addWidget(btn_open_dir)
        btn_row.addWidget(btn_reindex)
        layout.addLayout(btn_row)

        self.list_docs = QListWidget()
        layout.addWidget(self.list_docs)

        self.lbl_index_info = QLabel(f"Số đoạn văn bản đã lập chỉ mục: {len(self.rag.chunks_meta)}")
        layout.addWidget(self.lbl_index_info)

        self.tabs.addTab(tab, "Quản lý Tài liệu")

    def _build_history_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(["Thời gian", "Câu hỏi", "Đáp án", "Tin cậy", "Nguồn"])
        self.tbl_history.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tbl_history)

        self.tabs.addTab(tab, "Lịch sử xử lý")

    def _build_settings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()

        self.inp_api_key = QLineEdit()
        self.inp_api_key.setEchoMode(QLineEdit.Password)
        self.inp_api_key.setPlaceholderText("sk-...")
        self.inp_api_key.setText(self.cfg_mgr.get_api_key())

        btn_test_api = QPushButton("Kiểm tra kết nối API")
        btn_test_api.clicked.connect(self.test_openai_connection)

        key_row = QHBoxLayout()
        key_row.addWidget(self.inp_api_key)
        key_row.addWidget(btn_test_api)

        self.inp_model = QLineEdit(self.cfg_mgr.config.model)
        self.spin_confidence = QSpinBox()
        self.spin_confidence.setRange(50, 100)
        self.spin_confidence.setSuffix(" %")
        self.spin_confidence.setValue(self.cfg_mgr.config.confidence_threshold)

        self.spin_max_tokens = QSpinBox()
        self.spin_max_tokens.setRange(300, 4000)
        self.spin_max_tokens.setValue(self.cfg_mgr.config.max_tokens_per_request)

        form.addRow("OpenAI API Key:", key_row)
        form.addRow("Mô hình AI (Model):", self.inp_model)
        form.addRow("Ngưỡng tin cậy Auto Select:", self.spin_confidence)
        form.addRow("Giới hạn Max Tokens / câu:", self.spin_max_tokens)

        layout.addLayout(form)

        btn_save_cfg = QPushButton("💾 Lưu cài đặt")
        btn_save_cfg.clicked.connect(self.save_settings)
        layout.addWidget(btn_save_cfg)
        layout.addStretch()

        self.tabs.addTab(tab, "Cài đặt")

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        tray_menu = QMenu()

        act_show = QAction("Mở QuizHelper AI", self)
        act_show.triggered.connect(self.showNormal)

        act_start = QAction("Bắt đầu quét (Ctrl+1)", self)
        act_start.triggered.connect(self.start_scanning)

        act_stop = QAction("Dừng quét (Ctrl+2)", self)
        act_stop.triggered.connect(self.stop_scanning)

        act_quit = QAction("Thoát hoàn toàn", self)
        act_quit.triggered.connect(self.quit_application)

        tray_menu.addAction(act_show)
        tray_menu.addSeparator()
        tray_menu.addAction(act_start)
        tray_menu.addAction(act_stop)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(
            lambda reason: self.showNormal() if reason == QSystemTrayIcon.DoubleClick else None
        )
        self.tray_icon.show()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "QuizHelper AI",
            "Ứng dụng đang chạy nền. Nhấn Ctrl+1 để quét hoặc double-click biểu tượng khay hệ thống để mở.",
            QSystemTrayIcon.Information,
            2500,
        )

    def quit_application(self):
        self.stop_scanning()
        self.tray_icon.hide()
        QApplication.quit()

    def _on_auto_select_toggled(self, state):
        self.cfg_mgr.config.auto_select = bool(state)
        self.cfg_mgr.save_config()

    def save_settings(self):
        self.cfg_mgr.set_api_key(self.inp_api_key.text())
        self.cfg_mgr.config.model = self.inp_model.text().strip() or "gpt-4o-mini"
        self.cfg_mgr.config.confidence_threshold = self.spin_confidence.value()
        self.cfg_mgr.config.max_tokens_per_request = self.spin_max_tokens.value()
        self.cfg_mgr.save_config()
        QMessageBox.information(self, "Thành công", "Đã lưu cấu hình an toàn.")

    def test_openai_connection(self):
        client = QuizOpenAIClient(
            api_key=self.inp_api_key.text().strip(),
            model=self.inp_model.text().strip() or "gpt-4o-mini",
        )
        ok, msg = client.test_connection()
        if ok:
            QMessageBox.information(self, "Kiểm tra API", msg)
        else:
            QMessageBox.warning(self, "Lỗi API", msg)

    def refresh_document_list(self):
        self.list_docs.clear()
        for p in sorted(self.cfg_mgr.docs_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                size_kb = p.stat().st_size / 1024
                self.list_docs.addItem(f"{p.name} ({size_kb:.1f} KB)")
        self.lbl_index_info.setText(f"Số đoạn văn bản đã lập chỉ mục: {len(self.rag.chunks_meta)}")

    def import_documents(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn tài liệu học tập / tra cứu",
            "",
            "Tài liệu hỗ trợ (*.pdf *.docx *.doc *.xlsx *.xls *.txt *.md)",
        )
        if not files:
            return
        for f in files:
            src = Path(f)
            dst = self.cfg_mgr.docs_dir / src.name
            shutil.copy2(src, dst)
        self.refresh_document_list()
        self.trigger_reindex()

    def delete_selected_document(self):
        item = self.list_docs.currentItem()
        if not item:
            return
        fname = item.text().split(" (")[0]
        target = self.cfg_mgr.docs_dir / fname
        if target.exists():
            target.unlink()
        self.refresh_document_list()
        self.trigger_reindex()

    def trigger_reindex(self):
        api_key = self.cfg_mgr.get_api_key()
        if not api_key:
            QMessageBox.warning(self, "Thiếu API Key", "Vui lòng nhập và lưu OpenAI API Key trước khi lập chỉ mục.")
            return
        if self.index_worker and self.index_worker.isRunning():
            return
        self.lbl_status.setText("Đang lập chỉ mục tài liệu...")
        self.index_worker = IndexWorker(self.rag, api_key)
        self.index_worker.progress.connect(self.lbl_status.setText)
        self.index_worker.finished_sync.connect(self._on_reindex_done)
        self.index_worker.start()

    def _on_reindex_done(self, ok: bool, msg: str):
        self.lbl_status.setText(f"Trạng thái: {msg}")
        self.refresh_document_list()
        if not ok:
            QMessageBox.warning(self, "Lỗi lập chỉ mục", msg)

    def start_scanning(self):
        if self.scan_worker and self.scan_worker.isRunning():
            return
        if len(self.rag.chunks_meta) == 0:
            QMessageBox.warning(self, "Chưa có dữ liệu", "Vui lòng import tài liệu và lập chỉ mục trước khi quét.")
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.scan_worker = ScanWorker(self.cfg_mgr, self.rag, self.scanner)
        self.scan_worker.status_changed.connect(lambda s: self.lbl_status.setText(f"Trạng thái: {s}"))
        self.scan_worker.result_ready.connect(self._on_scan_result)
        self.scan_worker.error_occurred.connect(self._on_scan_error)
        self.scan_worker.start()

    def stop_scanning(self):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.stop()
            self.scan_worker.wait(1500)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("Trạng thái: Đã dừng quét.")

    def _on_scan_error(self, err: str):
        self.lbl_status.setText(f"Lỗi: {err}")
        self.tray_icon.showMessage("QuizHelper AI - Cảnh báo", err, QSystemTrayIcon.Warning, 3000)

    def _on_scan_result(self, data: dict):
        opts_fmt = "\n".join(f"  {o['label']}. {o['text']}" for o in data["options"])
        self.txt_question.setText(f"{data['question']}\n\n{opts_fmt}")

        sel_str = ", ".join(data["selected"]) if data["selected"] else "Không chọn (Thiếu căn cứ)"
        auto_note = f" [Đã tự động click: {', '.join(data['clicked'])}]" if data["clicked"] else ""
        self.lbl_answer.setText(f"Đáp án đề xuất: {sel_str}{auto_note}")
        self.lbl_confidence.setText(f"Độ tin cậy: {data['confidence']}% | Trạng thái: {data['status']}")
        self.lbl_source.setText(f"Nguồn tài liệu: {data['source']}")
        self.txt_explanation.setText(data["explanation"])

        row = self.tbl_history.rowCount()
        self.tbl_history.insertRow(row)
        self.tbl_history.setItem(row, 0, QTableWidgetItem(data["time"]))
        self.tbl_history.setItem(row, 1, QTableWidgetItem(data["question"][:80] + "..."))
        self.tbl_history.setItem(row, 2, QTableWidgetItem(sel_str))
        self.tbl_history.setItem(row, 3, QTableWidgetItem(f"{data['confidence']}%"))
        self.tbl_history.setItem(row, 4, QTableWidgetItem(data["source"]))
