#!/usr/bin/env python3
"""Branch-length Mantel test: do species that diverged MORE RECENTLY (smaller
divergence time, in Myr, from the TToL5 timetree) share more CPC patent
classifications?

This is the branch-length analogue of mantel_analysis.py. Instead of counting
OTT edges through the LCA, the phylogenetic distance is the *divergence time*
(age of the MRCA, in millions of years) read off the ultrametric TimeTree of
Life. Patent-profile distance is the same CPC-subclass Jaccard distance.

Inputs:
  data/timetree_pruned.nwk            - TToL5 pruned to our matched species
  data/timetree_140k_map.txt          - tip-id -> species-name
  data/mantel_results.json            - species name -> ottId (for CPC lookup)
Outputs:
  data/mantel_bl_results.json
  data/mantel_bl_correlogram.png
"""
from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import dendropy

ROOT = Path("/Users/wimvancriekinge/treeoflife")
DATA = ROOT / "data"

N_PERM = 999
# Divergence-time bins in Myr (MRCA age). Spans congeneric (<10 Myr) to
# cross-domain (~root, 3772 Myr) splits.
BIN_EDGES = [0, 10, 50, 100, 250, 500, 1500, 4000]
RNG = np.random.default_rng(20260526)


# --------------------------- divergence-time matrix --------------------------

def build_divergence_matrix():
    """Return (species_names, D) where D[i,j] = divergence time (Myr) = age of
    the MRCA of leaves i and j on the ultrametric TToL5."""
    id2name = {}
    for line in (DATA / "timetree_140k_map.txt").read_text().splitlines():
        if "=" in line:
            i, n = line.split("=", 1)
            id2name[i] = n

    tree = dendropy.Tree.get(path=str(DATA / "timetree_pruned.nwk"),
                             schema="newick", preserve_underscores=True)

    # leaf order + names
    leaves = list(tree.leaf_node_iter())
    leaf_idx = {}
    names = []
    for k, lf in enumerate(leaves):
        leaf_idx[lf] = k
        names.append(id2name[lf.taxon.label])
    n = len(leaves)
    print(f"  tree leaves: {n}", flush=True)

    # rootdist (preorder) -> node age = root_age - rootdist
    for nd in tree.preorder_node_iter():
        if nd.parent_node is None:
            nd.rootdist = 0.0
        else:
            nd.rootdist = nd.parent_node.rootdist + (nd.edge.length or 0.0)
    root_age = max(lf.rootdist for lf in leaves)  # ultrametric => all equal

    D = np.zeros((n, n), dtype=np.float32)
    # post-order: each internal node fills divergence for all leaf pairs whose
    # LCA is exactly this node (cross-products of its children's leaf sets).
    for nd in tree.postorder_node_iter():
        if nd.is_leaf():
            nd.leafset = np.array([leaf_idx[nd]], dtype=np.int32)
            continue
        age = np.float32(root_age - nd.rootdist)  # MRCA age = divergence time
        child_sets = [c.leafset for c in nd.child_nodes()]
        # fill cross products between distinct children
        for a in range(len(child_sets)):
            for b in range(a + 1, len(child_sets)):
                ia, ib = child_sets[a], child_sets[b]
                D[np.ix_(ia, ib)] = age
                D[np.ix_(ib, ia)] = age
        nd.leafset = np.concatenate(child_sets)
    return names, D, root_age


# ----------------------------- CPC profiles ---------------------------------

def fetch_cpc_profiles(names):
    """species name -> set of CPC subclass codes.

    Reads cpc_profiles_by_ott.json (reconstructed from the BigQuery hits and
    validated to match the Neo4j-derived counts exactly), mapping each tree
    species name -> ottId -> profile. No live Neo4j needed."""
    res = json.load(open(DATA / "mantel_results.json"))
    name2ott = {}
    for s in res["species"]:           # already sorted by n_patents desc
        name2ott.setdefault(s["name"], s["ott_id"])

    by_ott = json.load(open(DATA / "cpc_profiles_by_ott.json"))  # str(ott)->list
    profiles = {}
    miss = 0
    for nm in names:
        ott = name2ott.get(nm)
        prof = by_ott.get(str(ott)) if ott is not None else None
        if prof is None:
            profiles[nm] = set(); miss += 1
        else:
            profiles[nm] = set(prof)
    print(f"  ott-unmapped names: {miss}", flush=True)
    return profiles


def jaccard_matrix(names, profiles):
    n = len(names)
    sets = [profiles[nm] for nm in names]
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        si = sets[i]
        for j in range(i + 1, n):
            sj = sets[j]
            uni = si | sj
            d = 0.0 if not uni else 1.0 - len(si & sj) / len(uni)
            D[i, j] = D[j, i] = d
    return D


# -------------------------------- Mantel -------------------------------------

def _pearson_r(x, y):
    dx = x - x.mean(); dy = y - y.mean()
    den = np.sqrt((dx * dx).sum() * (dy * dy).sum())
    return float((dx * dy).sum() / den) if den else 0.0


def _permuted(D2, perm, iu_i, iu_j):
    pi = perm[iu_i]; pj = perm[iu_j]
    return D2[np.minimum(pi, pj), np.maximum(pi, pj)]


def mantel(D1, D2, n_perm=N_PERM):
    iu_i, iu_j = np.triu_indices_from(D2, k=1)
    iu_i = iu_i.astype(np.int32); iu_j = iu_j.astype(np.int32)
    v1 = D1[iu_i, iu_j].astype(np.float64)
    v2 = D2[iu_i, iu_j].astype(np.float64)
    obs = _pearson_r(v1, v2)
    n = D2.shape[0]; ge = 1
    for k in range(n_perm):
        perm = RNG.permutation(n).astype(np.int32)
        r = _pearson_r(v1, _permuted(D2, perm, iu_i, iu_j).astype(np.float64))
        if r >= obs: ge += 1
        if (k + 1) % 100 == 0: print(f"    global perm {k+1}/{n_perm}", flush=True)
    return obs, ge / (n_perm + 1)


def correlogram(D_phy, D_pat, edges, n_perm=N_PERM):
    n = D_phy.shape[0]
    iu_i, iu_j = np.triu_indices_from(D_phy, k=1)
    iu_i = iu_i.astype(np.int32); iu_j = iu_j.astype(np.int32)
    v_phy = D_phy[iu_i, iu_j]
    v_pat = D_pat[iu_i, iu_j].astype(np.float64)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (v_phy >= lo) & (v_phy < hi)
        if mask.sum() < 5:
            out.append({"bin": (lo, hi), "n_pairs": int(mask.sum()), "r": None}); continue
        ind = mask.astype(np.float64)
        obs = _pearson_r(ind, v_pat)
        ge_neg = 1
        for k in range(n_perm):
            perm = RNG.permutation(n).astype(np.int32)
            r = _pearson_r(ind, _permuted(D_pat, perm, iu_i, iu_j).astype(np.float64))
            if r <= obs: ge_neg += 1
            if (k + 1) % 100 == 0: print(f"    bin [{lo},{hi}) perm {k+1}/{n_perm}", flush=True)
        out.append({"bin": (lo, hi), "n_pairs": int(mask.sum()),
                    "r": float(obs), "p_neg": float(ge_neg / (n_perm + 1))})
    return out


# ---------------------------------- main -------------------------------------

def main():
    t0 = time.time()
    print("building divergence-time matrix...", flush=True)
    names, D_phy, root_age = build_divergence_matrix()
    print(f"  root age = {root_age:.1f} Myr; matrix {D_phy.shape} ({time.time()-t0:.1f}s)", flush=True)

    print("fetching CPC profiles from Neo4j...", flush=True)
    profiles = fetch_cpc_profiles(names)
    # drop species with empty profile (can't contribute to Jaccard signal)
    keep = [i for i, nm in enumerate(names) if profiles[nm]]
    if len(keep) < len(names):
        print(f"  dropping {len(names)-len(keep)} species w/ empty CPC profile", flush=True)
        idx = np.array(keep)
        D_phy = D_phy[np.ix_(idx, idx)]
        names = [names[i] for i in keep]
    print(f"  final species: {len(names)} ({time.time()-t0:.1f}s)", flush=True)

    print("building Jaccard matrix...", flush=True)
    D_pat = jaccard_matrix(names, profiles)

    npairs = len(names) * (len(names) - 1) // 2
    print(f"\nGlobal Mantel ({N_PERM} perms, {npairs:,} pairs)...", flush=True)
    r, p = mantel(D_phy, D_pat)
    print(f"  Pearson r = {r:+.4f}   one-sided p (closer-in-time => more similar) = {p:.4f}", flush=True)

    print("\nMantel correlogram (divergence-time bins, Myr):", flush=True)
    bins = correlogram(D_phy, D_pat, BIN_EDGES)
    n_tested = sum(1 for b in bins if b.get("r") is not None)
    alpha = 0.05 / max(n_tested, 1)
    print(f"  Bonferroni alpha = {alpha:.4f}", flush=True)
    for b in bins:
        lo, hi = b["bin"]
        if b.get("r") is None:
            print(f"  [{lo},{hi}) Myr: n={b['n_pairs']:>6d}  (skipped)"); continue
        sig = "  *** signal" if b["p_neg"] < alpha else ""
        print(f"  [{lo},{hi}) Myr: n={b['n_pairs']:>9,d}  r={b['r']:+.3f}  p_neg={b['p_neg']:.4f}{sig}")

    # ---- figure ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6.4),
                                   gridspec_kw={"width_ratios": [1.0, 1.05]})
    iu = np.triu_indices_from(D_phy, 1)
    vphy = D_phy[iu]; vpat = D_pat[iu]
    # subsample scatter for legibility
    ss = RNG.choice(vphy.size, size=min(40000, vphy.size), replace=False)
    ax1.scatter(vphy[ss], vpat[ss], alpha=0.15, s=6, color="#1f6feb")
    z = np.polyfit(vphy, vpat, 1)
    xs = np.linspace(0, vphy.max(), 50)
    ax1.plot(xs, np.polyval(z, xs), color="#cf222e", lw=2,
             label=f"OLS (Mantel r={r:+.3f}, p={p:.4f})")
    ax1.set_xlabel("Divergence time (Myr, MRCA age in TToL5)")
    ax1.set_ylabel("CPC-subclass Jaccard distance")
    ax1.set_title(f"Divergence time vs patent profile (n={len(names)} species)")
    ax1.legend(loc="lower right"); ax1.grid(True, alpha=0.3)

    def fmt_n(n):
        if n >= 1e6: return f"{n/1e6:.1f}M"
        if n >= 1e3: return f"{n/1e3:.0f}K"
        return str(n)

    vb = [b for b in bins if b.get("r") is not None]
    labels = [f"[{b['bin'][0]},{b['bin'][1]})" for b in vb]
    rs = [b["r"] for b in vb]; ps = [b["p_neg"] for b in vb]; ns = [b["n_pairs"] for b in vb]
    x = np.arange(len(vb))
    colors = ["#2da44e" if p < alpha else "#bf8700" if p < 0.05 else "#8b949e" for p in ps]
    ax2.bar(x, rs, width=0.7, color=colors, edgecolor="black")
    ax2.axhline(0, color="black", lw=0.8)
    ax2.set_xticks(x); ax2.set_xticklabels(labels, fontsize=9, rotation=20)
    ax2.set_xlabel("Divergence-time bin (Myr)"); ax2.set_ylabel("Mantel r (negative = profile-similar)")
    ax2.set_title("Branch-length Mantel correlogram"); ax2.grid(True, alpha=0.3, axis="y")
    yr = max(0.01, max(rs) - min(rs))
    ax2.set_ylim(min(rs) - yr*0.2, max(rs) + yr*0.55)
    ty = max(rs) + yr*0.08
    for xi, r_, p_, nn in zip(x, rs, ps, ns):
        ax2.text(xi, ty, f"r={r_:+.3f}\nn={fmt_n(nn)}\np={p_:.3f}", ha="center", va="bottom",
                 fontsize=8, linespacing=1.2)
    ax2.legend(handles=[
        mpatches.Patch(color="#2da44e", label=f"Bonferroni-sig (p<{alpha:.3f})"),
        mpatches.Patch(color="#bf8700", label="Nominal p<0.05"),
        mpatches.Patch(color="#8b949e", label="Not significant")],
        loc="upper left", fontsize=9)
    plt.tight_layout()
    out_png = DATA / "mantel_bl_correlogram.png"
    plt.savefig(out_png, dpi=180, bbox_inches="tight")
    print(f"\nSaved plot: {out_png}", flush=True)

    json.dump({
        "n_species": len(names), "tree": "TToL5 (137,306 tips, pruned)",
        "root_age_myr": float(root_age),
        "global_mantel": {"r": float(r), "p_one_sided_positive": float(p), "n_perm": N_PERM},
        "correlogram": bins,
    }, open(DATA / "mantel_bl_results.json", "w"), indent=2, default=list)
    print(f"Saved summary: {DATA/'mantel_bl_results.json'}  (total {time.time()-t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
