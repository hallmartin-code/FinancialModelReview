"""Charts, rendered to in-memory PNG.

One chart matters on this page: revenue by period, with actuals in solid fill and
projections hatched, so the boundary between what is proven and what is promised
is visible without reading a number.
"""

from __future__ import annotations

import io
from decimal import Decimal

import matplotlib

matplotlib.use("Agg")  # no display, no interactive backend

import matplotlib.pyplot as plt

#: Rendering is deterministic: fixed size, fixed dpi, no timestamps in the PNG.
DPI = 200


def revenue_chart_png(
    points: list[tuple[str, Decimal, bool]],
    *,
    actual_color: str,
    projected_color: str,
    text_color: str,
    width_in: float = 3.1,
    height_in: float = 1.15,
) -> bytes | None:
    """Bar chart of ``(period, value, is_projected)``. None if there is nothing to draw."""
    if len(points) < 2:
        return None

    labels = [label for label, _, _ in points]
    values = [float(value) for _, value, _ in points]
    projected = [flag for _, _, flag in points]

    figure, axes = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    try:
        for index, (value, is_projected) in enumerate(zip(values, projected, strict=True)):
            axes.bar(
                index,
                value,
                width=0.68,
                color=projected_color if is_projected else actual_color,
                hatch="////" if is_projected else None,
                edgecolor=actual_color if is_projected else "none",
                linewidth=0.5 if is_projected else 0.0,
            )

        axes.set_xticks(range(len(labels)))
        axes.set_xticklabels(labels, fontsize=5.5, color=text_color)
        axes.tick_params(axis="x", length=0, pad=1.5)
        axes.set_yticks([])
        for spine in ("top", "right", "left"):
            axes.spines[spine].set_visible(False)
        axes.spines["bottom"].set_color(text_color)
        axes.spines["bottom"].set_linewidth(0.4)
        axes.margins(x=0.04, y=0.18)

        buffer = io.BytesIO()
        figure.savefig(
            buffer,
            format="png",
            transparent=True,
            bbox_inches="tight",
            pad_inches=0.02,
            metadata={"Software": None},  # keep the bytes stable across runs
        )
        return buffer.getvalue()
    finally:
        plt.close(figure)
