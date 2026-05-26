#!/usr/bin/env python3
"""Mantel test + Mantel correlogram: do phylogenetically closer species share
more CPC patent classifications?

Approach:
  - Subset to species with >= MIN_PATENTS hits (drops noisy single-hit profiles).
  - Phylo distance: number of HAS_PARENT edges on the path through the lowest
    common ancestor in the OTT tree (topology only — we have no branch lengths).
  - Patent-profile distance: Jaccard distance over the set of *CPC subclasses*
    (4-character codes like A23K, C12N) each species' patents are tagged with.
    Subclass aggregation reduces noise vs. full subgroup codes and matches the
    granularity at which "applications" cluster.
  - Global Mantel test: Pearson r between flattened upper-triangles, p-value
    from N permutations of species labels in the patent matrix.
  - Mantel correlogram: bin pairs by phylo distance, test each bin's
    correlation separately with Bonferroni-corrected p-values.

Output:
  - Console: r, p, per-bin correlations.
  - data/mantel_correlogram.png: 2-panel plot (scatter + correlogram bars).
  - data/mantel_results.json: machine-readable summary.
"""
from __future__ import annotations
import json
import os
import sys
import re
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from neo4j import GraphDatabase

ROOT = Path("/Users/wimvancriekinge/treeoflife")
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = os.environ.get("NEO4J_DB", "treeoflife")

MIN_PATENTS = 5
# At full-tree scale (~10k species, ~50M pairs) 9,999 perms is computationally
# prohibitive (multi-hour). 999 perms still gives a p-value floor of 1/1000 =
# 0.001, far below any meaningful significance threshold. Reduced from 9,999.
N_PERM = 999
BIN_EDGES = [0, 3, 6, 9, 12, 100]  # phylo-distance bins (edges)
RNG = np.random.default_rng(20260506)


# ----------------------------- data extraction -------------------------------

def fetch_species_data(driver):
    """Return dict: ott_id -> {name, lineage, cpc_subclasses}.

    lineage: list of ott_ids from species up to root (inclusive both ends).
    cpc_subclasses: set of 4-char codes (e.g. 'A23K') used by patents linked to this taxon.
    """
    with driver.session(database=DB) as s:
        rows = s.run("""
            MATCH (t:Taxon)-[:HAS_PATENT]->(p:Patent)
            WITH t, count(DISTINCT p) AS n_patents
            WHERE n_patents >= $min_pat
            RETURN t.ottId AS ottId, t.name AS name, n_patents
            ORDER BY n_patents DESC
        """, min_pat=MIN_PATENTS).data()

    species = {}
    with driver.session(database=DB) as s:
        for r in rows:
            ott = r["ottId"]
            # ascend the parent chain to root
            path = s.run("""
                MATCH p = (t:Taxon {ottId: $ott})-[:HAS_PARENT*0..]->(root:Taxon)
                WHERE NOT (root)-[:HAS_PARENT]->()
                WITH p ORDER BY length(p) DESC LIMIT 1
                RETURN [n IN nodes(p) | n.ottId] AS lineage
            """, ott=ott).single()["lineage"]

            cpcs = s.run("""
                MATCH (t:Taxon {ottId: $ott})-[:HAS_PATENT]->(:Patent)-[:HAS_CPC]->(c:CPCClass)
                RETURN collect(DISTINCT c.subclassCode) AS subclasses
            """, ott=ott).single()["subclasses"]
            cpcs = {x for x in cpcs if x}  # drop nulls

            species[ott] = {
                "name": r["name"],
                "n_patents": r["n_patents"],
                "lineage": path,           # list[int]
                "cpc_subclasses": cpcs,    # set[str]
            }
    return species


# --------------------------- distance computations ---------------------------

def lca_distance(la: list[int], lb: list[int]) -> int:
    """Number of edges between two species, going through their LCA in the tree.

    Each lineage is species → ... → root. Find the first shared ancestor.
    """
    sb = set(lb)
    for i, anc in enumerate(la):
        if anc in sb:
            j = lb.index(anc)
            return i + j
    # fallback: should never happen since both share root
    return len(la) + len(lb)


def phylo_distance_matrix(species: dict) -> tuple[list[int], np.ndarray]:
    keys = list(species.keys())
    n = len(keys)
    D = np.zeros((n, n), dtype=int)
    for i in range(n):
        for j in range(i+1, n):
            d = lca_distance(species[keys[i]]["lineage"], species[keys[j]]["lineage"])
            D[i, j] = D[j, i] = d
    return keys, D


def jaccard_distance_matrix(keys: list[int], species: dict) -> np.ndarray:
    n = len(keys)
    D = np.zeros((n, n), dtype=float)
    for i in range(n):
        si = species[keys[i]]["cpc_subclasses"]
        for j in range(i+1, n):
            sj = species[keys[j]]["cpc_subclasses"]
            uni = si | sj
            if not uni:
                d = 0.0
            else:
                d = 1.0 - len(si & sj) / len(uni)
            D[i, j] = D[j, i] = d
    return D


# -------------------------------- Mantel tests --------------------------------

def upper_tri(M: np.ndarray) -> np.ndarray:
    iu = np.triu_indices_from(M, k=1)
    return M[iu]


def _permuted_v2(D2: np.ndarray, perm: np.ndarray, iu_i: np.ndarray, iu_j: np.ndarray) -> np.ndarray:
    """Return upper-triangle values of D2[perm,:][:,perm] WITHOUT materialising
    the full reordered matrix. Equivalent to D2[perm[i], perm[j]] for the
    (i, j) upper-triangle index pairs. ~800 MB → ~50 MB per perm and ~5×
    faster, plus no GC churn."""
    pi = perm[iu_i]
    pj = perm[iu_j]
    # symmetrise lookup so we always read upper triangle of original D2
    a = np.minimum(pi, pj)
    b = np.maximum(pi, pj)
    return D2[a, b]


def _pearson_r(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson r without numpy.corrcoef's allocation overhead."""
    n = x.size
    sx = x.mean(); sy = y.mean()
    dx = x - sx; dy = y - sy
    num = (dx * dy).sum()
    den = np.sqrt((dx * dx).sum() * (dy * dy).sum())
    return num / den if den else 0.0


def mantel(D1: np.ndarray, D2: np.ndarray, n_perm: int = N_PERM) -> tuple[float, float]:
    """Pearson Mantel correlation r and one-sided p-value (right tail).

    Vectorised permutation: shuffle indices, look up D2 values without
    rebuilding the matrix.
    """
    iu_i, iu_j = np.triu_indices_from(D2, k=1)
    iu_i = iu_i.astype(np.int32, copy=False)
    iu_j = iu_j.astype(np.int32, copy=False)
    v1 = D1[iu_i, iu_j].astype(np.float64)
    v2 = D2[iu_i, iu_j].astype(np.float64)
    obs = _pearson_r(v1, v2)
    n = D2.shape[0]
    ge = 1
    for k in range(n_perm):
        perm = RNG.permutation(n).astype(np.int32)
        vp = _permuted_v2(D2, perm, iu_i, iu_j)
        r = _pearson_r(v1, vp.astype(np.float64))
        if r >= obs:
            ge += 1
        if (k + 1) % 100 == 0:
            print(f"    global perm {k+1}/{n_perm}", flush=True)
    return obs, ge / (n_perm + 1)


def mantel_correlogram(D_phy: np.ndarray, D_pat: np.ndarray, edges: list[int],
                       n_perm: int = N_PERM) -> list[dict]:
    """Vectorised Mantel correlogram. For each phylo-distance bin, compute
    correlation between bin-membership indicator and the (permuted) patent
    distance vector — without rebuilding D_pat per permutation.
    """
    n = D_phy.shape[0]
    iu_i, iu_j = np.triu_indices_from(D_phy, k=1)
    iu_i = iu_i.astype(np.int32, copy=False)
    iu_j = iu_j.astype(np.int32, copy=False)
    v_phy = D_phy[iu_i, iu_j]
    v_pat = D_pat[iu_i, iu_j].astype(np.float64)
    out = []
    for bin_idx, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        mask = (v_phy >= lo) & (v_phy < hi)
        if mask.sum() < 5:
            out.append({"bin": (lo, hi), "n_pairs": int(mask.sum()),
                        "r": None, "p": None})
            continue
        bin_indicator = mask.astype(np.float64)
        obs = _pearson_r(bin_indicator, v_pat)

        ge_neg = 1
        for k in range(n_perm):
            perm = RNG.permutation(n).astype(np.int32)
            vp = _permuted_v2(D_pat, perm, iu_i, iu_j).astype(np.float64)
            r = _pearson_r(bin_indicator, vp)
            if r <= obs:
                ge_neg += 1
            if (k + 1) % 100 == 0:
                print(f"    bin [{lo},{hi}) perm {k+1}/{n_perm}", flush=True)
        p_neg = ge_neg / (n_perm + 1)
        out.append({"bin": (lo, hi), "n_pairs": int(mask.sum()),
                    "r": float(obs), "p_neg": float(p_neg)})
    return out


# ----------------------------------- main ------------------------------------

def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    print(f"Filtering to species with >= {MIN_PATENTS} patents...")
    species = fetch_species_data(driver)
    print(f"  {len(species)} species kept")

    keys, D_phy = phylo_distance_matrix(species)
    D_pat = jaccard_distance_matrix(keys, species)
    names = [species[k]["name"] for k in keys]

    print(f"\nPhylo distance: min={D_phy[D_phy>0].min()} max={D_phy.max()} mean={D_phy[np.triu_indices_from(D_phy,1)].mean():.1f}")
    print(f"Jaccard CPC dist: min={D_pat[np.triu_indices_from(D_pat,1)].min():.3f} max={D_pat[np.triu_indices_from(D_pat,1)].max():.3f}")

    print(f"\nGlobal Mantel test ({N_PERM} permutations)...")
    r, p = mantel(D_phy, D_pat, N_PERM)
    print(f"  Pearson r = {r:+.4f}")
    print(f"  one-sided p (positive correlation, i.e. closer species => more similar) = {p:.4f}")

    print(f"\nMantel correlogram across phylo-distance bins:")
    bins = mantel_correlogram(D_phy, D_pat, BIN_EDGES, N_PERM)
    n_bins_tested = sum(1 for b in bins if b.get("r") is not None)
    print(f"  Bonferroni alpha = 0.05 / {n_bins_tested} = {0.05/max(n_bins_tested,1):.4f}")
    for b in bins:
        lo, hi = b["bin"]
        if b.get("r") is None:
            print(f"  edges [{lo},{hi}): n={b['n_pairs']:>4d}  (skipped, too few pairs)")
            continue
        sig = "  *** signal" if b["p_neg"] < 0.05/max(n_bins_tested,1) else ""
        print(f"  edges [{lo},{hi}): n={b['n_pairs']:>4d}  r={b['r']:+.3f}  p_neg={b['p_neg']:.4f}{sig}")

    # ---------- visualization ----------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6.4),
                                   gridspec_kw={"width_ratios": [1.0, 1.05]})

    v_phy = upper_tri(D_phy); v_pat = upper_tri(D_pat)
    ax1.scatter(v_phy + RNG.normal(0, 0.08, size=v_phy.shape),
                v_pat, alpha=0.55, s=22, color="#1f6feb")
    z = np.polyfit(v_phy, v_pat, 1)
    xs = np.linspace(v_phy.min(), v_phy.max(), 50)
    ax1.plot(xs, np.polyval(z, xs), color="#cf222e", linewidth=2,
             label=f"OLS fit (Mantel r={r:+.3f}, p={p:.4f})")
    ax1.set_xlabel("Phylogenetic distance (OTT edges through LCA)")
    ax1.set_ylabel("CPC-subclass Jaccard distance")
    ax1.set_title(f"Phylogeny vs patent profile  (n={len(keys)} species)")
    ax1.legend(loc="lower right")
    ax1.grid(True, alpha=0.3)

    # ---- correlogram ----
    def fmt_n(n: int) -> str:
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000:     return f"{n/1_000:.0f}K"
        return f"{n}"

    valid_bins = [b for b in bins if b.get("r") is not None]
    bin_labels  = [f"[{b['bin'][0]},{b['bin'][1] if b['bin'][1] < 99 else '∞'})" for b in valid_bins]
    bin_rs      = [b["r"] for b in valid_bins]
    bin_ps      = [b["p_neg"] for b in valid_bins]
    bin_ns      = [b["n_pairs"] for b in valid_bins]
    x_pos       = np.arange(len(valid_bins))
    colors      = ["#2da44e" if p < 0.05/max(n_bins_tested,1)
                   else "#bf8700" if p < 0.05 else "#8b949e"
                   for p in bin_ps]

    bars = ax2.bar(x_pos, bin_rs, width=0.7, color=colors, edgecolor="black")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(bin_labels, fontsize=10)
    ax2.set_xlabel("Phylogenetic distance bin (OTT edges)", labelpad=10)
    ax2.set_ylabel("Mantel correlation r (negative = profile-similar)")
    ax2.set_title("Mantel correlogram")
    ax2.grid(True, alpha=0.3, axis="y")

    # Annotation strategy: ALL bar annotations go ABOVE the y=0 line, regardless of
    # whether the bar itself is positive or negative. That way:
    #   - they never collide with the x-axis tick labels (which sit below ymin);
    #   - they never collide with each other (one per bar, all on the same side);
    #   - readers see r-value, n-pairs, and p-value at a glance for every bin.
    y_range = max(0.01, max(bin_rs) - min(bin_rs))
    ymin = min(bin_rs) - y_range * 0.20  # tighter — annotations no longer live here
    ymax = max(bin_rs) + y_range * 0.55  # roomier — annotations live up here
    ax2.set_ylim(ymin, ymax)
    text_y = max(bin_rs) + y_range * 0.08
    for xi, r_, p_, n in zip(x_pos, bin_rs, bin_ps, bin_ns):
        ax2.text(xi, text_y, f"r={r_:+.3f}\nn={fmt_n(n)}\np={p_:.3f}",
                 ha="center", va="bottom", fontsize=8.5, linespacing=1.2,
                 color="#222")

    # Significance colour legend as a proper legend (no more two-line title overlap)
    legend_handles = [
        mpatches.Patch(color="#2da44e", label=f"Bonferroni-sig. (p<{0.05/max(n_bins_tested,1):.3f})"),
        mpatches.Patch(color="#bf8700", label="Nominal p<0.05"),
        mpatches.Patch(color="#8b949e", label="Not significant"),
    ]
    ax2.legend(handles=legend_handles, loc="upper left", fontsize=9, frameon=True)

    plt.tight_layout()
    out_png = ROOT / "data" / "mantel_correlogram.png"
    plt.savefig(out_png, dpi=180, bbox_inches="tight")
    print(f"\nSaved plot: {out_png}")

    summary = {
        "n_species": len(keys),
        "min_patents_filter": MIN_PATENTS,
        "global_mantel": {"r": float(r), "p_one_sided_positive": float(p),
                          "n_perm": N_PERM},
        "correlogram": bins,
        "species": [{"ott_id": k, "name": species[k]["name"],
                     "n_patents": species[k]["n_patents"],
                     "n_cpc_subclasses": len(species[k]["cpc_subclasses"])}
                    for k in keys],
    }
    out_json = ROOT / "data" / "mantel_results.json"
    with out_json.open("w") as f:
        json.dump(summary, f, indent=2, default=list)
    print(f"Saved summary: {out_json}")

    driver.close()


if __name__ == "__main__":
    main()
