import os
import pandas as pd
from treas_analyzer.main import plot_facets, MATURITY_ORDER

def test_each_maturity_has_points(tmp_path):
    # Create sample data with varying number of points
    dates = pd.date_range('2025-08-01', periods=5, freq='D').date
    data = {'Date': dates}
    for m in MATURITY_ORDER:
        data[m] = [4.0 + i*0.02 for i in range(len(dates))]
    df = pd.DataFrame(data)

    out_dir = tmp_path.as_posix()
    path = plot_facets(df, out_dir, '202508')
    assert os.path.exists(path), 'Facets image missing'
    size = os.path.getsize(path)
    # Expect larger than minimal (each subplot adds bytes); adjust threshold if palette/format changes
    assert size > 15_000, f'Facets image too small; plotting loop may have failed ({size} bytes)'
