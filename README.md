# IPO Intelligence Dashboard

A personal dashboard for Indian mainboard and SME IPOs. It collects subscription, listing and grey
market premium (GMP) data on its own schedule, works out your allotment odds and expected value, and
sends Telegram alerts. Everything runs by itself once it is started; all times are IST.

> The original single-page calculator is still in [`index.html`](index.html) and live at
> <https://jeevanba273.github.io/IPO-ALLOTMENT-PROBABILITY-CALCULATOR/>.

## Start it

**Windows:** double-click **`Start IPO Dashboard.cmd`**. It starts the local database and the app, then
opens <http://127.0.0.1:8000>. Keep its window open (minimised is fine); closing it stops the schedule.

**Anywhere else:** `ipo-intel serve --open` (after [setup](#setup-from-scratch)), or deploy it
([Railway](#deploy-to-railway)) so it runs around the clock.

## What's in it

| Page | What it shows |
|---|---|
| **Market** | Open IPOs as cards (closing countdown, minimum bid and its category, subscription by category, GMP with confidence), closing today, waiting to list, opening soon, recent listings |
| **Calendar** | Month view and the next three weeks: bidding opens and closes, allotment and listing days |
| **IPO page** | Timeline, key facts, day-wise subscription chart, projected final subscription, GMP chart, the allotment calculator, registrar and allotment link, and manual fixes (GMP, symbol, RHP reserved shares) |
| **Calculator** | "If I apply N lots from M PANs": odds now and at the projected final subscription, expected shares, expected value at the GMP |
| **Listings** | Listing gains by board and period, subscription against listing gain, flagged outliers |
| **Calibration** | How far GMP was from the actual listing gain, by board, and the history behind the projections |
| **Compare** | Up to three IPOs side by side |
| **Health** | Both API quotas, every job's last and next run, recent API calls |

The stage of every IPO (upcoming, open, closed, listed) comes from its dates, never from a source's
status text.

## Data sources and budgets

| Source | Used for | Limit and protection |
|---|---|---|
| **IndianAPI** (`dev.indianapi.in`) | IPO lists, details, day-wise subscription, listing gains, symbols | 10,000 requests per key, **shared with any other app on the same key**. Below 1,000 left the history backfill pauses; below 300 only open-IPO data is fetched and a Telegram warning goes out. `GET /usage` is free and checked daily and at startup. |
| **ipoalerts** (`api.ipoalerts.in`) | GMP history only (`GET /ipos/{symbol}/gmp`) | Plan: 25 calls a day and 750 a month. Our caps: **20 a day, 700 a month**, the backfill at most 8 a day. Each call reserves budget first; nothing is retried; a 429 pauses GMP until 00:00 IST. Only the schedule spends these calls: browsing the dashboard never does. |
| **Telegram** | Alerts | Each alert is sent once (`alerts_log`). |

Every external call goes through one client that applies a timeout, logs a `source_runs` row and keeps
keys out of logs. The browser only talks to this app; it never calls an external API.

## The schedule (IST)

| Job | When | What |
|---|---|---|
| `usage` | 07:00, and at startup | IndianAPI quota |
| `master` | 02:45, 08:45, 14:45, 20:45 | IPO lists (upcoming, open, closed, listed) |
| `open-details` | weekdays 09:00–17:00 every 2 h, otherwise every 6 h | Subscription of open IPOs, and the final day of just-closed ones |
| `listed` | 20:00 | Listing prices and gains, symbols |
| `gmp-live` | 10:30 (every open and not-yet-listed IPO), 15:30 (open mainboard again) | GMP |
| `gmp-backfill` | 21:00 | GMP history of recently listed IPOs, with what the live polls left |
| `alerts` | 07:10, 09:05, 12:05, 17:05, 18:05, 20:20, and a minute after startup | Telegram |
| `backfill` | by hand (Health page) | Day-wise history of up to 50 listed IPOs, for projections |

Only one copy of the app runs the schedule against a database (a Postgres advisory lock); a second
copy still serves the dashboard.

## Telegram alerts

| Alert | When |
|---|---|
| Opens tomorrow | from 18:00 the day before bidding opens |
| Closes today | 12:00–16:30 on the last bidding day, with subscription, GMP and your odds |
| Allotment day | from 17:00, with the registrar's allotment link |
| Lists today | 09:00–15:30 on listing day: the last GMP |
| Listing result | once the listing price is in: actual gain against the GMP-implied gain |
| Quota warnings | IndianAPI below 1,000 or 300 left (once a month each), ipoalerts paused or at 90% of the month |

Setup (once):
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...`, then send your new bot any message.
3. Run `ipo-intel telegram chats` and copy the `TELEGRAM_CHAT_ID=...` line into `.env`.
4. Run `ipo-intel telegram test`, then restart the dashboard.

## Setup from scratch

Needs Python 3.11, Node 22 and Postgres 16 (or nothing extra on Windows: the dev extras include a
bundled Postgres that `ipo-intel serve` starts for you).

```powershell
python -m venv .venv                        # or: python -m uv venv --python 3.11 .venv
.venv\Scripts\python -m pip install -e "backend[dev]"
cd frontend; npm ci; npm run build; cd ..
copy .env.example .env                      # then fill in the keys
cd backend; ..\.venv\Scripts\ipo-intel serve --open
```

Migrations run on startup.

## Environment variables

| Variable | Needed | Meaning |
|---|---|---|
| `INDIANAPI_KEY` | yes | IndianAPI key (`X-API-Key`) |
| `DATABASE_URL` | yes | Postgres URL. Locally `postgresql://postgres@127.0.0.1:54329/ipo_intel` |
| `IPOALERTS_KEY` | for GMP | ipoalerts key (`x-api-key`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for alerts | See [Telegram alerts](#telegram-alerts) |
| `DASHBOARD_PASSWORD` | when online | Sign-in (user `admin` unless `DASHBOARD_USER` is set). The app refuses to serve beyond this machine without it. |
| `DASHBOARD_URL` | no | Public address, for links in alerts |
| `IPOALERTS_DAILY_CAP`, `IPOALERTS_MONTHLY_CAP`, `IPOALERTS_BACKFILL_DAILY_CAP` | no | GMP budget (default 12 / 400 / 8; must stay below 25 / 750) |
| `INDIANAPI_PAUSE_BACKFILL_BELOW`, `INDIANAPI_ESSENTIAL_ONLY_BELOW` | no | Quota thresholds (1000 / 300) |
| `INDIANAPI_BACKFILL_LIMIT`, `INDIANAPI_BACKFILL_PACE_SECONDS` | no | History backfill size and spacing (50 / 3 s) |
| `DORMANT_AFTER_DAYS`, `HTTP_*`, `LOG_LEVEL`, `RULES_PATH`, `MIGRATIONS_DIR` | no | See `.env.example` |

Secrets are read only from the environment (or `.env`), never printed or logged. `ipo-intel config`
shows which are set without showing them.

## Editing the probability rules

The engine's assumptions are in [`config/rules_v1.yaml`](config/rules_v1.yaml). Edit a number, save,
and restart the dashboard. Every value is checked when the file loads; a bad one stops the app with a
message that names it.

| Key | Default | Meaning |
|---|---|---|
| `categories.retail_max_rs` | 200000 | Applications up to this are retail |
| `categories.snii_max_rs` | 1000000 | Above retail and up to this is small NII; above it, big NII |
| `categories.nii_split` | 0.3333 / 0.6667 | sNII / bNII share of the NII reservation (must add up to 1) |
| `retail.avg_min_bids_per_applicant` | mainboard 1.5, SME 1.0 | Average retail application in minimum bids. Retail odds run from 1/times (everyone bid the minimum) up to this / times |
| `nii.method` | proportionate | `proportionate`: expected shares = applied / times, P(at least 1 lot) = min(1, expected lots). `sebi_2022`: SEBI's rule since April 2022: the minimum NII bid (smallest lots above ₹2 lakh for sNII, above ₹10 lakh for bNII) by lottery, then leftover shares proportionately; the chance of any allotment is lower |
| `nii.avg_min_bids_per_applicant` | sNII 1.5, bNII 3.0 | `sebi_2022` only: average NII application in minimum NII bids (the high end of the odds) |
| `ev.gmp_multipliers` | 0.5, 0.75, 1.0 | Expected value is shown at these fractions of the GMP (GMP tends to run ahead of listings) |
| `projection.min_samples` | 8 | Past listings needed before a final subscription is projected ("not enough history" below) |
| `projection.min_base_times` | 0.05 | A day's subscription below this is too small to scale from |

How the odds work: retail is a lottery for the minimum bid, so its chance is a range, and 100% when
retail is not oversubscribed. NII is proportionate. Several PANs combine as 1 − (1 − p)^n. The projected
final subscription multiplies today's figure by the day-N-to-final multipliers of past listings (same
board, category and day): min, median and max, with odds quoted on the middle half. GMP confidence is
HIGH only for a mainboard IPO with 3+ GMP points in 48 h and the latest under 24 h old; SME is always
LOW, and LOW confidence gives a range with no single expected-value figure. Reserved shares come from
IndianAPI with a "verify against the RHP" warning until you type the RHP figures on the IPO page.

## Deploy to Railway

The repo has a `Dockerfile` (it builds the dashboard and the backend into one image) and a
`railway.json` (one replica, health check on `/healthz`).

1. Push the repo to GitHub, then in Railway: **New project → Deploy from GitHub repo**.
2. **New → Database → PostgreSQL**. In the app service's **Variables**, add
   `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Add `INDIANAPI_KEY`, `IPOALERTS_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DASHBOARD_PASSWORD`,
   and the caps you want (`IPOALERTS_DAILY_CAP=20`, `IPOALERTS_MONTHLY_CAP=700`).
4. **Settings → Networking → Generate domain**, then set `DASHBOARD_URL` to it.

Once it runs online, **stop the local copy**. The API budgets belong to the keys, but each database
keeps its own count, so two copies on two databases would spend each key twice as fast.

## Command line (optional)

Run from `backend/` with `..\.venv\Scripts\ipo-intel <command>`. The dashboard does all of this on its
own; these are for checking things.

| Command | Does |
|---|---|
| `serve [--open] [--host --port]` | The dashboard and its schedule |
| `ipos`, `ipo <id>`, `odds <id> [--lots --apps --gmp]` | The IPO list, one IPO, the calculator |
| `gmp show/set/clear <id>`, `symbol set/clear`, `reservation show/set/clear` | GMP and manual fixes |
| `jobs`, `runs`, `budget`, `multipliers`, `alerts preview` | What ran, API calls, GMP budget, projection history, due alerts |
| `run <job>` | Run one job now (`gmp-*` jobs need `--spend-budget`) |
| `telegram chats/test`, `localdb start/stop/status`, `migrate`, `config` | Setup |

## Development

```powershell
cd backend; ..\.venv\Scripts\python -m pytest     # 350 tests; no network, a throwaway Postgres
cd frontend; npm run dev                           # Vite on :5173, proxying /api to :8000
```

```
backend/ipo_intel/   config, db, migrate, budget, clients/ (the one HTTP wrapper), indianapi/, ipoalerts/,
                     ingest/ (jobs, runner, quota), engine/ (pure maths), analysis, calibration, alerts,
                     api/ (FastAPI), scheduler, cli
backend/tests/       unit and end-to-end tests, fixtures (hand-made and recorded live responses)
frontend/src/        React + Vite + Recharts dashboard
migrations/          plain SQL, applied in order and checksummed
config/rules_v1.yaml the probability engine's assumptions
docs/                IndianAPI reference, with the IPO endpoints checked against live responses
```

Privacy: no signed document links (RHP/DRHP) and no registrar contact details are stored, only the
registrar's name and its allotment-status page.
