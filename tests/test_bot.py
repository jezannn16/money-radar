import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from money_radar.bot import create_app, parse_expense_input
from money_radar.core import Ledger


def test_free_text_expense_parser_validates_and_defaults_currency():
    row=parse_expense_input("кофе 350 RUB",datetime(2026,10,30).date())
    assert (row.merchant,row.amount,row.currency,row.booked_on)==("кофе",350,"RUB",datetime(2026,10,30).date())
    assert parse_expense_input("такси 780").currency=="RUB"
    with pytest.raises(ValueError):parse_expense_input("непонятный текст")


def test_plain_text_expense_requires_confirmation_and_preview_owner(tmp_path):
    async def scenario():
        ledger=Ledger(tmp_path/"telegram.sqlite3")
        bot,dispatcher=create_app("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",{51,52},ledger)
        message=Message(message_id=7,date=datetime.now(UTC),chat=Chat(id=51,type="private"),from_user=User(id=51,is_bot=False,first_name="Test"),text="кофе 350 RUB")
        try:
            with patch("aiogram.client.bot.Bot.__call__",new_callable=AsyncMock) as telegram:
                await dispatcher.feed_update(bot,Update(update_id=1,message=message))
                assert ledger.filter_transactions(51)==[]
                preview=telegram.await_args.args[0]
                assert "350.00 RUB" in preview.text and "кофе" in preview.text
                confirm_data=preview.reply_markup.inline_keyboard[0][0].callback_data

                unauthorized=CallbackQuery(id="cb-other",from_user=User(id=52,is_bot=False,first_name="Other"),chat_instance="chat",message=message,data=confirm_data)
                await dispatcher.feed_update(bot,Update(update_id=2,callback_query=unauthorized))
                assert ledger.filter_transactions(51)==[] and ledger.filter_transactions(52)==[]

                authorized=CallbackQuery(id="cb-owner",from_user=User(id=51,is_bot=False,first_name="Test"),chat_instance="chat",message=message,data=confirm_data)
                await dispatcher.feed_update(bot,Update(update_id=3,callback_query=authorized))
            rows=ledger.filter_transactions(51)
            assert len(rows)==1 and rows[0]["merchant"]=="кофе" and rows[0]["amount"]=="350.00"
            assert ledger.filter_transactions(52)==[]
        finally:
            await bot.session.close()

    asyncio.run(scenario())


def test_group_chat_cannot_read_ledger_or_confirm_a_preview(tmp_path):
    async def scenario():
        ledger=Ledger(tmp_path/"group.sqlite3")
        ledger.add(51,parse_expense_input("кофе 350 RUB"))
        bot,dispatcher=create_app("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",{51},ledger)
        private=Message(message_id=1,date=datetime.now(UTC),chat=Chat(id=51,type="private"),from_user=User(id=51,is_bot=False,first_name="Owner"),text="такси 780")
        try:
            with patch("aiogram.client.bot.Bot.__call__",new_callable=AsyncMock) as telegram:
                await dispatcher.feed_update(bot,Update(update_id=1,message=private))
                preview=telegram.await_args.args[0]
                callback_data=preview.reply_markup.inline_keyboard[0][0].callback_data
                cancel_data=preview.reply_markup.inline_keyboard[0][1].callback_data
                group_message=Message(message_id=2,date=datetime.now(UTC),chat=Chat(id=-100,type="supergroup"),from_user=private.from_user,text="/month 2026-10")
                await dispatcher.feed_update(bot,Update(update_id=2,message=group_message))
                assert "только в личном чате" in telegram.await_args.args[0].text
                group_callback=CallbackQuery(id="group-confirm",from_user=private.from_user,chat_instance="group",message=Message(message_id=3,date=datetime.now(UTC),chat=group_message.chat,from_user=private.from_user,text="preview"),data=callback_data)
                await dispatcher.feed_update(bot,Update(update_id=3,callback_query=group_callback))
                group_cancel=CallbackQuery(id="group-cancel",from_user=private.from_user,chat_instance="group",message=group_callback.message,data=cancel_data)
                await dispatcher.feed_update(bot,Update(update_id=4,callback_query=group_cancel))
                assert "Доступ закрыт" in telegram.await_args.args[0].text
                assert len(ledger.filter_transactions(51))==1
                private_confirm=CallbackQuery(id="private-confirm",from_user=private.from_user,chat_instance="private",message=private,data=callback_data)
                await dispatcher.feed_update(bot,Update(update_id=5,callback_query=private_confirm))
                assert len(ledger.filter_transactions(51))==2
        finally:
            await bot.session.close()

    asyncio.run(scenario())
