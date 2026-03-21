import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv, set_key
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
import shioaji as sj

from core import fetch_all_accounts
from db import list_account_ids, load_snapshots, save_snapshot

ENV_PATH = Path(__file__).parent / ".env"

load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _bordered_dialog(dlg: "QDialog") -> QFrame:
    """在 dialog 內建立帶黑框的容器 QFrame，回傳供填入內容用。"""
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.Box)
    frame.setLineWidth(2)
    frame.setStyleSheet("QFrame { border: 2px solid #2c2c2c; border-radius: 4px; }")
    outer.addWidget(frame)
    return frame


class _ApiTestWorker(QThread):
    """背景執行 Shioaji 登入（或登入＋啟用憑證）測試。"""
    result = pyqtSignal(bool, str)  # success, message

    def __init__(self, api_key: str, secret_key: str,
                 ca_path: str = "", ca_passwd: str = "") -> None:
        super().__init__()
        self._api_key    = api_key
        self._secret_key = secret_key
        self._ca_path    = ca_path
        self._ca_passwd  = ca_passwd

    def run(self) -> None:
        api = sj.Shioaji()
        try:
            api.login(api_key=self._api_key, secret_key=self._secret_key)
            if self._ca_path:
                api.activate_ca(ca_path=self._ca_path, ca_passwd=self._ca_passwd)
            self.result.emit(True, "成功")
        except Exception as e:
            self.result.emit(False, str(e))
        finally:
            try:
                api.logout()
            except Exception:
                pass


class AccountSettingsDialog(QDialog):
    """讓使用者編輯 .env 中的帳戶設定並儲存。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("帳戶設定")
        self.setMinimumWidth(460)
        self._worker: _ApiTestWorker | None = None

        frame = _bordered_dialog(self)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(12, 12, 12, 12)
        inner.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self._inputs: dict[str, QLineEdit] = {}

        # API Key（明文顯示）
        self._inputs["API_KEY"] = QLineEdit(os.getenv("API_KEY", ""))
        form.addRow("API Key：", self._inputs["API_KEY"])

        # Secret Key（明文顯示）
        self._inputs["SECRET_KEY"] = QLineEdit(os.getenv("SECRET_KEY", ""))
        form.addRow("Secret Key：", self._inputs["SECRET_KEY"])

        # 憑證路徑 + 瀏覽按鈕
        ca_path_row = QHBoxLayout()
        self._inputs["YOUR_CA_PATH"] = QLineEdit(os.getenv("YOUR_CA_PATH", ""))
        browse_btn = QPushButton("瀏覽...")
        browse_btn.setFixedWidth(64)
        browse_btn.clicked.connect(self._browse_ca)
        ca_path_row.addWidget(self._inputs["YOUR_CA_PATH"])
        ca_path_row.addWidget(browse_btn)
        form.addRow("憑證路徑：", ca_path_row)

        # 憑證密碼 + 顯示/隱藏切換按鈕
        ca_pass_row = QHBoxLayout()
        ca_pass_edit = QLineEdit(os.getenv("YOUR_CA_PASS", ""))
        ca_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._inputs["YOUR_CA_PASS"] = ca_pass_edit
        toggle_btn = QPushButton("顯示")
        toggle_btn.setFixedWidth(64)
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(self._toggle_ca_pass)
        ca_pass_row.addWidget(ca_pass_edit)
        ca_pass_row.addWidget(toggle_btn)
        form.addRow("憑證密碼：", ca_pass_row)

        inner.addLayout(form)

        # 測試按鈕列
        test_row = QHBoxLayout()
        self._test_key_btn = QPushButton("測試登入 Key")
        self._test_ca_btn  = QPushButton("測試憑證")
        self._test_key_btn.clicked.connect(self._test_login_key)
        self._test_ca_btn.clicked.connect(self._test_ca)
        test_row.addWidget(self._test_key_btn)
        test_row.addWidget(self._test_ca_btn)
        test_row.addStretch()
        inner.addLayout(test_row)

        # 測試結果標籤
        self._test_status = QLabel("")
        self._test_status.setWordWrap(True)
        inner.addWidget(self._test_status)

        # 儲存 / 取消
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        inner.addWidget(buttons)

    # ------------------------------------------------------------------

    def _browse_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇憑證檔案", "", "憑證檔案 (*.pfx *.p12 *.pem *.cer);;所有檔案 (*)"
        )
        if path:
            self._inputs["YOUR_CA_PATH"].setText(path)

    def _toggle_ca_pass(self, checked: bool) -> None:
        edit = self._inputs["YOUR_CA_PASS"]
        btn = self.sender()
        if checked:
            edit.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("隱藏")
        else:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("顯示")

    def _set_testing(self, testing: bool) -> None:
        self._test_key_btn.setEnabled(not testing)
        self._test_ca_btn.setEnabled(not testing)
        if testing:
            self._test_status.setStyleSheet("")
            self._test_status.setText("測試中，請稍候...")

    def _test_login_key(self) -> None:
        api_key    = self._inputs["API_KEY"].text().strip()
        secret_key = self._inputs["SECRET_KEY"].text().strip()
        if not api_key or not secret_key:
            self._show_status(False, "請先填寫 API Key 及 Secret Key")
            return
        self._set_testing(True)
        self._worker = _ApiTestWorker(api_key, secret_key)
        self._worker.result.connect(lambda ok, msg: self._show_status(ok, f"登入測試：{msg}"))
        self._worker.start()

    def _test_ca(self) -> None:
        api_key    = self._inputs["API_KEY"].text().strip()
        secret_key = self._inputs["SECRET_KEY"].text().strip()
        ca_path    = self._inputs["YOUR_CA_PATH"].text().strip()
        ca_passwd  = self._inputs["YOUR_CA_PASS"].text().strip()
        if not api_key or not secret_key:
            self._show_status(False, "請先填寫 API Key 及 Secret Key")
            return
        if not ca_path or not ca_passwd:
            self._show_status(False, "請先填寫憑證路徑及憑證密碼")
            return
        self._set_testing(True)
        self._worker = _ApiTestWorker(api_key, secret_key, ca_path, ca_passwd)
        self._worker.result.connect(lambda ok, msg: self._show_status(ok, f"憑證測試：{msg}"))
        self._worker.start()

    def _show_status(self, success: bool, msg: str) -> None:
        color = "#1a7f37" if success else "#c0392b"
        self._test_status.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._test_status.setText(msg)
        self._set_testing(False)

    def _save(self) -> None:
        for key, edit in self._inputs.items():
            set_key(str(ENV_PATH), key, edit.text())
        load_dotenv(ENV_PATH, override=True)
        self.accept()


class AboutDialog(QDialog):
    """顯示程式版本與 Shioaji 版本。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("關於")
        self.setFixedSize(300, 160)

        frame = _bordered_dialog(self)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(20, 20, 20, 20)
        inner.setSpacing(8)

        title = QLabel("<b>永豐金帳戶管理員</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(title)

        app_version = "0.1.0"
        sj_version  = getattr(sj, "__version__", "未知")
        info = QLabel(f"版本：{app_version}\nShioaji 版本：{sj_version}")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(info)

        inner.addStretch()

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn.accepted.connect(self.accept)
        inner.addWidget(btn)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _save_all_snapshots(data: dict) -> None:
    today = str(date.today())
    for sa in data["stock_accounts"]:
        save_snapshot({
            "date": today,
            "account_id": sa["account_id"],
            "cash_balance": sa["cash_balance"],
            "total_settlement": sa["total_settlement"],
            "cash_level": sa["cash_level"],
            "stock_market_value": sa["stock_market_value"],
            "total_assets": sa["total_assets"],
            "cash_ratio": sa["cash_ratio"],
            "futopt_equity": None,
        })
    for fa in data["futopt_accounts"]:
        save_snapshot({
            "date": today,
            "account_id": fa["account_id"],
            "cash_balance": None,
            "total_settlement": None,
            "cash_level": None,
            "stock_market_value": None,
            "total_assets": None,
            "cash_ratio": None,
            "futopt_equity": fa["futopt_equity"],
        })


class FetchWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, api_key: str, secret_key: str,
                 ca_path: str | None, ca_passwd: str | None):
        super().__init__()
        self._api_key   = api_key
        self._secret_key = secret_key
        self._ca_path   = ca_path
        self._ca_passwd = ca_passwd

    def run(self) -> None:
        api = sj.Shioaji()
        try:
            self.progress.emit("登入中...")
            api.login(api_key=self._api_key, secret_key=self._secret_key)
            if self._ca_path and self._ca_passwd:
                self.progress.emit("啟用憑證...")
                api.activate_ca(ca_path=self._ca_path, ca_passwd=self._ca_passwd)
            self.progress.emit("抓取帳戶資料...")
            data = fetch_all_accounts(api)
            self.progress.emit("儲存快照...")
            _save_all_snapshots(data)
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            try:
                api.logout()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

def _check_env() -> list[str]:
    """回傳尚未設定的必要欄位名稱清單。"""
    required = {"API_KEY": "API Key", "SECRET_KEY": "Secret Key"}
    return [label for key, label in required.items() if not os.getenv(key, "").strip()]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("帳戶管理員")
        self.resize(960, 720)
        self._worker: FetchWorker | None = None
        self._setup_ui()
        self._refresh_account_dropdown()
        self._load_history()
        QTimer.singleShot(0, self._check_settings_on_start)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(6)

        # toolbar row
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("更新資料")
        self._refresh_btn.setFixedWidth(100)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._last_update_lbl = QLabel("尚未更新")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._last_update_lbl)
        toolbar.addStretch()
        root.addLayout(toolbar)

        # tabs
        tabs = QTabWidget()
        tabs.addTab(self._build_realtime_tab(), "即時狀態")
        tabs.addTab(self._build_history_tab(),  "歷史資料")
        root.addWidget(tabs)

        self._setup_menu()
        self.statusBar().showMessage("就緒")

    def _check_settings_on_start(self) -> None:
        missing = _check_env()
        if not ENV_PATH.exists():
            msg = "找不到 .env 設定檔，請先完成帳戶設定。"
        elif missing:
            msg = f"以下必要欄位尚未設定：{', '.join(missing)}\n請先完成帳戶設定。"
        else:
            return
        ret = QMessageBox.warning(
            self, "帳戶設定不完整", msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if ret == QMessageBox.StandardButton.Ok:
            self._show_account_settings()

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()

        settings_action = menu_bar.addAction("帳戶設定")
        settings_action.triggered.connect(self._show_account_settings)

        about_action = menu_bar.addAction("關於")
        about_action.triggered.connect(self._show_about)

    def _show_account_settings(self) -> None:
        dlg = AccountSettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("帳戶設定已儲存")

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _build_realtime_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._realtime_layout = QVBoxLayout(container)
        self._realtime_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._realtime_layout.setSpacing(10)
        placeholder = QLabel("請點擊「更新資料」以載入帳戶狀態")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._realtime_layout.addWidget(placeholder)
        scroll.setWidget(container)
        return scroll

    def _build_history_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("帳戶："))
        self._account_filter = QComboBox()
        self._account_filter.addItem("全部", None)
        self._account_filter.currentIndexChanged.connect(self._load_history)
        filter_row.addWidget(self._account_filter)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        cols = ["日期", "帳戶", "可用餘額", "待交割款", "現金水位",
                "股票市值", "總資產", "現金佔比%", "期貨權益"]
        self._history_table = QTableWidget(0, len(cols))
        self._history_table.setHorizontalHeaderLabels(cols)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._history_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._history_table)
        return widget

    # ------------------------------------------------------------------
    # Realtime tab
    # ------------------------------------------------------------------

    def _on_refresh_clicked(self) -> None:
        api_key    = os.getenv("API_KEY")
        secret_key = os.getenv("SECRET_KEY")
        if not api_key or not secret_key:
            QMessageBox.critical(self, "設定錯誤",
                                 "請在 .env 檔案中設定 API_KEY 和 SECRET_KEY")
            return
        ca_path   = os.getenv("YOUR_CA_PATH")
        ca_passwd = os.getenv("YOUR_CA_PASS")

        self._refresh_btn.setEnabled(False)
        self._worker = FetchWorker(api_key, secret_key, ca_path, ca_passwd)
        self._worker.progress.connect(self.statusBar().showMessage)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_finished(self, data: dict) -> None:
        self._populate_realtime(data)
        self._last_update_lbl.setText(
            f"最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._refresh_btn.setEnabled(True)
        self.statusBar().showMessage("更新完成")
        self._refresh_account_dropdown()
        self._load_history()

    def _on_worker_error(self, msg: str) -> None:
        self._refresh_btn.setEnabled(True)
        self.statusBar().showMessage("更新失敗")
        QMessageBox.critical(self, "連線錯誤", msg)

    def _populate_realtime(self, data: dict) -> None:
        # clear
        while self._realtime_layout.count():
            item = self._realtime_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # stock accounts
        for sa in data["stock_accounts"]:
            self._realtime_layout.addWidget(
                self._section_header(f"股票帳戶：{sa['account_id']}")
            )
            self._realtime_layout.addWidget(self._position_table(sa["positions"]))
            lbl = QLabel(
                f"現金：{sa['cash_balance']:,.0f}　　"
                f"待交割：{sa['total_settlement']:,.0f}　　"
                f"市值：{sa['stock_market_value']:,.0f}"
            )
            lbl.setContentsMargins(4, 2, 4, 8)
            self._realtime_layout.addWidget(lbl)

        # futopt accounts
        for fa in data["futopt_accounts"]:
            self._realtime_layout.addWidget(
                self._section_header(f"期貨帳戶：{fa['account_id']}")
            )
            self._realtime_layout.addWidget(self._futopt_form(fa))

        # separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        self._realtime_layout.addWidget(line)

        # summary
        lbl = QLabel(
            f"現金水位：{data['cash_level']:,.0f} 元  （{data['cash_ratio']:.1f}%）　　"
            f"總資產：{data['total_assets']:,.0f} 元\n"
            f"（總資產 = 現金水位 + 股票市值）"
        )
        font = lbl.font()
        font.setBold(True)
        lbl.setFont(font)
        lbl.setContentsMargins(4, 4, 4, 4)
        self._realtime_layout.addWidget(lbl)
        self._realtime_layout.addStretch()

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_header(text: str) -> QLabel:
        lbl = QLabel(text)
        font = lbl.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        lbl.setFont(font)
        return lbl

    @staticmethod
    def _num_item(value: float | None, fmt: str = ",.0f") -> QTableWidgetItem:
        text = f"{value:{fmt}}" if value is not None else "—"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _position_table(self, positions: list) -> QTableWidget:
        cols = ["代碼", "數量", "成本", "現價", "市值", "未實現損益"]
        tbl = QTableWidget(max(len(positions), 1), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if not positions:
            item = QTableWidgetItem("目前無股票持倉")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tbl.setItem(0, 0, item)
            tbl.setSpan(0, 0, 1, len(cols))
            tbl.setFixedHeight(50)
            return tbl

        for r, pos in enumerate(positions):
            tbl.setItem(r, 0, QTableWidgetItem(pos["code"]))
            tbl.setItem(r, 1, self._num_item(pos["quantity"], "d"))
            tbl.setItem(r, 2, self._num_item(pos["cost_price"], ".2f"))
            tbl.setItem(r, 3, self._num_item(pos["last_price"], ".2f"))
            tbl.setItem(r, 4, self._num_item(pos["market_value"]))
            tbl.setItem(r, 5, self._num_item(pos["pnl"]))
        tbl.setFixedHeight(min(len(positions) * 28 + 32, 220))
        return tbl

    @staticmethod
    def _futopt_form(fa: dict) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(4, 0, 4, 8)
        rows = [
            ("今日餘額",         fa["today_balance"]),
            ("期貨未平倉損益",   fa["future_open_pnl"]),
            ("選擇權未平倉損益", fa["option_open_pnl"]),
            ("期貨結算損益",     fa["future_settle_pnl"]),
            ("選擇權結算損益",   fa["option_settle_pnl"]),
            ("期貨權益數",       fa["futopt_equity"]),
        ]
        for label, value in rows:
            lbl = QLabel(f"{value:,.0f} 元")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if label == "期貨權益數":
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
            form.addRow(f"{label}：", lbl)
        return widget

    # ------------------------------------------------------------------
    # History tab
    # ------------------------------------------------------------------

    def _refresh_account_dropdown(self) -> None:
        current = self._account_filter.currentData()
        self._account_filter.blockSignals(True)
        self._account_filter.clear()
        self._account_filter.addItem("全部", None)
        for acc_id in list_account_ids():
            self._account_filter.addItem(acc_id, acc_id)
        idx = self._account_filter.findData(current)
        self._account_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._account_filter.blockSignals(False)

    def _load_history(self) -> None:
        acc_id = self._account_filter.currentData()
        rows = load_snapshots(acc_id)
        self._history_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._history_table.setItem(r, 0, QTableWidgetItem(row.get("date", "")))
            self._history_table.setItem(r, 1, QTableWidgetItem(row.get("account_id", "")))
            for c, key in enumerate(
                ["cash_balance", "total_settlement", "cash_level",
                 "stock_market_value", "total_assets"],
                start=2,
            ):
                self._history_table.setItem(r, c, self._num_item(row.get(key)))
            cr = row.get("cash_ratio")
            self._history_table.setItem(r, 7, self._num_item(cr, ".1f"))
            self._history_table.setItem(r, 8, self._num_item(row.get("futopt_equity")))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
