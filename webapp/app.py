from flask import Flask, render_template, request, send_from_directory, jsonify, g
import os
import sys
import threading
import time
import datetime as dt
import requests
import pandas as pd
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools

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

# Global cache and state for Cloud Run optimization
_cache = {}
_ready = False
_executor = ThreadPoolExecutor(max_workers=2)

def cache_key(year_month: str) -> str:
    """Generate cache key for data"""
    return f"data_{year_month}"

def is_cloud_run():
    """Detect if running in Cloud Run environment"""
    return os.environ.get("K_SERVICE") is not None

# Optimized startup for Cloud Run
def _run_regen_optimized():
    global _ready
    try:
        # In Cloud Run, be more conservative with startup generation
        if is_cloud_run():
            # Only regenerate if no recent data exists
            target_month = os.environ.get("STARTUP_MONTH", "auto")
            if target_month.lower() == "auto" or not target_month:
                today = dt.date.today()
                target_month = f"{today.year}{today.month:02d}"
            
            out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
            csv_path = os.path.join(out_dir, f"yields_{target_month}.csv")
            
            # Skip startup regeneration if data exists and is recent
            if os.path.exists(csv_path):
                file_age = time.time() - os.path.getmtime(csv_path)
                if file_age < 3600:  # Less than 1 hour old
                    print(f"[startup] Using existing data (age: {file_age/60:.1f}min)", file=sys.stderr)
                    _ready = True
                    return
        
        # Validate month format
        target_month = os.environ.get("STARTUP_MONTH", "auto")
        if target_month.lower() == "auto" or not target_month:
            today = dt.date.today()
            target_month = f"{today.year}{today.month:02d}"
        else:
            if not (len(target_month) == 6 and target_month.isdigit()):
                print(f"[startup] Invalid STARTUP_MONTH '{target_month}', falling back to current", file=sys.stderr)
                today = dt.date.today()
                target_month = f"{today.year}{today.month:02d}"
        
        from treas_analyzer import main as ta_main
        ta_main.main(["--month", target_month, "--force-regenerate"])
        print(f"[startup] Regeneration complete for {target_month}", file=sys.stderr)
        _ready = True
    except Exception as e:
        print(f"[startup] Regeneration failed: {e}", file=sys.stderr)
        _ready = True  # Mark ready even on failure to prevent blocking

def _startup_regenerate_async():
    if os.environ.get("DISABLE_STARTUP_REGENERATE") == "1":
        global _ready
        _ready = True
        return
    t = threading.Thread(target=_run_regen_optimized, name="regen-thread", daemon=True)
    t.start()

_startup_regenerate_async()

# Health and readiness endpoints for Cloud Run
@app.route("/health")
@app.route("/healthz")  
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "time": dt.datetime.utcnow().isoformat() + "Z"})

@app.route("/ready")
def ready():
    """Readiness check endpoint"""
    return jsonify({
        "status": "ready" if _ready else "starting",
        "ready": _ready,
        "time": dt.datetime.utcnow().isoformat() + "Z"
    }), 200 if _ready else 503


@app.route("/")
def index():
    # Check cache first for faster response
    ym = request.args.get("month")
    try:
        year_month = build_month_arg(ym)
    except Exception:
        year_month = build_month_arg(None)
    
    cache_k = cache_key(year_month)
    
    # Try to serve from cache if available
    if cache_k in _cache:
        cached_data = _cache[cache_k]
        # Check if cache is still fresh (within 30 minutes)
        if time.time() - cached_data['timestamp'] < 1800:
            return render_template(
                "index.html",
                **cached_data['template_data']
            )

    url = build_url(year_month)
    insecure = request.args.get("insecure") == "1"

    out_dir = os.path.join(os.path.dirname(__file__), "..", "out")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Optimized file existence check
    csv_path = os.path.join(out_dir, f"yields_{year_month}.csv")
    p_all = os.path.join(out_dir, f"yields_all_{year_month}.png")
    p_facets = os.path.join(out_dir, f"yields_facets_{year_month}.png")
    p_ytd = os.path.join(out_dir, f"yields_ytd_{year_month[:4]}.png")
    
    files_exist = os.path.exists(csv_path) and os.path.exists(p_all) and os.path.exists(p_facets)
    regen_needed = should_regenerate(out_dir, year_month, files_exist)

    # Cloud Run optimization: prefer existing data over regeneration during request
    if is_cloud_run() and files_exist and not regen_needed:
        # Load cached data efficiently in Cloud Run
        try:
            df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
        except Exception as e:
            return render_template("error.html", error=f"Failed to load cached CSV: {e}"), 500
    elif regen_needed:
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
        # Load cached data efficiently
        try:
            df = pd.read_csv(csv_path, parse_dates=["Date"]).assign(Date=lambda s: s["Date"].dt.date)
        except Exception as e:
            return render_template("error.html", error=f"Failed to load cached CSV: {e}"), 500
        
        # In Cloud Run, skip YTD generation during request to reduce latency
        if not is_cloud_run():
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
    pngs_ordered = [p_all, p_ytd, p_facets]
    png_files = [os.path.basename(p) for p in pngs_ordered if os.path.exists(p)]

    # Prepare template data
    template_data = {
        "year_month": year_month,
        "latest_date": latest_date,
        "png_files": png_files,
        "summary_text": summary_text,
        "best_overall": best_overall,
        "top5": top5,
    }
    
    # Cache the response for faster subsequent requests
    _cache[cache_k] = {
        'timestamp': time.time(),
        'template_data': template_data
    }
    
    # Keep cache size reasonable
    if len(_cache) > 10:
        oldest_key = min(_cache.keys(), key=lambda k: _cache[k]['timestamp'])
        del _cache[oldest_key]

    return render_template("index.html", **template_data)


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


@app.route("/ladder", methods=["GET", "POST"])
def ladder():
    """Treasury ladder calculator.
    
    Builds a Treasury ladder by spreading investment across multiple maturities.
    Supports different allocation strategies.
    """
    # Determine month (same logic as index)
    ym = request.args.get("month")
    try:
        year_month = build_month_arg(ym)
    except Exception:
        year_month = build_month_arg(None)

    # Parse parameters
    total_amount_raw = request.values.get("total_amount", "50000").replace(",", "").strip()
    rungs = int(request.values.get("rungs", "5"))
    strategy = request.values.get("strategy", "equal")
    
    # Parse custom allocations if strategy is custom
    custom_allocations = []
    if strategy == "custom":
        for i in range(rungs):
            alloc_raw = request.values.get(f"alloc_{i}", "")
            try:
                alloc = float(alloc_raw) if alloc_raw else (100.0 / rungs)
                custom_allocations.append(alloc)
            except ValueError:
                custom_allocations.append(100.0 / rungs)

    # Parse selected durations (multi-select checkboxes). Support 'ALL'.
    durations = request.values.getlist('durations') if request.values.getlist('durations') else []
    if 'ALL' in durations:
        durations = []  # empty means include all available maturities (subject to min-year filter)

    error = None
    try:
        total_amount = float(total_amount_raw)
    except ValueError:
        error = "Total amount must be a number"
        total_amount = 0.0
    else:
        if total_amount < 500 or total_amount > 10_000_000:
            error = "Total amount must be between 500 and 10,000,000"
        elif strategy == "custom" and custom_allocations:
            # Validate custom allocations total to 100%
            total_allocation = sum(custom_allocations)
            if abs(total_allocation - 100.0) > 0.1:
                error = f"Custom allocations must total 100% (currently {total_allocation:.1f}%)"

    url = build_url(year_month)
    insecure = request.args.get("insecure") == "1"
    out_dir = os.path.join(os.path.dirname(__file__), "..", "out")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, f"yields_{year_month}.csv")
    # If cached CSV missing, fetch minimally
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

    # Build ladder results
    ladder_results = None
    if total_amount > 0 and not error:
        # Get available maturities (filter out the shortest ones for ladder)
        available_maturities = []
        latest_row = df.iloc[-1]
        for xml_field, (label, years) in MATURITY_FIELDS.items():
            if label not in df.columns or years is None:
                continue
            # Skip very short terms unless explicitly selected by the user
            if years < 0.5 and not (durations and label in durations):
                continue
            # If user selected specific durations, only include those
            if durations and label not in durations:
                continue
            series = df[label].dropna()
            if series.empty:
                continue
            yld = float(series.iloc[-1])
            available_maturities.append({
                "maturity": label,
                "years": years,
                "yield_pct": yld
            })
        
        # If user didn’t select durations, default to Top Maturities: choose top-yielding set of size=rungs
        available_maturities.sort(key=lambda x: x["yield_pct"], reverse=True)
        if not durations:
            selected_maturities = available_maturities[: max(0, rungs)]
        else:
            # Durations were filtered above; use all available after filtering
            selected_maturities = available_maturities

        # Adjust rungs to match selection count
        rungs = min(rungs, len(selected_maturities)) if rungs > 0 else len(selected_maturities)
        selected_maturities = selected_maturities[:rungs]
        selected_labels = [m["maturity"] for m in selected_maturities]
        selected_maturities.sort(key=lambda x: x["years"])  # display/order
        
        # Apply allocation strategy
        if strategy == "equal":
            # Equal allocation
            weights = [1.0] * len(selected_maturities)
        elif strategy == "yield_weighted":
            # Weight by yield
            weights = [m["yield_pct"] for m in selected_maturities]
        elif strategy == "short_weighted":
            # More weight to shorter terms
            weights = [1.0 / (m["years"] ** 0.5) for m in selected_maturities]
        elif strategy == "long_weighted":
            # More weight to longer terms
            weights = [m["years"] ** 0.5 for m in selected_maturities]
        elif strategy == "custom" and custom_allocations:
            # Use custom allocations (convert percentages to weights)
            weights = [alloc / 100.0 for alloc in custom_allocations[:len(selected_maturities)]]
        else:
            weights = [1.0] * len(selected_maturities)
        
        # Normalize weights (except for custom which should already be normalized)
        if strategy != "custom":
            total_weight = sum(weights)
            weights = [w / total_weight for w in weights]
        
        # Calculate allocations
        rungs_data = []
        total_invested = 0
        total_annual_interest = 0
        weighted_yield_sum = 0
        weighted_years_sum = 0
        
        for i, maturity in enumerate(selected_maturities):
            amount = total_amount * weights[i]
            annual_interest = amount * (maturity["yield_pct"] / 100.0)
            maturity_value = amount + (annual_interest * maturity["years"])
            
            rungs_data.append({
                "maturity": maturity["maturity"],
                "years": maturity["years"],
                "yield_pct": maturity["yield_pct"],
                "amount": amount,
                "annual_interest": annual_interest,
                "maturity_value": maturity_value
            })
            
            total_invested += amount
            total_annual_interest += annual_interest
            weighted_yield_sum += maturity["yield_pct"] * amount
            weighted_years_sum += maturity["years"] * amount
        
        # Sort rungs by years for display
        rungs_data.sort(key=lambda x: x["years"])
        
        ladder_results = {
            "rungs": rungs_data,
            "total_invested": total_invested,
            "weighted_avg_yield": weighted_yield_sum / total_invested if total_invested > 0 else 0,
            "total_annual_interest": total_annual_interest,
            "avg_years": weighted_years_sum / total_invested if total_invested > 0 else 0
        }

    return render_template(
        "ladder.html",
        total_amount=total_amount,
        rungs=rungs,
        strategy=strategy,
    durations_selected=durations,
        selected_labels=selected_labels if 'selected_labels' in locals() else [],
        custom_allocations=custom_allocations if strategy == "custom" else None,
        error=error,
        ladder_results=ladder_results,
        year_month=year_month,
        latest_date=latest_date,
    )


@app.route("/plots/<path:filename>")
def plots(filename: str):
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
    return send_from_directory(out_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    # Disable debug mode in production
    debug_mode = not is_cloud_run()
    app.run(host="0.0.0.0", port=port, debug=debug_mode)


# Remove duplicate healthz endpoint - using the one defined earlier
