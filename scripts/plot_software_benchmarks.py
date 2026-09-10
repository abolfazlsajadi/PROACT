#!/usr/bin/env python3
"""Render saved synthetic measurements; this script never benchmarks devices."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def main():
    storage = json.loads((REPORTS / "storage_benchmark.json").read_text())
    startup = json.loads((REPORTS / "startup_benchmark.json").read_text())
    gui = json.loads((REPORTS / "gui_responsiveness.json").read_text())
    rows = max(storage["parameters"]["rows"])
    measurements = {s["variant"]: s for s in storage["summary"] if s["rows"] == rows}
    names = ["baseline_npz", "updated_npz", "baseline_h5", "updated_h5", "tracepack"]
    labels = ["Original NPZ", "Atomic NPZ", "Original HDF5", "Atomic HDF5", "Tracepack"]
    colors = ["#9aa7b6", "#416a93", "#9aa7b6", "#416a93", "#178571"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.edgecolor": "#c5ccd4",
                         "text.color": "#172c3f", "axes.labelcolor": "#3d5263"})
    fig, axes = plt.subplots(2, 2, figsize=(13.6, 8.4))
    fig.subplots_adjust(left=.14, right=.97, top=.82, bottom=.19, wspace=.54, hspace=.65)
    fig.text(.035, .952, "PROACT software update", fontsize=23, weight="bold")
    fig.text(.035, .908, "Measured host performance • synthetic data and fake devices only", fontsize=13)

    def bars(ax, labels, values, colors, title, xlabel, spread=None):
        y = list(range(len(labels)))
        ax.barh(y, values, color=colors, height=.62)
        if spread is not None:
            ax.errorbar(values, y, xerr=spread, fmt="none", ecolor="#253d50", capsize=3, lw=1)
        ax.set_yticks(y, labels); ax.invert_yaxis()
        ax.set_title(title, loc="left", pad=13, weight="bold", fontsize=12)
        ax.set_xlabel(xlabel, labelpad=7)
        ax.tick_params(axis="y", length=0)
        ax.set_axisbelow(True); ax.grid(axis="x", color="#e6ebef")
        ax.set_xlim(0, max(values) * 1.26)
        for i, value in enumerate(values):
            ax.text(value + max(values) * .025, i, f"{value:.2f}", va="center", fontsize=10)

    seconds = [measurements[n]["wall_seconds"]["median"] for n in names]
    spread = [[v - measurements[n]["wall_seconds"]["min"] for n, v in zip(names, seconds)],
              [measurements[n]["wall_seconds"]["max"] - v for n, v in zip(names, seconds)]]
    bars(axes[0, 0], labels, seconds, colors, f"Checkpoint writer • {rows:,} rows", "Seconds; median and min–max of 3 trials", spread)
    rss = [measurements[n]["peak_writer_rss_kib"]["median"] / 1024 for n in names]
    bars(axes[0, 1], labels, rss, colors, "Peak writer memory • includes runtime", "MiB; median of 3 trials")
    bars(axes[1, 0], ["Original", "Updated"],
         [startup["median_seconds"][n] * 1000 for n in ("baseline", "updated")],
         ["#9aa7b6", "#178571"], "Fresh-process package import", "Milliseconds; median of 9 trials")
    bars(axes[1, 1], ["Original", "Updated"],
         [gui["results"][n]["median_ms"] for n in ("baseline", "updated")],
         ["#9aa7b6", "#178571"], "GUI Disconnect callback returns", "Milliseconds; median of 5 trials")
    fig.text(.035, .048,
             "Lower is better. Safer atomic snapshots cost time; tracepack changes the storage format.\n"
             "Storage: 600 float32 samples/row, 250-row checkpoints. GUI: a fake lock waits 150 ms. Local results, not live capture rates.",
             fontsize=10, color="#4b5d6c", linespacing=1.5)
    for suffix in ("png", "svg"):
        output = REPORTS / f"software_performance.{suffix}"
        fig.savefig(output, dpi=150, facecolor="white")
        print(output)
    plt.close(fig)


if __name__ == "__main__":
    main()
