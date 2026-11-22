"""Fetchers converting public statistical APIs into a list of `Observation`.

Every fetcher has the same contract: it takes an `HttpClient` plus source-specific
parameters and returns `list[Observation]` (empty list if the source has no data).
"""

from __future__ import annotations
import json
import logging
from dataclasses import replace
from io import StringIO
from typing import Any, Callable, Optional
from urllib.parse import urlencode
import numpy as np
import pandas as pd
from client import HttpClient
from type import VALID_FREQUENCIES, Observation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _pick_column(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    """Return the first candidate present in `columns` (case-insensitive), else None."""
    lowered = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None

def _csv_to_observations(
    csv_text: str,
    source: str,
    indicator_code: str,
    url: str,
    unit_fallback: str,
    raw_prefix: str,
    period_cols: tuple[str, ...] = ("TIME_PERIOD",),
    value_cols: tuple[str, ...] = ("OBS_VALUE",),
    unit_cols: tuple[str, ...] = ("UNIT_MEASURE", "UNIT"),
    unit: Optional[str] = None,
    drop_missing: bool = True,
) -> list[Observation]:
    """Convert a tabular/SDMX CSV response into Observations.

    Args:
        unit: explicit unit. If given, it overrides anything found in the CSV.
        drop_missing: if True, rows whose value is not numeric (e.g. FRED's ".")
            are skipped instead of being stored with value=None.
    """
    if not csv_text or not csv_text.strip():
        return []

    df = pd.read_csv(StringIO(csv_text), dtype=str)
    if df.empty:
        return []

    cols = list(df.columns)
    period_col = _pick_column(cols, period_cols)
    value_col = _pick_column(cols, value_cols)
    if period_col is None or value_col is None:
        raise ValueError(
            f"Expected columns not found for {source}:{indicator_code}. "
            f"Looked for period in {period_cols}, value in {value_cols}; got {cols}"
        )
    unit_col = None if unit else _pick_column(cols, unit_cols)

    scraped_at = Observation.now_iso()
    observations: list[Observation] = []

    for rec in df.to_dict("records"):
        raw_period = rec[period_col]
        if pd.isna(raw_period) or not str(raw_period).strip():
            continue

        raw_val = rec[value_col]
        num_val = pd.to_numeric(raw_val, errors="coerce")
        if pd.isna(num_val):
            if drop_missing:
                continue
            num_val = None

        row_unit = unit
        if row_unit is None:
            raw_unit = rec.get(unit_col) if unit_col else None
            row_unit = str(raw_unit).strip() if pd.notna(raw_unit) and str(raw_unit).strip() else unit_fallback

        period = str(raw_period).strip()
        observations.append(
            Observation(
                source=source,
                indicator=indicator_code,
                period=period,
                value=None if num_val is None else float(num_val),
                unit=row_unit,
                url=url,
                scraped_at=scraped_at,
                raw_text=f"{raw_prefix} [{period}] -> {raw_val}",
            )
        )
    return observations



# ECB fetcher

def fetch_ecb_observations(
    client: HttpClient,
    dataflow: str = "EXR",
    key: str = "M.USD.EUR.SP00.A",
    indicator_code: str = "eur_usd_monthly",
    start_period: str = "2015",
    unit: Optional[str] = None,
) -> list[Observation]:
    """Fetch a series from the ECB Data Portal (SDMX REST API, CSV format).

    With `detail=dataonly` the response carries no unit attribute, so pass `unit`
    explicitly (e.g. "USD per EUR") to avoid a generic fallback.
    """
    url = (
        f"https://data-api.ecb.europa.eu/service/data/{dataflow}/{key}"
        f"?startPeriod={start_period}&detail=dataonly&format=csvdata"
    )
    return _csv_to_observations(
        csv_text=client.get_text(url),
        source="ECB",
        indicator_code=indicator_code,
        url=url,
        unit_fallback="ECB_OBS",
        raw_prefix=f"{dataflow}:{key}",
        unit=unit,
    )



# AMECO 

def fetch_ameco_observations(
    client: HttpClient,
    key: str = "A.ITA.1.0.0.0.UVGD",
    indicator_code: str = "ameco_gdp_current_prices",
    start_period: str = "2000",
    unit: Optional[str] = None,
) -> list[Observation]:
    """Fetch an AMECO series via the ECB SDMX REST endpoint."""
    # NB: format=csvdata is required, otherwise the ECB returns SDMX-ML (XML).
    url = (
        f"https://data-api.ecb.europa.eu/service/data/AME/{key}"
        f"?startPeriod={start_period}&detail=dataonly&format=csvdata"
    )
    return _csv_to_observations(
        csv_text=client.get_text(url),
        source="AMECO",
        indicator_code=indicator_code,
        url=url,
        unit_fallback="AMECO_UNIT",
        raw_prefix=f"AME:{key}",
        unit=unit,
    )



# Eurostat (JSON)

def _category_positions(dimension: dict) -> dict[int, str]:
    """Map category position -> code for one JSON-stat dimension."""
    index = dimension.get("category", {}).get("index", {})
    if isinstance(index, list):  # list form: position is the list index
        return {i: str(code) for i, code in enumerate(index)}
    return {int(pos): str(code) for code, pos in index.items()}


def fetch_eurostat_observations(
    client: HttpClient,
    dataset: str = "nama_10_gdp",
    indicator_code: str = "it_gdp_current_meur",
    geo: str = "IT",
    extra_params: Optional[dict[str, str]] = None,
    unit: Optional[str] = None,
) -> list[Observation]:
    """Fetch one series from the Eurostat Statistics API (JSON-stat 2.0).

    All dimensions except `time` must resolve to a single category, otherwise
    different series would be mixed under the same (indicator, period) key.
    Add the missing filters to `extra_params` (e.g. unit, na_item, s_adj, freq).
    """
    params: dict[str, str] = {"geo": geo, "lang": "EN"}
    if extra_params:
        params.update(extra_params)

    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        f"{dataset}?{urlencode(params)}"
    )
    payload = json.loads(client.get_text(url))

    dim_ids: list[str] = payload.get("id", [])
    dim_sizes: list[int] = payload.get("size", [])
    dimensions: dict = payload.get("dimension", {})
    raw_values = payload.get("value", {})

    if not dim_ids or len(dim_ids) != len(dim_sizes):
        raise ValueError(f"Invalid JSON-stat structure for dataset '{dataset}'.")
    if "time" not in dim_ids:
        raise ValueError(f"No 'time' dimension in dataset '{dataset}'.")

    ambiguous = [d for d, s in zip(dim_ids, dim_sizes) if d != "time" and s > 1]
    if ambiguous:
        raise ValueError(
            f"Dataset '{dataset}' returned several categories for {ambiguous}. "
            "Add filters in extra_params so the query identifies a single series."
        )

    # JSON-stat may give values as a dict {flat_index: value} or as a dense list.
    if isinstance(raw_values, list):
        value_items = [(i, v) for i, v in enumerate(raw_values)]
    else:
        value_items = [(int(i), v) for i, v in raw_values.items()]

    positions = {name: _category_positions(dimensions.get(name, {})) for name in dim_ids}
    time_axis = dim_ids.index("time")

    # Unit: explicit argument > query parameter > unit dimension > fallback.
    resolved_unit = unit or params.get("unit")
    if not resolved_unit and "unit" in dim_ids:
        resolved_unit = positions["unit"].get(0)
    resolved_unit = resolved_unit or "EUROSTAT_UNIT"

    fixed_summary = ", ".join(
        f"{name}={positions[name].get(0)}" for name in dim_ids if name != "time"
    )
    scraped_at = Observation.now_iso()
    observations: list[Observation] = []

    for flat_idx, raw_val in value_items:
        if raw_val is None:
            continue
        coords = np.unravel_index(flat_idx, dim_sizes)
        period = positions["time"].get(int(coords[time_axis]), str(flat_idx))
        observations.append(
            Observation(
                source="EUROSTAT",
                indicator=indicator_code,
                period=period,
                value=float(raw_val),
                unit=resolved_unit,
                url=url,
                scraped_at=scraped_at,
                raw_text=f"{dataset} ({fixed_summary}) [{period}] = {raw_val}",
            )
        )

    observations.sort(key=lambda o: o.period)
    return observations



# FRED (public CSV)

def fetch_fred_csv_observations(
    client: HttpClient,
    series_id: str = "IRLTLT01ITM156N",
    indicator_code: str = "italy_10y_bond_yield_fred",
    start_date: str = "2015-01-01",
    unit: Optional[str] = None,
) -> list[Observation]:
    """Fetch a FRED series from the public fredgraph.csv endpoint.

    The endpoint is unofficial; for production use prefer the official API
    (api.stlouisfed.org, free key). Missing values (".") are dropped.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start_date}"
    return _csv_to_observations(
        csv_text=client.get_text(url),
        source="FRED",
        indicator_code=indicator_code,
        url=url,
        unit_fallback="FRED_SERIES",
        raw_prefix=series_id,
        period_cols=("observation_date", "DATE"),
        value_cols=(series_id,),
        unit=unit,
    )



# OECD

def fetch_oecd_observations(
    client: HttpClient,
    agency_flow_version: str = "OECD.SDD.TPS,DSD_PDB@DF_PDB_LV,1.0",
    key: str = "ITA.A.GDPHRS..USD_PPP_H.Q...",
    indicator_code: str = "oecd_gdp_per_hour_ita",
    start_period: str = "2015",
    unit: Optional[str] = None,
) -> list[Observation]:
    """Fetch a series from the OECD SDMX REST API (CSV with labels).

    The key depends on the dataflow's DSD: copy it from the Data Explorer
    ("Developer API") instead of writing it by hand.
    """
    url = (
        f"https://sdmx.oecd.org/public/rest/data/{agency_flow_version}/{key}"
        f"?startPeriod={start_period}&format=csvfilewithlabels"
    )
    return _csv_to_observations(
        csv_text=client.get_text(url),
        source="OECD",
        indicator_code=indicator_code,
        url=url,
        unit_fallback="OECD_UNIT",
        raw_prefix=agency_flow_version,
        unit_cols=("UNIT_MEASURE", "Unit of measure"),
        unit=unit,
    )


# Registry: lets the pipeline dispatch by source name without if/elif chains

FETCHERS: dict[str, Callable[..., list[Observation]]] = {
    "ecb": fetch_ecb_observations,
    "eurostat": fetch_eurostat_observations,
    "ameco": fetch_ameco_observations,
    "fred": fetch_fred_csv_observations,
    "oecd": fetch_oecd_observations,
}


def fetch_series(client: HttpClient, cfg: dict[str, Any]) -> list[Observation]:
    """Dispatch a config entry to its fetcher and tag the rows with the declared frequency.

    `cfg` must contain `source` and `frequency` (A, Q or M); every other key is passed
    to the fetcher as a keyword argument (so config keys must match its parameter names).
    """
    params = dict(cfg)
    source = str(params.pop("source")).lower()
    frequency = str(params.pop("frequency", "")).strip().upper()

    if source not in FETCHERS:
        raise ValueError(f"Unsupported source '{source}'. Available: {sorted(FETCHERS)}")
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(
            f"Series '{params.get('indicator_code')}': 'frequency' must be one of "
            f"{list(VALID_FREQUENCIES)}, got '{frequency}'."
        )

    observations = FETCHERS[source](client=client, **params)
    return [replace(o, frequency=frequency) for o in observations]


__all__ = [
    "FETCHERS",
    "fetch_series",
    "fetch_ecb_observations",
    "fetch_eurostat_observations",
    "fetch_ameco_observations",
    "fetch_fred_csv_observations",
    "fetch_oecd_observations",
]
