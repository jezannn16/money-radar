import sqlite3
from datetime import date
from decimal import Decimal
from stat import S_IMODE

import pytest

from money_radar.core import (
    CbrRates,
    Ledger,
    TransactionInput,
    commit_import,
    money,
    parse_statement,
)


def test_decimal_and_rounding():
    assert money("1.005","RUB")==Decimal("1.00")
    assert money("1.015","RUB")==Decimal("1.02")


def test_user_isolation_report_budget_and_delete(tmp_path):
    ledger=Ledger(tmp_path/"db.sqlite3")
    row=TransactionInput(date(2026,1,2),Decimal("350.00"),"RUB","coffee","Food")
    assert ledger.add(10,row) and not ledger.add(10,row)
    # Same amount from another merchant is not an automatic duplicate.
    assert ledger.add(10,TransactionInput(date(2026,1,2),Decimal(350),"RUB","taxi","Travel"))
    ledger.set_budget(10,"2026-01","Food",Decimal(500),"RUB")
    assert ledger.month_report(10,"2026-01")["items"][0]["remaining"]==Decimal("150.00")
    assert ledger.month_report(20,"2026-01")["items"]==[]
    ledger.delete_user(10); assert ledger.month_report(10,"2026-01")["items"]==[]


def test_import_preview_then_idempotent_commit(tmp_path):
    csv_data=b"date,amount,currency,merchant,category\n2026-01-01,350,RUB,Cafe,Food\n2026-01-01,350,RUB,Cafe,Food\nbad,x,RUB,,\n"
    preview=parse_statement(csv_data,{"date":"date","amount":"amount","currency":"currency","merchant":"merchant","category":"category"},"synthetic.csv")
    assert len(preview.rows)==1 and preview.duplicates==1 and preview.errors
    ledger=Ledger(tmp_path/"db.sqlite3")
    result=commit_import(ledger,1,preview)
    assert result["imported"]==1
    assert commit_import(ledger,1,preview)["already_imported"]


def test_backup_is_consistent_and_never_overwrites(tmp_path):
    ledger = Ledger(tmp_path / "source.sqlite3")
    row = TransactionInput(date(2026, 1, 2), Decimal("12.34"), "RUB", "synthetic cafe")
    assert ledger.add(4, row)
    backup_path = ledger.backup(tmp_path / "backup" / "snapshot.sqlite3")
    assert S_IMODE(backup_path.stat().st_mode) == 0o600
    restored = Ledger(backup_path)
    assert restored.month_report(4, "2026-01")["items"][0]["spent"] == Decimal("12.34")
    with pytest.raises(FileExistsError):
        ledger.backup(backup_path)
    with pytest.raises(ValueError, match="differ"):
        ledger.backup(ledger.path)


def test_database_file_is_private_by_default(tmp_path):
    path = tmp_path / "private.sqlite3"
    Ledger(path)
    assert S_IMODE(path.stat().st_mode) == 0o600


def test_categories_and_transaction_filters_are_user_scoped(tmp_path):
    ledger=Ledger(tmp_path/"filters.db")
    ledger.add_category(1,"Food");ledger.add_category(2,"Private")
    ledger.add(1,TransactionInput(date(2026,1,2),Decimal(12),"RUB","Cafe North","Food"))
    ledger.add(1,TransactionInput(date(2026,2,2),Decimal(20),"USD","Cafe South","Travel"))
    ledger.add(2,TransactionInput(date(2026,1,2),Decimal(99),"RUB","Cafe North","Food"))
    assert ledger.categories(1)==["Food","Travel"] and ledger.categories(2)==["Food","Private"]
    rows=ledger.filter_transactions(1,date_from="2026-01-01",date_to="2026-01-31",category="Food",currency="rub",merchant="north")
    assert len(rows)==1 and rows[0]["amount"]=="12.00"
    assert len(ledger.filter_transactions(1,category="Food",merchant="north"))==1
    with pytest.raises(ValueError,match="on or before"):
        ledger.filter_transactions(1,date_from="2026-02-01",date_to="2026-01-01")


def test_unusual_spending_is_explainable_and_user_scoped(tmp_path):
    ledger = Ledger(tmp_path / "db.sqlite3")
    for month_num in range(7, 13):
        ledger.add(7, TransactionInput(date(2026, month_num, 10), Decimal(10), "RUB", "market", "Food"))
    ledger.add(7, TransactionInput(date(2027, 1, 4), Decimal(50), "RUB", "market", "Food"))
    ledger.add(8, TransactionInput(date(2027, 1, 4), Decimal(500), "RUB", "market", "Food"))
    candidates = ledger.unusual_expenses(7, "2027-01")
    assert len(candidates) == 1
    assert candidates[0]["baseline_median"] == Decimal("10.00")
    assert candidates[0]["baseline_count"] == 6
    assert ledger.unusual_expenses(8, "2027-01") == []
    with pytest.raises(ValueError):
        ledger.unusual_expenses(7, "2027-1")


def test_recurring_rules_apply_once_per_month_and_are_user_scoped(tmp_path):
    path = tmp_path / "db.sqlite3"
    ledger = Ledger(path)
    rule_id = ledger.add_recurring_rule(31, "Rent", "Home", Decimal("1250.00"), "EUR", 31)
    assert ledger.recurring_rules(32) == []
    result = ledger.run_recurring(31, "2026-02")
    assert result == {"created": 1, "duplicates": 0, "already_processed": 0}
    row = Ledger(path).month_report(31, "2026-02")["items"][0]
    assert row["spent"] == Decimal("1250.00")
    with ledger._db() as db:
        tx = db.execute("SELECT booked_on,external_id FROM transactions WHERE source='recurring'").fetchone()
    assert tx["booked_on"] == "2026-02-28"
    assert tx["external_id"] == f"recurring:{rule_id}:2026-02"
    assert Ledger(path).run_recurring(31, "2026-02") == {"created": 0, "duplicates": 0, "already_processed": 1}
    assert Ledger(path).month_report(31, "2026-02")["items"][0]["count"] == 1
    assert ledger.disable_recurring_rule(32, rule_id) is False
    assert ledger.disable_recurring_rule(31, rule_id) is True
    assert ledger.run_recurring(31, "2026-03")["created"] == 0


def test_recurring_schema_migrates_existing_database(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE recurring_rules(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,merchant TEXT NOT NULL,category TEXT NOT NULL,amount TEXT,currency TEXT,enabled INTEGER NOT NULL DEFAULT 1)")
        db.execute("INSERT INTO recurring_rules(user_id,merchant,category,amount,currency) VALUES(1,'Rent','Home','10','RUB')")
    ledger = Ledger(path)
    assert ledger.recurring_rules(1)[0]["day_of_month"] == 1
    with ledger._db() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(recurring_rules)")}
        assert "day_of_month" in columns
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3


def test_cbr_transport_historical_no_fallback_and_cache():
    daily=b'<ValCurs><Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Value>90,00</Value></Valute></ValCurs>'
    historic=b'<ValCurs><Record><Nominal>1</Nominal><Value>88,50</Value></Record></ValCurs>'
    calls=[]
    def fake(url,timeout):
        calls.append(url); return daily if "XML_daily" in url else historic
    adapter=CbrRates(fake)
    assert adapter.rate("USD",date(2025,1,2))==Decimal("88.50")
    assert "R01235" in calls[1]
    assert adapter.rate("USD",date(2025,1,2))==Decimal("88.50") and len(calls)==2


def test_cbr_rate_cache_survives_adapter_restart(tmp_path):
    ledger=Ledger(tmp_path/"rates.db")
    requested=date(2026,10,30)
    daily=b'<ValCurs Date="30.10.2026"><Valute ID="R01235"><CharCode>USD</CharCode></Valute></ValCurs>'
    historical=b'<ValCurs><Record Date="30.10.2026"><Nominal>1</Nominal><Value>80,25</Value></Record></ValCurs>'
    calls=[]
    first=CbrRates(lambda url,timeout:(calls.append(url) or (daily if "XML_daily" in url else historical)),ledger=ledger)
    quote=first.quote("USD",requested)
    second=CbrRates(lambda url,timeout:(_ for _ in ()).throw(AssertionError("persistent cache should avoid network")),ledger=Ledger(ledger.path))
    assert second.quote("USD",requested)==quote
    assert len(calls)==2 and quote.effective_date==requested
