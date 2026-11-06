# Forosh Config Bot

A Telegram bot for selling and managing 3X-UI VPN subscriptions. Users browse plans, place orders, and upload payment proof; admins review orders and provision accounts directly on the panel.

## Features

- **Plan catalog** — plans are defined in `plans.json` and shown as inline menus.
- **Order flow** — users pick a plan, upload a payment screenshot, and get a pending order.
- **Admin approval** — admins approve/reject orders from inside the chat; approved orders get a subscription link and QR code.
- **Panel management commands** — check server status, inbounds, clients, nodes, hosts, subscription links, online clients, and Xray status.
- **Test orders** — admins can create test accounts (no payment).
- **Multi-language** — UI strings are externalized in `translations.json`; users can switch language with `/lang`.
- **SQLite storage** — orders and user settings are persisted in `bot.db`.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Start the bot and show the main menu |
| `/help` | Show help |
| `/buy` | Browse available plans |
| `/lang` | Change language |
| `/status` | Server status (admin) |
| `/inbounds` | List inbounds (admin) |
| `/clients` | List clients (admin) |
| `/nodes` | List nodes (admin) |
| `/hosts` | List hosts (admin) |
| `/links` | Get all subscription links (admin) |
| `/online` | Show online clients (admin) |
| `/xray` | Xray status (admin) |
| `/settings` | Bot settings (admin) |

## Requirements

- Python 3.10+
- A [3X-UI](https://github.com/MHSanaei/3x-ui) panel with API access (or compatible API)

## Installation

```bash
git clone https://github.com/mmdMadi/Forosh_Config_Bot.git
cd Forosh_Config_Bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_USER_IDS` | Comma-separated Telegram user IDs of admins |
| `PANEL_URL` | 3X-UI panel base URL (e.g. `https://panel.example.com:2087`) |
| `PANEL_API_PATH` | Panel API path (keep secret) |
| `PANEL_USERNAME` / `PANEL_PASSWORD` | Panel login credentials |
| `PANEL_API_TOKEN` | Optional panel API token |
| `PANEL_SUB_PORT` | Subscription link port (usually `2096`) |

## Running

```bash
python bot.py
```

## Tests

```bash
pytest
```

## Project structure

```
bot.py             Telegram bot logic and handlers
config.py          Loads settings from environment / .env
db.py              SQLite storage (orders, user settings)
panel_api.py       3X-UI panel API client
plans.json         Plan definitions
translations.json  UI strings (i18n)
tests/             pytest suite
```

## License

MIT
