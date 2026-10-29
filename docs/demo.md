# Demo

Use only synthetic information.

1. Add a synthetic transaction, set a monthly category budget, and review the report.
2. Import the sample rows below first without `--confirm`. Observe a preview and row error; confirm in a second invocation.
3. Repeat the same import. It reports that the exact file batch has already been imported.
4. Export the user's rows, then test `delete-user` against a disposable database.

```csv
date,amount,currency,merchant,category
2026-10-02,350.00,RUB,Coffee shop,Food
2026-10-03,780.00,RUB,Taxi,Transport
invalid,nope,RUB,,
```

```sh
uv run money-radar import sample.csv --user 7 --map date=date --map amount=amount --map currency=currency --map merchant=merchant --map category=category
```

The CLI uses the actual database and never sends this file to an external service.
