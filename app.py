import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)
import shioaji as sj

from core import fetch_all_accounts
from db import list_account_ids, load_snapshots, save_snapshot

load_dotenv()


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

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("帳戶管理員")
        self.resize(960, 720)
        self._worker: FetchWorker | None = None
        self._setup_ui()
        self._refresh_account_dropdown()
        self._load_history()

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

        self.statusBar().showMessage("就緒")

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
