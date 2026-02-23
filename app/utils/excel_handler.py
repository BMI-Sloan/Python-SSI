"""
Excel handler utility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def read_excel(path: str | Path, sheet: int | str = 0) -> list[dict[str, Any]]:
    import pandas as pd

    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(p, dtype=str)
    else:
        df = pd.read_excel(p, sheet_name=sheet, dtype=str)

    df = df.dropna(how="all")
    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    return df.to_dict(orient="records")


def write_results(
    results: list[dict[str, Any]],
    output_path: str | Path,
    sheet_name: str = "Results",
) -> Path:
    import pandas as pd

    p = Path(output_path)
    df = pd.DataFrame(results)
    df.to_excel(p, index=False, sheet_name=sheet_name)
    return p


def get_column(rows: list[dict[str, Any]], column: str) -> list[Any]:
    return [row.get(column) for row in rows if row.get(column) is not None]
