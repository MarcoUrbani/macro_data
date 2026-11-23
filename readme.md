# Macro Tracker

A small, reproducible pipeline that downloads macroeconomic time series from public statistical APIs (Eurostat, ECB and others) and stores them in one tidy, validated CSV file.

- **One command** downloads every configured series and writes `data/processed/macro_series.csv`.
- **One common format**: every row is a single observation (long format) with its source, unit, frequency, download URL and timestamp.
- **Fails loudly**: a wrong frequency, an ambiguous Eurostat query or an invalid URL stops the run with an explicit message instead of producing silently wrong data.

## Series included

| Indicator | Source | Dataset / key | Frequency | Description |
|---|---|---|---|---|
| `it_gdp_q_clv10` | Eurostat | `namq_10_gdp` (`B1GQ`, `CLV10_MEUR`, `SCA`) | Quarterly | Italy, GDP at market prices, chain-linked volumes, million EUR, seasonally and calendar adjusted |
| `eur_usd_m` | ECB | `EXR` / `M.USD.EUR.SP00.A` | Monthly | EUR/USD exchange rate, monthly average (USD per EUR) |

Fetchers for **FRED**, **AMECO** (through the ECB SDMX endpoint) and **OECD** are implemented as well; see [Status and limitations](#status-and-limitations).

## Quick start

Requires Python 3.10 or newer (developed and tested on 3.12).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

The output is written to `data/processed/italy_macro_series.csv` and a summary is printed to the log: series downloaded, series that failed, rows saved, and any duplicates removed.

## Output format

One row per observation, in long format.

| Column | Meaning |
|---|---|
| `source` | Data provider (`ECB`, `EUROSTAT`, `FRED`, `AMECO`, `OECD`) |
| `indicator` | Series identifier chosen in the configuration |
| `period` | Period exactly as published by the source (`2023-Q1`, `2023-01`, `2023`, ...) |
| `date` | **End date of the period**, as a real date (see conventions below) |
| `frequency` | `A` annual, `Q` quarterly, `M` monthly |
| `value` | Numeric value |
| `unit` | Unit of measure |
| `url` | Exact request URL the data was downloaded from |
| `scraped_at` | Download timestamp (UTC, ISO 8601) |
| `raw_text` | The source value as received, for traceability |

Conventions:

- **`date` is the last day of the period**: `2023-Q1` → `2023-03-31`, `2023-02` → `2023-02-28`, `2023` → `2023-12-31`. Series of different frequencies can therefore be sorted and joined on one time axis.
- **The frequency is declared in the configuration, never guessed from the text.** A monthly series published as `2023-01-01` (FRED) would otherwise look daily.
- **The period format must match the declared frequency.** `2023-01` declared as quarterly is rejected, because it would otherwise be read as 2023-Q1.
- **Missing values are dropped** (e.g. FRED's `.`), not stored as empty rows.
- **Uniqueness**: the key is `(source, indicator, date)`. If duplicates appear, the last one is kept and a warning is logged. A duplicate usually means a filter is missing in the series configuration.
- **Mixed frequencies are not aligned automatically.** To compare a monthly and a quarterly series, aggregate explicitly (mean for rates, sum for flows, last value for stocks) and keep only complete periods.

> **Opening the CSV in Excel:** the file uses a comma separator and a dot as decimal separator. With a European locale, use *Data → From Text/CSV* and select the comma as delimiter, instead of double-clicking the file.

## Configuring series

Series are listed in `SERIES_CONFIG` in `run.py`. Every entry needs three keys:

- `source`: `eurostat`, `ecb`, `ameco`, `fred` or `oecd`
- `indicator_code`: the name of the series in the output file
- `frequency`: `A`, `Q` or `M`

All other keys are passed to the fetcher of that source as keyword arguments:

| Source | Parameters |
|---|---|
| `eurostat` | `dataset`, `geo`, `extra_params` (filters), `unit` |
| `ecb` | `dataflow`, `key`, `start_period`, `unit` |
| `ameco` | `key`, `start_period`, `unit` |
| `fred` | `series_id`, `start_date`, `unit` |
| `oecd` | `agency_flow_version`, `key`, `start_period`, `unit` |

Example:

```python
{
    "source": "ecb",
    "indicator_code": "eur_usd_m",
    "frequency": "M",
    "dataflow": "EXR",
    "key": "M.USD.EUR.SP00.A",
    "start_period": "2023-01",
    "unit": "USD per EUR",
}
```

Notes:

- For Eurostat, every dimension other than `time` must resolve to a single category. If the query returns several (e.g. two units), the run stops and asks for additional filters in `extra_params`, rather than mixing different series under one indicator.
- The ECB endpoint does not return a unit with `detail=dataonly`, so pass `unit` explicitly.
- `freq` inside Eurostat's `extra_params` is Eurostat's own filter; `frequency` at the top level is this project's declaration. Both are needed.

## Project structure

```
.
├── run.py                       # series configuration and pipeline entry point
├── fetchers.py                  # one fetcher per source + dispatcher (fetch_series)
├── client.py                    # HTTP client with timeout, retry and backoff
├── type.py                      # Observation record, validation, period parsing
├── tests/                       # offline unit tests (no network)
├── data/processed/              # output CSV (created by the pipeline)
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests run offline against small, hand-written responses. They cover period parsing and its validation, the parsing of every source (including Eurostat's JSON-stat), the dispatcher, and the pipeline behaviour: output file, duplicates, one failing series not stopping the others, and a clear failure when nothing can be downloaded. They check the parsing logic, not the availability of the live APIs.

## Status and limitations

- **Enabled by default:** Eurostat GDP and ECB EUR/USD.
- **FRED and AMECO** fetchers are implemented and covered by offline tests, but are not enabled in the default configuration. The FRED fetcher uses the public `fredgraph.csv` endpoint, which is not an official API; for production use the official FRED API (free key) is more stable. The AMECO key has not been verified against the live service.
- **OECD** fetcher is implemented but has not been run against the live API. OECD keys depend on the dataflow's structure: copy them from the OECD Data Explorer ("Developer API") instead of writing them by hand.
- Raw API responses are not archived and every run overwrites the output file.
- Values are stored as published. No transformation (growth rates, rebasing, aggregation) is applied.

## Data sources and terms of use

Data are downloaded from [Eurostat](https://ec.europa.eu/eurostat), the [European Central Bank](https://data.ecb.europa.eu), and, where enabled, FRED (Federal Reserve Bank of St. Louis), AMECO (European Commission) and the OECD. The data remain subject to each provider's terms of use; check them and cite the source when redistributing or publishing results.
