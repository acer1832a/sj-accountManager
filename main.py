import os
from datetime import date

from dotenv import load_dotenv
import shioaji as sj

from core import fetch_all_accounts, StockAccountData, FutoptAccountData, AllAccountsData
from db import save_snapshot

load_dotenv()

DB_PATH = "account_history.db"


def _print_stock_account(data: StockAccountData) -> None:
    label = data["account_id"]

    print(f"\n=== 股票部位 [{label}] ===")
    if data["positions"]:
        print(f"{'代碼':<8} {'數量':>6} {'成本':>10} {'現價':>10} {'市值':>12} {'未實現損益':>12}")
        print("-" * 62)
        for pos in data["positions"]:
            print(
                f"{pos['code']:<8} {pos['quantity']:>6} {pos['cost_price']:>10.2f} "
                f"{pos['last_price']:>10.2f} {pos['market_value']:>12,.0f} "
                f"{pos['pnl']:>12,.0f}"
            )
        print(f"\n股票市值合計：{data['stock_market_value']:>12,.0f} 元")
    else:
        print("目前無股票持倉")

    print(f"\n=== 現金部位 [{label}] ===")
    print(f"可用餘額：{data['cash_balance']:>12,.0f} 元")

    print(f"\n=== 待交割款 [{label}] ===")
    if data["settlements"]:
        print(f"{'交割日':<12} {'金額':>14}")
        print("-" * 28)
        for s in data["settlements"]:
            print(f"{s['date']:<12} {s['amount']:>14,.0f}")
        print(f"\n待交割合計：{data['total_settlement']:>12,.0f} 元")
    else:
        print("目前無待交割款項")


def _print_futopt_account(data: FutoptAccountData) -> None:
    label = data["account_id"]
    print(f"\n=== 期貨權益數 [{label}] ===")
    print(f"今日餘額：              {data['today_balance']:>12,.0f} 元")
    print(f"期貨未平倉損益：        {data['future_open_pnl']:>12,.0f} 元")
    print(f"選擇權未平倉損益：      {data['option_open_pnl']:>12,.0f} 元")
    print(f"期貨結算損益：          {data['future_settle_pnl']:>12,.0f} 元")
    print(f"選擇權結算損益：        {data['option_settle_pnl']:>12,.0f} 元")
    print(f"{'─' * 40}")
    print(f"期貨權益數：            {data['futopt_equity']:>12,.0f} 元")


def show_account_status(api) -> None:
    today = str(date.today())
    data: AllAccountsData = fetch_all_accounts(api)

    for sa in data["stock_accounts"]:
        _print_stock_account(sa)
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
        _print_futopt_account(fa)
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

    total_cash       = sum(sa["cash_balance"]       for sa in data["stock_accounts"])
    total_settlement = sum(sa["total_settlement"]   for sa in data["stock_accounts"])
    cl = data["cash_level"]
    ta = data["total_assets"]
    cr = data["cash_ratio"]

    print("\n=== 現金水位（合計）===")
    print(f"可用餘額：        {total_cash:>12,.0f} 元")
    print(f"待交割款（淨額）：{total_settlement:>12,.0f} 元")
    print(f"{'─' * 36}")
    print(f"現金水位：        {cl:>12,.0f} 元  ({cr:.1f}% / 總資產 {ta:,.0f} 元)")
    print(f"（總資產 = 現金水位 + 股票市值）")
    print(f"\n已儲存各帳戶快照至 {DB_PATH}")


def main() -> None:
    api_key    = os.getenv("API_KEY")
    secret_key = os.getenv("SECRET_KEY")
    ca_path    = os.getenv("YOUR_CA_PATH")
    ca_passwd  = os.getenv("YOUR_CA_PASS")

    if not api_key or not secret_key:
        print("錯誤：請在 .env 檔案中設定 API_KEY 和 SECRET_KEY")
        return

    api = sj.Shioaji()

    print("登入中...")
    accounts = api.login(api_key=api_key, secret_key=secret_key)

    print("\n=== 帳戶清單 ===")
    for acc in accounts:
        print(acc)

    if ca_path and ca_passwd:
        print("\n啟用憑證中...")
        api.activate_ca(ca_path=ca_path, ca_passwd=ca_passwd)
        print("憑證啟用成功")
    else:
        print("\n警告：未設定 YOUR_CA_PATH 或 YOUR_CA_PASS，跳過憑證啟用")

    try:
        show_account_status(api)
    except Exception as e:
        print(f"\n錯誤：{e}")
    finally:
        api.logout()
        print("\n已登出")


if __name__ == "__main__":
    main()
