from datetime import date
from decimal import Decimal

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
