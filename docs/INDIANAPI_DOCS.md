# IndianAPI — Complete Reference Documentation (v2.0.0)

> Copied from the AI POWERED STOCK ANALYSIS project. Sections that mention
> `price_service.py`, `indianStockService.ts` or tables like `price_history` describe that
> project, not this one. The **IPO** section (29–31) and **API Usage Stats** (38) were rewritten
> for the IPO Intelligence Dashboard with real responses captured on 01 Nov 2026. Where they
> disagree with the published OpenAPI examples (https://dev.indianapi.in/openapi.json), the live
> responses win. Scrubbed copies of those responses are in
> `backend/tests/fixtures/indianapi/recorded/`.
>
> **The 10,000-request hard limit belongs to the key, not to a project.** The live `/usage`
> showed thousands of calls from the stock-analysis app (`/stock`, `/historical_data`, ...) on the
> same key, so both projects draw from one quota.

## Overview

IndianAPI provides real-time and historical Indian stock market data. This document covers every available endpoint across both servers, authentication, request/response formats, and usage notes for the Portfolio Analytics Platform.

**API Version:** 2.0.0 (OpenAPI 3.1.0)

---

## Servers

| Server | URL | Role | Notes |
|--------|-----|------|-------|
| **Dev (Primary)** | `https://dev.indianapi.in` | All requests go here first | Premium/dedicated server, more endpoints, v2.0.0 |
| **Normal (Fallback)** | `https://stock.indianapi.in` | Used if dev server is down or rate-limited | v0.1.0, fewer endpoints |

**Fallback logic in `price_service.py`:**
```python
PRIMARY_BASE = "https://dev.indianapi.in"
FALLBACK_BASE = "https://stock.indianapi.in"
```
Try primary first. On 5xx or timeout, retry against fallback.

---

## Authentication

| Header | Value | Required |
|--------|-------|----------|
| `X-API-Key` | Your IndianAPI key | Yes, all endpoints |

```
GET /stock?name=Reliance
Headers:
  X-API-Key: YOUR_API_KEY_HERE
```

**Error on missing/invalid key:** 401 Unauthorized

> Note: The dev server uses `X-API-Key` (capital X). The fallback server uses `x-api-key` (lowercase). Our client should send `X-API-Key` which works for both.

---

## Rate Limits & Usage Tracking

Rate limits are not publicly documented, but the dev server provides a usage tracking endpoint:

```
GET /usage
```

**Recommendations:**
- Call `/usage` periodically to monitor consumption
- Implement retry with exponential backoff (start 1s, max 30s)
- Use **batch endpoints** (`/nse_stock_batch_live_price`) instead of per-symbol calls
- Cache aggressively in `price_history` / `instrument_metadata` tables
- Monitor for 429 (Too Many Requests) responses

---

## Static Data Files

The dev server hosts pre-built JSON files for all supported instruments:

| File | URL | Content |
|------|-----|---------|
| All Stocks | `https://dev.indianapi.in/static/all_stocks.json` | ~1,100+ stocks with BSE code, name, id, NSE code |
| All Mutual Funds | `https://dev.indianapi.in/static/all_mf.json` | All MF schemes by category |

**Stock list entry format:**
```json
{
    "bse-code": "532540",
    "name": "Tata Consultancy Services",
    "id": "S0003051",
    "nse-code": "TCS"
}
```

**Our usage:** Download once at setup, use `id` field for endpoints that need `stock_id`, use `nse-code` for batch price lookups.

---

## Endpoints — Complete Reference

### Category: Search & Discovery

---

#### 1. Get Stock Details

| Field | Value |
|-------|-------|
| Endpoint | `GET /stock` |
| Priority | **CRITICAL** — company metadata, current price, corporate actions, F&O data |

**Parameters (provide at least one):**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `name` | One of three | string | Company name, short name, or ticker (e.g., "Reliance", "TCS") |
| `symbol` | One of three | string | NSE/BSE symbol (e.g., "INFY", "500325") |
| `id` | One of three | string | IndianAPI stock ID (e.g., "S0003032") |

> **Dev server improvement:** Accepts `name`, `symbol`, or `id`. Fallback server only accepts `name`.

**Request:**
```
GET /stock?name=TCS
GET /stock?symbol=INFY
GET /stock?id=S0003032
```

**Response:**
```json
{
    "companyName": "Tata Consultancy Services",
    "industry": "Software & Programming",
    "companyProfile": {
        "companyDescription": "...",
        "mgIndustry": "Software & Programming",
        "isInId": "INE467B01029"
    },
    "currentPrice": {
        "BSE": 3890.50,
        "NSE": 3888.75
    },
    "stockTechnicalData": { "...": "technical indicators" },
    "percentChange": -0.45,
    "yearHigh": 4592.25,
    "yearLow": 3311.80,
    "financials": { "...": "financial data" },
    "keyMetrics": { "...": "PE, EPS, market cap etc." },
    "futureExpiryDates": ["2024-06-28", "2024-07-26"],
    "futureOverviewData": { "..." },
    "analystView": { "...": "buy/sell/hold ratings" },
    "recosBar": { "..." },
    "riskMeter": { "..." },
    "shareholding": { "...": "promoter, FII, DII breakdown" },
    "stockCorporateActionData": { "..." },
    "stockDetailsReusableData": { "..." },
    "recentNews": [{"headline": "...", "url": "..."}]
}
```

**Field mapping to our schema:**

| API Field | Our Table.Column | Usage |
|-----------|-----------------|-------|
| `companyName` | `instrument_metadata.company_name` | Display name |
| `industry` | `instrument_metadata.industry` | Sector analytics |
| `companyProfile.mgIndustry` | `instrument_metadata.industry` | More specific industry |
| `companyProfile.isInId` | `instrument_metadata.isin` | ISIN code |
| `currentPrice.NSE` | `price_history.close` | **Primary price** for valuation |
| `currentPrice.BSE` | — | Backup price source |
| `percentChange` | — | Dashboard display |
| `yearHigh` / `yearLow` | — | Holdings detail |
| `keyMetrics` | — | P/E, EPS display |
| `futureExpiryDates` | — | F&O expiry calendar |
| `futureOverviewData` | — | F&O context |
| `shareholding` | — | Optional display |
| `stockCorporateActionData` | `corporate_actions` table | Split/bonus/dividend detection |

**Notes:**
- Use NSE price as primary; BSE as fallback
- For bulk price updates, prefer `/nse_stock_batch_live_price` over calling `/stock` per symbol

---

#### 2. Industry Search

| Field | Value |
|-------|-------|
| Endpoint | `GET /industry_search` |
| Priority | **IMPORTANT** — populate `instrument_metadata` table |

**Parameters:**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `query` | Yes | string | Industry, sector, or company search term |

**Response:**
```json
[
    {
        "id": "S0003051",
        "commonName": "Tata Consultancy Services",
        "mgIndustry": "Software & Programming",
        "mgSector": "Technology",
        "stockType": "Equity",
        "exchangeCodeBse": "532540",
        "exchangeCodeNsi": "TCS",
        "bseRic": "TCS.BO",
        "nseRic": "TCS.NS",
        "activeStockTrends": {
            "shortTermTrends": "Bearish",
            "longTermTrends": "Moderately Bearish",
            "overallRating": "Moderately Bearish"
        }
    }
]
```

**Field mapping:**

| API Field | Our Table.Column |
|-----------|-----------------|
| `commonName` | `instrument_metadata.company_name` |
| `mgIndustry` | `instrument_metadata.industry` |
| `mgSector` | `instrument_metadata.sector` |
| `exchangeCodeBse` | `instrument_metadata.bse_code` |
| `exchangeCodeNsi` | `instrument_metadata.nse_symbol` |
| `bseRic` | yfinance BSE ticker (e.g., "TCS.BO") |
| `nseRic` | yfinance NSE ticker (e.g., "TCS.NS") |

---

#### 3. Mutual Fund Search

| Field | Value |
|-------|-------|
| Endpoint | `GET /mutual_fund_search` |
| Priority | Not needed (unless MF tracking added) |

**Parameters:** `query` (string, required)

---

### Category: Market Overview

---

#### 4. Market Indices *(NEW — dev server only)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /indices` |
| Priority | **IMPORTANT** — live Nifty 50, Sensex, sectoral indices for dashboard header |

**Parameters:**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `exchange` | No | enum: `NSE` / `BSE` | Filter by exchange. Omit for both. |
| `index_type` | No | enum: `POPULAR` / `SECTOR` | Filter by type. |

**Request:**
```
GET /indices
GET /indices?exchange=NSE
GET /indices?exchange=NSE&index_type=POPULAR
```

**Response:**
```json
{
    "indices": [
        {
            "name": "SENSEX",
            "price": "82814.71",
            "percentChange": "0.38",
            "netChange": "316.57",
            "tickerId": "I0001",
            "exchangeType": "BSE",
            "date": "2026-02-20",
            "time": "10:45:08"
        },
        {
            "name": "NIFTY 50",
            "price": "25571.25",
            "percentChange": "0.46",
            "netChange": "116.90",
            "tickerId": "I0002",
            "exchangeType": "NSI",
            "date": "2026-02-20",
            "time": "10:01:21"
        }
    ]
}
```

**Notes:**
- Values are **strings** — parse to float
- Provides **live/delayed** index values only, not historical
- For historical index data (needed for beta, benchmark charts), still use **yfinance**
- `exchangeType` uses "NSI" (not "NSE") for National Stock Exchange

**Our usage:**
- Display live market indices in dashboard header bar
- Show Nifty 50, Sensex, Bank Nifty at a glance
- NOT suitable for historical benchmark computation

---

#### 5. Trending Stocks

| Field | Value |
|-------|-------|
| Endpoint | `GET /trending` |
| Priority | Optional — market overview widget |

**Parameters:** None

**Response:**
```json
{
    "trending_stocks": {
        "top_gainers": [
            {
                "ticker_id": "S0003057",
                "company_name": "NTPC",
                "price": "382.75",
                "percent_change": "1.94",
                "net_change": "7.30",
                "bid": "0.00",
                "ask": "382.75",
                "high": "385.00",
                "low": "373.55",
                "open": "374.95",
                "low_circuit_limit": "337.95",
                "up_circuit_limit": "412.95",
                "volume": "20774556",
                "date": "2026-02-24",
                "time": "10:20:08",
                "close": "375.45",
                "bid_size": "0.00",
                "ask_size": "26537",
                "exchange_type": "NSI",
                "lot_size": "1.00",
                "overall_rating": "Bullish",
                "short_term_trends": "Bullish",
                "long_term_trends": "Bullish",
                "total_share_outstanding": "969.67",
                "year_low": "306.55",
                "year_high": "376.50",
                "ric": "NTPC.NS"
            }
        ],
        "top_losers": [ ... ]
    }
}
```

**Notes:** All values are strings. `total_share_outstanding` is in Crores. Data may be empty when market is closed.

---

#### 6. 52-Week High/Low

| Field | Value |
|-------|-------|
| Endpoint | `GET /fetch_52_week_high_low_data` |
| Priority | Optional — market overview |

**Parameters:** None. Response may be empty when market is closed.

---

#### 7. NSE Most Active

| Field | Value |
|-------|-------|
| Endpoint | `GET /NSE_most_active` |
| Priority | Optional — market overview |

**Response:**
```json
[
    {
        "ticker": "ETEA.NS",
        "company": "Eternal",
        "price": 254,
        "percent_change": -5.22,
        "net_change": -14,
        "bid": 254,
        "ask": 0,
        "high": 264.9,
        "low": 251.8,
        "open": 260,
        "low_circuit_limit": 241.2,
        "up_circuit_limit": 294.8,
        "volume": 76836130,
        "close": 268,
        "overall_rating": "Bearish",
        "short_term_trend": "Bearish",
        "long_term_trend": "Bearish",
        "52_week_low": 194.8,
        "52_week_high": 368.45
    }
]
```

> Note: Dev server returns numeric values (not strings like `/trending`). Includes trend ratings and 52-week range.

---

#### 8. BSE Most Active

| Field | Value |
|-------|-------|
| Endpoint | `GET /BSE_most_active` |
| Priority | Optional |

Same structure as NSE Most Active but with `.BO` tickers and BSE script codes.

---

#### 9. Price Shockers

| Field | Value |
|-------|-------|
| Endpoint | `GET /price_shockers` |
| Priority | Optional — market overview |

**Dev server response structure (updated):**
```json
{
    "BSE_PriceShocker": [
        {
            "tickerId": "S0005103",
            "displayName": "Krishna Institute Of Medical Sciences",
            "price": "693.25",
            "percentChange": "-0.86",
            "averagePrice": "865.54",
            "deviation": "19.91",
            "volume": "5284",
            "shortTermTrends": "Bearish",
            "longTermTrends": "Bearish",
            "overallRating": "Bearish"
        }
    ],
    "NSE_PriceShocker": [ ... ]
}
```

> Note: Dev server separates BSE/NSE and adds `deviation` and `averagePrice` fields.

---

### Category: Live Pricing *(NEW — dev server only)*

---

#### 10. NSE Batch Live Price

| Field | Value |
|-------|-------|
| Endpoint | `POST /nse_stock_batch_live_price` |
| Priority | **CRITICAL** — replaces per-symbol `/stock` calls in price update cron |

**Request Body:**
```json
{
    "stock_symbols": ["INFY", "TCS", "RELIANCE"]
}
```

**Response:**
```json
{
    "INFY": {
        "open": 1957.9,
        "high": 1964,
        "low": 1914.1,
        "close": 1952.75,
        "ltp": 1919,
        "volume": 4782119,
        "timestamp": "2024-10-10 15:59:25",
        "day_change": -33.75,
        "day_change_percent": -1.73,
        "total_buy_qty": 529,
        "total_sell_qty": 0,
        "last_trade_qty": 1,
        "last_trade_time": "2024-10-10 15:59:25"
    },
    "TCS": {
        "open": 4253.25,
        "high": 4293.85,
        "low": 4198.6,
        "close": 4252.95,
        "ltp": 4227.4,
        "volume": 2378875,
        "timestamp": "2024-10-10 15:59:42",
        "day_change": -25.55,
        "day_change_percent": -0.6,
        "total_buy_qty": 250,
        "total_sell_qty": 0
    }
}
```

**Response field mapping:**

| API Field | Our Table.Column | Notes |
|-----------|-----------------|-------|
| `open` | `price_history.open` | Today's open |
| `high` | `price_history.high` | Today's high |
| `low` | `price_history.low` | Today's low |
| `close` | — | Previous day's close |
| `ltp` | `price_history.close` | **Last Traded Price = today's close** |
| `volume` | `price_history.volume` | Today's volume |
| `day_change` | — | Dashboard display |
| `day_change_percent` | — | Dashboard display |

**This is the most efficient endpoint for our price update cron:**
- ONE call for ALL held NSE symbols instead of N individual `/stock` calls
- Returns **full OHLCV** data (unlike `/historical_data` which only gives close)
- Use `ltp` as today's closing price after market hours

**Our cron usage:**
```python
symbols = get_all_held_nse_symbols()  # e.g., ["INFY", "TCS", "RELIANCE", ...]
response = await client.post("/nse_stock_batch_live_price", json={"stock_symbols": symbols})
for symbol, data in response.items():
    upsert_price_history(symbol, today, data["open"], data["high"], data["low"], data["ltp"], data["volume"])
```

---

#### 11. BSE Batch Live Price

| Field | Value |
|-------|-------|
| Endpoint | `POST /bse_stock_batch_live_price` |
| Priority | Fallback for BSE-only stocks |

**Request Body:** Uses BSE script codes (numeric), not tickers.
```json
{
    "stock_symbols": ["532822", "543257"]
}
```

**Response:** Same structure as NSE batch but keyed by BSE code.

---

#### 12. Get Stock Data (comprehensive single-stock endpoint)

| Field | Value |
|-------|-------|
| Endpoint | `GET /get_stock_data` |
| Priority | **CRITICAL** — returns comprehensive company data (not just live OHLCV) |

**Parameters:** `stock_name` (string, required)

> **Dev server reality (verified via testing):** Despite the OpenAPI spec suggesting simple OHLCV, the dev server returns a **rich response** including company metadata, fundamentals, financials, and price data — essentially a superset of what `/stock` returns on the fallback server.

**Actual dev server response fields (verified):**
```json
{
    "name": "Tata Consultancy Services",
    "company_summary": "As a leading IT services...",
    "industry": "IT - Software",
    "nse_scrip_code": "TCS",
    "bse_scrip_code": "532540",
    "price_data": {
        "nse": { "yearLowPrice": 2346.2, "yearHighPrice": 3630.5 },
        "bse": { "yearLowPrice": 2346.35, "yearHighPrice": 3630.0 }
    },
    "stats": {
        "marketCap": 894933.95,
        "peRatio": 18.23,
        "pbRatio": 10.57,
        "divYield": 4.45,
        "bookValue": 234.07,
        "roe": 57.981,
        "debtToEquity": 0.1105,
        "cappedType": "Large Cap",
        "epsTtm": 135.7,
        "industryPe": 21.31,
        "faceValue": 1,
        "returnOnAssets": 35.0,
        "operatingProfitMargin": 30.88,
        "netProfitMargin": 23.65,
        "evToEbitda": 12.58,
        "priceToSales": 4.05,
        "pegRatio": 8.04,
        "roic": 59.56
    },
    "fundamentals": [
        { "name": "Market Cap", "shortName": "Mkt Cap", "value": "₹8,94,934Cr" },
        { "name": "ROE", "shortName": "ROE", "value": "57.98%" }
    ],
    "financials": [
        {
            "title": "Revenue",
            "yearly": { "2022": 195772.0, "2023": 228907.0, "2024": 245315.0 },
            "quarterly": { "Mar '25": 65507.0, "Jun '25": 65097.0 },
            "cagr": { "oneYearTtm": 0.09, "threeYearCagr": 0.06 }
        },
        { "title": "Profit", "yearly": {}, "quarterly": {} },
        { "title": "Net Worth", "yearly": {} }
    ]
}
```

**Fallback:** On normal server, use `GET /stock?name=TICKER` which returns similar data under different field names (see fallback transformer in `indianStockService.ts`).

---

#### 13. 1-Day Intraday Data

| Field | Value |
|-------|-------|
| Endpoint | `POST /1D_intraday_data` |
| Priority | Optional — intraday chart display |

**Parameters:** `stock_id` (string, required — IndianAPI ID like "S0003105")

**Response:**
```json
[
    {
        "tickerId": "S0003105",
        "returnValue": 3.76,
        "values": [
            {"timeStamp": "2024-10-18 09:20:00", "price": 544.35},
            {"timeStamp": "2024-10-18 09:25:00", "price": 548.90}
        ]
    }
]
```

**Notes:**
- 5-minute intervals during market hours; 1-minute after market close
- Timestamps in IST
- Uses `stock_id` (IndianAPI ID), not ticker symbol — look up from `/static/all_stocks.json`

---

### Category: Company Fundamentals

---

#### 14. Company Logo *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /logo` |
| Priority | Nice-to-have — display logos in holdings table |

**Parameters:**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `stock_name` | One required | string | Company name |
| `mutual_fund` | One required | string | Mutual fund name |

**Response:**
```json
{
    "content_type": "image/png",
    "base64_image": "iVBORw0KGgoAAAANSUhEUgAAALQ..."
}
```

**Usage:** Decode base64, cache as static asset or in DB. Display alongside holdings.

---

#### 15. Corporate Actions

| Field | Value |
|-------|-------|
| Endpoint | `GET /corporate_actions` |
| Priority | **CRITICAL** — detect splits, bonuses, dividends for cost basis adjustment |

**Parameters:** `stock_name` (string, required)

**Our usage:**
- Weekly cron checks for all held symbols
- New actions stored in `corporate_actions` table
- Splits/bonuses trigger FIFO cost basis recalculation
- Dividends trigger `cash_flows` entries

---

#### 16. Credit Ratings *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /credit_ratings` |
| Priority | Optional — display on stock detail page |

**Parameters:** `stock_name` (string, required)

---

#### 17. Conference Calls *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /concalls` |
| Priority | Optional — link to earnings call transcripts |

**Parameters:** `stock_name` (string, required)

---

#### 18. Annual Reports *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /annual_reports` |
| Priority | Optional — link to annual reports |

**Parameters:** `stock_name` (string, required)

---

#### 19. Company Documents *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /documents` |
| Priority | Optional — regulatory filings |

**Parameters:** `stock_name` (string, required)

---

#### 20. Recent Announcements

| Field | Value |
|-------|-------|
| Endpoint | `GET /recent_announcements` |
| Priority | Optional — per-stock announcements |

**Parameters:** `stock_name` (string, required)

---

### Category: Historical & Financials

---

#### 21. Historical Price Data

| Field | Value |
|-------|-------|
| Endpoint | `GET /historical_data` |
| Priority | **CRITICAL** — backfill `price_history` table |

**Parameters:**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `stock_name` | Yes | string | Stock name or ticker |
| `period` | Yes | enum | `1m` / `6m` / `1yr` / `3yr` / `5yr` / `10yr` / `max` |
| `filter` | Yes | enum | `default` / `price` / `pe` / `sm` / `evebitda` / `ptb` / `mcs` |

**Response:**
```json
{
    "datasets": [
        {
            "metric": "Price",
            "label": "Price on NSE",
            "values": [["2024-06-27", "3934.15"], ["2024-06-28", "3904.15"]],
            "meta": {"is_weekly": false}
        },
        {
            "metric": "DMA50",
            "label": "50 DMA",
            "values": [["2024-06-27", "3856.07"]]
        },
        {
            "metric": "DMA200",
            "label": "200 DMA",
            "values": [["2024-06-27", "3770.33"]]
        },
        {
            "metric": "Volume",
            "label": "Volume",
            "values": [["2024-06-27", 4727409, {"delivery": null}]]
        }
    ]
}
```

**Critical notes:**
- **Close prices only** — no OHLC candles. Only `close` can be populated in `price_history`.
- **For OHLC** — use `/nse_stock_batch_live_price` for today, yfinance for historical
- **Prices are strings** — parse to float
- **`is_weekly` flag** — long periods (10yr, max) may return weekly data. Check `meta.is_weekly`.
- **Volume delivery** — can be `null`, handle gracefully
- **Works for indices too** — pass index names like `NIFTY 50`, `NIFTY Bank` as `stock_name` to get historical index data (no need for yfinance for this)

---

#### 22. Historical Financial Stats

| Field | Value |
|-------|-------|
| Endpoint | `GET /historical_stats` |
| Priority | Optional — fundamental analysis |

**Parameters:** `stock_name` (string, required), `stats` (enum, required)

**Stats values:** `quarter_results` / `yoy_results` / `balancesheet` / `cashflow` / `ratios` / `profit_loss_stats` / `shareholding_pattern_quarterly` / `shareholding_pattern_yearly` / `all`

**Response (quarter_results example):**
```json
{
    "Sales": {"Jun 2021": 45411, "Sep 2021": 46867, ...},
    "Expenses": {...},
    "Operating Profit": {...},
    "OPM %": {...},
    "Net Profit": {...},
    "EPS in Rs": {...}
}
```

---

#### 23. Financial Statements

| Field | Value |
|-------|-------|
| Endpoint | `GET /statement` |
| Priority | Optional — same data as `/historical_stats`, alternative endpoint |

**Parameters:** `stock_name` (string, required), `stats` (string, required)

---

### Category: Forecasts & Targets

---

#### 24. Stock Forecasts

| Field | Value |
|-------|-------|
| Endpoint | `GET /stock_forecasts` |
| Priority | Optional — EPS/Revenue forecasts |

**Parameters:**

| Name | Required | Type | Values |
|------|----------|------|--------|
| `stock_id` | Yes | string | Stock ticker |
| `measure_code` | Yes | enum | `EPS`, `CPS`, `CPX`, `DPS`, `EBI`, `EBT`, `GPS`, `GRM`, `NAV`, `NDT`, `NET`, `PRE`, `ROA`, `ROE`, `SAL` |
| `period_type` | Yes | enum | `Annual`, `Interim` |
| `data_type` | Yes | enum | `Actuals`, `Estimates` |
| `age` | Yes | enum | `Current`, `OneWeekAgo`, `ThirtyDaysAgo`, `SixtyDaysAgo`, `NinetyDaysAgo` |

**MeasureCode reference:**

| Code | Meaning |
|------|---------|
| `EPS` | Earnings Per Share |
| `CPS` | Cash Flow per Share |
| `CPX` | Capital Expenditure |
| `DPS` | Dividends Per Share |
| `EBI` | EBIT |
| `EBT` | EBITDA |
| `GPS` | EPS - Fully Reported |
| `GRM` | Gross Margin |
| `NAV` | Net Asset Value |
| `NDT` | Net Debt |
| `NET` | Net Income |
| `PRE` | Pre-tax Profit |
| `ROA` | Return on Assets |
| `ROE` | Return on Equity |
| `SAL` | Revenue |

---

#### 25. Stock Target Price & Recommendations

| Field | Value |
|-------|-------|
| Endpoint | `GET /stock_target_price` |
| Priority | Optional — analyst consensus display |

**Parameters:** `stock_id` (string, required)

**Response includes:**
- `priceTarget`: Mean, High, Low, Median, NumberOfEstimates, StandardDeviation
- `priceTargetSnapshots`: Same data at OneWeekAgo, 30/60/90 days ago
- `recommendation`: Mean score (1=Buy, 2=Outperform, 3=Hold, 4=Underperform, 5=Sell)
- `recommendationSnapshots`: Historical recommendation data

---

### Category: News & Insights

---

#### 26. Market News

| Field | Value |
|-------|-------|
| Endpoint | `GET /news` |
| Priority | Optional — news feed widget |

**Parameters:**

| Name | Required | Type | Default | Description |
|------|----------|------|---------|-------------|
| `page_no` | No | integer | 1 | Page number |
| `size` | No | integer | 20 | Articles per page |

**Response:**
```json
[
    {
        "title": "India's IT giants are rewriting the services playbook",
        "summary": "Artificial intelligence is triggering a structural reset...",
        "url": "https://example.com/article",
        "image_url": "https://example.com/image.jpg",
        "pub_date": "2026-02-22T15:48:34",
        "source": "Financial News",
        "topics": ["Cost Cutting", "Artificial Intelligence"]
    }
]
```

---

#### 27. Company-Specific News *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /company_news` |
| Priority | Optional — per-holding news on stock detail page |

**Parameters:** `stock_name` (string, required)

---

#### 28. AI-Curated News *(NEW, Beta)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /ai_news` |
| Priority | Optional — AI-summarized market insights |

**Parameters:**

| Name | Required | Type | Description |
|------|----------|------|-------------|
| `category` | No | string | Filter: `stock_market`, `mutual_funds`, `ipo`, `investing`, `economy`, `commodities`. Omit for all. |

---

### Category: IPO

---

> Verified with live calls on 01 Nov 2026. Field names are exact. Dates in every IPO endpoint
> name Indian (IST) calendar days. Status strings differ between endpoints (`open` in /ipo/v2,
> `active` in /ipo) and lag reality, so work out the stage from the dates.

#### 29. IPO Data (legacy buckets)

| Field | Value |
|-------|-------|
| Endpoint | `GET /ipo` |
| Parameters | None |
| Size | ~37 KB, every bucket in one call |

**Response:** an object of buckets, each a list. Counts seen live:

| Bucket | Items | What is in it |
|--------|-------|---------------|
| `pre_apply` | 1 | bidding opens soon (`"additional_text": "Bid starts on 02 Nov at 10 AM"`) |
| `active` | 6 | bidding now (the same IPOs /ipo/v2 calls `open`) |
| `upcoming` | 25 | mostly DRHP-stage companies with no dates (Razorpay, PharmEasy, Jio Platforms, ...) |
| `closed` | 7 | bidding over, not listed yet |
| `listed` | 50 | the 50 most recent listings |

**Item (live):**
```json
{
  "symbol": "CORE4",
  "name": "Core4 Engineer",
  "status": "listed",
  "is_sme": true,
  "additional_text": "Listed at 74.4 for 20.00% loss",
  "min_price": 88.0,
  "max_price": 93.0,
  "issue_price": 93,
  "listing_gains": -19.999999999999993,
  "listing_price": 74.4,
  "bidding_start_date": "2026-10-23",
  "bidding_end_date": null,
  "listing_date": "2026-10-30",
  "lot_size": null,
  "document_url": "https://www.sebi.gov.in/..."
}
```

**Quirks:**
- **No IPO id.** Join to /ipo/v2 by `symbol` first: all 50 listed symbols matched a /ipo/v2 symbol.
  Fall back to a normalised name, since names are often shortened ("Core4 Engineer" here, "Core4
  Engineers" in v2; "Maxwell Engineering" vs "Maxwell Engineering Solutions").
- `listing_gains` is an **unrounded float** (`-19.999999999999993` = a 20.00% loss). Round to 2
  places before comparing.
- Several listings show exactly `0.0` (listing price = issue price). Treat 0% and -20% as possible
  default or price-band values, not real listing moves.
- `listing_price` can be null on listed records. `bidding_end_date` and `lot_size` are often null
  even when other endpoints know them.
- `document_url` is a SEBI filing link.

---

#### 30. Enhanced IPO Data v2

| Field | Value |
|-------|-------|
| Endpoint | `GET /ipo/v2?status={upcoming\|open\|closed\|listed}` |
| Optional | `issue_type=mainboard` or `issue_type=sme` |
| Errors | 422 (FastAPI `HTTPValidationError`) when `status` is missing |

**Response (live, `status=open`, one item shown):**
```json
{
  "summary": {"count": 6, "mainboardCount": 1, "smeCount": 5},
  "ipos": [
    {
      "id": "maxwell-engineering-solutions-limited-ipo",
      "name": "Maxwell Engineering Solutions",
      "status": "open",
      "issueType": "SME",
      "industry": "Engineering",
      "symbol": "MAXENGS",
      "isin": "INE1W3F01014",
      "nseEnabled": true,
      "bseEnabled": false,
      "listingExchange": "NSE",
      "minimumPrice": 150.0,
      "maximumPrice": 158.0,
      "cutOffPrice": 158.0,
      "faceValue": 10.0,
      "issueSize": 58.0,
      "lotSize": 800,
      "minimumQuantity": 1600.0,
      "biddingStartDate": "2026-10-30",
      "biddingEndDate": "2026-11-03",
      "rhpUrl": "https://dev.indianapi.in/...",
      "drhpUrl": null,
      "allotmentLinkUrl": "https://ipostatus.kfintech.com/",
      "totalSubscription": "4.65",
      "timeline": {
        "preApplyStartDate": "2026-10-29",
        "applicationStartDate": "2026-10-30",
        "applicationEndDate": "2026-11-03",
        "allotmentStartDate": "2026-11-04",
        "refundInitiationDate": "2026-11-05",
        "allotmentDate": "2026-11-05",
        "listingDate": "2026-11-06",
        "mandateEndDate": "2026-12-15"
      }
    }
  ]
}
```

**Quirks:**
- Dates are `YYYY-MM-DD` live. The published example shows `2024-10-28T00:00:00.000`; both name an IST day.
- The **timeline keys differ from /ipo/{id}**: v2 uses `applicationStartDate`, `allotmentDate`,
  `listingDate`, `preApplyStartDate`; the detail endpoint uses `biddingStart`,
  `allotmentFinalization`, `listingDay`, `preApplicationStart`.
- `status=open` works live (6 IPOs on 01 Nov 2026). If it ever comes back empty, try `status=active`.
- `issueSize` is in ₹ crore. `minimumQuantity` is a float (`1600.0`), and the SME minimum is usually 2 lots.
- `cutOffPrice` equals the upper band. Upcoming IPOs without a price band send 0s.
- `symbol` and `isin` are `""` for many upcoming IPOs; every listed IPO had a symbol.
- `status=listed` returned **157 IPOs**, with bidding dates from 22 Jul to 23 Oct 2026, i.e. about
  3 months back. That is as far as a backfill can reach.
- `totalSubscription` is a string; the detail endpoint has the per-category numbers.
- `rhpUrl` points at a dev.indianapi.in document link or at SEBI/exchange sites; it may expire, so
  don't store it.
- `allotmentLinkUrl` is the registrar's public status page, with no signature. Seen live:
  ipostatus.kfintech.com, in.mpms.mufg.com, maashitla.com, (ipo.)bigshareonline.com,
  ipo.cameoindia.com, purvashare.com.
- Response sizes: `listed` ~150 KB, others 6–13 KB.

---

#### 31. IPO Details by ID

| Field | Value |
|-------|-------|
| Endpoint | `GET /ipo/{id}` |
| `id` | the /ipo/v2 `id` slug, e.g. `bls-polymers-limited-ipo` (some slugs are odd: `vectras-enprocon-limited-logo`, `zetwerk-manufacturing-limted-ipo`) |
| Size | 1.7–3 KB |

**Response (live, a closed mainboard IPO; two of the three days collapsed):**
```json
{
  "status": "success",
  "data": {
    "basic": {
      "id": "bls-polymers-limited-ipo", "name": "BLS Polymers", "symbol": "BLSPOLY",
      "status": "closed", "type": "Mainboard", "industry": "Petrochemicals"
    },
    "pricing": {"priceRange": {"min": 142.0, "max": 150.0, "cutOff": 150.0}, "faceValue": 10.0},
    "issue": {
      "size": 255.0, "sizeFormatted": "₹255.0 Cr", "lotSize": 100,
      "minimumQuantity": 100.0, "minimumInvestment": "₹15,000.00"
    },
    "dates": {
      "bidding": {"start": "2026-10-27", "end": "2026-10-29"},
      "listing": "2026-11-03", "allotment": "2026-11-02"
    },
    "timeline": {
      "preApplicationStart": "2026-10-23", "biddingStart": "2026-10-27", "biddingEnd": "2026-10-29",
      "allotmentProcessStart": "2026-10-30", "refundInitiation": "2026-11-02",
      "allotmentFinalization": "2026-11-02", "listingDay": "2026-11-03", "mandateEnd": "2026-12-10"
    },
    "exchanges": {"primary": "BSE,NSE", "nse": true, "bse": true},
    "reservation": {"retail": 5950000, "nonInstitutional": 2550000, "qualifiedInstitutional": 3400000},
    "subscription": {
      "overall": "239.02",
      "overallFormatted": "239.02x",
      "daily": [
        {
          "date": "29-10-2026",
          "categories": {
            "retailIndividual":       {"applied": "740759300",  "reserved": "5950000",  "times": "124.5",  "timesFormatted": "124.50x"},
            "qualifiedInstitutional": {"applied": "441695400",  "reserved": "3400000",  "times": "129.91", "timesFormatted": "129.91x"},
            "nonInstitutional":       {"applied": "1414157400", "reserved": "2550000",  "times": "554.57", "timesFormatted": "554.57x"},
            "overall":                {"applied": "2844360100", "reserved": "11900000", "times": "239.02", "timesFormatted": "239.02x"}
          }
        },
        {"date": "28-10-2026", "categories": {"...": "..."}},
        {"date": "27-10-2026", "categories": {"...": "..."}}
      ]
    },
    "links": {"prospectus": {"rhp": "https://dev.indianapi.in/..."}, "allotment": "https://ipostatus.kfintech.com/"},
    "registrar": {
      "name": "KFin Technologies Limited",
      "contact": {"person": "...", "email": "...", "phone": "..."},
      "website": "https://www.kfintech.com/"
    }
  }
}
```

**Listed IPOs also carry `performance` (live, Tonbo Imaging):**
```json
"performance": {
  "price": {"ipo": 700.0, "listing": 1160.0},
  "gain": {"absolute": 460.0, "percentage": 65.71428571428571, "formatted": "+65.71%"}
}
```

**Quirks:**
- Dates are `YYYY-MM-DD` in `dates` and `timeline`, but `DD-MM-YYYY` in `subscription.daily[].date`.
- `daily` is **newest first**. Each entry is cumulative to the end of that bidding day, and
  non-trading days (weekends) have no entry. An open IPO checked mid-window had only its first
  day (Maxwell: bidding 30 Oct–03 Nov, one entry on Sunday 01 Nov).
- `applied`, `reserved` and `times` are **strings**. Quantities are shares, and `times` = applied / reserved.
- The categories are `retailIndividual`, `nonInstitutional`, `qualifiedInstitutional` and
  `overall`. There is no sNII/bNII split and no employee or shareholder quota.
- `reservation` is in shares and is 0 before bidding opens.
- `subscription.overall` is a string (the overall times), not an object.
- `performance` appears only once listed, and `gain.percentage` is unrounded, like /ipo.
- `links.prospectus.rhp` is a document link (it may expire; don't store it). `links.allotment` is
  the registrar's status page.
- `registrar.contact` holds a named person, an email and a phone number. Don't store it; keep only
  `registrar.name` and `links.allotment`.
- Live responses have no `documents` object. An older shape used `documents.allotmentStatus`.
- Formatted strings (`sizeFormatted`, `minimumInvestment`, `timesFormatted`, `overallFormatted`,
  `gain.formatted`) duplicate the numbers; read the numeric fields instead.

---

### Category: Mutual Funds

---

#### 32. All Mutual Funds

| Field | Value |
|-------|-------|
| Endpoint | `GET /mutual_funds` |
| Priority | Not needed (unless MF tracking added) |

---

#### 33. Mutual Fund Details

| Field | Value |
|-------|-------|
| Endpoint | `GET /mutual_funds_details` |
| Priority | Not needed |

**Parameters:** `stock_name` (string, required — full fund name)

---

#### 34. Mutual Fund NAV History *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /get_mf_historical_data` |
| Priority | Not needed |

**Parameters:** `stock_id` (string, required — MF ID), `stats` (enum: `5D`/`1M`/`6M`/`1Y`/`5Y`/`MAX`)

---

#### 35. Mutual Fund Holdings *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /mf_holdings` |
| Priority | Not needed |

**Parameters:** `stock_id` (string, required — MF ID)

---

### Category: Commodities

---

#### 36. Commodity Futures

| Field | Value |
|-------|-------|
| Endpoint | `GET /commodities` |
| Priority | Not needed |

**Response:** Live MCX futures data (Gold, Silver, Crude Oil, Aluminium, etc.) with OHLC, volume, open interest.

---

### Category: Utility

---

#### 37. Health Check *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /ping` |
| Priority | Useful — check if API is up before cron runs |

---

#### 38. API Usage Stats *(NEW)*

| Field | Value |
|-------|-------|
| Endpoint | `GET /usage` |
| Priority | Useful — monitor API consumption |

```
GET /usage
```

**Response (live, 01 Nov 2026, `endpoint_usage` shortened):**
```json
{
  "total_requests": 242,
  "hard_limit": 10000,
  "remaining_requests": 9758,
  "endpoint_usage": {"/ipo/{id}": 59, "/ipo/v2": 45, "/ipo": 8, "/stock": 1705, "/historical_data": 453, "...": 0}
}
```

- **Calls to `/usage` are not counted.** Between two readings, `total_requests` rose by exactly
  the number of other calls made, and `/usage` never appears in `endpoint_usage`.
- `endpoint_usage` counts calls per endpoint over the key's lifetime, across every app using the
  key. `total_requests` + `remaining_requests` = `hard_limit`.
- The numbers update immediately, so a reading taken right after a batch of calls already
  includes them.

---

## Endpoint Usage — All 38 Endpoints In Use

| Endpoint | Method | Used In | Component/File |
|----------|--------|---------|---------------|
| `/nse_stock_batch_live_price` | POST | Live prices, sidebar watchlist | indianStockService.ts, Sidebar.tsx |
| `/bse_stock_batch_live_price` | POST | BSE-only stock fallback | indianStockService.ts |
| `/get_stock_data` | GET | Stock details, fundamentals, peers | indianStockService.ts, ai.ts |
| `/historical_data` | GET | Price charts (all timeframes) | indianStockService.ts |
| `/company_news` | GET | News feed, AI sentiment | indianStockService.ts |
| `/indices` | GET | Market indices | indianStockService.ts |
| `/stock` | GET | Fallback stock details | indianStockService.ts |
| `/industry_search` | GET | Screener page | Screener.tsx |
| `/trending` | GET | Market heatmap, gainers/losers | Market.tsx |
| `/NSE_most_active` | GET | Market page | Market.tsx |
| `/BSE_most_active` | GET | Market page | Market.tsx |
| `/price_shockers` | GET | Market page | Market.tsx |
| `/fetch_52_week_high_low_data` | GET | Market page | Market.tsx |
| `/stock_target_price` | GET | Analyst targets, AI prompt | AnalystTargets.tsx, ai.ts |
| `/stock_forecasts` | GET | EPS/Revenue forecasts | Forecasts.tsx |
| `/corporate_actions` | GET | Corporate actions, earnings calendar, AI prompt | CorporateActions.tsx, EarningsCalendar.tsx, ai.ts |
| `/historical_stats` | GET | Quarterly financials in dialog | StockSummary.tsx |
| `/logo` | GET | Company logo in header | indianStockService.ts |
| `/credit_ratings` | GET | Credit ratings tab | Documents.tsx |
| `/concalls` | GET | Conference calls tab | Documents.tsx |
| `/annual_reports` | GET | Annual reports tab | Documents.tsx |
| `/documents` | GET | Regulatory filings tab | Documents.tsx |
| `/recent_announcements` | GET | Announcements tab | Documents.tsx |
| `/news` | GET | News page (paginated) | News.tsx |
| `/ai_news` | GET | (removed — stale data from API) | — |
| `/company_news` | GET | Per-stock news, sentiment | indianStockService.ts |
| `/commodities` | GET | Commodities page | Commodities.tsx |
| `/mutual_funds` | GET | MF browse by category | MutualFunds.tsx |
| `/mutual_funds_details` | GET | MF fund detail | MutualFunds.tsx |
| `/mutual_fund_search` | GET | MF search | MutualFunds.tsx |
| `/get_mf_historical_data` | GET | MF NAV chart | MutualFunds.tsx |
| `/mf_holdings` | GET | MF top holdings | MutualFunds.tsx |
| `/ipo` | GET | Legacy IPO endpoint | — |
| `/ipo/v2` | GET | IPO tracker (upcoming/open/listed) | IPO.tsx |
| `/ipo/{id}` | GET | IPO detail expansion | IPO.tsx |
| `/1D_intraday_data` | POST | 1D intraday chart | Index.tsx |
| `/ping` | GET | API health check | TopBar.tsx |
| `/usage` | GET | API usage counter | TopBar.tsx |
| `/static/all_stocks.json` | GET | Stock catalog (server startup) | stockCatalog.ts |
| `/static/all_mf.json` | GET | MF catalog (server startup) | stockCatalog.ts |

---

## Dev Server vs Fallback Server — Endpoint Availability

| Endpoint | Dev Server | Fallback Server |
|----------|-----------|-----------------|
| `/stock` (with symbol/id params) | Yes | No (name only) |
| `/indices` | Yes | No |
| `/nse_stock_batch_live_price` | Yes | No |
| `/bse_stock_batch_live_price` | Yes | No |
| `/get_stock_data` | Yes | No |
| `/1D_intraday_data` | Yes | No |
| `/logo` | Yes | No |
| `/credit_ratings` | Yes | No |
| `/concalls` | Yes | No |
| `/annual_reports` | Yes | No |
| `/documents` | Yes | No |
| `/company_news` | Yes | No |
| `/ai_news` | Yes | No |
| `/ipo/v2` | Yes | No |
| `/ipo/{id}` | Yes | No |
| `/get_mf_historical_data` | Yes | No |
| `/mf_holdings` | Yes | No |
| `/ping` | Yes | No |
| `/usage` | Yes | No |
| `/news` (with pagination) | Yes | Yes (no pagination) |
| All other endpoints | Yes | Yes |

**Fallback strategy (implemented in `indianStockService.ts`):**
- Server-side proxy at `/api/proxy/dev/*` (dev server) and `/api/proxy/fallback/*` (normal server)
- 1-hour cache with empty response rejection (empty arrays/objects are not cached)
- Retry on 500 with 300ms delay
- Server-side aggregation at `/api/stock/:ticker` (combines data from multiple endpoints)
- When dev server returns 5xx or times out, fall back to the normal server
- `/nse_stock_batch_live_price` (dev-only) → falls back to `GET /stock?name=` on normal server (degraded: returns current price only, no full OHLCV)
- `/get_stock_data` (dev-only) → falls back to `GET /stock?name=` on normal server (response transformed to match expected shape)
- `/historical_data` → same endpoint on both servers (direct fallback)
- `/company_news` (dev-only) → no fallback, returns empty array gracefully
- `/indices` (dev-only) → no fallback
- Auth errors (403) never trigger fallback (same API key is used on both servers)

---

## Known Limitations & Workarounds

| Limitation | Impact | Workaround |
|------------|--------|------------|
| `/historical_data` returns close prices only (no OHLC) | Can't backfill historical open/high/low | Use `/nse_stock_batch_live_price` for today's OHLC; yfinance for historical OHLC |
| No F&O option chain / historical premium data | Can't fetch historical option/futures prices | Store from trade imports only; no external backfill |
| Historical data may be weekly for long periods | Check `meta.is_weekly` in response | Use yfinance for guaranteed daily data |
| Price values often returned as strings | Must parse everywhere | Apply `float()` / `int()` conversion consistently |
| Dev-only endpoints have no fallback equivalent | If dev server is down, batch pricing etc. unavailable | Fall back to normal server `/stock` endpoint for degraded data, or yfinance for critical pricing |
| Batch pricing uses NSE symbols / BSE codes | Need to know which exchange a stock trades on | Maintain `instrument_metadata` with both `nse_symbol` and `bse_code` |

> **Note:** Historical index data IS available via `/historical_data`. The `/indices` endpoint is live-only, but `/historical_data` covers the historical gap.
>
> **Tested index names for `/historical_data`:**
> | `stock_name` value | Returns | Verified |
> |---|---|---|
> | `NIFTY` | Nifty 50 (NSE) | ~24,000 |
> | `SENSEX` | Sensex (BSE) | ~77,000 |
> | `NIFTY BANK` or `BANKNIFTY` | Bank Nifty (NSE) | ~54,800 |
>
> **Caution:** `NIFTY 50` (with space and "50") returns **empty datasets**. Use `NIFTY` instead. `Nifty50` matches an ETF (ICICI Prudential Nifty50 Value 20 ETF), not the index.

---

## Example: Python Client with Primary/Fallback

```python
import httpx
from typing import Optional

class IndianAPIClient:
    PRIMARY = "https://dev.indianapi.in"
    FALLBACK = "https://stock.indianapi.in"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self.headers = {"X-API-Key": api_key}
        self.timeout = timeout

    async def _request(
        self, method: str, path: str,
        params: dict = None, json_body: dict = None,
        fallback_ok: bool = True
    ) -> dict:
        """Try primary server, fall back to secondary on 5xx/timeout."""
        for base_url in [self.PRIMARY] + ([self.FALLBACK] if fallback_ok else []):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(
                        method, f"{base_url}{path}",
                        params=params, json=json_body,
                        headers=self.headers
                    )
                    if resp.status_code < 500:
                        resp.raise_for_status()
                        return resp.json()
            except (httpx.TimeoutException, httpx.HTTPStatusError):
                if base_url == self.FALLBACK:
                    raise
                continue
        raise Exception(f"Both servers failed for {path}")

    # --- CRITICAL endpoints ---

    async def batch_nse_prices(self, symbols: list[str]) -> dict:
        """Batch OHLCV for multiple NSE symbols. Dev server only."""
        return await self._request(
            "POST", "/nse_stock_batch_live_price",
            json_body={"stock_symbols": symbols},
            fallback_ok=False  # dev-only endpoint
        )

    async def get_stock(self, name: str = None, symbol: str = None, id: str = None) -> dict:
        params = {}
        if name: params["name"] = name
        if symbol: params["symbol"] = symbol
        if id: params["id"] = id
        return await self._request("GET", "/stock", params=params)

    async def get_historical_data(
        self, stock_name: str, period: str = "max", filter: str = "default"
    ) -> dict:
        return await self._request("GET", "/historical_data", params={
            "stock_name": stock_name, "period": period, "filter": filter
        })

    async def get_corporate_actions(self, stock_name: str) -> dict:
        return await self._request("GET", "/corporate_actions",
            params={"stock_name": stock_name})

    async def get_indices(self, exchange: str = None, index_type: str = None) -> dict:
        params = {}
        if exchange: params["exchange"] = exchange
        if index_type: params["index_type"] = index_type
        return await self._request("GET", "/indices", params=params,
            fallback_ok=False)  # dev-only

    async def get_live_stock(self, stock_name: str) -> dict:
        return await self._request("GET", "/get_stock_data",
            params={"stock_name": stock_name}, fallback_ok=False)

    async def get_logo(self, stock_name: str) -> dict:
        return await self._request("GET", "/logo",
            params={"stock_name": stock_name}, fallback_ok=False)

    # --- Utility ---

    async def health_check(self) -> bool:
        try:
            await self._request("GET", "/ping", fallback_ok=False)
            return True
        except Exception:
            return False

    async def get_usage(self) -> dict:
        return await self._request("GET", "/usage", fallback_ok=False)
```

---

## yfinance Supplement (for gaps IndianAPI doesn't cover)

yfinance is only needed for **historical OHLC candles** — IndianAPI's `/historical_data` returns close prices only (no open/high/low). For historical index data, prefer `/historical_data?stock_name=NIFTY 50` over yfinance.

```python
import yfinance as yf

# Historical OHLCV for individual stocks (IndianAPI /historical_data is close-only)
stock = yf.download("RELIANCE.NS", period="max")
# Returns: Open, High, Low, Close, Adj Close, Volume
```

**yfinance ticker format:**
- NSE stocks: `SYMBOL.NS` (e.g., `RELIANCE.NS`, `TCS.NS`)
- BSE stocks: `SYMBOL.BO` (e.g., `RELIANCE.BO`)
- Nifty 50: `^NSEI`
- Sensex: `^BSESN`
- Bank Nifty: `^NSEBANK`

## Historical Index Data via IndianAPI

Instead of yfinance, use `/historical_data` directly for index historical data:

```
GET /historical_data?stock_name=NIFTY&period=5yr&filter=price        → Nifty 50
GET /historical_data?stock_name=SENSEX&period=5yr&filter=price       → Sensex
GET /historical_data?stock_name=NIFTY BANK&period=3yr&filter=price   → Bank Nifty
GET /historical_data?stock_name=BANKNIFTY&period=3yr&filter=price    → Bank Nifty (alias)
```

Returns the same `datasets` structure as stock historical data (close prices + DMA50 + DMA200). Volume is 0 for indices.

> **Important:** Do NOT use `NIFTY 50` — it returns empty datasets. Use `NIFTY` instead.
