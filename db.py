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
            settlement_t1 REAL,
            settlement_t2 REAL,
            cash_level REAL,
            stock_market_value REAL,
            total_assets REAL,
            cash_ratio REAL,
            futopt_equity REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, account_id)
        )
    """)
    # Migration: add new columns for existing databases
    for col in ("settlement_t1", "settlement_t2"):
        try:
            con.execute(f"ALTER TABLE daily_snapshot ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass  # column already exists
    con.commit()


def save_snapshot(data: dict) -> None:
    """INSERT OR REPLACE 單一帳戶快照。data 必須包含 date 與 account_id。"""
    con = _connect()
    _ensure_schema(con)
    con.execute("""
        INSERT OR REPLACE INTO daily_snapshot
        (date, account_id, cash_balance, settlement_t1, settlement_t2,
         cash_level, stock_market_value, total_assets, cash_ratio, futopt_equity)
        VALUES (:date, :account_id, :cash_balance, :settlement_t1, :settlement_t2,
                :cash_level, :stock_market_value, :total_assets, :cash_ratio, :futopt_equity)
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


def reconcile_previous_snapshot(
    account_id: str,
    current_date: str,
    t0_amount: float,   # 當天 T+0（應等於前一交易日的 T+1）
    t1_amount: float,   # 當天 T+1（應等於前一交易日的 T+2）
) -> None:
    """
    找出前一個有紀錄的交易日快照，若 settlement_t1 / settlement_t2
    與當天實際入帳金額不符，則更新修正並同步重算 total_settlement。
    """
    con = _connect()
    _ensure_schema(con)
    cur = con.execute(
        "SELECT date, settlement_t1, settlement_t2, cash_balance, stock_market_value "
        "FROM daily_snapshot "
        "WHERE account_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (account_id, current_date),
    )
    row = cur.fetchone()
    if row is None:
        con.close()
        return

    prev_date  = row["date"]
    prev_t1    = row["settlement_t1"]
    prev_t2    = row["settlement_t2"]
    cash_bal   = row["cash_balance"] or 0.0
    stock_mv   = row["stock_market_value"] or 0.0

    new_t1 = t0_amount if prev_t1 != t0_amount else prev_t1
    new_t2 = t1_amount if prev_t2 != t1_amount else prev_t2

    if new_t1 != prev_t1 or new_t2 != prev_t2:
        new_cash_level  = cash_bal + (new_t1 or 0.0) + (new_t2 or 0.0)
        new_total_assets = new_cash_level + stock_mv
        new_cash_ratio  = (new_cash_level / new_total_assets * 100
                           if new_total_assets else 0.0)
        con.execute(
            "UPDATE daily_snapshot "
            "SET settlement_t1 = ?, settlement_t2 = ?, "
            "cash_level = ?, total_assets = ?, cash_ratio = ? "
            "WHERE date = ? AND account_id = ?",
            (new_t1, new_t2, new_cash_level, new_total_assets, new_cash_ratio,
             prev_date, account_id),
        )
        con.commit()
    con.close()


def load_previous_total_assets(current_date: str) -> float | None:
    """
    找出 current_date 之前最近一個有股票帳戶資料的交易日，
    回傳該日所有股票帳戶的 total_assets 合計；若無資料則回傳 None。
    """
    con = _connect()
    _ensure_schema(con)
    cur = con.execute(
        "SELECT MAX(date) FROM daily_snapshot WHERE date < ? AND total_assets IS NOT NULL",
        (current_date,),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        con.close()
        return None
    prev_date = row[0]
    cur = con.execute(
        "SELECT SUM(total_assets) FROM daily_snapshot WHERE date = ? AND total_assets IS NOT NULL",
        (prev_date,),
    )
    result = cur.fetchone()
    con.close()
    return float(result[0]) if result and result[0] is not None else None


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
