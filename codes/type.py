
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
 
# Supported frequencies: A = annual, Q = quarterly, M = monthly.
VALID_FREQUENCIES = ("A", "Q", "M")
_PANDAS_FREQ = {"A": "Y", "Q": "Q", "M": "M"}
 
# Accepted period strings per frequency. Checked strictly because pandas would
# otherwise read e.g. '2023-01' as 2023-Q1 when the frequency is declared 'Q'.
_PERIOD_PATTERNS = {
    "A": re.compile(r"\d{4}"),
    "Q": re.compile(r"\d{4}-?Q[1-4]"),
    "M": re.compile(r"\d{4}-\d{2}(-\d{2})?|\d{4}M\d{2}"),
}
 
REQUIRED_COLUMNS = [
    "source",
    "indicator",
    "period",
    "frequency",
    "value",
    "unit",
    "url",
    "scraped_at",
]
 
OUTPUT_COLUMNS = [
    "source",
    "indicator",
    "period",
    "date",
    "frequency",
    "value",
    "unit",
    "url",
    "scraped_at",
    "raw_text",
]
 
 
@dataclass(frozen=True)
class Observation:
    """Single macro or public budget observation in standard format."""
 
    source: str
    indicator: str
    period: str
    value: Optional[float]
    unit: str
    url: str
    scraped_at: str
    raw_text: Optional[str] = None
    frequency: Optional[str] = None
 
    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
 
 
def parse_period(period: str, frequency: str) -> pd.Timestamp:
    """Return the END date of a period, read according to its declared frequency.
 
    Examples: ('2023-Q1', 'Q') -> 2023-03-31; ('2023-01', 'M') -> 2023-01-31;
    ('2023-01-01', 'M') -> 2023-01-31 (FRED monthly); ('2023', 'A') -> 2023-12-31.
    """
    text = str(period).strip()
    if not _PERIOD_PATTERNS[frequency].fullmatch(text):
        raise ValueError(f"Period '{text}' does not match frequency '{frequency}'.")
    legacy = re.fullmatch(r"(\d{4})M(\d{2})", text)  # Eurostat legacy monthly: 2023M03
    if legacy:
        text = f"{legacy.group(1)}-{legacy.group(2)}"
    return pd.Period(text, freq=_PANDAS_FREQ[frequency]).end_time.normalize()
 
 
def _add_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add `date` (period end) computed from `period` and `frequency`."""
    mapping: dict[tuple[str, str], pd.Timestamp] = {}
    for period, freq in df[["period", "frequency"]].drop_duplicates().itertuples(index=False):
        try:
            mapping[(period, freq)] = parse_period(period, freq)
        except ValueError as exc:
            raise ValueError(
                f"Cannot read period '{period}' as frequency '{freq}'. "
                "Check the 'frequency' declared in the series config."
            ) from exc
    df["date"] = [mapping[(p, f)] for p, f in zip(df["period"], df["frequency"])]
    return df
 
 
def to_validated_dataframe(observations: list[Observation]) -> pd.DataFrame:
    """Convert a list of Observation records into a validated DataFrame for downstream modules."""
    if not observations:
        raise ValueError("No observations extracted: the resulting DataFrame would be empty.")
 
    df = pd.DataFrame([asdict(obs) for obs in observations])
 
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")
 
    indicator_clean = df["indicator"].astype(str).str.strip()
    if df["indicator"].isna().any() or (indicator_clean == "").any():
        raise ValueError("Found rows with empty or null 'indicator'.")
 
    if df["frequency"].isna().any() or not df["frequency"].isin(VALID_FREQUENCIES).all():
        raise ValueError(f"Found rows with missing or invalid 'frequency' (allowed: {VALID_FREQUENCIES}).")
 
    url_clean = df["url"].astype(str).str.strip()
    valid_url = (
        df["url"].notna()
        & df["url"].map(lambda x: isinstance(x, str))
        & url_clean.str.startswith(("http://", "https://"))
    )
    if not valid_url.all():
        raise ValueError("Found invalid URLs in the 'url' column.")
 
    df["url"] = url_clean
    # Coerce value to numeric where possible, keeping NaN for non-numeric/text entries
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
 
    df = _add_date_column(df)
    return df[OUTPUT_COLUMNS]
 