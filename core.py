from decimal import Decimal
from typing import TypedDict

import shioaji as sj


class PositionRow(TypedDict):
    code: str
    quantity: int
    cost_price: float
    last_price: float
    market_value: float
    pnl: float


class StockAccountData(TypedDict):
    account_id: str
    positions: list[PositionRow]
    stock_market_value: float
    cash_balance: float
    total_settlement: float
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
    cash_level: float
    total_assets: float
    cash_ratio: float


def acc_label(acc) -> str:
    return f"{acc.broker_id}-{acc.account_id}"


def fetch_stock_account(api: sj.Shioaji, acc) -> StockAccountData:
    label = acc_label(acc)

    positions_raw = api.list_positions(acc)
    positions: list[PositionRow] = []
    stock_market_value = Decimal(0)
    for pos in positions_raw:
        market_value = Decimal(str(pos.last_price)) * pos.quantity * 1000
        stock_market_value += market_value
        positions.append({
            "code": pos.code,
            "quantity": pos.quantity,
            "cost_price": float(pos.price),
            "last_price": float(pos.last_price),
            "market_value": float(market_value),
            "pnl": float(pos.pnl),
        })

    balance = api.account_balance(account=acc)
    cash_balance = Decimal(str(balance.acc_balance))

    settlements_raw = api.settlements(acc)
    total_settlement = Decimal(0)
    settlements = []
    for s in settlements_raw:
        settlements.append({"date": str(s.date), "amount": float(s.amount), "T": s.T})
        if s.T != 0:
            total_settlement += Decimal(str(s.amount))

    cash_level = cash_balance + total_settlement
    total_assets = cash_level + stock_market_value
    cash_ratio = float(cash_level) / float(total_assets) * 100 if total_assets else 0.0

    return {
        "account_id": label,
        "positions": positions,
        "stock_market_value": float(stock_market_value),
        "cash_balance": float(cash_balance),
        "total_settlement": float(total_settlement),
        "cash_level": float(cash_level),
        "total_assets": float(total_assets),
        "cash_ratio": cash_ratio,
        "settlements": settlements,
    }


def fetch_futopt_account(api: sj.Shioaji, acc) -> FutoptAccountData:
    label = acc_label(acc)
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


def fetch_all_accounts(api: sj.Shioaji) -> AllAccountsData:
    all_accounts = api.list_accounts()
    stock_accs  = [a for a in all_accounts if "Stock"  in type(a).__name__]
    futopt_accs = [a for a in all_accounts if "Futopt" in type(a).__name__
                   or "Future" in type(a).__name__]

    stock_accounts  = [fetch_stock_account(api, acc)  for acc in stock_accs]
    futopt_accounts = [fetch_futopt_account(api, acc) for acc in futopt_accs]

    total_cash       = Decimal(str(sum(sa["cash_balance"]       for sa in stock_accounts)))
    total_settlement = Decimal(str(sum(sa["total_settlement"]   for sa in stock_accounts)))
    total_stock_mv   = Decimal(str(sum(sa["stock_market_value"] for sa in stock_accounts)))
    cash_level   = total_cash + total_settlement
    total_assets = cash_level + total_stock_mv
    cash_ratio   = float(cash_level) / float(total_assets) * 100 if total_assets else 0.0

    return {
        "stock_accounts":  stock_accounts,
        "futopt_accounts": futopt_accounts,
        "cash_level":   float(cash_level),
        "total_assets": float(total_assets),
        "cash_ratio":   cash_ratio,
    }
