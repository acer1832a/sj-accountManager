import sqlite3
from pathlib import Path

DB_PATH = Path("account_history.db")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_snapshot (
            date TEXT,
            account_id TEXT,
            cash_balance REAL,
            total_settlement REAL,
            cash_level REAL,
            stock_market_value REAL,
            total_assets REAL,
            cash_ratio REAL,
            futopt_equity REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, account_id)
        )
    """)
    con.commit()


def save_snapshot(data: dict) -> None:
    """INSERT OR REPLACE 單一帳戶快照。data 必須包含 date 與 account_id。"""
    con = _connect()
    _ensure_schema(con)
    con.execute("""
        INSERT OR REPLACE INTO daily_snapshot
        (date, account_id, cash_balance, total_settlement, cash_level,
         stock_market_value, total_assets, cash_ratio, futopt_equity)
        VALUES (:date, :account_id, :cash_balance, :total_settlement, :cash_level,
                :stock_market_value, :total_assets, :cash_ratio, :futopt_equity)
    """, data)
    con.commit()
    con.close()


def load_snapshots(account_id: str | None = None) -> list[dict]:
    """讀取快照，account_id=None 表示全部。依 date DESC 排序。"""
    con = _connect()
    _ensure_schema(con)
    if account_id:
        cur = con.execute(
            "SELECT * FROM daily_snapshot WHERE account_id = ? ORDER BY date DESC",
            (account_id,),
        )
    else:
        cur = con.execute("SELECT * FROM daily_snapshot ORDER BY date DESC")
    rows = [dict(row) for row in cur.fetchall()]
    con.close()
    return rows


def list_account_ids() -> list[str]:
    """回傳所有出現過的 account_id，供下拉選單使用。"""
    con = _connect()
    _ensure_schema(con)
    cur = con.execute(
        "SELECT DISTINCT account_id FROM daily_snapshot ORDER BY account_id"
    )
    result = [row[0] for row in cur.fetchall()]
    con.close()
    return result
