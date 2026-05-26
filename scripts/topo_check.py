#!/usr/bin/env python3
"""Robustness check: topological (edge-count) Mantel on the SAME TToL5 tree and
the SAME 5,306 species used in the branch-length analysis. Isolates the effect
of using calibrated branch lengths (Myr) vs merely counting nodes, on identical
taxa. Reuses the CPC profiles, Jaccard and Mantel machinery from
mantel_branchlength.py."""
import json, time
from pathlib import Path
import numpy as np
import dendropy

import mantel_branchlength as bl  # build leaf set / profiles / mantel / correlogram

DATA = Path("/Users/wimvancriekinge/treeoflife/data")
N_PERM = 199  # robustness pass; observed r is independent of n_perm


def build_edge_matrix():
    """D[i,j] = number of edges on the path i->LCA->j in the pruned TToL5
    (general patristic with unit branch lengths). Returns (names, D)."""
    id2name = {}
    for line in (DATA / "timetree_140k_map.txt").read_text().splitlines():
        if "=" in line:
            i, n = line.split("=", 1)
            id2name[i] = n
    tree = dendropy.Tree.get(path=str(DATA / "timetree_pruned.nwk"),
                             schema="newick", preserve_underscores=True)
    leaves = list(tree.leaf_node_iter())
    leaf_idx = {lf: k for k, lf in enumerate(leaves)}
    names = [id2name[lf.taxon.label] for lf in leaves]
    n = len(leaves)

    # rootdepth in EDGES (root=0, each step +1)
    for nd in tree.preorder_node_iter():
        nd.rdepth = 0 if nd.parent_node is None else nd.parent_node.rdepth + 1
    rd_leaf = np.zeros(n, dtype=np.int32)
    for lf in leaves:
        rd_leaf[leaf_idx[lf]] = lf.rdepth

    D = np.zeros((n, n), dtype=np.float32)
    for nd in tree.postorder_node_iter():
        if nd.is_leaf():
            nd.leafset = np.array([leaf_idx[nd]], dtype=np.int32)
            continue
        rdN = nd.rdepth
        sets = [c.leafset for c in nd.child_nodes()]
        for a in range(len(sets)):
            for b in range(a + 1, len(sets)):
                ia, ib = sets[a], sets[b]
                # edges = (rd[x]-rdN) + (rd[y]-rdN)
                sub = (rd_leaf[ia][:, None] - rdN) + (rd_leaf[ib][None, :] - rdN)
                D[np.ix_(ia, ib)] = sub
                D[np.ix_(ib, ia)] = sub.T
        nd.leafset = np.concatenate(sets)
    return names, D


def main():
    t0 = time.time()
    print("building edge-count matrix on same TToL5 tree...", flush=True)
    names, D_topo = build_edge_matrix()
    print(f"  {len(names)} leaves; edge dist min={int(D_topo[D_topo>0].min())} "
          f"max={int(D_topo.max())} mean={D_topo[np.triu_indices_from(D_topo,1)].mean():.1f} "
          f"({time.time()-t0:.1f}s)", flush=True)

    profiles = bl.fetch_cpc_profiles(names)
    keep = [i for i, nm in enumerate(names) if profiles[nm]]
    idx = np.array(keep)
    D_topo = D_topo[np.ix_(idx, idx)]
    names = [names[i] for i in keep]
    print(f"  species with CPC profile: {len(names)}", flush=True)

    D_pat = bl.jaccard_matrix(names, profiles)

    # quantile-based edge bins (5 bins ~equal pairs) for the correlogram
    iu = np.triu_indices_from(D_topo, 1)
    v = D_topo[iu]
    qs = np.unique(np.quantile(v, [0, .2, .4, .6, .8, 1.0]).astype(int))
    edges = [int(x) for x in qs[:-1]] + [int(qs[-1]) + 1]
    print(f"  edge bins: {edges}", flush=True)

    print(f"\nGlobal topological Mantel ({N_PERM} perms)...", flush=True)
    r, p = bl.mantel(D_topo, D_pat, N_PERM)
    print(f"  Pearson r = {r:+.4f}  one-sided p = {p:.4f}", flush=True)

    print("\nCorrelogram (edge bins):", flush=True)
    bins = bl.correlogram(D_topo, D_pat, edges, N_PERM)
    nt = sum(1 for b in bins if b.get("r") is not None)
    alpha = 0.05 / max(nt, 1)
    for b in bins:
        lo, hi = b["bin"]
        if b.get("r") is None:
            print(f"  [{lo},{hi}) edges: n={b['n_pairs']:>9,}  (skipped)"); continue
        sig = " ***" if b["p_neg"] < alpha else ""
        print(f"  [{lo},{hi}) edges: n={b['n_pairs']:>9,}  r={b['r']:+.4f}  p_neg={b['p_neg']:.4f}{sig}")

    json.dump({"n_species": len(names), "n_perm": N_PERM,
               "global_mantel": {"r": float(r), "p_one_sided_positive": float(p)},
               "edge_bins": edges, "correlogram": bins},
              open(DATA / "topo_check_results.json", "w"), indent=2, default=list)
    print(f"\nSaved {DATA/'topo_check_results.json'} (total {time.time()-t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
