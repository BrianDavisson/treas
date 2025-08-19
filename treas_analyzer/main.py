import argparse
import datetime as dt
import os
import sys
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
import xml.etree.ElementTree as ET
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import math

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}

# Mapping of Treasury XML fields to maturity labels and years to maturity
MATURITY_FIELDS = {
    "BC_1MONTH": ("1M", 1 / 12),
    "BC_2MONTH": ("2M", 2 / 12),
    "BC_3MONTH": ("3M", 3 / 12),
    "BC_6MONTH": ("6M", 6 / 12),
    "BC_1YEAR": ("1Y", 1.0),
    "BC_2YEAR": ("2Y", 2.0),
    "BC_3YEAR": ("3Y", 3.0),
    "BC_5YEAR": ("5Y", 5.0),
    "BC_7YEAR": ("7Y", 7.0),
    "BC_10YEAR": ("10Y", 10.0),
    "BC_20YEAR": ("20Y", 20.0),
    "BC_30YEAR": ("30Y", 30.0),
}

MATURITY_ORDER = [
    "1M",
    "2M",
    "3M",
    "6M",
    "1Y",
    "2Y",
    "3Y",
    "5Y",
    "7Y",
    "10Y",
    "20Y",
    "30Y",
]

# Explicit color overrides for selected maturities to ensure visual distinction.
# Palette chosen to be colorblind-friendly (Okabe-Ito style hues):
# 1M (orange), 2M (vermillion), 20Y (blue), 30Y (bluish green)
MATURITY_COLORS = {
    "1M": "#E69F00",  # orange
    "2M": "#D55E00",  # vermillion
    "20Y": "#0072B2", # blue
    "30Y": "#009E73", # bluish green
}


@dataclass
class Trend:
    slope_per_day: float
    slope_bps_per_month: float
    r2: float

# Helper functions (build_month_arg, build_url, etc.) remain unchanged
def build_month_arg(year_month: Optional[str]) -> str:
    if year_month:
        if not (len(year_month) == 6 and year_month.isdigit()):
            raise ValueError("--month must be in YYYYMM format")
        return year_month
    today = dt.date.today()
    return f"{today.year}{today.month:02d}"


def build_url(year_month: str) -> str:
    base = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    )
    return (
        f"{base}?data=daily_treasury_yield_curve&field_tdr_date_value_month={year_month}"
    )


def _et_now() -> dt.datetime:
    """Current time in America/New_York (ET)."""
    return dt.datetime.now(tz=ZoneInfo("America/New_York"))


def _marker_path(out_dir: str, year_month: str) -> Path:
    return Path(out_dir) / f".generated_{year_month}.json"


def load_last_generated_ymd(out_dir: str, year_month: str) -> Optional[dt.date]:
    p = _marker_path(out_dir, year_month)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        ymd = data.get("last_generated_ymd")
        if ymd:
            return dt.date.fromisoformat(ymd)
    except Exception:
        return None
    return None


def write_generated_marker(out_dir: str, year_month: str, when_et: Optional[dt.datetime] = None) -> None:
    when = when_et or _et_now()
    payload = {
        "last_generated_ymd": when.date().isoformat(),
        "last_generated_et": when.isoformat(),
    }
    p = _marker_path(out_dir, year_month)
    p.write_text(json.dumps(payload, indent=2))


def should_regenerate(out_dir: str, year_month: str, files_exist: bool) -> bool:
    """Return True if we should regenerate network-based outputs now (ET).

    - If required files are missing, regenerate.
    - If already generated today, do not regenerate.
    - If not yet generated today and current ET time >= 12:00, regenerate.
    - Otherwise, skip regeneration and use cache.
    """
    if not files_exist:
        return True
    now = _et_now()
    today = now.date()
    last = load_last_generated_ymd(out_dir, year_month)
    if last == today:
        return False
    if now.time() >= dt.time(hour=12, minute=0):
        return True
    return False


def fetch_xml(url: str, timeout: int = 30, verify_ssl: bool = True) -> str:
    headers = {
        "User-Agent": "treas-analyzer/1.0 (+https://github.com/) Python-requests",
        "Accept": "application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    resp = requests.get(url, timeout=timeout, headers=headers, verify=verify_ssl)
    resp.raise_for_status()
    return resp.text


def parse_feed(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows: List[Dict[str, Any]] = []

    for entry in root.findall("atom:entry", ATOM_NS):
        props = entry.find("atom:content/m:properties", ATOM_NS)
        if props is None:
            continue
        row: Dict[str, Any] = {}

        date_el = props.find("d:NEW_DATE", ATOM_NS)
        if date_el is None or date_el.text is None:
            continue
        date_str = date_el.text.strip()
        try:
            date = dt.datetime.fromisoformat(date_str).date()
        except Exception:
            date = dt.date.fromisoformat(date_str[:10])
        row["Date"] = date

        for xml_field, (label, _years) in MATURITY_FIELDS.items():
            el = props.find(f"d:{xml_field}", ATOM_NS)
            val: Optional[float] = None
            if el is not None and el.text is not None and el.text.strip() != "":
                try:
                    val = float(el.text)
                except ValueError:
                    val = None
            row[label] = val

        rows.append(row)

    if not rows:
        raise RuntimeError("No entries parsed from XML feed; structure may have changed.")

    df = pd.DataFrame(rows)
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def compute_trend(dates: pd.Series, values: pd.Series) -> Optional[Trend]:
    mask = values.notna()
    x_dates = dates[mask]
    y = values[mask].astype(float)
    if len(y) < 3:
        return None

    x = x_dates.map(lambda d: d.toordinal()).astype(float).to_numpy()
    y_np = y.to_numpy()

    slope, intercept = np.polyfit(x, y_np, 1)
    y_pred = slope * x + intercept

    ss_res = float(np.sum((y_np - y_pred) ** 2))
    ss_tot = float(np.sum((y_np - np.mean(y_np)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    slope_per_day = float(slope)
    slope_bps_per_month = slope_per_day * 30.0 * 100.0
    return Trend(slope_per_day=slope_per_day, slope_bps_per_month=slope_bps_per_month, r2=r2)

def summarize(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    latest_row = df.iloc[-1]
    latest_date = latest_row["Date"]

    metrics = []
    for label in MATURITY_ORDER:
        if label not in df.columns:
            continue
        series = df[label]
        if series.notna().sum() == 0:
            continue
        current = float(series.dropna().iloc[-1])
        tr = compute_trend(df["Date"], series)
        years = next((yrs for k, (lab, yrs) in MATURITY_FIELDS.items() if lab == label), None)
        if years is None:
            continue
        if years > 0:
            intensity = current / (years ** 0.5)
        else:
            intensity = np.nan

        trend_effect = 0.0
        if tr is not None:
            adj = tr.slope_bps_per_month * max(tr.r2, 0.3)
            adj_pct = adj / 100.0
            trend_effect = -0.5 * max(adj_pct, 0.0) + 0.25 * min(adj_pct, 0.0)
        value_score = current + trend_effect

        metrics.append(
            {
                "Maturity": label,
                "Years": years,
                "CurrentYieldPct": current,
                "IntensityPctPerYear": intensity,
                "TrendBpsPerMonth": tr.slope_bps_per_month if tr else np.nan,
                "TrendR2": tr.r2 if tr else np.nan,
                "ValueScore": value_score,
            }
        )

    mdf = pd.DataFrame(metrics)
    if mdf.empty:
        raise RuntimeError("No maturity metrics computed.")

    def rank_desc(s: pd.Series) -> pd.Series:
        return s.rank(ascending=False, method="min")

    mdf["RankYield"] = rank_desc(mdf["CurrentYieldPct"])
    mdf["RankIntensity"] = rank_desc(mdf["IntensityPctPerYear"])
    mdf["RankValue"] = rank_desc(mdf["ValueScore"])
    mdf["CompositeRank"] = mdf[["RankYield", "RankIntensity", "RankValue"]].mean(axis=1)

    best_row = mdf.sort_values(["CompositeRank", "Maturity"]).iloc[0]
    best_intensity_row = mdf.sort_values('IntensityPctPerYear', ascending=False).iloc[0]
    bi_maturity = best_intensity_row['Maturity']
    bi_yield = best_intensity_row['CurrentYieldPct']
    bi_years = best_intensity_row['Years']
    bi_sqrt_years = math.sqrt(bi_years) if bi_years > 0 else float('nan')
    bi_intensity = best_intensity_row['IntensityPctPerYear']

    def fmt(x: float) -> str:
        return f"{x:.2f}"

    lines = [
        f"Summary for {latest_date:%Y-%m} (latest data {latest_date:%Y-%m-%d})",
        "",
        f"Highest current yield: {mdf.sort_values('CurrentYieldPct', ascending=False).iloc[0]['Maturity']} (@ {fmt(mdf['CurrentYieldPct'].max())}%)",
        f"Best duration-adjusted yield (yield / sqrt(years)): {mdf.sort_values('IntensityPctPerYear', ascending=False).iloc[0]['Maturity']} (@ {fmt(mdf['IntensityPctPerYear'].max())} adj units)",
        f"Best trend-adjusted yield: {mdf.sort_values('ValueScore', ascending=False).iloc[0]['Maturity']} (@ {fmt(mdf['ValueScore'].max())}%)",
        f"Best overall (composite): {best_row['Maturity']}",
        "",
        "Notes: Intensity now = yield / sqrt(years); avoids unrealistic inflation of very short maturities.",
        "Trend notes (bps/month, higher = rising yields):",
    ]

    for _, r in mdf.sort_values("Years").iterrows():
        t = r["TrendBpsPerMonth"]
        r2 = r["TrendR2"]
        t_str = "n/a" if np.isnan(t) else f"{t:+.1f}"
        r2_str = "n/a" if np.isnan(r2) else f"{r2:.2f}"
        lines.append(f" - {r['Maturity']:>4}: {t_str} bps/mo (R²={r2_str}), curr {fmt(r['CurrentYieldPct'])}%")

    # Append detailed explanation of duration-adjusted yield using dynamic variables
    lines.extend([
        "",
        "Explanation of duration-adjusted yield:",
        f"Chosen maturity: {bi_maturity} (highest duration-adjusted yield).",
        f"Latest {bi_maturity} annualized yield: {bi_yield:.2f}%.",
        f"Years to maturity: {bi_years:.6f}.",
        f"sqrt(years): {bi_sqrt_years:.6f}.",
        f"Duration-adjusted value: {bi_yield:.2f} / {bi_sqrt_years:.6f} = {bi_intensity:.2f} adj units.",
        "Interpretation: dimensionless score; higher = more yield per unit sqrt(years); not a percent.",
    ])

    summary_text = "\n".join(lines)
    return mdf, summary_text

def plot_facets(df: pd.DataFrame, out_dir: str, year_month: str) -> str:
    """Create per-maturity small multiple facet plot and return path."""
    os.makedirs(out_dir, exist_ok=True)
    cols = 4
    maturities = [m for m in MATURITY_ORDER if m in df.columns and df[m].notna().any()]
    rows = int(np.ceil(len(maturities) / cols)) or 1
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.0, rows * 2.4), sharex=True)
    if not isinstance(axes, np.ndarray):
        axes = np.array([[axes]])
    axes = axes.reshape(rows, cols)

    for idx, m in enumerate(maturities):
        r = idx // cols
        c = idx % cols
        ax = axes[r, c]
        color = MATURITY_COLORS.get(m)
        ax.plot(df["Date"], df[m], color=color, linewidth=1.2)
        ax.set_title(m)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    # Remove unused axes
    for j in range(len(maturities), rows * cols):
        r = j // cols
        c = j % cols
        fig.delaxes(axes[r, c])

    # Hide x tick labels on non-last rows
    for r in range(rows - 1):
        for c in range(cols):
            try:
                axes[r, c].tick_params(labelbottom=False)
            except Exception:
                pass

    fig.suptitle(f"Treasury Yields by Maturity ({year_month})")
    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    p = os.path.join(out_dir, f"yields_facets_{year_month}.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p

def plot_all(df: pd.DataFrame, out_dir: str, year_month: str, show: bool = False) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    pngs: List[str] = []

    # Combined plot
    fig, ax = plt.subplots(figsize=(10, 6))
    for label in MATURITY_ORDER:
        if label in df.columns and df[label].notna().any():
            color = MATURITY_COLORS.get(label)
            ax.plot(df["Date"], df[label], label=label, color=color, linewidth=1.2)
    ax.set_title(f"Treasury Yields ({year_month})")
    ax.set_xlabel("Date")
    ax.set_ylabel("Yield (%)")
    ax.legend(ncol=4, fontsize=8)
    ax.grid(True, alpha=0.3)
    p1 = os.path.join(out_dir, f"yields_all_{year_month}.png")
    fig.tight_layout()
    fig.savefig(p1, dpi=150)
    pngs.append(p1)
    plt.close(fig)

    # Facets
    p2 = plot_facets(df, out_dir, year_month)
    pngs.append(p2)

    if show:
        # Optionally display last figure (facets) if interactive
        img = plt.imread(p2)  # just to keep consistent; skip heavy show logic
        plt.imshow(img)
        plt.axis('off')
        plt.show()
    else:
        plt.close('all')
    return pngs

def _months_ytd(year_month: str) -> List[str]:
    year = int(year_month[:4])
    month = int(year_month[4:])
    return [f"{year}{m:02d}" for m in range(1, month + 1)]

def fetch_month_df(year_month: str, verify_ssl: bool = True) -> pd.DataFrame:
    url = build_url(year_month)
    xml_text = fetch_xml(url, verify_ssl=verify_ssl)
    return parse_feed(xml_text)

def build_ytd_df(year_month: str, verify_ssl: bool = True) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for ym in _months_ytd(year_month):
        try:
            frames.append(fetch_month_df(ym, verify_ssl=verify_ssl))
        except Exception:
            continue
    if not frames:
        raise RuntimeError("No data available for YTD plot")
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("Date").reset_index(drop=True)
    return df

def plot_ytd(df: pd.DataFrame, out_dir: str, year_month: str, show: bool = False) -> str:
    year = year_month[:4]
    os.makedirs(out_dir, exist_ok=True)

    plt.figure(figsize=(10, 6))
    for label in MATURITY_ORDER:
        if label in df.columns and df[label].notna().any():
            color = MATURITY_COLORS.get(label)
            plt.plot(df["Date"], df[label], label=label, linewidth=1.2, color=color)
    plt.title(f"Treasury Yields YTD ({year})")
    plt.xlabel("Date")
    plt.ylabel("Yield (%)")
    plt.legend(ncol=4, fontsize=8)
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    p = os.path.join(out_dir, f"yields_ytd_{year}.png")
    plt.tight_layout()
    plt.savefig(p, dpi=150)
    if show:
        plt.show()
    else:
        plt.close("all")
    return p

# New function to encapsulate core logic
def process_and_summarize_data(year_month: str, insecure: bool) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetches, processes, and summarizes Treasury yield data.
    Returns the raw data and the summary metrics.
    """
    url = build_url(year_month)
    xml_text = fetch_xml(url, verify_ssl=not insecure)
    df = parse_feed(xml_text)
    metrics_df, _ = summarize(df)
    return df, metrics_df

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Treasury yield XML for a month, plot, and summarize value.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--month",
        help="Month in YYYYMM, defaults to current month",
        default=None,
    )
    parser.add_argument(
        "--out",
        help="Output directory",
        default=os.path.join(os.path.dirname(__file__), "..", "out"),
    )
    parser.add_argument(
        "--insecure",
        help="Disable TLS/SSL certificate verification (use only if your network uses a proxy with a self-signed certificate)",
        action="store_true",
    )
    parser.add_argument(
        "--show",
        help="Show plots interactively",
        action="store_true",
    )
    parser.add_argument(
        "--force-regenerate",
        help="Force regeneration regardless of ET-based cache window",
        action="store_true",
    )
    args = parser.parse_args(argv)

    year_month = build_month_arg(args.month)
    out_dir = os.path.abspath(os.path.join(args.out))
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, f"yields_{year_month}.csv")
    p_all = os.path.join(out_dir, f"yields_all_{year_month}.png")
    p_ytd = os.path.join(out_dir, f"yields_ytd_{year_month[:4]}.png")
    p_facets = os.path.join(out_dir, f"yields_facets_{year_month}.png")
    files_exist = all(os.path.exists(p) for p in [csv_path, p_all, p_ytd, p_facets])
    regen_needed = args.force_regenerate or should_regenerate(out_dir, year_month, files_exist)

    df = None
    metrics_df = None
    regen_status = "Using cached outputs"

    if regen_needed:
        try:
            df, metrics_df = process_and_summarize_data(year_month, args.insecure)
            df.to_csv(csv_path, index=False)
            plot_all(df, out_dir, year_month, show=args.show)
            try:
                df_ytd = build_ytd_df(year_month, verify_ssl=not args.insecure)
                plot_ytd(df_ytd, out_dir, year_month, show=False)
            except Exception as e:
                print(f"Warning: could not build YTD plot: {e}", file=sys.stderr)
            write_generated_marker(out_dir, year_month)
            regen_status = "Generated new outputs"
        except Exception as e:
            print(f"Error obtaining fresh data: {e}", file=sys.stderr)
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
                metrics_df, _ = summarize(df)
            else:
                return 2
    else:
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
            metrics_df, _ = summarize(df)
        else:
            try:
                df, metrics_df = process_and_summarize_data(year_month, args.insecure)
                df.to_csv(csv_path, index=False)
                plot_all(df, out_dir, year_month, show=args.show)
                try:
                    df_ytd = build_ytd_df(year_month, verify_ssl=not args.insecure)
                    plot_ytd(df_ytd, out_dir, year_month, show=False)
                except Exception as e:
                    print(f"Warning: could not build YTD plot: {e}", file=sys.stderr)
                write_generated_marker(out_dir, year_month)
                regen_status = "Generated new outputs"
            except Exception as e:
                print(f"Error obtaining data: {e}", file=sys.stderr)
                return 2

    _, summary = summarize(df)
    summary_path = os.path.join(out_dir, f"summary_{year_month}.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n\n")
        f.write("Top 5 by Composite Rank:\n")
        top5 = metrics_df.sort_values(["CompositeRank"]).head(5)
        for _, r in top5.iterrows():
            f.write(
                f"  {r['Maturity']}: yield {r['CurrentYieldPct']:.2f}% | trend {r['TrendBpsPerMonth'] if not np.isnan(r['TrendBpsPerMonth']) else 'n/a'} bps/mo | composite rank {r['CompositeRank']:.1f}\n"
            )

    print(
        textwrap.dedent(
            f"""
            {regen_status} for {year_month}.
            Rows: {len(df)} | CSV: {csv_path}
            Summary:
            {summary}
            Full summary saved to: {summary_path}
            """
        ).strip()
    )

    return 0

if __name__ == "__main__":
    sys.exit(main())