"""Money domain, CSV/XLSX preview-import, reports and CBR exchange rates."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from openpyxl.utils.exceptions import InvalidFileException


def money(value: str | Decimal, currency: str) -> Decimal:
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must be a three-letter ISO code")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Amount is not a valid decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Expense amount must be a finite positive number")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class TransactionInput:
    booked_on: date
    amount: Decimal
    currency: str
    merchant: str
    category: str = "Uncategorized"
    description: str = ""
    source: str = "manual"
    external_id: str | None = None


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,name TEXT NOT NULL,UNIQUE(user_id,name));
            CREATE TABLE IF NOT EXISTS transactions(
              id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,booked_on TEXT NOT NULL,amount TEXT NOT NULL,
              currency TEXT NOT NULL,merchant TEXT NOT NULL,category TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL,external_id TEXT,fingerprint TEXT NOT NULL,import_batch_id INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_external ON transactions(user_id,source,external_id) WHERE external_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_fingerprint ON transactions(user_id,fingerprint);
            CREATE TABLE IF NOT EXISTS monthly_budgets(user_id INTEGER NOT NULL,month TEXT NOT NULL,category TEXT NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,PRIMARY KEY(user_id,month,category,currency));
            CREATE TABLE IF NOT EXISTS recurring_rules(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,merchant TEXT NOT NULL,category TEXT NOT NULL,amount TEXT,currency TEXT,enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS import_batches(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,file_hash TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,file_hash));
            CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,CURRENT_TIMESTAMP);
            """)

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row; return db

    @staticmethod
    def fingerprint(row: TransactionInput) -> str:
        # Deliberately includes date, currency and merchant; equal amounts alone are not duplicates.
        canonical="|".join((row.booked_on.isoformat(),str(row.amount),row.currency.upper(),row.merchant.strip().casefold()))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def add(self,user_id:int,row:TransactionInput,batch_id:int|None=None)->bool:
        if not row.merchant.strip(): raise ValueError("Merchant cannot be empty")
        amount=money(row.amount,row.currency)
        try:
            with self._db() as db:
                db.execute("INSERT INTO transactions(user_id,booked_on,amount,currency,merchant,category,description,source,external_id,fingerprint,import_batch_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id,row.booked_on.isoformat(),str(amount),row.currency.upper(),row.merchant.strip(),row.category.strip() or "Uncategorized",row.description,row.source,row.external_id,self.fingerprint(row),batch_id))
            return True
        except sqlite3.IntegrityError:
            return False

    def add_category(self,user_id:int,name:str)->None:
        name=name.strip()
        if not name: raise ValueError("Category name cannot be empty")
        with self._db() as db: db.execute("INSERT OR IGNORE INTO categories(user_id,name) VALUES(?,?)",(user_id,name))

    def set_budget(self,user_id:int,month:str,category:str,amount:Decimal,currency:str)->None:
        try: datetime.strptime(month,"%Y-%m")
        except ValueError as exc: raise ValueError("Month must use YYYY-MM") from exc
        amt=money(amount,currency)
        with self._db() as db:
            db.execute("INSERT INTO monthly_budgets VALUES(?,?,?,?,?) ON CONFLICT(user_id,month,category,currency) DO UPDATE SET amount=excluded.amount",(user_id,month,category,str(amt),currency.upper()))

    def month_report(self,user_id:int,month:str)->dict:
        with self._db() as db:
            rows=list(db.execute("SELECT category,currency,amount FROM transactions WHERE user_id=? AND substr(booked_on,1,7)=? ORDER BY category",(user_id,month)))
            budgets=list(db.execute("SELECT category,currency,amount FROM monthly_budgets WHERE user_id=? AND month=?",(user_id,month)))
        totals={}
        for row in rows:
            key=(row["category"],row["currency"])
            spent,count=totals.get(key,(Decimal("0.00"),0)); totals[key]=(spent+Decimal(row["amount"]),count+1)
        report=[]
        for (category,currency),(spent,count) in sorted(totals.items()):
            budget=next((Decimal(x["amount"]) for x in budgets if x["category"]==category and x["currency"]==currency),None)
            report.append({"category":category,"currency":currency,"spent":spent,"count":count,"budget":budget,"remaining":budget-spent if budget is not None else None})
        return {"month":month,"items":report,"budget_categories_without_spending":[{"category":b["category"],"currency":b["currency"],"budget":Decimal(b["amount"])} for b in budgets if not any(i["category"]==b["category"] and i["currency"]==b["currency"] for i in report)]}

    def export_csv(self,user_id:int)->str:
        with self._db() as db: rows=list(db.execute("SELECT booked_on,amount,currency,merchant,category,description,source,external_id FROM transactions WHERE user_id=? ORDER BY booked_on,id",(user_id,)))
        out=io.StringIO(); writer=csv.writer(out); writer.writerow(["date","amount","currency","merchant","category","description","source","external_id"])
        writer.writerows([tuple(row) for row in rows]); return out.getvalue()

    def delete_user(self,user_id:int)->None:
        with self._db() as db:
            db.execute("DELETE FROM transactions WHERE user_id=?",(user_id,)); db.execute("DELETE FROM categories WHERE user_id=?",(user_id,)); db.execute("DELETE FROM monthly_budgets WHERE user_id=?",(user_id,)); db.execute("DELETE FROM recurring_rules WHERE user_id=?",(user_id,)); db.execute("DELETE FROM import_batches WHERE user_id=?",(user_id,))


@dataclass
class ImportPreview:
    rows:list[TransactionInput]
    errors:list[str]
    duplicates:int
    file_hash:str


def parse_statement(data:bytes, mapping:dict[str,str], filename:str)->ImportPreview:
    digest=hashlib.sha256(data).hexdigest(); parsed=[]; errors=[]
    try:
        if filename.lower().endswith((".xlsx",".xlsm")):
            from openpyxl import load_workbook
            sheet=load_workbook(io.BytesIO(data),read_only=True,data_only=True).active
            all_rows=sheet.iter_rows(values_only=True); headers=[str(x or "").strip() for x in next(all_rows)]
            records=[dict(zip(headers,r)) for r in all_rows]
        else:
            text=data.decode("utf-8-sig")
            sample=text[:4096]; dialect=csv.Sniffer().sniff(sample,delimiters=",;\t")
            records=list(csv.DictReader(io.StringIO(text),dialect=dialect))
    except (OSError, ValueError, TypeError, UnicodeError, csv.Error, StopIteration, zipfile.BadZipFile, InvalidFileException) as exc:
        return ImportPreview([], [f"Cannot read file: {type(exc).__name__}"],0,digest)
    required={"date","amount","merchant"}
    if not required.issubset(mapping):
        return ImportPreview([], ["Mapping must include date, amount and merchant"],0,digest)
    for line,record in enumerate(records,start=2):
        try:
            values={key:record.get(column) for key,column in mapping.items()}
            day=date.fromisoformat(str(values["date"]).strip()[:10])
            curr=str(values.get("currency") or "RUB").upper().strip()
            amt=money(str(values["amount"]).replace(" ","").replace(",","."),curr)
            merchant=str(values["merchant"] or "").strip()
            if not merchant: raise ValueError("merchant is empty")
            parsed.append(TransactionInput(day,amt,curr,merchant,str(values.get("category") or "Uncategorized").strip(),str(values.get("description") or "").strip(),"import",str(values.get("external_id") or "").strip() or None))
        except (ValueError,InvalidOperation,TypeError) as exc: errors.append(f"Row {line}: {exc}")
    seen=set(); unique=[]; duplicates=0
    for row in parsed:
        key=Ledger.fingerprint(row)
        if key in seen: duplicates+=1
        else: seen.add(key); unique.append(row)
    return ImportPreview(unique,errors,duplicates,digest)


def commit_import(ledger:Ledger,user_id:int,preview:ImportPreview)->dict:
    """Commit only after explicit caller confirmation; batch record makes reruns visible/idempotent."""
    imported=duplicates=0
    with ledger._db() as db:
        try: cur=db.execute("INSERT INTO import_batches(user_id,file_hash) VALUES(?,?)",(user_id,preview.file_hash))
        except sqlite3.IntegrityError: return {"batch_id":None,"imported":0,"duplicates":len(preview.rows),"errors":preview.errors,"already_imported":True}
        batch_id=int(cur.lastrowid)
        for row in preview.rows:
            if not row.merchant.strip(): raise ValueError("Merchant cannot be empty")
            try:
                db.execute("INSERT INTO transactions(user_id,booked_on,amount,currency,merchant,category,description,source,external_id,fingerprint,import_batch_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id,row.booked_on.isoformat(),str(money(row.amount,row.currency)),row.currency.upper(),row.merchant.strip(),row.category.strip() or "Uncategorized",row.description,row.source,row.external_id,ledger.fingerprint(row),batch_id))
                imported+=1
            except sqlite3.IntegrityError: duplicates+=1
    return {"batch_id":batch_id,"imported":imported,"duplicates":duplicates+preview.duplicates,"errors":preview.errors,"already_imported":False}


class CbrRates:
    """Public Bank of Russia XML daily/dynamic rate adapter with bounded network timeout."""
    BASE="https://www.cbr.ru/scripts/"
    def __init__(self,transport:Callable[[str,float],bytes]|None=None,timeout:float=5.0):
        self.transport=transport or self._get; self.timeout=timeout; self.cache={}
    @staticmethod
    def _get(url:str,timeout:float)->bytes:
        request=urllib.request.Request(url,headers={"User-Agent":"MoneyRadar/0.1 (+personal local tool)"})
        with urllib.request.urlopen(request,timeout=timeout) as response: return response.read(2_000_000)
    def rate(self,currency:str,on_date:date|None=None)->Decimal:
        code=currency.upper(); day=on_date or date.today(); key=(code,day)
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("Currency must be a three-letter ISO code")
        if code=="RUB": return Decimal(1)
        if key in self.cache: return self.cache[key]
        url=f"{self.BASE}XML_daily.asp?date_req={day:%d/%m/%Y}"
        try: payload=self.transport(url,self.timeout)
        except (TimeoutError,urllib.error.URLError) as exc: raise RuntimeError("CBR rates are temporarily unavailable; retry later") from exc
        root=ET.fromstring(payload)
        if on_date:
            ref=next((n.attrib.get("ID") for n in root.findall("Valute") if n.findtext("CharCode")==code),None)
            if ref is None: raise ValueError(f"Currency {code} not found in CBR rates for {day}")
            url=f"{self.BASE}XML_dynamic.asp?date_req1={day:%d/%m/%Y}&date_req2={day:%d/%m/%Y}&VAL_NM_RQ={ref}"
            try: payload=self.transport(url,self.timeout)
            except (TimeoutError,urllib.error.URLError) as exc: raise RuntimeError("CBR rates are temporarily unavailable; retry later") from exc
            root=ET.fromstring(payload)
            vals=root.findall("Record")
            if not vals: raise ValueError(f"No CBR rate available for {code} on {day}")
            node=vals[-1]; nominal=Decimal(node.findtext("Nominal","1")); value=Decimal(node.findtext("Value","0").replace(",",".")); result=value/nominal
        else:
            node=next((n for n in root.findall("Valute") if n.findtext("CharCode")==code),None)
            if node is None: raise ValueError(f"Currency {code} not found in CBR daily rates")
            result=Decimal(node.findtext("Value","0").replace(",","."))/Decimal(node.findtext("Nominal","1"))
        self.cache[key]=result; return result
