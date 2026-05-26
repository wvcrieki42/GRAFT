#!/usr/bin/env python3
"""Build Figure 1 (schema diagram) and Figure 3 (CPC class distribution)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np

OUT = Path("/Users/wimvancriekinge/treeoflife/data")


# ============================ FIGURE 1: SCHEMA ============================

def figure_schema():
    fig, ax = plt.subplots(figsize=(12, 7.0))
    ax.set_xlim(0, 12); ax.set_ylim(0, 7.0); ax.axis("off")

    # Generous spacing so edge labels never overlap a box.
    nodes = {
        "Taxon":       (3.6, 4.8, "#1f6feb", "white"),
        "Vernacular":  (1.5, 1.8, "#2da44e", "white"),
        "Patent":      (7.0, 4.8, "#bf8700", "white"),
        "IPCClass":    (10.0, 5.8, "#8250df", "white"),
        "CPCClass":    (10.0, 3.6, "#cf222e", "white"),
    }
    NODE_W, NODE_H = 1.8, 0.85

    def draw_node(label, x, y, fc, tc, sub=None):
        box = FancyBboxPatch((x - NODE_W/2, y - NODE_H/2), NODE_W, NODE_H,
                             boxstyle="round,pad=0.04,rounding_size=0.14",
                             facecolor=fc, edgecolor="black", linewidth=1.1)
        ax.add_patch(box)
        ax.text(x, y + 0.12, f":{label}", ha="center", va="center",
                color=tc, fontsize=11.5, fontweight="bold")
        if sub:
            ax.text(x, y - 0.18, sub, ha="center", va="center",
                    color=tc, fontsize=7.2, fontstyle="italic", linespacing=1.15)

    sub_text = {
        "Taxon":      "ottId, name, rank,\nncbiTaxId, gbifTaxKey",
        "Vernacular": "name, language,\nsource",
        "Patent":     "pubNumber, title,\ncountry, pubDate",
        "IPCClass":   "code, definition,\nsection, sectionTitle",
        "CPCClass":   "code, definition,\nsection, sectionTitle",
    }
    for label, (x, y, fc, tc) in nodes.items():
        draw_node(label, x, y, fc, tc, sub=sub_text.get(label))

    def box_edge_point(node, target):
        """Return the point on `node`'s bounding rectangle nearest to `target`."""
        nx, ny, *_ = nodes[node]
        tx, ty, *_ = nodes[target] if isinstance(target, str) else (*target, None, None)
        dx, dy = tx - nx, ty - ny
        if dx == 0 and dy == 0:
            return nx, ny
        # Intersect ray with rectangle
        scale_x = (NODE_W/2) / abs(dx) if dx else float("inf")
        scale_y = (NODE_H/2) / abs(dy) if dy else float("inf")
        s = min(scale_x, scale_y)
        return nx + s*dx, ny + s*dy

    def edge(a, b, label, label_offset=(0, 0.32), curve=0.0):
        ax1, ay1 = box_edge_point(a, b)
        ax2, ay2 = box_edge_point(b, a)
        arr = FancyArrowPatch((ax1, ay1), (ax2, ay2),
                              connectionstyle=f"arc3,rad={curve}",
                              arrowstyle="-|>", mutation_scale=14,
                              linewidth=1.2, color="#444")
        ax.add_patch(arr)
        mx, my = (ax1 + ax2)/2, (ay1 + ay2)/2
        ax.text(mx + label_offset[0], my + label_offset[1], label,
                ha="center", va="center", fontsize=9, fontstyle="italic",
                color="#222",
                bbox=dict(facecolor="white", edgecolor="#bbb", boxstyle="round,pad=0.15", linewidth=0.5))

    # Self-loop for HAS_PARENT — anchored to the TOP of :Taxon, arc curls upward, label above the arc.
    tx, ty, *_ = nodes["Taxon"]
    loop_start = (tx - 0.18, ty + NODE_H/2 - 0.02)
    loop_end   = (tx + 0.18, ty + NODE_H/2 - 0.02)
    self_arc = FancyArrowPatch(loop_start, loop_end,
                               connectionstyle="arc3,rad=-2.3",
                               arrowstyle="-|>", mutation_scale=14,
                               linewidth=1.2, color="#444")
    ax.add_patch(self_arc)
    ax.text(tx, ty + NODE_H/2 + 0.70, "HAS_PARENT",
            ha="center", va="center", fontsize=9, fontstyle="italic", color="#222",
            bbox=dict(facecolor="white", edgecolor="#bbb", boxstyle="round,pad=0.15", linewidth=0.5))

    # Place each edge's label offset perpendicular to the arrow so it never crosses a box edge.
    edge("Taxon", "Vernacular", "HAS_VERNACULAR", label_offset=(-0.65, 0.0))
    edge("Taxon", "Patent",     "HAS_PATENT",     label_offset=(0, 0.32))
    edge("Patent", "IPCClass",  "HAS_IPC",        label_offset=(0.0, 0.32))
    edge("Patent", "CPCClass",  "HAS_CPC",        label_offset=(0.0, -0.32))

    # Title
    ax.text(6, 6.65, "Integrated graph schema",
            ha="center", fontsize=14, fontweight="bold")

    sources = [
        ("4,529,570 nodes — Open Tree of Life v3.7.3 + NCBI Taxonomy", "#1f6feb"),
        ("524,609 nodes — GBIF Backbone (en + nl filtered)",            "#2da44e"),
        ("759,182 nodes — Google Patents BigQuery, full-tree scan",     "#bf8700"),
        ("20,746 nodes — WIPO IPC scheme (defs resolved 100%)",         "#8250df"),
        ("35,139 nodes — EPO/USPTO CPC scheme (defs resolved 100%)",    "#cf222e"),
    ]
    for i, (txt, color) in enumerate(sources):
        y = 1.1 - i * 0.24
        ax.add_patch(mpatches.Rectangle((0.3, y), 0.20, 0.15, facecolor=color, edgecolor="black", lw=0.5))
        ax.text(0.6, y + 0.075, txt, va="center", fontsize=8.8, color="#222")

    plt.tight_layout()
    fig.savefig(OUT / "fig1_schema.png", dpi=220, bbox_inches="tight")
    print(f"wrote {OUT/'fig1_schema.png'}")
    plt.close(fig)


# ====================== FIGURE 3: CPC DISTRIBUTION ========================

def figure_cpc_distribution():
    # Data from Neo4j (computed and pasted in for reproducibility of the figure)
    sections = [
        ("A", "HUMAN NECESSITIES",                  2581111),
        ("C", "CHEMISTRY; METALLURGY",               841826),
        ("Y", "CLIMATE / CROSS-CUTTING TAGGING",     132442),
        ("G", "PHYSICS",                              74185),
        ("B", "PERFORMING OPERATIONS; TRANSPORTING",  47175),
        ("D", "TEXTILES; PAPER",                      16190),
        ("F", "MECH. ENG.; LIGHTING; HEATING; …",      4655),
        ("H", "ELECTRICITY",                           2389),
        ("E", "FIXED CONSTRUCTIONS",                   1630),
    ]

    subclasses = [
        ("A61K", "Medicinal preparations",         205090, 10261),
        ("C12N", "Microorgs.; genetic engineering",135207,  7012),
        ("A61P", "Therapeutic activity",           131979,  6266),
        ("Y02A", "Climate-change adaptation",       71219,  5595),
        ("A23V", "Food / lactic-acid bact. index",  69472,  4691),
        ("A23L", "Foods, foodstuffs, beverages",    69214,  4798),
        ("C12R", "Microorganism index",             61721,  4545),
        ("C07K", "Peptides",                        55099,  2757),
        ("C12P", "Fermentation processes",          43905,  3448),
        ("A01N", "Biocides; pest control",          39614,  4945),
        ("A61Q", "Cosmetic uses",                   39351,  4144),
        ("C12Q", "Enzyme/nucleic-acid assays",      29683,  3255),
        ("Y02P", "Climate-change in production",    23635,  3344),
        ("A23K", "Fodder",                          22649,  2391),
        ("A01G", "Horticulture; forestry",          22280,  3316),
    ]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 15.0),
                                   gridspec_kw={"height_ratios": [1.1, 1.0],
                                                "hspace": 0.40})

    # Left: section pie
    sec_codes = [s[0] for s in sections]
    sec_titles = [s[1] for s in sections]
    sec_counts = [s[2] for s in sections]
    total = sum(sec_counts)
    palette = ["#1f6feb", "#cf222e", "#bf8700", "#8250df", "#2da44e",
               "#0969da", "#9a6700", "#57606a", "#a3aab2"]

    def fmt(pct):
        return f"{pct:.1f}%" if pct >= 3 else ""
    # Only the wedges that are big enough to host text get an outside label;
    # the rest are identified via the legend below — prevents the small slivers
    # from stacking their letter codes on top of each other.
    inline_labels = [code if (c / total) >= 0.03 else ""
                     for code, c in zip(sec_codes, sec_counts)]
    wedges, _, autotxts = ax1.pie(sec_counts, labels=inline_labels, colors=palette,
                                  autopct=fmt, startangle=90, radius=1.15,
                                  labeldistance=1.08,
                                  pctdistance=0.62,
                                  textprops={"fontsize": 14, "fontweight": "bold"},
                                  wedgeprops={"linewidth": 1, "edgecolor": "white"})
    for at in autotxts:
        at.set_color("white"); at.set_fontsize(12); at.set_fontweight("bold")
    ax1.set_aspect("equal")
    ax1.set_title(f"a   CPC sections across {total:,} Patent→Class edges",
                  loc="center", fontsize=15, fontweight="bold", pad=14)
    # Legend below the pie so the pie can fill the subplot width and stay centered.
    # Section codes that aren't labelled inline get a small percentage in the legend
    # so the reader can still place them.
    handles = [
        mpatches.Patch(color=palette[i],
                       label=f"{sec_codes[i]} — {sec_titles[i]}"
                             + (f"  ({sec_counts[i]/total*100:.2f}%)" if sec_counts[i]/total < 0.03 else ""))
        for i in range(len(sections))
    ]
    ax1.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.05),
               fontsize=11, ncol=3, frameon=False)

    # Right: top-15 subclasses
    sub_codes  = [f"{s[0]}: {s[1]}" for s in subclasses]
    sub_pat    = [s[2] for s in subclasses]
    sub_spec   = [s[3] for s in subclasses]
    y_pos = np.arange(len(subclasses))[::-1]

    ax2.barh(y_pos - 0.20, sub_pat,  height=0.40, label="patents",
             color="#1f6feb", edgecolor="black", linewidth=0.4)
    ax2.barh(y_pos + 0.20, sub_spec, height=0.40, label="species (with that subclass)",
             color="#2da44e", edgecolor="black", linewidth=0.4)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(sub_codes, fontsize=12)
    ax2.set_xlabel("Count", fontsize=12)
    ax2.tick_params(axis="x", labelsize=11)
    ax2.set_title("b   Top 15 CPC subclasses by patent volume",
                  loc="left", fontsize=14, fontweight="bold", pad=10)
    ax2.legend(loc="lower right", fontsize=12)
    ax2.grid(True, axis="x", alpha=0.3)
    xmax = max(sub_pat) * 1.10
    ax2.set_xlim(0, xmax)
    for i, (p, s) in enumerate(zip(sub_pat, sub_spec)):
        ax2.text(p + xmax*0.005, y_pos[i] - 0.20, f"{p:,}", va="center", fontsize=10)
        ax2.text(s + xmax*0.005, y_pos[i] + 0.20, f"{s:,}", va="center", fontsize=10)

    plt.tight_layout()
    fig.savefig(OUT / "fig3_cpc_distribution.png", dpi=220, bbox_inches="tight")
    print(f"wrote {OUT/'fig3_cpc_distribution.png'}")
    plt.close(fig)


if __name__ == "__main__":
    figure_schema()
    figure_cpc_distribution()
