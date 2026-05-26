#!/usr/bin/env python3
"""Feasibility check: how many of the 9,918 analysis binomials are tips in the
TimeTree of Life (140k-species TToL5)? Matches by name (tree IDs are MEGA-TT
internal IDs, not NCBI), writes the matched set for the later patristic run."""
import re, json
from pathlib import Path

DATA = Path("/Users/wimvancriekinge/treeoflife/data")

id2name = {}
for line in (DATA / "timetree_140k_map.txt").read_text().splitlines():
    if "=" in line:
        i, n = line.split("=", 1)
        id2name[i] = n

tree = (DATA / "timetree_140k.nwk").read_text()
leaf_ids = set(re.findall(r"[(,](\d+):", tree))
leaf_names = {id2name[i] for i in leaf_ids if i in id2name}

ours = {l.strip() for l in (DATA / "analysis_species.txt").read_text().splitlines() if l.strip()}

leaf_lc = {n.lower(): n for n in leaf_names}
matched = {o for o in ours if o.lower() in leaf_lc}
tree_genera = {n.split()[0] for n in leaf_names if " " in n}
sp_with_genus = {o for o in ours if o.split()[0] in tree_genera}

print(f"TimeTree tips (unique leaf ids): {len(leaf_ids)}")
print(f"TimeTree tip names resolved:     {len(leaf_names)}")
print(f"our analysis binomials:          {len(ours)}")
print(f"exact-species matches:           {len(matched)} ({100*len(matched)/len(ours):.1f}%)")
print(f"species whose GENUS is in tree:  {len(sp_with_genus)} ({100*len(sp_with_genus)/len(ours):.1f}%)")

json.dump(sorted(matched), open(DATA / "timetree_matched_species.json", "w"))
print(f"wrote {DATA/'timetree_matched_species.json'} ({len(matched)} species)")

unmatched = sorted(ours - matched)
print("sample UNMATCHED:", unmatched[:15])
