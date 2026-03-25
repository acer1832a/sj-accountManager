import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv, set_key
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
import shioaji as sj

from core import fetch_all_accounts
from db import (list_account_ids, load_previous_total_assets, load_snapshots,
                reconcile_previous_snapshot, save_snapshot)

if getattr(sys, "frozen", False):
    # PyInstaller 打包後，以 exe 所在目錄為基準
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).parent

ENV_PATH = _BASE_DIR / ".env"

load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _enrich_error(msg: str) -> str:
    """針對已知錯誤訊息補充說明。"""
    if "Token doesn't have production permission" in msg:
        msg += "\n（該 API Key 無勾選正式環境，請重新產生 API Key）"
    elif "Token doesn't have permission" in msg:
        msg += "\n（該 API Key 無勾選帳務權限，請重新產生 API Key）"
    return msg


def _mask_person_id(msg: str) -> str:
    """若錯誤訊息中含有 person_id 值，僅保留前 3 碼，其餘以星號取代。"""
    def _replace(m: re.Match) -> str:
        value = m.group(1)
        if len(value) <= 3:
            return m.group(0)
        return m.group(0).replace(value, value[:3] + "*" * (len(value) - 3))
    return re.sub(r"person_id[^A-Za-z0-9]*([A-Za-z0-9]+)", _replace, msg)


def _mask_account_id(acc_id: str) -> str:
    """若 HIDE_ACCOUNT_INFO=true，將 acc_id 中 '-' 之後的部分以星號取代。"""
    if os.getenv("HIDE_ACCOUNT_INFO", "false").lower() != "true":
        return acc_id
    if "-" in acc_id:
        prefix, suffix = acc_id.split("-", 1)
        return f"{prefix}-{'*' * len(suffix)}"
    return acc_id


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
    """背景執行 Shioaji 登入測試。"""
    result = pyqtSignal(bool, str)  # success, message

    def __init__(self, api_key: str, secret_key: str) -> None:
        super().__init__()
        self._api_key    = api_key
        self._secret_key = secret_key

    def run(self) -> None:
        api = sj.Shioaji()
        try:
            api.login(api_key=self._api_key, secret_key=self._secret_key)
            self.result.emit(True, "成功")
        except Exception as e:
            self.result.emit(False, _enrich_error(_mask_person_id(str(e))))
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
        self._orig_api_key      = os.getenv("API_KEY", "")
        self._orig_secret_key   = os.getenv("SECRET_KEY", "")
        self._verified_api_key    = ""
        self._verified_secret_key = ""

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

        inner.addLayout(form)

        # 測試按鈕列
        test_row = QHBoxLayout()
        self._test_key_btn = QPushButton("測試登入 Key")
        self._test_key_btn.clicked.connect(self._test_login_key)
        test_row.addWidget(self._test_key_btn)
        test_row.addStretch()
        inner.addLayout(test_row)

        # 測試結果標籤
        self._test_status = QTextEdit("")
        self._test_status.setReadOnly(True)
        self._test_status.setFixedHeight(72)
        inner.addWidget(self._test_status)

        # 儲存 / 取消
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._save)
        self._button_box.rejected.connect(self.reject)
        inner.addWidget(self._button_box)

    # ------------------------------------------------------------------

    def _set_testing(self, testing: bool) -> None:
        self._test_key_btn.setEnabled(not testing)
        self._button_box.setEnabled(not testing)
        if testing:
            self._test_status.setStyleSheet("")
            self._test_status.setPlainText("測試中，請稍候...")

    def _test_login_key(self) -> None:
        api_key    = self._inputs["API_KEY"].text().strip()
        secret_key = self._inputs["SECRET_KEY"].text().strip()
        if not api_key or not secret_key:
            self._show_status(False, "請先填寫 API Key 及 Secret Key")
            return
        self._set_testing(True)
        self._worker = _ApiTestWorker(api_key, secret_key)
        self._worker.result.connect(
            lambda ok, msg, k=api_key, s=secret_key: self._on_login_test_result(ok, msg, k, s)
        )
        self._worker.start()

    def _on_login_test_result(self, success: bool, msg: str,
                              api_key: str, secret_key: str) -> None:
        if success:
            self._verified_api_key    = api_key
            self._verified_secret_key = secret_key
        self._show_status(success, f"登入測試：{msg}")

    def _show_status(self, success: bool, msg: str) -> None:
        color = "#1a7f37" if success else "#c0392b"
        self._test_status.setStyleSheet(
            f"QTextEdit {{ color: {color}; font-weight: bold; }}"
        )
        self._test_status.setPlainText(msg)
        self._set_testing(False)

    def _save(self) -> None:
        api_key    = self._inputs["API_KEY"].text().strip()
        secret_key = self._inputs["SECRET_KEY"].text().strip()
        if not api_key or not secret_key:
            QMessageBox.warning(
                self, "欄位未填寫",
                "API Key 及 Secret Key 為必填欄位，請輸入後再儲存。"
            )
            return
        keys_changed = (api_key != self._orig_api_key or
                        secret_key != self._orig_secret_key)
        already_verified = (api_key == self._verified_api_key and
                            secret_key == self._verified_secret_key)
        if keys_changed and not already_verified:
            self._set_testing(True)
            self._worker = _ApiTestWorker(api_key, secret_key)
            self._worker.result.connect(self._on_save_login_result)
            self._worker.start()
        else:
            self._do_save()

    def _on_save_login_result(self, success: bool, msg: str) -> None:
        self._set_testing(False)
        if not success:
            self._show_status(False, f"登入失敗，設定未儲存：{msg}")
            QMessageBox.critical(
                self, "登入失敗",
                f"所輸入的 Key 無法執行登入動作，設定不會儲存。\n\n原因：{msg}"
            )
            return
        self._do_save()

    def _do_save(self) -> None:
        for key, edit in self._inputs.items():
            set_key(str(ENV_PATH), key, edit.text())
        load_dotenv(ENV_PATH, override=True)
        self.accept()


class SystemSettingsDialog(QDialog):
    """系統行為設定（帳號遮罩、自動更新等）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("系統設定")
        self.setMinimumWidth(340)

        frame = _bordered_dialog(self)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(12)

        self._hide_account_chk = QCheckBox("隱藏帳號資訊（「-」號後以星號顯示）")
        self._hide_account_chk.setChecked(
            os.getenv("HIDE_ACCOUNT_INFO", "false").lower() == "true"
        )
        inner.addWidget(self._hide_account_chk)

        self._auto_refresh_chk = QCheckBox("程式執行時自動更新帳戶資訊")
        self._auto_refresh_chk.setChecked(
            os.getenv("AUTO_REFRESH", "false").lower() == "true"
        )
        inner.addWidget(self._auto_refresh_chk)

        self._show_positions_chk = QCheckBox("顯示股票庫存頁籤")
        self._show_positions_chk.setChecked(
            os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"
        )
        inner.addWidget(self._show_positions_chk)

        inner.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        inner.addWidget(buttons)

    def _save(self) -> None:
        set_key(str(ENV_PATH), "HIDE_ACCOUNT_INFO",
                "true" if self._hide_account_chk.isChecked() else "false")
        set_key(str(ENV_PATH), "AUTO_REFRESH",
                "true" if self._auto_refresh_chk.isChecked() else "false")
        set_key(str(ENV_PATH), "SHOW_STOCK_POSITIONS",
                "true" if self._show_positions_chk.isChecked() else "false")
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

        app_version = "0.4.0"
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
    stock_accs = data["stock_accounts"]
    # 取第一個股票帳戶的交易日作為共用快照日期，沒有則 fallback 到今天
    snapshot_date = (
        stock_accs[0]["snapshot_date"] if stock_accs else str(date.today())
    )
    for sa in stock_accs:
        save_snapshot({
            "date": sa["snapshot_date"],
            "account_id": sa["account_id"],
            "cash_balance": sa["cash_balance"],
            "settlement_t1": sa["settlement_t1"],
            "settlement_t2": sa["settlement_t2"],
            "cash_level": sa["cash_level"],
            "stock_market_value": sa["stock_market_value"],
            "total_assets": sa["total_assets"],
            "cash_ratio": sa["cash_ratio"],
            "futopt_equity": None,
        })
        reconcile_previous_snapshot(
            sa["account_id"], sa["snapshot_date"],
            sa["settlement_t0"], sa["settlement_t1"],
        )
    for fa in data["futopt_accounts"]:
        save_snapshot({
            "date": snapshot_date,
            "account_id": fa["account_id"],
            "cash_balance": None,
            "settlement_t1": None,
            "settlement_t2": None,
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

    def __init__(self, api_key: str, secret_key: str):
        super().__init__()
        self._api_key    = api_key
        self._secret_key = secret_key

    def run(self) -> None:
        api = sj.Shioaji()
        fetch_positions = os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"
        try:
            self.progress.emit("登入中...")
            if fetch_positions:
                self.progress.emit("登入並下載商品檔...")
            api.login(api_key=self._api_key, secret_key=self._secret_key,
                      fetch_contract=fetch_positions)
            self.progress.emit("抓取帳戶資料...")
            data = fetch_all_accounts(api, fetch_names=fetch_positions)
            self.progress.emit("儲存快照...")
            _save_all_snapshots(data)
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(_enrich_error(str(e)))
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
        self._last_data: dict | None = None
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
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_realtime_tab(),   "即時狀態")
        self._positions_tab_index = 1
        self._tabs.addTab(self._build_positions_tab(),  "股票庫存")
        self._tabs.addTab(self._build_history_tab(),    "歷史資料")
        self._update_positions_tab_visibility()
        root.addWidget(self._tabs)

        self._setup_menu()
        self.statusBar().showMessage("就緒")

    def _check_settings_on_start(self) -> None:
        missing = _check_env()
        if not ENV_PATH.exists():
            msg = "找不到 .env 設定檔，請先完成帳戶設定。"
        elif missing:
            msg = f"以下必要欄位尚未設定：{', '.join(missing)}\n請先完成帳戶設定。"
        else:
            self._auto_refresh_if_enabled()
            return
        ret = QMessageBox.warning(
            self, "帳戶設定不完整", msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if ret == QMessageBox.StandardButton.Ok:
            self._show_account_settings()
        if not _check_env():
            self._auto_refresh_if_enabled()

    def _auto_refresh_if_enabled(self) -> None:
        if os.getenv("AUTO_REFRESH", "false").lower() == "true":
            self._on_refresh_clicked()

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()

        sys_action = menu_bar.addAction("系統設定")
        sys_action.triggered.connect(self._show_system_settings)

        settings_action = menu_bar.addAction("帳戶設定")
        settings_action.triggered.connect(self._show_account_settings)

        about_action = menu_bar.addAction("關於")
        about_action.triggered.connect(self._show_about)

    def _show_system_settings(self) -> None:
        dlg = SystemSettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("系統設定已儲存")
            self._update_positions_tab_visibility()
            if self._last_data is not None:
                self._populate_realtime(self._last_data)
                self._update_positions_content()
            self._refresh_account_dropdown()
            self._load_history()

    def _show_account_settings(self) -> None:
        dlg = AccountSettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("帳戶設定已儲存")
            if self._last_data is not None:
                self._populate_realtime(self._last_data)
            self._refresh_account_dropdown()
            self._load_history()

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _build_realtime_tab(self) -> QWidget:
        self._stock_selector: QComboBox | None = None
        self._futopt_selector: QComboBox | None = None

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

        cols = ["日期", "帳戶", "可用餘額", "T+1 交割", "T+2 交割", "現金水位",
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

    def _build_positions_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("股票帳戶："))
        self._positions_selector = QComboBox()
        self._positions_selector.currentIndexChanged.connect(self._update_positions_table)
        selector_row.addWidget(self._positions_selector)
        selector_row.addStretch()
        layout.addLayout(selector_row)

        cols = ["股票代號", "股票名稱", "數量(股)", "成本", "股價", "市值", "未實現損益"]
        self._positions_table = QTableWidget(0, len(cols))
        self._positions_table.setHorizontalHeaderLabels(cols)
        self._positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._positions_table.verticalHeader().setVisible(False)
        self._positions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._positions_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._positions_table)
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
        self._refresh_btn.setEnabled(False)
        self._worker = FetchWorker(api_key, secret_key)
        self._worker.progress.connect(self.statusBar().showMessage)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_finished(self, data: dict) -> None:
        self._last_data = data
        self._rebuild_realtime_content()
        self._update_positions_content()
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
        """設定儲存後重新套用遮罩時呼叫。"""
        self._rebuild_realtime_content()

    def _rebuild_realtime_content(self) -> None:
        """清空並重建即時狀態內容，保留上次選取的帳戶。"""
        if self._last_data is None:
            return
        data = self._last_data

        # 儲存上次選取（重建前先讀取）
        prev_stock  = self._stock_selector.currentData()  if self._stock_selector  else None
        prev_futopt = self._futopt_selector.currentData() if self._futopt_selector else None

        # 清空 layout
        while self._realtime_layout.count():
            item = self._realtime_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def _make_selector(accounts: list, prev_sel: str | None) -> QComboBox:
            cb = QComboBox()
            cb.setEnabled(len(accounts) > 1)
            for acc in accounts:
                cb.addItem(_mask_account_id(acc["account_id"]), acc["account_id"])
            idx = cb.findData(prev_sel)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.currentIndexChanged.connect(self._rebuild_realtime_content)
            return cb

        def _section_header_row(label: str, cb: QComboBox) -> QWidget:
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(self._section_header(label))
            row.addWidget(cb)
            row.addStretch()
            return w

        # === 股票帳戶區塊 ===
        if data["stock_accounts"]:
            self._stock_selector = _make_selector(data["stock_accounts"], prev_stock)
            self._realtime_layout.addWidget(
                _section_header_row("股票帳戶", self._stock_selector)
            )
            acc_id = self._stock_selector.currentData()
            sa = next((s for s in data["stock_accounts"] if s["account_id"] == acc_id), None)
            if sa:
                self._realtime_layout.addWidget(self._cash_summary_widget(sa))

        # === 期貨帳戶區塊 ===
        if data["futopt_accounts"]:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            self._realtime_layout.addWidget(line)

            self._futopt_selector = _make_selector(data["futopt_accounts"], prev_futopt)
            self._realtime_layout.addWidget(
                _section_header_row("期貨帳戶", self._futopt_selector)
            )
            acc_id = self._futopt_selector.currentData()
            fa = next((f for f in data["futopt_accounts"] if f["account_id"] == acc_id), None)
            if fa:
                self._realtime_layout.addWidget(self._futopt_form(fa))

        # === 跨帳戶合計 ===
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        self._realtime_layout.addWidget(line)

        cash_level = data["cash_level"]
        total_assets = data["total_assets"]
        cash_color = ' style="color:#c0392b;"' if cash_level < 0 else ""
        cash_html = (
            f'<span{cash_color}>'
            f'現金水位：{cash_level:,.0f} 元  （{data["cash_ratio"]:.1f}%）'
            f'</span>'
        )

        # 查前一交易日總資產
        snapshot_date = (data["stock_accounts"][0]["snapshot_date"]
                         if data["stock_accounts"] else str(date.today()))
        prev_assets = load_previous_total_assets(snapshot_date)
        if prev_assets is not None and prev_assets != 0:
            diff = total_assets - prev_assets
            diff_pct = diff / prev_assets * 100
            diff_color = "#c0392b" if diff < 0 else "#1a7f37"
            sign = "+" if diff >= 0 else ""
            change_html = (
                f'　<span style="color:{diff_color}; font-weight:bold;">'
                f'（{sign}{diff:,.0f} 元 / {sign}{diff_pct:.2f}%）</span>'
            )
        else:
            change_html = ""

        lbl = QLabel(
            f'{cash_html}　　'
            f'總資產：{total_assets:,.0f} 元<br>'
            f'<span style="font-weight:normal;">'
            f'（總資產 = 現金水位 + 股票市值，跨所有帳戶合計）</span>'
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
        font = lbl.font()
        font.setBold(True)
        lbl.setFont(font)
        lbl.setContentsMargins(4, 4, 4, 4)
        self._realtime_layout.addWidget(lbl)

        if change_html:
            change_lbl = QLabel(f'總資產變動（較前一交易日）：{change_html}')
            change_lbl.setTextFormat(Qt.TextFormat.RichText)
            change_lbl.setContentsMargins(4, 0, 4, 4)
            self._realtime_layout.addWidget(change_lbl)

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

    @staticmethod
    def _cash_summary_widget(sa: dict) -> QWidget:
        """顯示現金、待交割明細（含日期）及交割後剩餘金額。"""
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(4, 4, 4, 8)
        form.setSpacing(4)

        def _rlabel(text: str, bold: bool = False, color: str = "") -> QLabel:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if bold:
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
            if color:
                lbl.setStyleSheet(f"color: {color};")
            return lbl

        # 可用餘額
        form.addRow("可用餘額：", _rlabel(f"{sa['cash_balance']:,.0f} 元"))

        # 待交割明細（每筆獨立顯示）
        settlements = sa.get("settlements", [])
        if settlements:
            for s in settlements:
                t_tag = f"T+{s['T']}" if s["T"] > 0 else "T+0（已入帳）"
                label = f"  {s['date']}（{t_tag}）："
                form.addRow(label, _rlabel(f"{s['amount']:,.0f} 元"))

        # 待交割淨額（T+1 起）
        net_lbl = _rlabel(f"{sa['settlement_t1'] + sa['settlement_t2']:,.0f} 元")
        form.addRow("待交割淨額（T+1 起）：", net_lbl)

        # 交割後餘額（= 可用餘額 + 待交割淨額）
        cash_level = sa["cash_level"]
        cash_color = "#c0392b" if cash_level < 0 else ""
        form.addRow("交割後餘額：", _rlabel(f"{cash_level:,.0f} 元", bold=True, color=cash_color))

        # 股票市值
        form.addRow("股票市值：", _rlabel(f"{sa['stock_market_value']:,.0f} 元"))

        return widget

    # ------------------------------------------------------------------
    # History tab
    # ------------------------------------------------------------------

    def _update_positions_tab_visibility(self) -> None:
        visible = os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"
        self._tabs.tabBar().setTabVisible(self._positions_tab_index, visible)

    def _update_positions_content(self) -> None:
        if self._last_data is None:
            return
        stock_accs = self._last_data["stock_accounts"]
        prev = self._positions_selector.currentData()
        self._positions_selector.blockSignals(True)
        self._positions_selector.clear()
        for sa in stock_accs:
            self._positions_selector.addItem(
                _mask_account_id(sa["account_id"]), sa["account_id"]
            )
        idx = self._positions_selector.findData(prev)
        self._positions_selector.setCurrentIndex(idx if idx >= 0 else 0)
        self._positions_selector.blockSignals(False)
        self._update_positions_table()

    def _update_positions_table(self) -> None:
        if self._last_data is None:
            return
        acc_id = self._positions_selector.currentData()
        sa = next(
            (s for s in self._last_data["stock_accounts"] if s["account_id"] == acc_id),
            None,
        )
        positions = sa["positions"] if sa else []
        cols = self._positions_table.columnCount()
        if not positions:
            self._positions_table.setRowCount(1)
            item = QTableWidgetItem("目前無股票持倉（或未啟用股票庫存抓取）")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._positions_table.setItem(0, 0, item)
            self._positions_table.setSpan(0, 0, 1, cols)
            return
        self._positions_table.clearSpans()
        self._positions_table.setRowCount(len(positions))
        for r, pos in enumerate(positions):
            self._positions_table.setItem(r, 0, QTableWidgetItem(pos["code"]))
            self._positions_table.setItem(r, 1, QTableWidgetItem(pos.get("name", "")))
            self._positions_table.setItem(r, 2, self._num_item(pos["quantity"], ",d"))
            self._positions_table.setItem(r, 3, self._num_item(pos["cost_price"], ".2f"))
            self._positions_table.setItem(r, 4, self._num_item(pos["last_price"], ".2f"))
            self._positions_table.setItem(r, 5, self._num_item(pos["market_value"]))
            self._positions_table.setItem(r, 6, self._num_item(pos["pnl"]))

    def _refresh_account_dropdown(self) -> None:
        current = self._account_filter.currentData()
        self._account_filter.blockSignals(True)
        self._account_filter.clear()
        self._account_filter.addItem("全部", None)
        for acc_id in list_account_ids():
            self._account_filter.addItem(_mask_account_id(acc_id), acc_id)
        idx = self._account_filter.findData(current)
        self._account_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._account_filter.blockSignals(False)

    def _load_history(self) -> None:
        acc_id = self._account_filter.currentData()
        rows = load_snapshots(acc_id)
        self._history_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._history_table.setItem(r, 0, QTableWidgetItem(row.get("date", "")))
            self._history_table.setItem(r, 1, QTableWidgetItem(_mask_account_id(row.get("account_id", ""))))
            for c, key in enumerate(
                ["cash_balance", "settlement_t1", "settlement_t2", "cash_level",
                 "stock_market_value", "total_assets"],
                start=2,
            ):
                self._history_table.setItem(r, c, self._num_item(row.get(key)))
            cr = row.get("cash_ratio")
            self._history_table.setItem(r, 8, self._num_item(cr, ".1f"))
            self._history_table.setItem(r, 9, self._num_item(row.get("futopt_equity")))


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
