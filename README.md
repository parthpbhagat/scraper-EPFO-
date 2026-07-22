# EPFO Establishment Scraper

This scraper reads company names from `compony.txt`, searches the EPFO public establishment portal, stores search results, View Details sections, and View Payment Details rows in MySQL.

CAPTCHA is intentionally manual. The program saves and opens the CAPTCHA image, then asks you to type it. If EPFO rejects the CAPTCHA, the scraper loads a new CAPTCHA and stays on the same company until a correct value is entered, skipped, or stopped. Automatic CAPTCHA solving is not included.

## Setup

```powershell
python -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and set your local MySQL or TiDB credentials:

```text
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=provident_fund
```

Do not commit `.env`; it is ignored by git.

## Company File

Add one company per line in `compony.txt`:

```text
RELIANCE INDUSTRIES LIMITED
TATA CONSULTANCY SERVICES
```

Blank lines and lines starting with `#` are ignored.
You can copy `compony.example.txt` to `compony.txt` and replace the names.

## Run

```powershell
python epfo_scraper.py
```

If `compony.txt` is missing:

```powershell
copy compony.example.txt compony.txt
```

Then edit `compony.txt` and add one company name per line.

Useful options:

```powershell
python epfo_scraper.py --details-workers 5
python epfo_scraper.py --max-captcha-attempts 10
python epfo_scraper.py --no-open-captcha
python epfo_scraper.py --first-section-only
python epfo_scraper.py --max-search-variants 5
python epfo_scraper.py --no-search-variants
```

By default `--max-captcha-attempts` is `0`, so a wrong CAPTCHA keeps loading a new CAPTCHA and asking again until the correct CAPTCHA is entered. It will not move to the next search term or company while EPFO says the CAPTCHA is invalid.

If the exact company name returns no result, the scraper tries a few safer shorter search terms, for example removing `Private Limited` / `Limited` and punctuation. Each new search term needs a fresh manual CAPTCHA because EPFO requires it.

Do not run `epfo_scraper.py` with PM2/background because CAPTCHA needs interactive keyboard input. Use PM2 for `api_server.py` only.

## JSON API

Start the API server:

```powershell
python api_server.py
```

Base URL:

```text
http://127.0.0.1:8000
```

List all APIs:

```text
GET /api/tables
```

Table endpoints:

```text
GET /api/scrape-runs
GET /api/company-queries
GET /api/establishments
GET /api/company-query-establishments
GET /api/establishment-section-data
GET /api/payment-details
GET /api/establishment-raw-pages
GET /api/scrape-errors
```

Schema endpoints:

```text
GET /api/schema/scrape-runs
GET /api/schema/company-queries
GET /api/schema/establishments
GET /api/schema/company-query-establishments
GET /api/schema/establishment-section-data
GET /api/schema/payment-details
GET /api/schema/establishment-raw-pages
GET /api/schema/scrape-errors
```

Every endpoint returns JSON and supports:

```text
limit=100
offset=0
q=search text
include_raw=true
```

Examples:

```text
GET /api/establishments?limit=20
GET /api/payment-details?est_id=DLCPM1477048000
GET /api/establishment-section-data?est_id=DLCPM1477048000&section_name=validity_status_online_coverage
GET /api/company-queries?run_id=2
GET /api/establishment-raw-pages?est_id=DLCPM1477048000&include_raw=true
```

Company EPFO CSV download:

```text
GET /api/company-csv?company_name=Bird%20Delhi%20General%20Aviation%20Services%20Private%20Limited
```

Optional params:

```text
run_id=0
month_year=July_2026
```

`run_id=0` means the API uses the latest completed query for that company. If the company name is partial and matches multiple companies, the API returns JSON suggestions instead of a CSV file.

Large raw HTML fields are hidden by default and returned as `*_length` plus `*_preview`. Use `include_raw=true` when you need the complete raw HTML or traceback.

## Deploy on Ubuntu/aaPanel with PM2

Open the aaPanel terminal or SSH into the server, then run:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nodejs npm
sudo npm install -g pm2
```

Clone the project:

```bash
cd /www/wwwroot
git clone https://github.com/parthpbhagat/scraper-EPFO-.git epfo-scraper
cd epfo-scraper
```

Create Python environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `.env`:

```bash
cp .env.example .env
nano .env
```

Fill your MySQL/TiDB credentials in `.env`. Do not upload this file to GitHub.

Start the API in background with PM2:

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

Check status/logs:

```bash
pm2 status
pm2 logs epfo-api
```

Test locally on the server:

```bash
curl http://127.0.0.1:8000/api/health
```

If you want to access the API from outside the server, open/reverse-proxy port `8000` from aaPanel/Nginx/firewall and use:

```text
http://SERVER_IP:8000/api/company-csv?company_name=Company%20Name
```

## Tables Created

- `scrape_runs`
- `company_queries`
- `establishments`
- `company_query_establishments`
- `establishment_section_data`
- `payment_details`
- `establishment_raw_pages`
- `scrape_errors`

## Notes

- All visible payment table rows in the returned HTML are parsed, including rows hidden behind client-side pagination.
- If EPFO changes its `_HDIV_STATE_` URLs or HTML, rerun the program. It parses fresh URLs from the home/search/detail HTML each time.
- If detail fetching fails often, reduce `--details-workers` to `1` or increase `--delay`.
