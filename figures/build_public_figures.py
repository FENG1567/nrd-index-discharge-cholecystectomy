"""Build the six manuscript figures from public aggregate source files only.

The script never reads licensed NRD records.  It accepts repository-relative
paths so that the same command works on Windows, macOS, and Linux.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle
import numpy as np
import pandas as pd


BLUE, ORANGE, NAVY, GREY, LIGHT, MID, GREEN, RED = (
    "#377eb8", "#e68a2e", "#1f3b5b", "#6e7781", "#e9eef3", "#b9c3cc", "#4d8b68", "#bb5a5a"
)
mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "font.size": 6.3,
        "axes.linewidth": 0.65,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.facecolor": "white",
    }
)


def args() -> argparse.Namespace:
    here = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=here / "public_results")
    parser.add_argument("--output-dir", type=Path, default=here / "outputs" / "figures")
    parser.add_argument("--dpi", type=int, default=600)
    return parser.parse_args()


def read_inputs(data_dir: Path) -> dict[str, object]:
    def csv(name: str) -> pd.DataFrame:
        path = data_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"public aggregate input not found: {path}")
        return pd.read_csv(path)

    summary_path = data_dir / "analysis_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "summary": summary,
        "outcomes": csv("outcome_components.csv"),
        "balance": csv("strict_primary_balance_detail.csv"),
        "ps": csv("strict_primary_ps_overlap_plot_source.csv"),
        "robust": csv("robustness_strict_robustness_results.csv"),
        "baseline": csv("baseline_model_sensitivity.csv"),
        "annual": csv("record_reporting.csv"),
        "hospital": csv("hospital_descriptive.csv"),
    }


def panel(ax: plt.Axes, label: str) -> None:
    ax.text(-0.12, 1.12, label, transform=ax.transAxes, fontsize=8, fontweight="bold", va="top")


def axis(ax: plt.Axes) -> None:
    ax.tick_params(labelsize=5.8, length=2.3, pad=2)
    ax.grid(axis="x", color="#d9dfe5", lw=0.45, zorder=0)


def _strip_svg_metadata(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    stripped = re.sub(r"\s*<metadata>.*?</metadata>", "", text, count=1, flags=re.S)
    if stripped != text:
        path.write_text(stripped, encoding="utf-8")


def _strip_png_software(path: Path) -> None:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return
    out = bytearray(data[:8])
    pos, removed = 8, 0
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        chunk_type = data[pos + 4:pos + 8]
        end = pos + 12 + length
        keep = True
        if chunk_type in (b"tEXt", b"iTXt", b"zTXt"):
            payload = data[pos + 8:pos + 8 + length]
            if b"Matplotlib" in payload or payload.split(b"\x00", 1)[0] == b"Software":
                keep = False
        if keep:
            out += data[pos:end]
        else:
            removed += 1
        pos = end
        if chunk_type == b"IEND":
            break
    if removed:
        path.write_bytes(bytes(out))


def save(fig: plt.Figure, stem: str, output_dir: Path, dpi: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight", metadata={"Creator": "", "Producer": ""})
    fig.savefig(output_dir / f"{stem}.svg", bbox_inches="tight", metadata={"Creator": "", "Date": "", "Title": ""})
    fig.savefig(output_dir / f"{stem}.png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    _strip_svg_metadata(output_dir / f"{stem}.svg")
    _strip_png_software(output_dir / f"{stem}.png")


def box(ax: plt.Axes, xy: tuple[float, float], width: float, height: float, text: str, face: str = "#f7f9fb", edge: str = NAVY, fs: float = 5.6) -> None:
    ax.add_patch(FancyBboxPatch(xy, width, height, boxstyle="round,pad=.012,rounding_size=.018", facecolor=face, edgecolor=edge, lw=0.75))
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, ha="center", va="center", fontsize=fs, wrap=True)


def arrow(ax: plt.Axes, a: tuple[float, float], b: tuple[float, float]) -> None:
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=8, lw=0.75, color=GREY))


def _figure1_panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(0.015, 0.985, label, transform=ax.transAxes, ha="left", va="top", fontsize=8.4, fontweight="bold", color="#26323b")


def _figure1_arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float], color: str = GREY, lw: float = 0.85, mutation: float = 8) -> None:
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=mutation, lw=lw, color=color, shrinkA=0, shrinkB=0))


def _figure1_strip(ax: plt.Axes, x: float, y: float, width: float, height: float, face: str, edge: str, label: str, text_color: str = "#26323b", fs: float = 5.4) -> None:
    ax.add_patch(Rectangle((x, y), width, height, facecolor=face, edgecolor=edge, linewidth=0.7, joinstyle="round"))
    ax.text(x + width / 2, y + height / 2, label, ha="center", va="center", fontsize=fs, color=text_color, linespacing=1.05)


def _draw_figure1_cohort(ax: plt.Axes, summary: dict[str, object]) -> None:
    counts = summary["counts"]
    _figure1_panel_label(ax, "a"); ax.set_axis_off(); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.5, 0.93, "2018-2022 NRD annual files", ha="center", va="center", fontsize=7.0, fontweight="bold", color=NAVY)
    ax.plot([0.11, 0.89], [0.885, 0.885], color=MID, lw=1.0)
    gates = [(0.14, "Adults"), (0.38, "Principal\nK85.10"), (0.62, "Lower-severity\nclaims phenotype"), (0.86, "Live discharge\n+ Jan-Sep")]
    for i, (x, label) in enumerate(gates):
        ax.scatter([x], [0.735], s=115, color="white", edgecolor=NAVY, linewidth=1.1, zorder=4)
        ax.scatter([x], [0.735], s=22, color=GREEN, edgecolor="white", linewidth=0.4, zorder=5)
        ax.text(x, 0.655, label, ha="center", va="top", fontsize=5.55, color="#26323b", linespacing=1.02)
        if i < len(gates) - 1:
            _figure1_arrow(ax, (x + 0.055, 0.735), (gates[i + 1][0] - 0.055, 0.735), color=MID, lw=0.85, mutation=7)
    ax.add_patch(Polygon([[0.19, 0.535], [0.81, 0.535], [0.70, 0.465], [0.30, 0.465]], closed=True, facecolor="#edf2f5", edgecolor=MID, linewidth=0.75))
    ax.text(0.5, 0.505, "Eligibility gates", ha="center", va="center", fontsize=5.2, color=GREY)
    _figure1_arrow(ax, (0.5, 0.465), (0.5, 0.395), color=MID, lw=0.9, mutation=7)
    _figure1_strip(ax, 0.18, 0.305, 0.64, 0.09, "#edf6f0", GREEN, f"Analytic cohort    n = {counts['n']:,}", text_color=NAVY, fs=6.1)
    ax.text(0.5, 0.265, "Discharge-status classification", ha="center", va="center", fontsize=5.3, color=GREY)
    a0, a1, n = int(counts["A0"]), int(counts["A1"]), int(counts["n"])
    a0w, a1w, x0, y0, height = 0.76 * a0 / n, 0.76 * a1 / n, 0.12, 0.145, 0.085
    ax.add_patch(Rectangle((x0, y0), a0w, height, facecolor=BLUE, edgecolor="white", linewidth=0.8))
    ax.add_patch(Rectangle((x0 + a0w, y0), a1w, height, facecolor=ORANGE, edgecolor="white", linewidth=0.8))
    ax.text(x0 + a0w / 2, y0 + height / 2, f"A0  {a0:,}  ({100*a0/n:.1f}%)", ha="center", va="center", fontsize=5.45, color="white", fontweight="bold")
    ax.text(x0 + a0w + a1w / 2, y0 + height / 2, f"A1  {a1:,}  ({100*a1/n:.1f}%)", ha="center", va="center", fontsize=5.45, color="white", fontweight="bold")
    ax.text(x0 + a0w / 2, 0.105, "No recorded\nindex-admission cholecystectomy", ha="center", va="top", fontsize=5.0, color=BLUE, linespacing=1.0)
    ax.text(x0 + a0w + a1w / 2, 0.105, "Completed by\nindex discharge", ha="center", va="top", fontsize=5.0, color=ORANGE, linespacing=1.0)
    ax.text(0.5, 0.025, "A0 and A1 are defined before post-discharge follow-up", ha="center", va="bottom", fontsize=5.05, color=GREY)


def _draw_figure1_timeline(ax: plt.Axes, summary: dict[str, object]) -> None:
    counts, primary = summary["counts"], summary["primary"]
    _figure1_panel_label(ax, "b"); ax.set_axis_off(); ax.set_xlim(-0.04, 1.10); ax.set_ylim(0, 1)
    ax.text(0.50, 0.93, "Index stay to post-discharge follow-up", ha="center", va="center", fontsize=7.0, fontweight="bold", color=NAVY)
    ax.axvspan(0.54, 1.02, ymin=0.12, ymax=0.86, facecolor="#f7f9fa", edgecolor="none", zorder=0)
    ax.text(0.78, 0.855, "90-day inpatient outcome window", ha="center", va="center", fontsize=5.0, color=GREY)
    xs = [0.04, 0.30, 0.54, 0.70, 0.84, 0.98]
    labels = ["Admission", "Procedure\nwindow", "Discharge\n(time zero)", "Day 30", "Day 60", "Day 90"]
    for x, label in zip(xs, labels):
        ax.plot([x, x], [0.26, 0.77], color="#d5dce1", lw=0.55, zorder=1)
        ax.text(x, 0.19, label, ha="center", va="top", fontsize=5.15, color="#26323b", linespacing=1.0)
    y0, y1 = 0.68, 0.42
    ax.plot([xs[0], xs[-1]], [y0, y0], color=BLUE, lw=2.0, solid_capstyle="round", zorder=2)
    ax.plot([xs[0], xs[-1]], [y1, y1], color=ORANGE, lw=2.0, solid_capstyle="round", zorder=2)
    for y, color in [(y0, BLUE), (y1, ORANGE)]:
        ax.scatter(xs, [y] * len(xs), s=20, facecolor="white", edgecolor=color, linewidth=0.9, zorder=3)
        ax.scatter([xs[2]], [y], s=33, facecolor=GREEN, edgecolor="white", linewidth=0.8, zorder=4)
    ax.text(-0.005, y0, "A0", ha="right", va="center", fontsize=7.0, fontweight="bold", color=BLUE)
    ax.text(-0.005, y1, "A1", ha="right", va="center", fontsize=7.0, fontweight="bold", color=ORANGE)
    ax.text(0.035, y0 + 0.075, f"n = {counts['A0']:,}", ha="left", va="center", fontsize=5.15, color=BLUE)
    ax.text(0.035, y1 - 0.075, f"n = {counts['A1']:,}", ha="left", va="center", fontsize=5.15, color=ORANGE)
    ax.scatter([0.30], [y1], s=68, facecolor=ORANGE, edgecolor="white", linewidth=0.8, zorder=5); ax.text(0.30, y1, "+", ha="center", va="center", fontsize=7.0, color="white", fontweight="bold", zorder=6)
    ax.text(0.30, y1 - 0.105, "0FT4* recorded", ha="center", va="top", fontsize=5.1, color=ORANGE)
    ax.scatter([0.30], [y0], s=52, facecolor="white", edgecolor=BLUE, linewidth=0.9, zorder=5)
    ax.plot([0.285, 0.315], [y0 - 0.015, y0 + 0.015], color=BLUE, lw=0.9, zorder=6); ax.plot([0.285, 0.315], [y0 + 0.015, y0 - 0.015], color=BLUE, lw=0.9, zorder=6)
    ax.text(0.30, y0 + 0.105, "no 0FT4* recorded", ha="center", va="bottom", fontsize=5.1, color=BLUE)
    ax.plot([0.575, 0.575], [y1, y0], color=NAVY, lw=0.85, clip_on=False); ax.plot([0.575, 0.610], [y0, y0], color=NAVY, lw=0.85, clip_on=False); ax.plot([0.575, 0.610], [y1, y1], color=NAVY, lw=0.85, clip_on=False)
    ax.text(0.615, 0.55, f"90-day standardized\nrisk difference\nA0 - A1 = {primary['rd_percent']:.3f} pp\n(95% CI {primary['ci_low_percent']:.3f}-{primary['ci_high_percent']:.3f})", ha="left", va="center", fontsize=5.45, color=NAVY, linespacing=1.03)
    ax.text(0.52, 0.055, "Adjusted discharge-status association among live-discharge survivors; not an admission-time strategy effect.", ha="center", va="bottom", fontsize=5.0, color="#634f31")


def figure1(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    fig = plt.figure(figsize=(7.2, 4.35), constrained_layout=True)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.06, 1.72], wspace=0.10)
    _draw_figure1_cohort(fig.add_subplot(grid[0, 0]), d["summary"])
    _draw_figure1_timeline(fig.add_subplot(grid[0, 1]), d["summary"])
    save(fig, "Figure_1", output_dir, dpi)


def figure2(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    balance, ps, annual = d["balance"], d["ps"], d["annual"].query("year != 'overall'").copy()
    # A two-row composition gives the dense balance and propensity panels enough
    # horizontal room.  Panel c spans the lower row so the annual trend remains
    # legible without changing any underlying values.
    fig = plt.figure(figsize=(7.2, 5.9), constrained_layout=True)
    grid = fig.add_gridspec(
        2, 2,
        height_ratios=[1.24, 0.76],
        width_ratios=[1.12, 1.0],
        wspace=0.38,
        hspace=0.28,
    )
    ax = fig.add_subplot(grid[0, 0]); panel(ax, "a")
    wanted = ["AGE", "prior_admissions_90d", "prior_biliary_90d", "FEMALE", "PAY1", "HOSP_URCAT4", "HOSP_UR_TEACH", "year"]
    x = balance[balance.variable.isin(wanted)].copy(); x["label"] = np.where(x.level.astype(str).eq("continuous"), x.variable, x.variable + "=" + x.level.astype(str))
    order = x[x.weighting == "unweighted"].sort_values("smd", key=lambda z: z.abs()).label.tolist()
    pre = x[x.weighting == "unweighted"].set_index("label").loc[order]; post = x[x.weighting == "overlap_weighted"].set_index("label").loc[order]; y = np.arange(len(order))
    ax.scatter(pre.smd.abs(), y, color="#9ba7b2", s=16, label="Unweighted", zorder=3); ax.scatter(post.smd.abs(), y, color=BLUE, s=16, label="Overlap weighted", zorder=3); ax.axvline(0.1, color=RED, ls="--", lw=0.8)
    ax.set_yticks(y, [z.replace("_", " ") for z in order], fontsize=5); ax.set_xlabel("Absolute standardized mean difference"); ax.set_xlim(-0.018, max(0.37, float(pre.smd.abs().max()) * 1.15)); ax.invert_yaxis(); axis(ax); ax.legend(loc="lower right", fontsize=5.1, handletextpad=0.3, borderpad=0.2); ax.set_title("Selected covariate balance", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[0, 1]); panel(ax, "b"); ps = ps.copy(); ps["center"] = (ps.bin_lower + ps.bin_upper) / 2; width = float((ps.bin_upper - ps.bin_lower).iloc[0]) * 0.92
    for arm, color, label, sign in [(0, BLUE, "A0", 1), (1, ORANGE, "A1", -1)]:
        part = ps[ps.A == arm]; ax.bar(part.center, sign * part.weighted_fraction, width=width, color=color, alpha=0.82, label=label, lw=0)
    ax.axhline(0, color="#525a61", lw=0.65); ax.set(xlim=(-0.025, 1.025), ylim=(-0.26, 0.26), xlabel="Estimated propensity score", ylabel="Weighted fraction\n(A0 above; A1 below)"); axis(ax); ax.legend(loc="upper right", fontsize=5.2); ax.set_title("Aggregate overlap after weighting", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, :]); panel(ax, "c"); annual["completion"] = annual.A1_n / annual.analysis_n * 100; years = annual.year.astype(int).to_numpy(); ax.bar(years, annual.analysis_n.to_numpy() / 1000, color="#c9d4de", width=0.62); ax.set(ylabel="Eligible cohort, thousands", xlabel="Discharge year"); ax.set_xticks(years); axis(ax); second = ax.twinx(); second.plot(years, annual.completion, color=ORANGE, marker="o", lw=1.25, ms=3.3); second.set(ylim=(0, 75), ylabel="A1 completion, %"); second.tick_params(axis="y", colors=ORANGE, labelsize=5.8, length=2.3)
    for year, rate in zip(years, annual.completion): second.text(year, rate + 2, f"{rate:.1f}", ha="center", color=ORANGE, fontsize=5)
    ax.text(0.02, 0.97, "Descriptive only", transform=ax.transAxes, va="top", fontsize=5.1, color=GREY); ax.set_title("Annual cohort and completion reporting", fontsize=7.4, loc="left", pad=10, fontweight="bold")
    save(fig, "Figure_2", output_dir, dpi)


def _endpoint_rows(outcomes: pd.DataFrame) -> list[pd.Series]:
    wanted = ["y90_component_k80", "y90_component_k81", "y90_component_k830", "y90_component_k85_total", "y90_component_k851"]
    return [outcomes.loc[outcomes.outcome == name].iloc[0] for name in wanted]


def figure3(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    summary, outcomes = d["summary"], d["outcomes"]
    primary = summary["primary"]; rows = _endpoint_rows(outcomes)
    # Use a two-row composition so the long diagnostic labels in panel b and
    # the event-count labels in panel c are not compressed into narrow columns.
    fig = plt.figure(figsize=(7.2, 5.9), constrained_layout=True)
    grid = fig.add_gridspec(
        2, 2,
        height_ratios=[1.05, 1.25],
        width_ratios=[0.92, 1.28],
        wspace=0.32,
        hspace=0.42,
    )
    ax = fig.add_subplot(grid[0, 0]); panel(ax, "a"); values = [primary["risk_A0_percent"], primary["risk_A1_percent"]]; ax.bar([0, 1], values, color=[BLUE, ORANGE], width=0.62); ax.set(xticks=[0, 1], xticklabels=["A0", "A1"], ylim=(0, 13.5), ylabel="Standardized 90-day risk, %")
    for x, value in enumerate(values): ax.text(x, value + 0.35, f"{value:.3f}%", ha="center", fontsize=6.2, fontweight="bold")
    ax.text(0.5, 0.72, f"A0 minus A1\n{primary['rd_percent']:.3f} percentage points\n95% CI {primary['ci_low_percent']:.3f} to {primary['ci_high_percent']:.3f}", transform=ax.transAxes, ha="center", va="center", fontsize=5.5, bbox=dict(boxstyle="round,pad=.3", facecolor="#f4f7f9", edgecolor=MID, lw=0.5)); axis(ax); ax.set_title("Primary standardized risks", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[0, 1]); panel(ax, "b")
    labels = ["Primary composite", "Cholelithiasis (K80*)", "Cholecystitis (K81*)", "Cholangitis (K83.0)", "Acute pancreatitis (K85*)", "Biliary acute pancreatitis (K85.1*)"]
    estimates = [(primary["rd_percent"], primary["ci_low_percent"], primary["ci_high_percent"], NAVY)] + [(round(row.rd_A0_minus_A1 * 100, 3), round(row.fixed_score_ci_low * 100, 3), round(row.fixed_score_ci_high * 100, 3), GREY) for row in rows]
    y = np.arange(len(estimates))[::-1]
    for yy, (rd, lo, hi, color) in zip(y, estimates):
        ax.plot([lo, hi], [yy, yy], color=color, lw=1.15); ax.scatter(rd, yy, color=color, s=22, zorder=3)
    ax.axvline(0, color="#4f5961", lw=0.65); ax.set_yticks(y, labels, fontsize=5.25); ax.set(xlim=(-0.25, 10.1), xlabel="A0 minus A1 adjusted association risk difference, percentage points"); axis(ax); ax.text(0.03, 0.92, "Primary: corrected full-refit CI\nComponents: supportive fixed-score CI", transform=ax.transAxes, fontsize=5.0, va="top", color=GREY, bbox=dict(boxstyle="round,pad=.18", facecolor="white", edgecolor="none", alpha=0.9)); ax.set_title("Primary and diagnostic-component associations", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, :]); panel(ax, "c"); components = ["biliary_composite", "k80", "k81", "k830", "k85_total", "k851"]; label_map = {"biliary_composite": "Primary composite", "k80": "K80*", "k81": "K81*", "k830": "K83.0", "k85_total": "K85*", "k851": "K85.1*"}; event_rows = {str(r.component): r for r in outcomes.itertuples()}; y = np.arange(len(components))[::-1]; n0, n1, text0, text1 = [], [], [], []
    for component in components:
        row = event_rows[component] if component in event_rows else event_rows.get(component, None)
        if row is None:
            row = next(r for r in outcomes.itertuples() if r.outcome == "y90_component_" + component)
        a0, a1 = str(row.events_A0), str(row.events_A1); text0.append(a0); text1.append(a1); n0.append(10 if a0 == "<11" else float(a0)); n1.append(10 if a1 == "<11" else float(a1))
    ax.barh(y + 0.18, n0, height=0.32, color=BLUE, label="A0"); ax.barh(y - 0.18, n1, height=0.32, color=ORANGE, label="A1"); ax.set_xscale("log"); ax.set(xlim=(7, max(7000, max(n0 + n1) * 1.35)), yticks=y, yticklabels=[label_map[x] for x in components], xlabel="Observed event count (log scale)"); axis(ax)
    for yy, x0, x1, t0, t1 in zip(y, n0, n1, text0, text1): ax.text(x0 * 1.1, yy + 0.18, t0, va="center", fontsize=5.0, color=BLUE); ax.text(x1 * 1.1, yy - 0.18, t1, va="center", fontsize=5.0, color=ORANGE)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=5); ax.set_title("Observed component events\n(non-mutually exclusive; <11 suppressed)", fontsize=6.7, loc="left", pad=10, fontweight="bold")
    save(fig, "Figure_3", output_dir, dpi)


def figure4(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    summary, robust, baseline = d["summary"], d["robust"], d["baseline"]
    fig = plt.figure(figsize=(7.2, 4.85), constrained_layout=True); grid = fig.add_gridspec(2, 2, height_ratios=[1.18, 0.95], wspace=0.34, hspace=0.35)
    order = ["resident_only", "aprdrg_severity_1_2", "exclude_all_J96", "broad_K851_phenotype", "chronic_proxy_main_cohort", "legacy_expanded_main_cohort"]
    labels = {"resident_only": "Resident only", "aprdrg_severity_1_2": "APR-DRG severity 1-2", "exclude_all_J96": "Exclude all J96", "broad_K851_phenotype": "Broad K85.1* phenotype", "chronic_proxy_main_cohort": "Chronic-proxy adjustment", "legacy_expanded_main_cohort": "Expanded adjustment set"}
    ax = fig.add_subplot(grid[0, 0]); panel(ax, "a"); rr = robust.set_index("sensitivity"); y = np.arange(len(order))[::-1]
    ax.axvline(summary["primary"]["rd_percent"], color=NAVY, ls="--", lw=0.85, label="Primary point estimate")
    for yy, name in zip(y, order):
        if name not in rr.index: continue
        row = rr.loc[name]; ax.plot([row.fixed_score_ci_low * 100, row.fixed_score_ci_high * 100], [yy, yy], color=GREY, lw=1); ax.scatter(row.rd_A0_minus_A1 * 100, yy, color=GREEN, s=18, zorder=3)
    ax.set(yticks=y, yticklabels=[labels[x] for x in order], xlim=(7.8, 10.0), xlabel="A0 minus A1 risk difference, percentage points"); axis(ax); ax.legend(loc="lower right", fontsize=5.0); ax.set_title("Feasible strict robustness analyses", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[0, 1]); panel(ax, "b"); base_rows = [baseline.iloc[0], baseline.iloc[1], baseline.iloc[2]]; names = ["Corrected full refit\n(primary)", "Strict fixed score\n(supportive)", "Strict EIF\n(supportive)"]; vals = [(summary["primary"]["rd_percent"], summary["primary"]["ci_low_percent"], summary["primary"]["ci_high_percent"], NAVY), (base_rows[0].rd_A0_minus_A1 * 100, base_rows[0].fixed_score_ci_low * 100, base_rows[0].fixed_score_ci_high * 100, GREY), (base_rows[0].rd_A0_minus_A1 * 100, base_rows[0].eif_ci_low * 100, base_rows[0].eif_ci_high * 100, MID)]; y = np.arange(3)[::-1]
    for yy, (rd, lo, hi, color) in zip(y, vals): ax.plot([lo, hi], [yy, yy], color=color, lw=1.25); ax.scatter(rd, yy, color=color, s=23, zorder=3)
    ax.set(yticks=y, yticklabels=names, xlim=(7.9, 9.2), xlabel="Risk difference, percentage points"); axis(ax); ax.set_title("Primary uncertainty comparison", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, 0]); panel(ax, "c"); max_smd = [float(row.max_abs_smd) for _, row in baseline.iterrows()]; ax.bar(range(len(max_smd)), max_smd, color=[BLUE, GREEN, GREY], width=0.6); ax.axhline(0.1, color=RED, ls="--", lw=0.75); ax.set(ylim=(0, 0.012), xticks=range(len(max_smd)), xticklabels=["Strict", "Chronic proxy", "Expanded"], ylabel="Maximum weighted |SMD|"); axis(ax)
    for i, value in enumerate(max_smd): ax.text(i, value + 0.00045, f"{value:.4f}", ha="center", fontsize=5.2)
    ax.set_title("Maximum weighted imbalance", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, 1]); panel(ax, "d"); a0 = [float(row.ess_A0) / 1000 for _, row in baseline.iterrows()]; a1 = [float(row.ess_A1) / 1000 for _, row in baseline.iterrows()]; x = np.arange(len(a0)); width = 0.34; ax.bar(x - width / 2, a0, width, color=BLUE, label="A0"); ax.bar(x + width / 2, a1, width, color=ORANGE, label="A1"); ax.set(xticks=x, xticklabels=["Strict", "Chronic proxy", "Expanded"], ylabel="Effective sample size, thousands"); axis(ax); ax.legend(fontsize=5, loc="upper right"); ax.set_title("Overlap-weighted effective sample size", fontsize=7.4, loc="left", pad=10, fontweight="bold")
    save(fig, "Figure_4", output_dir, dpi)


def figure5(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    """Effect-scale and uncertainty diagnostics from aggregate public results."""
    summary, outcomes, baseline = d["summary"], d["outcomes"], d["baseline"]
    primary = summary["primary"]
    fig = plt.figure(figsize=(7.2, 4.8), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, wspace=0.34, hspace=0.42)

    ax = fig.add_subplot(grid[0, 0]); panel(ax, "a")
    names = ["Primary", "K80*", "K81*", "K83.0", "K85*", "K85.1*"]
    rows = [None] + [outcomes.loc[outcomes.outcome == f"y90_component_{x}"].iloc[0] for x in ["k80", "k81", "k830", "k85_total", "k851"]]
    rds = [primary["rd_percent"]] + [float(r.rd_A0_minus_A1) * 100 for r in rows[1:]]
    rrs = [primary["rr_A0_over_A1"]] + [float(r.rr_A0_over_A1) for r in rows[1:]]
    y = np.arange(len(names))[::-1]
    ax.scatter(rds, y, s=27, color=[NAVY] + [GREY] * 5, zorder=3)
    for yy, rd, rr in zip(y, rds, rrs): ax.text(rd + 0.12, yy, f"RD {rd:.2f}; RR {rr:.2f}", va="center", fontsize=5.1)
    ax.axvline(0, color="#4f5961", lw=0.6); ax.set(yticks=y, yticklabels=names, xlim=(-0.5, 10.0), xlabel="A0 minus A1 association, percentage points"); axis(ax)
    ax.set_title("Absolute and relative effect scales", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[0, 1]); panel(ax, "b")
    method_names = ["Corrected full refit", "Fixed-score bootstrap", "EIF comparison"]
    method_vals = [(primary["ci_low_percent"], primary["ci_high_percent"]), (float(baseline.iloc[0].fixed_score_ci_low) * 100, float(baseline.iloc[0].fixed_score_ci_high) * 100), (float(baseline.iloc[0].eif_ci_low) * 100, float(baseline.iloc[0].eif_ci_high) * 100)]
    cols = [NAVY, GREY, MID]; y = np.arange(3)[::-1]
    for yy, (lo, hi), col in zip(y, method_vals, cols): ax.plot([lo, hi], [yy, yy], color=col, lw=1.35); ax.scatter([(lo + hi) / 2], [yy], color=col, s=21, zorder=3)
    ax.axvline(primary["rd_percent"], color=NAVY, ls="--", lw=0.75); ax.set(yticks=y, yticklabels=method_names, xlim=(7.9, 9.2), xlabel="90-day primary association, percentage points"); axis(ax)
    ax.set_title("Primary uncertainty by interval method", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, 0]); panel(ax, "c")
    labels = ["Strict", "Chronic proxy", "Expanded"]
    x = np.arange(3); width = 0.34
    ax.bar(x - width / 2, baseline.ess_A0 / 1000, width, color=BLUE, label="A0")
    ax.bar(x + width / 2, baseline.ess_A1 / 1000, width, color=ORANGE, label="A1")
    ax.set(xticks=x, xticklabels=labels, ylabel="Effective sample size, thousands"); axis(ax); ax.legend(fontsize=5, loc="upper right")
    ax.set_title("Information retained after overlap weighting", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, 1]); panel(ax, "d")
    comp_names = ["Primary", "K80*", "K81*", "K83.0", "K85*", "K85.1*"]
    comp_ids = ["biliary_composite", "k80", "k81", "k830", "k85_total", "k851"]
    lookup = {str(r.component): r for r in outcomes.itertuples()}
    a0, a1, t0, t1 = [], [], [], []
    for cid in comp_ids:
        row = lookup[cid] if cid in lookup else next(r for r in outcomes.itertuples() if r.outcome == "y90_component_" + cid)
        t0.append(str(row.events_A0)); t1.append(str(row.events_A1)); a0.append(10 if t0[-1] == "<11" else float(t0[-1])); a1.append(10 if t1[-1] == "<11" else float(t1[-1]))
    y = np.arange(len(comp_names))[::-1]
    ax.barh(y + 0.17, a0, height=0.30, color=BLUE, label="A0"); ax.barh(y - 0.17, a1, height=0.30, color=ORANGE, label="A1"); ax.set_xscale("log"); ax.set(yticks=y, yticklabels=comp_names, xlim=(7, max(7000, max(a0 + a1) * 1.35)), xlabel="Observed event count (log scale)"); axis(ax)
    for yy, x0, x1, s0, s1 in zip(y, a0, a1, t0, t1): ax.text(x0 * 1.1, yy + 0.17, s0, va="center", fontsize=5.0, color=BLUE); ax.text(x1 * 1.1, yy - 0.17, s1, va="center", fontsize=5.0, color=ORANGE)
    ax.legend(fontsize=5, loc="upper left"); ax.set_title("Event-count support for component estimates", fontsize=7.4, loc="left", pad=10, fontweight="bold")
    save(fig, "Figure_5", output_dir, dpi)


def figure6(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    """Descriptive hospital-practice and annual record-reporting panels."""
    annual = d["annual"].query("year != 'overall'").copy(); hosp = d["hospital"]
    # The completion-rate and annual-count panels carry horizontal annotations;
    # placing them in a two-row layout avoids compressing those annotations into
    # slender columns while preserving the three-panel reading order.
    fig = plt.figure(figsize=(7.2, 5.75), constrained_layout=True)
    grid = fig.add_gridspec(
        2, 2,
        height_ratios=[0.92, 1.16],
        width_ratios=[1.0, 1.18],
        wspace=0.34,
        hspace=0.36,
    )

    ax = fig.add_subplot(grid[0, 0]); panel(ax, "a")
    nvals = [10, 10, 13]; xpos = np.arange(3); ax.bar(xpos, nvals, color="#c9d4de", width=0.58); ax.set(xticks=xpos, xticklabels=["P25", "Median", "P75"], xlabel="Hospital-year patient count", ylabel="Aggregate count"); axis(ax); ax.set_ylim(0, 15)
    for x, v, lab in zip(xpos, nvals, ["<11", "<11", "13"]): ax.text(x, v + 0.5, lab, ha="center", fontsize=5.5)
    ax.text(0.02, 0.96, "P25 and median are suppressed as <11.", transform=ax.transAxes, va="top", fontsize=5.0, color=GREY)
    ax.set_title("Hospital-year sample size", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[0, 1]); panel(ax, "b")
    vals = [float(hosp.q25_completion_rate.iloc[0]) * 100, float(hosp.median_completion_rate.iloc[0]) * 100, float(hosp.q75_completion_rate.iloc[0]) * 100, float(hosp.min_completion_rate.iloc[0]) * 100, float(hosp.max_completion_rate.iloc[0]) * 100]
    ax.plot([vals[3], vals[4]], [1, 1], color=MID, lw=2.0); ax.plot([vals[0], vals[2]], [1, 1], color=NAVY, lw=6.0, solid_capstyle="butt"); ax.scatter(vals[1], 1, color=ORANGE, s=30, zorder=3); ax.set(yticks=[1], yticklabels=["Hospital-year"], xlim=(-5, 105), ylim=(0.72, 1.32), xlabel="A1 completion rate, %"); axis(ax); ax.text(vals[1], 1.16, f"median {vals[1]:.1f}%", ha="center", fontsize=5.2, color=ORANGE)
    ax.set_title("Completion-rate range", fontsize=7.4, loc="left", pad=10, fontweight="bold")

    ax = fig.add_subplot(grid[1, :]); panel(ax, "c")
    years = annual.year.astype(int).to_numpy(); ax.plot(years, annual.primary_events_A0, marker="o", color=BLUE, lw=1.35, label="A0"); ax.plot(years, annual.primary_events_A1, marker="o", color=ORANGE, lw=1.35, label="A1"); ax.set(xticks=years, xlabel="Discharge year", ylabel="Observed primary events"); axis(ax); ax.legend(fontsize=5.2, loc="upper right")
    ax.set_title(f"Annual event counts\n(n = {int(hosp.hospital_years.iloc[0]):,} hospital-years; descriptive summary)", fontsize=7.4, loc="left", pad=10, fontweight="bold")
    save(fig, "Figure_6", output_dir, dpi)


def supplementary1(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    balance = d["balance"].copy(); balance["label"] = np.where(balance.level.astype(str).eq("continuous"), balance.variable, balance.variable + "=" + balance.level.astype(str)); order = balance[balance.weighting == "unweighted"].sort_values("smd", key=lambda z: z.abs()).label.tolist(); pre = balance[balance.weighting == "unweighted"].set_index("label").loc[order]; post = balance[balance.weighting == "overlap_weighted"].set_index("label").loc[order]; y = np.arange(len(order)); fig, ax = plt.subplots(figsize=(7.2, 7), constrained_layout=True); panel(ax, "a"); ax.scatter(pre.smd.abs(), y, s=9, color="#aeb8c1", label="Unweighted"); ax.scatter(post.smd.abs(), y, s=9, color=BLUE, label="Overlap weighted"); ax.axvline(0.1, color=RED, ls="--", lw=0.75); ax.set_yticks(y, [z.replace("_", " ") for z in order], fontsize=5.0); ax.invert_yaxis(); ax.set(xlim=(0, 0.38), xlabel="Absolute standardized mean difference"); axis(ax); ax.legend(loc="lower right", fontsize=5.2); ax.set_title("Supplementary Figure S1 | Full observed-covariate balance", fontsize=8, loc="left", pad=12, fontweight="bold"); save(fig, "Supplementary_Figure_S1", output_dir, dpi)


def supplementary2(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    annual = d["annual"].query("year != 'overall'").copy(); annual["r0"] = annual.primary_events_A0 / annual.A0_n * 100; annual["r1"] = annual.primary_events_A1 / annual.A1_n * 100; years = annual.year.astype(int); fig, ax = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True); panel(ax, "a"); ax.plot(years, annual.r0, marker="o", color=BLUE, lw=1.4, label="A0"); ax.plot(years, annual.r1, marker="o", color=ORANGE, lw=1.4, label="A1")
    for x, y0, y1 in zip(years, annual.r0, annual.r1): ax.text(x, y0 + 0.25, f"{y0:.1f}", ha="center", color=BLUE, fontsize=5.1); ax.text(x, y1 - 0.55, f"{y1:.1f}", ha="center", color=ORANGE, fontsize=5.1)
    ax.set(xticks=years, xlabel="Discharge year", ylabel="Crude observed primary-event proportion, %", ylim=(0, max(13, annual.r0.max() + 1.5))); axis(ax); ax.legend(loc="center right", bbox_to_anchor=(0.99, 0.63), fontsize=5.5); ax.text(0.02, 0.96, "Descriptive only; not standardized or adjusted.", transform=ax.transAxes, va="top", fontsize=5.25, color=GREY); ax.set_title("Supplementary Figure S2 | Annual crude observed primary-event proportions", fontsize=8, loc="left", pad=12, fontweight="bold"); save(fig, "Supplementary_Figure_S2", output_dir, dpi)


def supplementary3(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    robust = d["robust"]; order = ["resident_only", "aprdrg_severity_1_2", "exclude_all_J96", "broad_K851_phenotype", "chronic_proxy_main_cohort", "legacy_expanded_main_cohort"]
    labels = {"resident_only": "Resident only", "aprdrg_severity_1_2": "APR-DRG 1-2", "exclude_all_J96": "Exclude J96", "broad_K851_phenotype": "Broad K85.1*", "chronic_proxy_main_cohort": "Chronic proxy", "legacy_expanded_main_cohort": "Expanded set"}
    rr = robust.set_index("sensitivity"); fig, ax = plt.subplots(figsize=(7.2, 4.3), constrained_layout=True); panel(ax, "a"); y = np.arange(len(order))[::-1]
    for yy, name in zip(y, order):
        row = rr.loc[name]; ax.plot([row.fixed_score_ci_low * 100, row.fixed_score_ci_high * 100], [yy, yy], color=GREY, lw=1.2); ax.scatter(row.rd_A0_minus_A1 * 100, yy, color=GREEN, s=20, zorder=3)
    ax.axvline(float(d["summary"]["primary"]["rd_percent"]), color=NAVY, ls="--", lw=0.8); ax.set(yticks=y, yticklabels=[labels[x] for x in order], xlim=(7.8, 10.0), xlabel="A0 minus A1 risk difference, percentage points"); axis(ax); ax.text(0.02, 0.03, "Supportive fixed-score intervals; canonical full-refit interval is reported in the main text.", transform=ax.transAxes, fontsize=5.4, color=GREY); ax.set_title("Supplementary Figure S3 | Complete robustness interval display", fontsize=8, loc="left", pad=12, fontweight="bold"); save(fig, "Supplementary_Figure_S3", output_dir, dpi)


def supplementary4(d: dict[str, object], output_dir: Path, dpi: int) -> None:
    annual = d["annual"].query("year != 'overall'").copy(); years = annual.year.astype(int).to_numpy(); fig, ax = plt.subplots(figsize=(7.2, 4.0), constrained_layout=True); panel(ax, "a")
    stages = [("Adult records", "adult_records"), ("Principal diagnosis", "diagnosis_match"), ("No severe proxy", "no_severe_proxy"), ("Survived discharge", "survived_discharge"), ("Jan-Sep discharge", "discharge_month_1_to_9")]
    for label, col in stages: ax.plot(years, annual[col] / annual.raw_records * 100, marker="o", lw=1.15, label=label)
    ax.set(xticks=years, xlabel="Discharge year", ylabel="Retained records from annual raw files, %", ylim=(0, 105)); axis(ax); ax.legend(fontsize=5.1, ncol=2, loc="lower left"); ax.text(0.02, 0.96, "Descriptive cohort-reporting denominators; not patient-level missingness estimates.", transform=ax.transAxes, va="top", fontsize=5.2, color=GREY); ax.set_title("Supplementary Figure S4 | Annual record-reporting and hospital-practice diagnostics", fontsize=8, loc="left", pad=12, fontweight="bold"); save(fig, "Supplementary_Figure_S4", output_dir, dpi)


def main() -> int:
    parsed = args(); data = read_inputs(parsed.data_dir.resolve()); output = parsed.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    figure1(data, output, parsed.dpi); figure2(data, output, parsed.dpi); figure3(data, output, parsed.dpi); figure4(data, output, parsed.dpi); figure5(data, output, parsed.dpi); figure6(data, output, parsed.dpi); supplementary1(data, output, parsed.dpi); supplementary2(data, output, parsed.dpi); supplementary3(data, output, parsed.dpi); supplementary4(data, output, parsed.dpi)
    expected = [f"{stem}.{ext}" for stem in ["Figure_1", "Figure_2", "Figure_3", "Figure_4", "Figure_5", "Figure_6", "Supplementary_Figure_S1", "Supplementary_Figure_S2", "Supplementary_Figure_S3", "Supplementary_Figure_S4"] for ext in ["pdf", "png", "svg"]]
    missing = [name for name in expected if not (output / name).is_file() or (output / name).stat().st_size == 0]
    if missing: raise RuntimeError("figure build did not produce: " + ", ".join(missing))
    print(json.dumps({"status": "PASS", "output_dir": str(output), "files": len(expected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
