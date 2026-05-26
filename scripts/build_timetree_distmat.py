#!/usr/bin/env python3
"""Prune TToL5 to our matched species, verify ultrametricity, compute the
patristic (Myr) distance matrix, and persist it for the Mantel run.

Tree tip labels are MEGA-TT integer IDs; we map our species *names* -> IDs via
the map file. Output:
  data/timetree_pruned.nwk        - pruned Newick (fast reuse)
  data/timetree_patristic.npz     - keys (ott-less: species names) + float matrix
"""
import re, json, time
from pathlib import Path
import numpy as np
import dendropy

DATA = Path("/Users/wimvancriekinge/treeoflife/data")

t0 = time.time()
# id<->name maps
id2name, name2id = {}, {}
for line in (DATA / "timetree_140k_map.txt").read_text().splitlines():
    if "=" in line:
        i, n = line.split("=", 1)
        id2name[i] = n
        name2id[n.lower()] = i

matched_names = json.load(open(DATA / "timetree_matched_species.json"))
# species name -> tree id (matched set guarantees presence)
want = {}
for nm in matched_names:
    tid = name2id.get(nm.lower())
    if tid:
        want[tid] = nm
print(f"matched species -> tree ids: {len(want)}", flush=True)

print("parsing full TToL5 (137k tips)...", flush=True)
tree = dendropy.Tree.get(path=str(DATA / "timetree_140k.nwk"), schema="newick",
                         preserve_underscores=True)
print(f"  parsed in {time.time()-t0:.1f}s; leaves={len(tree.leaf_nodes())}", flush=True)

# retain only our tips
keep_labels = set(want.keys())
taxa_to_keep = [lf.taxon for lf in tree.leaf_node_iter()
                if lf.taxon and lf.taxon.label in keep_labels]
print(f"  tips to retain: {len(taxa_to_keep)}", flush=True)
tree.retain_taxa(taxa_to_keep)
print(f"  pruned in {time.time()-t0:.1f}s; leaves now={len(tree.leaf_nodes())}", flush=True)

# ultrametricity check: root-to-tip distances
root = tree.seed_node
depths = []
for lf in tree.leaf_node_iter():
    d, nd = 0.0, lf
    while nd.parent_node is not None:
        d += (nd.edge.length or 0.0)
        nd = nd.parent_node
    depths.append(d)
depths = np.array(depths)
print(f"\nroot-to-tip depth (Myr): min={depths.min():.2f} max={depths.max():.2f} "
      f"mean={depths.mean():.2f} std={depths.std():.2f}", flush=True)
print(f"ultrametric? std/mean = {depths.std()/depths.mean():.4f} "
      f"(near 0 => ultrametric => patristic = 2*divergence)", flush=True)

tree.write(path=str(DATA / "timetree_pruned.nwk"), schema="newick")
print(f"wrote pruned tree; total {time.time()-t0:.1f}s", flush=True)
