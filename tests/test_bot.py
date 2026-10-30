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
