from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

from .core import CbrRates, Ledger, TransactionInput, commit_import, money, parse_statement


def main()->None:
    p=argparse.ArgumentParser(prog="money-radar"); p.add_argument("--db",default="money_radar.sqlite3")
    sub=p.add_subparsers(dest="command",required=True)
    add=sub.add_parser("add"); add.add_argument("amount"); add.add_argument("currency"); add.add_argument("merchant"); add.add_argument("--date",default=date.today().isoformat()); add.add_argument("--category",default="Uncategorized"); add.add_argument("--user",type=int,required=True)
    report=sub.add_parser("month"); report.add_argument("month"); report.add_argument("--user",type=int,required=True)
    budget=sub.add_parser("budget"); budget.add_argument("month"); budget.add_argument("category"); budget.add_argument("amount"); budget.add_argument("currency"); budget.add_argument("--user",type=int,required=True)
    imp=sub.add_parser("import"); imp.add_argument("file"); imp.add_argument("--user",type=int,required=True); imp.add_argument("--map",action="append",required=True,help="field=CSV column (repeat for date, amount, merchant, currency, category, description, external_id)"); imp.add_argument("--confirm",action="store_true")
    exp=sub.add_parser("export"); exp.add_argument("--user",type=int,required=True)
    delete=sub.add_parser("delete-user"); delete.add_argument("--user",type=int,required=True)
    rate=sub.add_parser("rate"); rate.add_argument("currency"); rate.add_argument("--date")
    args=p.parse_args(); ledger=Ledger(args.db)
    if args.command=="add":
        row=TransactionInput(date.fromisoformat(args.date),money(args.amount,args.currency),args.currency,args.merchant,args.category)
        print("Расход добавлен." if ledger.add(args.user,row) else "Дубликат не добавлен.")
    elif args.command=="month":
        result=ledger.month_report(args.user,args.month)
        print(f"Отчёт {args.month}")
        for item in result["items"]:
            budget_text=f" / бюджет {item['budget']}, остаток {item['remaining']}" if item["budget"] is not None else " / бюджет не задан"
            print(f"{item['category']}: {item['spent']} {item['currency']} ({item['count']} операций){budget_text}")
        for item in result["budget_categories_without_spending"]:
            print(f"{item['category']}: расходов нет; бюджет {item['budget']} {item['currency']} не использован")
        if not result["items"]: print("Операций нет.")
    elif args.command=="budget":
        ledger.set_budget(args.user,args.month,args.category,Decimal(args.amount),args.currency); print("Бюджет сохранён.")
    elif args.command=="import":
        mapping={}
        for entry in args.map:
            if "=" not in entry: raise SystemExit("Mapping must be field=column")
            key,value=entry.split("=",1); mapping[key]=value
        path=Path(args.file); preview=parse_statement(path.read_bytes(),mapping,path.name)
        print(f"Preview: {len(preview.rows)} valid, {len(preview.errors)} errors, {preview.duplicates} duplicate rows")
        for error in preview.errors: print(error)
        for row in preview.rows[:10]: print(f"{row.booked_on} | {row.amount} {row.currency} | {row.merchant} | {row.category}")
        if args.confirm: print(commit_import(ledger,args.user,preview))
        else: print("Ничего не записано. Повторите команду с --confirm для импорта.")
    elif args.command=="export": print(ledger.export_csv(args.user),end="")
    elif args.command=="delete-user": ledger.delete_user(args.user); print("Данные пользователя удалены.")
    elif args.command=="rate": print(CbrRates().rate(args.currency,date.fromisoformat(args.date) if args.date else None))

if __name__=="__main__": main()
