import os
import pandas as pd
from treas_analyzer.main import plot_all, MATURITY_ORDER


def test_facets_contains_all_maturities(tmp_path):
    # Build a tiny dataframe with a few dates and all maturities populated
    dates = pd.date_range('2025-08-01', periods=3, freq='D').date
    data = { 'Date': dates }
    for m in MATURITY_ORDER:
        data[m] = [4.0 + i*0.01 for i in range(3)]  # simple increasing
    df = pd.DataFrame(data)

    out_dir = tmp_path.as_posix()
    pngs = plot_all(df, out_dir, '202508', show=False)
    # facets file should be second when YTD omitted inside plot_all
    facets = [p for p in pngs if 'facets' in p]
    assert facets, 'Facets plot path not returned'
    facets_path = facets[0]
    assert os.path.exists(facets_path), 'Facets image not created'

    # Basic heuristic: file size should exceed a small threshold proportional to number of panels
    size = os.path.getsize(facets_path)
    assert size > 10_000, f'Facets image unexpectedly small ({size} bytes)'

    # NOTE: For deeper validation we could parse the PNG and count text labels, but size + existence catches indentation bug.
