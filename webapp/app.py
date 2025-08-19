from flask import Flask, render_template, request, send_from_directory, jsonify
import os
import sys
import threading
import time
import datetime as dt
import requests
import pandas as pd

from treas_analyzer.main import (
    build_month_arg,
    build_url,
    fetch_xml,
    parse_feed,
    plot_all,
    plot_ytd,
    build_ytd_df,
    summarize,
    should_regenerate,
    write_generated_marker,
    MATURITY_FIELDS,
    MATURITY_ORDER,
)

app = Flask(__name__)

# On startup, always regenerate analysis for requested month unless disabled
def _run_regen():
    try:
        from treas_analyzer import main as ta_main
        # Allow explicit override via STARTUP_MONTH (set to 'auto' or unset for current)
        target_month = os.environ.get("STARTUP_MONTH", "auto")
        if target_month.lower() == "auto" or not target_month:
            # Current month in YYYYMM
            today = dt.date.today()
            target_month = f"{today.year}{today.month:02d}"
        else:
            # Basic validation; if invalid fallback to current
            if not (len(target_month) == 6 and target_month.isdigit()):
                print(f"[startup] Invalid STARTUP_MONTH '{target_month}', falling back to current", file=sys.stderr)
                today = dt.date.today()
                target_month = f"{today.year}{today.month:02d}"
        ta_main.main(["--month", target_month, "--force-regenerate"])
        print("[startup] Regeneration complete", file=sys.stderr)
    except Exception as e:
        print(f"[startup] Regeneration failed: {e}", file=sys.stderr)

def _startup_regenerate_async():
    if os.environ.get("DISABLE_STARTUP_REGENERATE") == "1":
        return
    t = threading.Thread(target=_run_regen, name="regen-thread", daemon=True)
    t.start()

_startup_regenerate_async()


@app.route("/")
def index():
    # month query param overrides current
    ym = request.args.get("month")
    try:
        year_month = build_month_arg(ym)
    except Exception:
        year_month = build_month_arg(None)

    url = build_url(year_month)
    insecure = request.args.get("insecure") == "1"

    out_dir = os.path.join(os.path.dirname(__file__), "..", "out")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Determine whether to regenerate
    csv_path = os.path.join(out_dir, f"yields_{year_month}.csv")
    p_all = os.path.join(out_dir, f"yields_all_{year_month}.png")
    p_facets = os.path.join(out_dir, f"yields_facets_{year_month}.png")
    files_exist = os.path.exists(csv_path) and os.path.exists(p_all) and os.path.exists(p_facets)
    regen_needed = should_regenerate(out_dir, year_month, files_exist)

    if regen_needed:
        try:
            xml_text = fetch_xml(url, verify_ssl=not insecure)
            df = parse_feed(xml_text)
        except requests.exceptions.SSLError:
            try:
                xml_text = fetch_xml(url, verify_ssl=False)
                df = parse_feed(xml_text)
            except Exception as e:
                return render_template("error.html", error=f"SSL error and insecure fallback failed: {e}"), 500
        except Exception as e:
            return render_template("error.html", error=str(e)), 500
        # Write artifacts
        df.to_csv(csv_path, index=False)
        pngs = plot_all(df, out_dir, year_month, show=False)
        # Attempt to also build YTD plot (non-fatal if it fails)
        try:
            df_ytd = build_ytd_df(year_month, verify_ssl=not insecure)
            plot_ytd(df_ytd, out_dir, year_month, show=False)
        except Exception:
            pass
        write_generated_marker(out_dir, year_month)
    else:
        # Load cached
        try:
            df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
        except Exception as e:
            return render_template("error.html", error=f"Failed to load cached CSV: {e}"), 500
        # If YTD image missing, attempt to build (non-fatal)
        ytd_path = os.path.join(out_dir, f"yields_ytd_{year_month[:4]}.png")
        if not os.path.exists(ytd_path):
            try:
                df_ytd = build_ytd_df(year_month, verify_ssl=not insecure)
                plot_ytd(df_ytd, out_dir, year_month, show=False)
            except Exception:
                pass

    metrics_df, summary_text = summarize(df)
    # Best overall maturity (lowest CompositeRank)
    try:
        best_row = metrics_df.sort_values(["CompositeRank"]).iloc[0]
        best_overall = {
            "Maturity": best_row["Maturity"],
            "CurrentYieldPct": float(best_row["CurrentYieldPct"]),
            "CompositeRank": float(best_row["CompositeRank"]),
        }
    except Exception:
        best_overall = None

    latest_date = df.iloc[-1]["Date"]
    top5 = (
        metrics_df.sort_values(["CompositeRank"]).head(5)[
            ["Maturity", "CurrentYieldPct", "TrendBpsPerMonth", "CompositeRank"]
        ].to_dict(orient="records")
    )

    # Convert relative paths for serving via static route
    p_ytd = os.path.join(out_dir, f"yields_ytd_{year_month[:4]}.png")
    pngs_ordered = [p_all, p_ytd, p_facets]
    png_files = [os.path.basename(p) for p in pngs_ordered if os.path.exists(p)]

    return render_template(
        "index.html",
        year_month=year_month,
        latest_date=latest_date,
        png_files=png_files,
        summary_text=summary_text,
        best_overall=best_overall,
        top5=top5,
    )


@app.route("/invest", methods=["GET", "POST"])
def invest():
    """Simple investment return estimator per maturity.

    User supplies an amount (500 - 10,000,000). We compute simple interest over the full maturity
    term using the latest annualized yield: interest = principal * (yield_pct/100) * years.
    This ignores compounding, price fluctuations, reinvestment, tax, and day-count conventions.
    """
    # Determine month (same logic as index); default current month
    ym = request.args.get("month")
    try:
        year_month = build_month_arg(ym)
    except Exception:
        year_month = build_month_arg(None)

    # Amount: support both GET query (?amount=) and POST form
    amt_raw = request.values.get("amount", "10000").replace(",", "").strip()
    error = None
    try:
        amount = float(amt_raw)
    except ValueError:
        error = "Amount must be a number"
        amount = 0.0
    else:
        if amount < 500 or amount > 10_000_000:
            error = "Amount must be between 500 and 10,000,000"

    url = build_url(year_month)
    insecure = request.args.get("insecure") == "1"
    out_dir = os.path.join(os.path.dirname(__file__), "..", "out")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, f"yields_{year_month}.csv")
    # If cached CSV missing, fetch minimally (no plots required here)
    if not os.path.exists(csv_path):
        try:
            xml_text = fetch_xml(url, verify_ssl=not insecure)
            df = parse_feed(xml_text)
            df.to_csv(csv_path, index=False)
            write_generated_marker(out_dir, year_month)
        except Exception as e:
            return render_template("error.html", error=f"Failed to fetch data: {e}"), 500
    else:
        try:
            df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
        except Exception as e:
            return render_template("error.html", error=f"Failed to load cached data: {e}"), 500

    latest_date = df.iloc[-1]["Date"] if not df.empty else None

    # Determine best maturity by composite rank for highlight
    try:
        metrics_df, _ = summarize(df)
        best_maturity = metrics_df.sort_values(["CompositeRank"]).iloc[0]["Maturity"]
    except Exception:
        best_maturity = None

    # Build return table
    rows = []
    if amount > 0 and not error:
        latest_row = df.iloc[-1]
        for xml_field, (label, years) in MATURITY_FIELDS.items():
            if label not in df.columns:
                continue
            # Use the most recent non-null value
            series = df[label].dropna()
            if series.empty:
                continue
            yld = float(series.iloc[-1])  # already annualized percent
            if years and years > 0:
                interest = amount * (yld / 100.0) * years  # simple interest over full term
                total_value = amount + interest
            else:
                interest = float('nan')
                total_value = float('nan')
            rows.append({
                "Maturity": label,
                "Years": years,
                "YieldPct": yld,
                "Interest": interest,
                "Total": total_value,
            })

        # Order rows by defined order
        order_index = {m: i for i, m in enumerate(MATURITY_ORDER)}
        rows.sort(key=lambda r: order_index.get(r["Maturity"], 999))

    return render_template(
        "invest.html",
        amount=amount,
        amount_display=f"{amount:,.2f}" if amount else "",
        error=error,
        rows=rows,
        year_month=year_month,
        latest_date=latest_date,
    best_maturity=best_maturity,
    )


@app.route("/plots/<path:filename>")
def plots(filename: str):
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
    return send_from_directory(out_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": dt.datetime.utcnow().isoformat() + "Z"})
