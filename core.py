import time
from datetime import date
from decimal import Decimal
from threading import Lock
from typing import TypedDict

import shioaji as sj


class _RateLimiter:
    """滑動視窗速率限制器，確保在 period 秒內不超過 max_calls 次呼叫。"""

    def __init__(self, max_calls: int, period: float) -> None:
        self._max_calls = max_calls
        self._period = period
        self._calls: list[float] = []
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max_calls:
                sleep_time = self._period - (now - self._calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                now = time.monotonic()
                self._calls = [t for t in self._calls if now - t < self._period]
            self._calls.append(time.monotonic())


# Accounting API：25 次 / 5 秒
_acct_limiter = _RateLimiter(25, 5.0)
# Market Data API：50 次 / 5 秒
_market_limiter = _RateLimiter(50, 5.0)


class PositionRow(TypedDict):
    code: str
    name: str
    quantity: int       # 以「股」為單位
    cost_price: float
    last_price: float
    market_value: float
    pnl: float


class StockAccountData(TypedDict):
    account_id: str
    snapshot_date: str        # T+0 交割日（即當天交易日）
    positions: list[PositionRow]
    stock_market_value: float
    cash_balance: float
    settlement_t0: float      # 今日已入帳金額（T=0）
    settlement_t1: float
    settlement_t2: float
    cash_level: float
    total_assets: float
    cash_ratio: float
    settlements: list[dict]  # [{date, amount, T}, ...]


class FutoptAccountData(TypedDict):
    account_id: str
    today_balance: float
    future_open_pnl: float
    option_open_pnl: float
    future_settle_pnl: float
    option_settle_pnl: float
    futopt_equity: float


class AllAccountsData(TypedDict):
    stock_accounts: list[StockAccountData]
    futopt_accounts: list[FutoptAccountData]
    snapshot_date: str   # 最後交易日，供期貨快照使用
    cash_level: float
    total_assets: float
    cash_ratio: float


def acc_label(acc) -> str:
    return f"{acc.broker_id}-{acc.account_id}"


def fetch_last_trading_date(api: sj.Shioaji) -> str:
    """透過 scanners 取得最後交易日日期（scan.date 為正確交易日，夜盤不會跨日錯位）。
    若查詢失敗則 fallback 到今天。"""
    try:
        _market_limiter.wait()
        result = api.scanners(
            scanner_type=sj.constant.ScannerType.VolumeRank,
            ascending=False,
            count=1,
        )
        if result:
            return result[0].date
    except Exception:
        pass
    return str(date.today())


def fetch_stock_account(api: sj.Shioaji, acc, fetch_names: bool = False) -> StockAccountData:
    label = acc_label(acc)

    _acct_limiter.wait()
    positions_raw = api.list_positions(acc)
    positions: list[PositionRow] = []
    stock_market_value = Decimal(0)
    for pos in positions_raw:
        shares = pos.quantity * 1000
        gross_value = Decimal(str(pos.last_price)) * shares
        if pos.cond == "MarginTrading":
            market_value = gross_value - Decimal(str(pos.margin_purchase_amount))
        elif pos.cond == "ShortSelling":
            market_value = Decimal(0)
        else:
            market_value = gross_value
        stock_market_value += market_value
        if fetch_names:
            contract = api.Contracts.Stocks.get(pos.code)
            name = contract.name if contract else ""
        else:
            name = ""
        positions.append({
            "code": pos.code,
            "name": name,
            "quantity": shares,
            "cost_price": float(pos.price),
            "last_price": float(pos.last_price),
            "market_value": float(market_value),
            "pnl": float(pos.pnl),
        })

    _acct_limiter.wait()
    balance = api.account_balance(account=acc)
    cash_balance = Decimal(str(balance.acc_balance))

    _acct_limiter.wait()
    settlements_raw = api.settlements(acc)
    snapshot_date = str(date.today())  # fallback
    settlement_t0 = Decimal(0)
    settlement_t1 = Decimal(0)
    settlement_t2 = Decimal(0)
    settlements = []
    for s in settlements_raw:
        settlements.append({"date": str(s.date), "amount": float(s.amount), "T": s.T})
        if s.T == 0:
            snapshot_date = str(s.date)
            settlement_t0 = Decimal(str(s.amount))
        elif s.T == 1:
            settlement_t1 += Decimal(str(s.amount))
        elif s.T == 2:
            settlement_t2 += Decimal(str(s.amount))

    cash_level = cash_balance + settlement_t1 + settlement_t2
    total_assets = cash_level + stock_market_value
    cash_ratio = float(cash_level) / float(total_assets) * 100 if total_assets else 0.0

    return {
        "account_id": label,
        "snapshot_date": snapshot_date,
        "positions": positions,
        "stock_market_value": float(stock_market_value),
        "cash_balance": float(cash_balance),
        "settlement_t0": float(settlement_t0),
        "settlement_t1": float(settlement_t1),
        "settlement_t2": float(settlement_t2),
        "cash_level": float(cash_level),
        "total_assets": float(total_assets),
        "cash_ratio": cash_ratio,
        "settlements": settlements,
    }


def fetch_futopt_account(api: sj.Shioaji, acc) -> FutoptAccountData:
    label = acc_label(acc)
    _acct_limiter.wait()
    margin = api.margin(acc)
    today_balance     = Decimal(str(margin.today_balance))
    future_open_pnl   = Decimal(str(margin.future_open_position))
    option_open_pnl   = Decimal(str(margin.option_open_position))
    future_settle_pnl = Decimal(str(margin.future_settle_profitloss))
    option_settle_pnl = Decimal(str(margin.option_settle_profitloss))
    futopt_equity     = (today_balance + future_open_pnl + option_open_pnl
                         + future_settle_pnl + option_settle_pnl)
    return {
        "account_id": label,
        "today_balance":     float(today_balance),
        "future_open_pnl":   float(future_open_pnl),
        "option_open_pnl":   float(option_open_pnl),
        "future_settle_pnl": float(future_settle_pnl),
        "option_settle_pnl": float(option_settle_pnl),
        "futopt_equity":     float(futopt_equity),
    }


def fetch_all_accounts(api: sj.Shioaji, fetch_names: bool = False) -> AllAccountsData:
    all_accounts = api.list_accounts()
    stock_accs  = [a for a in all_accounts if "Stock"  in type(a).__name__]
    futopt_accs = [a for a in all_accounts if "Futopt" in type(a).__name__
                   or "Future" in type(a).__name__]

    stock_accounts  = [fetch_stock_account(api, acc, fetch_names) for acc in stock_accs]
    futopt_accounts = [fetch_futopt_account(api, acc) for acc in futopt_accs]

    # 決定快照日期：優先用股票帳戶的 T+0 交割日，否則透過 scanners 取最後交易日
    if stock_accounts:
        snapshot_date = stock_accounts[0]["snapshot_date"]
    else:
        snapshot_date = fetch_last_trading_date(api)

    total_cash     = Decimal(str(sum(sa["cash_balance"]     for sa in stock_accounts)))
    total_t1       = Decimal(str(sum(sa["settlement_t1"]   for sa in stock_accounts)))
    total_t2       = Decimal(str(sum(sa["settlement_t2"]   for sa in stock_accounts)))
    total_stock_mv = Decimal(str(sum(sa["stock_market_value"] for sa in stock_accounts)))
    cash_level   = total_cash + total_t1 + total_t2
    total_assets = cash_level + total_stock_mv
    cash_ratio   = float(cash_level) / float(total_assets) * 100 if total_assets else 0.0

    return {
        "stock_accounts":  stock_accounts,
        "futopt_accounts": futopt_accounts,
        "snapshot_date":   snapshot_date,
        "cash_level":   float(cash_level),
        "total_assets": float(total_assets),
        "cash_ratio":   cash_ratio,
    }
