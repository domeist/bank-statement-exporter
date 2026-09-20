# bank-statement-exporter

A Streamlit app that turns Monzo and Revolut transactions into a spreadsheet, downloaded as Excel or CSV. Everything is previewed and editable before download.

## Project structure

```
bank-statement-exporter/
├── app.py                        # Streamlit page flow — Monzo, Revolut, download
├── config.json                   # Local config, all keys optional (gitignored)
├── config/                       # Cached Monzo token (gitignored)
├── bank_statement_exporter/
│   ├── config.py                 # Config loading + defaults
│   ├── export.py                 # Output: .xlsx / .csv
│   ├── model.py                  # Canonical transaction shape + ordering
│   ├── monzo_api.py              # Monzo OAuth + API client
│   ├── parser.py                 # Monzo transaction parsing
│   ├── revolut_parser.py         # Revolut CSV parsing + GBP conversion
│   └── ui.py                     # Shared Streamlit editors + download buttons
└── tests/
```

## Core shape

Parsers produce DataFrames with neutral columns (`Date`, `Amount`, `Category`,
`Description`) — real `date` objects and **always positive** amounts. The editors
in `ui.py` turn those into canonical transaction dicts (`model.transaction`):
`date, amount, description, category, trip, kind, source`.

`kind` is `expense`, `income` or `bill`; it carries the meaning that a sign would
otherwise imply, and becomes the `Type` column in the export. Keep output
formatting in the output module — never push it back into the parsers.

This repo previously wrote directly into a fixed Google Sheets layout. That was
removed deliberately (the layout was personal and blocked everyone else); the
download is the only output. Don't reintroduce a spreadsheet-specific format in
the parsers — add an output module instead.

## Optional integration

Monzo is optional: `cfg.monzo_enabled` (client id + secret) gates the section,
and placeholder values from `config.example.json` count as unset. `load_config`
only raises when `config.json` exists but cannot be read — a missing file is a
valid setup, and the app must stay fully usable without one.

## Monzo parsing

Monzo's `/transactions` returns declined payments alongside successful ones.
`is_declined` (a truthy `decline_reason`) must stay in every parse path — no
money moved, so counting them inflates spending.

## Revolut CSV parsing

- Only `COMPLETED` rows are processed
- The `Fee` column is charged on top of `Amount`; the parser folds it in
  (`amount - fee`) before currency conversion, and notes it in `Original`
- A row that cannot be read (bad date, missing amount) is reported in
  `filtered_out` with a `Reason` — never let one bad row abort the statement
- The `Type` column is upper case (`EXCHANGE`); `REVOLUT_SKIP_TYPES` is compared
  case-insensitively — do not switch it back to an exact match
- Revolut writes expenses negative; the parser flips them to positive
- A failed rate lookup sets `Rate Failed`; app.py surfaces the count and marks
  the row ⚠️ so a foreign amount is never passed off as GBP
- Rates are fetched once per currency via `prefetch_rates` (a date-range
  request), not once per row; `_rate_for` falls back to a single-date lookup
- Failures are cached only for `RATE_RETRY_SECONDS`, so one blip is not
  permanent for the life of the process

## SCA (Monzo Secure Customer Authentication)

After a fresh Monzo login, the app shows a blocking SCA screen. The "I've approved it"
button calls `get_accounts()` directly inside the handler — if it raises
`MonzoSCARequired`, the user is prompted to try again without resetting the session.
Do not move this API call outside the button handler.

Monzo only serves transactions older than 90 days for five minutes after auth; the
fetch error message says so.

## Output safety

- `export._safe_text` prefixes `= + - @ tab CR` with `'`: descriptions come from
  an uploaded statement and payment references are attacker-chosen, so an
  unescaped cell is a formula the spreadsheet will offer to execute
- A row whose FX conversion failed keeps `rate_failed`; `app.py` disables the
  download until the user confirms they corrected it, and the `Note` column
  carries the warning into the file. Never let a foreign amount sit unmarked in
  the Amount column

## Session and dates

- `get_accounts()` is cached in `session_state` (`_accounts`): Streamlit reruns
  the whole script on every widget interaction, so an uncached call hits a
  rate-limited, SCA-gated endpoint on every keystroke
- Only a 401/403 clears the Monzo token. A timeout or DNS failure must not,
  because re-auth costs the user access to transactions older than 90 days
- Monzo timestamps are UTC; `_created_at` converts to `cfg.timezone`
  (default `Europe/London`) so late-night BST spending gets the right date
- `parser.month_window` builds the fetch window in that same zone and converts
  to UTC. Both ends must use one zone: a UTC window with London dates pulls in
  the next month's first hour and drops this month's last
- Fetched transactions are stored with the `(year, month)` they were fetched
  for, and dropped on failure — stale rows under a new heading are silently
  wrong output
- `ZoneInfo` needs `tzdata` on Windows; it is a hard dependency for that reason
- Token and OAuth state files are written `0600` via `_write_private`

## Constraints

- `config.json` and `config/` are gitignored — never commit secrets
- Repo is LF-only via `.gitattributes`; don't reintroduce CRLF
- Python 3.10+ (PEP 604 `X | None` annotations are evaluated at runtime)
- `AppTest` cannot drive `st.data_editor`; assert on defaults and cover the rest
  with unit tests
