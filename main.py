import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
import shioaji as sj

from core import fetch_all_accounts, StockAccountData, FutoptAccountData, AllAccountsData
from db import reconcile_previous_snapshot, save_snapshot

if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).parent

load_dotenv(_BASE_DIR / ".env")

DB_PATH = "account_history.db"


def _red(text: str) -> str:
    """若輸出至終端機，以 ANSI 紅色顯示文字；否則原樣回傳。"""
    if sys.stdout.isatty():
        return f"\033[91m{text}\033[0m"
    return text


def _enrich_error(msg: str) -> str:
    """針對已知錯誤訊息補充說明。"""
    if "Token doesn't have production permission" in msg:
        msg += "\n（該 API Key 無勾選正式環境，請重新產生 API Key）"
    elif "Token doesn't have permission" in msg:
        msg += "\n（該 API Key 無勾選帳務權限，請重新產生 API Key）"
    return msg


def _mask_account_id(acc_id: str) -> str:
    """若 HIDE_ACCOUNT_INFO=true，將 acc_id 中 '-' 之後的部分以星號取代。"""
    if os.getenv("HIDE_ACCOUNT_INFO", "false").lower() != "true":
        return acc_id
    if "-" in acc_id:
        prefix, suffix = acc_id.split("-", 1)
        return f"{prefix}-{'*' * len(suffix)}"
    return acc_id


def _print_stock_account(data: StockAccountData) -> None:
    label = _mask_account_id(data["account_id"])
    show_positions = os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"

    if show_positions:
        print(f"\n=== 股票部位 [{label}] ===")
        if data["positions"]:
            print(f"{'股票代號':<8} {'股票名稱':<10} {'數量(股)':>10} {'成本':>10} {'股價':>10} {'市值':>12} {'未實現損益':>12}")
            print("-" * 76)
            for pos in data["positions"]:
                print(
                    f"{pos['code']:<8} {pos.get('name', ''):<10} {pos['quantity']:>10,} "
                    f"{pos['cost_price']:>10.2f} {pos['last_price']:>10.2f} "
                    f"{pos['market_value']:>12,.0f} {pos['pnl']:>12,.0f}"
                )
        else:
            print("目前無股票持倉")

    print(f"\n股票市值合計 [{label}]：{data['stock_market_value']:>12,.0f} 元")

    print(f"\n=== 現金部位 [{label}] ===")
    print(f"可用餘額：{data['cash_balance']:>12,.0f} 元")

    print(f"\n=== 待交割款 [{label}] ===")
    if data["settlements"]:
        print(f"{'交割日':<12} {'金額':>14}")
        print("-" * 28)
        for s in data["settlements"]:
            print(f"{s['date']:<12} {s['amount']:>14,.0f}")
        print(f"\n待交割合計：{data['settlement_t1'] + data['settlement_t2']:>12,.0f} 元")
    else:
        print("目前無待交割款項")


def _print_futopt_account(data: FutoptAccountData) -> None:
    label = _mask_account_id(data["account_id"])
    print(f"\n=== 期貨權益數 [{label}] ===")
    print(f"今日餘額：              {data['today_balance']:>12,.0f} 元")
    print(f"期貨未平倉損益：        {data['future_open_pnl']:>12,.0f} 元")
    print(f"選擇權未平倉損益：      {data['option_open_pnl']:>12,.0f} 元")
    print(f"期貨結算損益：          {data['future_settle_pnl']:>12,.0f} 元")
    print(f"選擇權結算損益：        {data['option_settle_pnl']:>12,.0f} 元")
    print(f"{'─' * 40}")
    print(f"期貨權益數：            {data['futopt_equity']:>12,.0f} 元")


def show_account_status(api) -> None:
    fetch_positions = os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"
    data: AllAccountsData = fetch_all_accounts(api, fetch_names=fetch_positions)
    stock_accs = data["stock_accounts"]
    snapshot_date = data["snapshot_date"]

    for sa in stock_accs:
        _print_stock_account(sa)
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
        _print_futopt_account(fa)
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

    total_cash       = sum(sa["cash_balance"]     for sa in data["stock_accounts"])
    total_settlement = sum(sa["settlement_t1"] + sa["settlement_t2"]
                           for sa in data["stock_accounts"])
    cl = data["cash_level"]
    ta = data["total_assets"]
    cr = data["cash_ratio"]

    cash_line = f"現金水位：        {cl:>12,.0f} 元  ({cr:.1f}%)"
    if cl < 0:
        cash_line = _red(cash_line)

    print("\n=== 現金水位（合計）===")
    print(f"可用餘額：        {total_cash:>12,.0f} 元")
    print(f"待交割款（淨額）：{total_settlement:>12,.0f} 元")
    print(f"{'─' * 36}")
    print(cash_line)
    print(f"總資產：          {ta:>12,.0f} 元")
    print(f"（總資產 = 現金水位 + 股票市值）")
    print(f"\n已儲存各帳戶快照至 {DB_PATH}")


def main() -> None:
    api_key    = os.getenv("API_KEY")
    secret_key = os.getenv("SECRET_KEY")

    if not api_key or not secret_key:
        print("錯誤：請在 .env 檔案中設定 API_KEY 和 SECRET_KEY")
        return

    api = sj.Shioaji()
    fetch_positions = os.getenv("SHOW_STOCK_POSITIONS", "false").lower() == "true"

    if fetch_positions:
        print("登入並下載商品檔...")
    else:
        print("登入中...")
    api.login(api_key=api_key, secret_key=secret_key, fetch_contract=fetch_positions)

    try:
        show_account_status(api)
    except Exception as e:
        print(f"\n錯誤：{_enrich_error(str(e))}")
    finally:
        api.logout()
        print("\n已登出")


if __name__ == "__main__":
    main()
