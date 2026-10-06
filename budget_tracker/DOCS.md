# Budget Tracker

A private household budget tracker for two people (or one): receipts and card statements read by a local AI model,
credit cards and installments, recurring income and bills, budgets, a 14-month plan, savings insights, investments
and an optional Telegram bot.

## First run

Open the **Budget** panel in the sidebar. A short setup asks for:

- **Region** — *None* for a generic setup, or a region pack. *Turkey* adds Central Bank of Türkiye exchange rates,
  Turkish gold coins (quarter, half, full …) priced from the Grand Bazaar, Borsa Istanbul tickers and parsing of
  Turkish bank statements.
- **Number and date format** — e.g. `en-US` (1,234.56 · Oct 6, 2026) or `de-DE` (1.234,56 · 6. Okt. 2026).
- **Base currency** — all totals, budgets and plans are shown in it. Transactions can be in other enabled
  currencies and are converted with daily European Central Bank rates.
- **Features** — investments on/off; document reading and Telegram status.

Everything can be changed later in **More → Settings**.

## Options

| Option | Description |
|---|---|
| `ollama_url` | Address of an Ollama server on your network that reads receipts and statements, e.g. `http://192.168.1.10:11434`. Empty = manual entry only. |
| `ollama_model` | A vision model. Default `qwen3-vl:8b-instruct`. |
| `allowed_users` | Home Assistant usernames (or display names) that may open the app. Empty = all users. |
| `telegram_bot_token` | Optional Telegram bot token from @BotFather. |
| `telegram_auto_delete_hours` | Bot messages and your messages are deleted from Telegram after this many hours (0 = off, max 47). |
| `notify_services` | Notify services for reminders, e.g. `mobile_app_my_phone` (find them in Developer tools → Actions by searching `notify.`). |
| `notify_time` | Time of day (HH:MM) for daily reminders. |
| `timezone` | IANA time zone. Empty = Home Assistant's time zone. |

## Reading documents with a local model (Ollama)

Documents are read by [Ollama](https://ollama.com) running on a computer in your home — nothing is sent to the cloud.

1. Install Ollama and run `ollama pull qwen3-vl:8b-instruct` (≈6 GB; an Apple Silicon Mac or a PC with a GPU works well).
2. Make it reachable from Home Assistant: start it with `OLLAMA_HOST=0.0.0.0` (on macOS see
   `scripts/com.budgettracker.ollama.plist` in the repository).
3. Set `ollama_url` in the add-on configuration and restart the add-on.

Text PDFs (most bank statements) are parsed line by line with rules that understand US (`1,234.56`) and European
(`1.234,56`) number formats and common installment notations; the model only reads the header and suggests
categories. Photos, screenshots and scanned PDFs are read by the vision model. You always confirm a document before
it is saved; suspicious reads (totals that don't match, unreadable dates) are flagged.

If the computer is off, uploads wait with an error and can be retried later.

## Telegram bot (optional)

Create a bot with @BotFather, put the token in the options, then in the app go to **More → Telegram** and link each
person with the one-time code. Only linked household members can use the bot.

- `coffee 4.50`, `groceries 62.30 yesterday`, `taxi 18 cash` — quick expenses (category is guessed and learned).
- Send a receipt photo or a statement PDF — it is read and you confirm with a button.
- `/summary`, `/recent`, `/upcoming`, `/fund` — summaries; investment messages like `10g gold` or `AAPL 5 shares` go to Investments.
- Messages are deleted automatically after `telegram_auto_delete_hours`.

## Notifications

- Card due dates (3 days, 1 day before and on the day) and statement reminders the day after the closing date
- Recurring payments (the day before and on the day), raise / price-increase months
- Budget warnings at 80% and 100%
- Monday summary: what is left this month and next month at most
- Document read / failed

## Data and backups

All data is stored in SQLite under `/data` and included in Home Assistant backups. A daily snapshot is kept for
14 days in `/data/backups`.
