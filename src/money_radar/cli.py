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
    category_add=sub.add_parser("category-add"); category_add.add_argument("name"); category_add.add_argument("--user",type=int,required=True)
    category_list=sub.add_parser("categories"); category_list.add_argument("--user",type=int,required=True)
    tx_filter=sub.add_parser("filter"); tx_filter.add_argument("--from",dest="date_from"); tx_filter.add_argument("--to",dest="date_to"); tx_filter.add_argument("--category"); tx_filter.add_argument("--currency"); tx_filter.add_argument("--merchant"); tx_filter.add_argument("--limit",type=int,default=100); tx_filter.add_argument("--user",type=int,required=True)
    report=sub.add_parser("month"); report.add_argument("month"); report.add_argument("--user",type=int,required=True)
    unusual=sub.add_parser("unusual"); unusual.add_argument("month"); unusual.add_argument("--user",type=int,required=True)
    recurring_add=sub.add_parser("recurring-add"); recurring_add.add_argument("merchant"); recurring_add.add_argument("category"); recurring_add.add_argument("amount"); recurring_add.add_argument("currency"); recurring_add.add_argument("--day",type=int,default=1); recurring_add.add_argument("--user",type=int,required=True)
    recurring_list=sub.add_parser("recurring-list"); recurring_list.add_argument("--user",type=int,required=True)
    recurring_run=sub.add_parser("recurring-run"); recurring_run.add_argument("period"); recurring_run.add_argument("--user",type=int,required=True)
    recurring_disable=sub.add_parser("recurring-disable"); recurring_disable.add_argument("rule_id",type=int); recurring_disable.add_argument("--user",type=int,required=True)
    budget=sub.add_parser("budget"); budget.add_argument("month"); budget.add_argument("category"); budget.add_argument("amount"); budget.add_argument("currency"); budget.add_argument("--user",type=int,required=True)
    imp=sub.add_parser("import"); imp.add_argument("file"); imp.add_argument("--user",type=int,required=True); imp.add_argument("--map",action="append",required=True,help="field=CSV column (repeat for date, amount, merchant, currency, category, description, external_id)"); imp.add_argument("--confirm",action="store_true")
    exp=sub.add_parser("export"); exp.add_argument("--user",type=int,required=True)
    backup=sub.add_parser("backup"); backup.add_argument("destination",help="new path; existing files are never overwritten")
    delete=sub.add_parser("delete-user"); delete.add_argument("--user",type=int,required=True)
    rate=sub.add_parser("rate"); rate.add_argument("currency"); rate.add_argument("--date")
    args=p.parse_args(); ledger=Ledger(args.db)
    if args.command=="add":
        row=TransactionInput(date.fromisoformat(args.date),money(args.amount,args.currency),args.currency,args.merchant,args.category)
        print("Расход добавлен." if ledger.add(args.user,row) else "Дубликат не добавлен.")
    elif args.command=="category-add":
        ledger.add_category(args.user,args.name);print("Категория сохранена.")
    elif args.command=="categories":
        print("\n".join(ledger.categories(args.user)) or "Категорий нет.")
    elif args.command=="filter":
        rows=ledger.filter_transactions(args.user,date_from=args.date_from,date_to=args.date_to,category=args.category,currency=args.currency,merchant=args.merchant,limit=args.limit)
        for row in rows:print(f"#{row['id']} {row['booked_on']} | {row['merchant']} | {row['amount']} {row['currency']} | {row['category']}")
        if not rows:print("Операций по фильтру нет.")
    elif args.command=="month":
        result=ledger.month_report(args.user,args.month)
        print(f"Отчёт {args.month}")
        for item in result["items"]:
            budget_text=f" / бюджет {item['budget']}, остаток {item['remaining']}" if item["budget"] is not None else " / бюджет не задан"
            print(f"{item['category']}: {item['spent']} {item['currency']} ({item['count']} операций){budget_text}")
        for item in result["budget_categories_without_spending"]:
            print(f"{item['category']}: расходов нет; бюджет {item['budget']} {item['currency']} не использован")
        if not result["items"]: print("Операций нет.")
    elif args.command=="unusual":
        candidates=ledger.unusual_expenses(args.user,args.month)
        if not candidates: print(f"{args.month}: нет кандидатов; нужны минимум 5 предыдущих расходов в той же категории и валюте.")
        for item in candidates:
            print(f"Кандидат #{item['transaction_id']}: {item['date']} | {item['merchant']} | {item['category']} | {item['amount']} {item['currency']} против медианы {item['baseline_median']} ({item['baseline_count']} расходов, порог ×{item['factor']}). Проверьте вручную.")
    elif args.command=="recurring-add":
        rule_id=ledger.add_recurring_rule(args.user,args.merchant,args.category,Decimal(args.amount),args.currency,args.day)
        print(f"Recurring rule #{rule_id} saved.")
    elif args.command=="recurring-list":
        for rule in ledger.recurring_rules(args.user):
            print(f"#{rule['id']} {rule['merchant']} | {rule['category']} | {rule['amount'] or 'unset'} {rule['currency'] or ''} | day {rule['day_of_month']} | {'enabled' if rule['enabled'] else 'disabled'}")
    elif args.command=="recurring-run":
        print(ledger.run_recurring(args.user,args.period))
    elif args.command=="recurring-disable":
        print("Rule disabled." if ledger.disable_recurring_rule(args.user,args.rule_id) else "Enabled rule not found.")
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
    elif args.command=="backup": print(f"SQLite backup created: {ledger.backup(args.destination)}")
    elif args.command=="delete-user": ledger.delete_user(args.user); print("Данные пользователя удалены.")
    elif args.command=="rate":
        quote=CbrRates(ledger=ledger).quote(args.currency,date.fromisoformat(args.date) if args.date else None)
        print(f"{quote.currency}: {quote.rate} RUB за 1 {quote.currency}; дата курса {quote.effective_date} (запрошено {quote.requested_date})")

if __name__=="__main__": main()
