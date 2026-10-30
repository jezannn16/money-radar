"""Optional Telegram entry point. Financial writes require a callback confirmation."""
from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from .core import Ledger, TransactionInput, money


def main()->None:
    load_dotenv();token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    allowed={int(v) for v in os.getenv("ALLOWED_USER_IDS","").split(",") if v.strip().isdigit()}
    if not token or not allowed:raise SystemExit("Set TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS in .env")
    ledger=Ledger(os.getenv("DATABASE_PATH","money_radar.sqlite3"));bot=Bot(token);dp=Dispatcher()
    pending:dict[str,TransactionInput]={}

    async def authorize(message:Message)->bool:
        if not message.from_user or message.from_user.id not in allowed:
            await message.answer("Доступ закрыт. Добавьте свой Telegram ID в ALLOWED_USER_IDS.");return False
        return True

    @dp.message(CommandStart())
    async def start(message:Message)->None:
        if await authorize(message):await message.answer("Money Radar готов. /add coffee 350 RUB — проверить и подтвердить расход; /help — команды.")

    @dp.message(Command("help"))
    async def help_cmd(message:Message)->None:
        if await authorize(message):await message.answer("/add merchant amount [currency] — preview с кнопками подтверждения\n/month YYYY-MM — отчёт\n/unusual YYYY-MM — кандидаты на необычные расходы\n/category-add имя; /categories\n/filter from=YYYY-MM-DD to=YYYY-MM-DD category=Food currency=RUB merchant=cafe (все фильтры необязательны)\n/budget YYYY-MM category amount currency\n/recurring-add merchant category amount currency [day]\n/recurring-list, /recurring-run YYYY-MM, /recurring-disable ID\nЭкспорт/импорт выписок доступны через CLI.")

    @dp.message(Command("category-add"))
    async def category_add(message:Message)->None:
        if not await authorize(message):return
        name=(message.text or "").partition(" ")[2].strip()
        if not name:await message.answer("Формат: /category-add название");return
        try:ledger.add_category(message.from_user.id,name)
        except ValueError as exc:await message.answer(str(exc));return
        await message.answer("Категория сохранена.")

    @dp.message(Command("categories"))
    async def categories(message:Message)->None:
        if not await authorize(message):return
        names=ledger.categories(message.from_user.id)
        await message.answer("Категории:\n"+"\n".join(names) if names else "Категорий пока нет.")

    @dp.message(Command("filter"))
    async def transaction_filter(message:Message)->None:
        if not await authorize(message):return
        filters={}
        for part in (message.text or "").split()[1:]:
            if "=" not in part:await message.answer("Фильтры задаются как ключ=значение; используйте /help.");return
            key,value=part.split("=",1)
            if key not in {"from","to","category","currency","merchant"} or not value:await message.answer(f"Неизвестный или пустой фильтр: {key}");return
            filters[key]=value.replace("_"," ") if key in {"category","merchant"} else value
        try:rows=ledger.filter_transactions(message.from_user.id,date_from=filters.get("from"),date_to=filters.get("to"),category=filters.get("category"),currency=filters.get("currency"),merchant=filters.get("merchant"),limit=30)
        except ValueError as exc:await message.answer(str(exc));return
        if not rows:await message.answer("Операций по фильтру нет.");return
        await message.answer("Операции:\n"+"\n".join(f"{r['booked_on']} {r['merchant']}: {r['amount']} {r['currency']} — {r['category']}" for r in rows))

    @dp.message(Command("add"))
    async def add(message:Message)->None:
        if not await authorize(message):return
        text=(message.text or "").partition(" ")[2].strip()
        match=re.fullmatch(r"(.+?)\s+([0-9]+(?:[.,][0-9]{1,4})?)(?:\s+([A-Za-z]{3}))?",text)
        if not match:
            await message.answer("Формат: /add кофе 350 RUB. Сумму/валюту проверьте в preview.");return
        merchant,amount,currency=match.group(1),match.group(2).replace(",","."),(match.group(3) or "RUB").upper()
        try:row=TransactionInput(date.today(),money(amount,currency),currency,merchant)
        except ValueError as exc:await message.answer(f"Не удалось распознать расход: {exc}");return
        key=f"{message.from_user.id}:{message.message_id}";pending[key]=row
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подтвердить",callback_data=f"confirm:{key}"),InlineKeyboardButton(text="Отмена",callback_data=f"cancel:{key}")]])
        await message.answer(f"Проверьте расход:\nСумма: {row.amount} {row.currency}\nДата: {row.booked_on}\nМесто: {row.merchant}\nКатегория: {row.category}",reply_markup=kb)

    @dp.callback_query(F.data.startswith("confirm:"))
    async def confirm(call:CallbackQuery)->None:
        if not call.from_user or call.from_user.id not in allowed:await call.answer("Доступ закрыт",show_alert=True);return
        key=call.data.removeprefix("confirm:");row=pending.pop(key,None)
        if row is None:await call.answer("Preview истёк или уже обработан",show_alert=True);return
        saved=ledger.add(call.from_user.id,row)
        if call.message:await call.message.edit_text("Расход сохранён." if saved else "Похожая операция уже есть; дубль не добавлен.")
        await call.answer()

    @dp.callback_query(F.data.startswith("cancel:"))
    async def cancel(call:CallbackQuery)->None:
        if not call.from_user or call.from_user.id not in allowed:await call.answer("Доступ закрыт",show_alert=True);return
        pending.pop(call.data.removeprefix("cancel:"),None)
        if call.message:await call.message.edit_text("Добавление отменено.")
        await call.answer()

    @dp.message(Command("month"))
    async def month(message:Message)->None:
        if not await authorize(message):return
        value=(message.text or "").partition(" ")[2].strip()
        try:report=ledger.month_report(message.from_user.id,value)
        except ValueError:await message.answer("Укажите месяц YYYY-MM");return
        lines=[f"{x['category']}: {x['spent']} {x['currency']} ({x['count']})"+(f"; бюджет {x['budget']}, остаток {x['remaining']}" if x['budget'] is not None else "") for x in report["items"]]
        await message.answer(f"Отчёт {value}:\n"+("\n".join(lines) if lines else "операций нет"))

    @dp.message(Command("budget"))
    async def budget(message:Message)->None:
        if not await authorize(message):return
        parts=(message.text or "").split(maxsplit=4)
        if len(parts)!=5:await message.answer("Формат: /budget YYYY-MM category amount currency");return
        try:ledger.set_budget(message.from_user.id,parts[1],parts[2],Decimal(parts[3]),parts[4]);await message.answer("Бюджет сохранён.")
        except ValueError as exc:await message.answer(str(exc))

    @dp.message(Command("unusual"))
    async def unusual(message:Message)->None:
        if not await authorize(message):return
        month_value=(message.text or "").partition(" ")[2].strip()
        try:candidates=ledger.unusual_expenses(message.from_user.id,month_value)
        except ValueError as exc:await message.answer(str(exc));return
        if not candidates:
            await message.answer("Кандидатов нет. Для сравнения нужны минимум 5 предыдущих расходов в той же категории и валюте.")
            return
        lines=[f"{item['date']} {item['merchant']}: {item['amount']} {item['currency']} — медиана {item['baseline_median']} по {item['baseline_count']} прошлым расходам (порог ×{item['factor']})." for item in candidates[:15]]
        await message.answer("Возможные выбросы, проверьте вручную:\n"+"\n".join(lines))

    @dp.message(Command("recurring-add"))
    async def recurring_add(message:Message)->None:
        if not await authorize(message):return
        parts=(message.text or "").split(maxsplit=5)
        if len(parts) not in {5,6}:
            await message.answer("Формат: /recurring-add аренда Дом 50000 RUB [day 1..31]");return
        try:
            day=int(parts[5]) if len(parts)==6 else 1
            rule_id=ledger.add_recurring_rule(message.from_user.id,parts[1],parts[2],Decimal(parts[3]),parts[4],day)
        except (ValueError,ArithmeticError) as exc:await message.answer(f"Не удалось создать правило: {exc}");return
        await message.answer(f"Ежемесячное правило #{rule_id} создано. День проводки: {day} (последний день месяца используется для 29–31).")

    @dp.message(Command("recurring-list"))
    async def recurring_list(message:Message)->None:
        if not await authorize(message):return
        rows=ledger.recurring_rules(message.from_user.id)
        if not rows:await message.answer("Повторяющихся правил нет.");return
        await message.answer("Ежемесячные правила:\n"+"\n".join(f"#{r['id']} {r['merchant']} — {r['amount']} {r['currency']}, {r['category']}, день {r['day_of_month']}" for r in rows))

    @dp.message(Command("recurring-run"))
    async def recurring_run(message:Message)->None:
        if not await authorize(message):return
        period=(message.text or "").partition(" ")[2].strip()
        try:result=ledger.run_recurring(message.from_user.id,period)
        except ValueError as exc:await message.answer(str(exc));return
        await message.answer(f"Повторяющиеся расходы за {period}: создано {result['created']}, существовало/дубликаты {result['already_processed']+result['duplicates']}.")

    @dp.message(Command("recurring-disable"))
    async def recurring_disable(message:Message)->None:
        if not await authorize(message):return
        try:rule_id=int((message.text or "").partition(" ")[2].strip())
        except ValueError:await message.answer("Формат: /recurring-disable 12");return
        changed=ledger.disable_recurring_rule(message.from_user.id,rule_id)
        await message.answer("Правило отключено." if changed else "Активное правило с таким ID не найдено.")

    dp.run_polling(bot)


if __name__=="__main__":main()
