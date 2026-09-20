# Bank Statement Exporter

A Streamlit app that turns **Monzo** and **Revolut** transactions into a tidy spreadsheet — categorised, converted to GBP, and editable before you download it as **Excel** or **CSV**.

No account or API key is needed to try it: upload a Revolut statement and download a clean file.

## What it does

- Parses a Revolut CSV export, or pulls transactions straight from Monzo via OAuth
- Converts non-GBP amounts using historical ECB rates, keeping the original amount alongside
- Filters out pot transfers, currency exchanges, internal savings movements and declined payments
- Folds Revolut fees into the amount they were charged on
- Maps Monzo's categories onto your own, and lets you fix anything in an editable table
- Labels rows as expense, income or bill, and says why anything was skipped
- Merges both sources chronologically and exports to `.xlsx` or `.csv`

## Quick start

```bash
git clone https://github.com/domeist/bank-statement-exporter.git
cd bank-statement-exporter
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Export a monthly CSV from the Revolut app (*Accounts → Statement → CSV*), upload it, review the table, and download your spreadsheet.

**Requires Python 3.10+.**

## Optional: import from Monzo

Connecting to Monzo pulls a whole month of transactions directly, with no CSV in the middle.

1. Create a client at [developers.monzo.com](https://developers.monzo.com/) (type: **Confidential**)
2. Set its redirect URI to exactly `http://localhost:8501/` — trailing slash included, matching Streamlit's default port
3. Copy the example config and fill in the two values:

```bash
cp config.example.json config.json
```

```json
{
  "monzo_client_id": "your-monzo-client-id",
  "monzo_client_secret": "your-monzo-client-secret"
}
```

Restart the app, click **Connect to Monzo**, approve the notification on your phone, then pick a month and fetch. The token is cached in `config/`, so you only do this once per session.

**Monzo only serves transactions older than 90 days for five minutes after you authenticate.** To import an older month, reconnect and fetch straight away.

`config.json` and `config/` are gitignored and never committed.

## The exported file

| Column | Notes |
|---|---|
| Date | Real date, `YYYY-MM-DD` |
| Description | Merchant name where Monzo provides one |
| Category | Yours to set; blank where nothing was matched |
| Amount | Always positive — `Type` says which direction |
| Type | `Expense`, `Income` or `Bill` |
| Source | `Monzo` or `Revolut` |
| Trip | Optional tag you set per source |

## Configuration

Every key in `config.json` is optional. Paths are resolved from the repo root.

| Key | Default | What it does |
|---|---|---|
| `monzo_client_id` / `monzo_client_secret` | — | Enables the Monzo section |
| `monzo_token_path` | `config/monzo-token.json` | Where the cached Monzo token is kept |
| `redirect_uri` | `http://localhost:8501/` | Must match the URI registered with Monzo |
| `categories` | Groceries, Food & Drinks, … | Options in the Category dropdown |
| `monzo_category_map` | see below | Maps Monzo API categories to your category names |

Default Monzo category mapping:

| Monzo | Yours |
|---|---|
| groceries | Groceries |
| eating_out | Food & Drinks |
| shopping | General Shopping |
| transport | Transportation |
| entertainment | Entertainment |
| gifts | Gifts |
| holidays | Holidays |
| bills | Labelled as a bill |
| transfers | Skipped |

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the parsers, the export, config handling, and end-to-end runs of the Streamlit app with the Monzo API stubbed. Nothing touches the network.

## Licence

MIT — see [LICENSE](LICENSE).
