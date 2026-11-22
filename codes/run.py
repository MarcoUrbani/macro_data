import logging
from pathlib import Path
from typing import Any
import pandas as pd

from fetchers import fetch_series
from client import HttpClient
from type import Observation, to_validated_dataframe


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = BASE_DIR / "data" / "processed" / "italy_macro_series.csv"

# Every entry needs `source`, `indicator_code` and `frequency` (A, Q or M).
# All other keys are passed to the fetcher as keyword arguments.
SERIES_CONFIG: list[dict[str, Any]] = [
    # Quarterly - Eurostat real GDP, Italy, seasonally adjusted
    {
        "source": "eurostat",
        "indicator_code": "it_gdp_q_clv10",
        "frequency": "Q",
        "dataset": "namq_10_gdp",
        "geo": "IT",
        "extra_params": {
            "freq": "Q",
            "unit": "CLV10_MEUR",
            "na_item": "B1GQ",
            "s_adj": "SCA",
        },
    },
    # Monthly - ECB EUR/USD exchange rate
    {
        "source": "ecb",
        "indicator_code": "eur_usd_m",
        "frequency": "M",
        "dataflow": "EXR",
        "key": "M.USD.EUR.SP00.A",
        "start_period": "2023-01",
        "unit": "USD per EUR",
    }
]


def run_pipeline(
    series_config: list[dict[str, Any]] = SERIES_CONFIG,
    output_path: Path = OUTPUT_CSV,
) -> pd.DataFrame:
    """Fetch configured series, validate observations, and write output to CSV."""
    client = HttpClient(timeout=30.0, max_retries=3)
    all_observations: list[Observation] = []
    succeeded: list[str] = []
    failed: list[str] = []

    for cfg in series_config:
        label = f"{cfg['source']}:{cfg['indicator_code']}"
        try:
            obs = fetch_series(client, cfg)
            if not obs:
                logging.warning("No observations returned for %s", label)
                failed.append(label)
                continue
            all_observations.extend(obs)
            succeeded.append(label)
            logging.info("OK %s -> %d rows", label, len(obs))
        except Exception as exc:
            logging.error("ERROR on %s: %s", label, exc)
            failed.append(label)

    df = to_validated_dataframe(all_observations)

    # Keep the latest observation for each unique (source, indicator, date) key.
    # Duplicates usually mean a missing filter in the series config: check the warning.
    before_dedup = len(df)
    df = df.drop_duplicates(subset=["source", "indicator", "date"], keep="last")
    removed_dups = before_dedup - len(df)
    if removed_dups > 0:
        logging.warning("Removed %d duplicate rows on (source, indicator, date)", removed_dups)
    df = df.sort_values(["indicator", "date"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    logging.info(
        "Done: %d series succeeded, %d failed, %d rows saved to %s",
        len(succeeded),
        len(failed),
        len(df),
        output_path,
    )
    if failed:
        logging.warning("Series with issues in this run: %s", ", ".join(failed))

    return df


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    run_pipeline()
