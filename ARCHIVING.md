# Archiving GRAFT on Zenodo (minting a citable DOI)

This repo ships citation metadata (`CITATION.cff`, `.zenodo.json`) and is tagged
`v1.0.0`. To obtain a permanent DOI the paper can cite, use **one** of the routes
below. (Zenodo's automatic GitHub webhook only works with **github.com**, not the
UGent enterprise instance, so route A is the manual one that works as-is.)

## Route A — manual upload (works for github.ugent.be)

1. Sign in at <https://zenodo.org> (you can log in with ORCID 0000-0003-2971-5539).
2. **New upload** → upload the release archive `GRAFT-v1.0.0.zip` (built from this tag).
3. Zenodo pre-fills from `.zenodo.json`; confirm: upload type *Software*, license *MIT*,
   author *Van Criekinge, Wim* (ORCID linked), version *1.0.0*.
4. (Optional) Under *Related/alternate identifiers*, add the bioRxiv preprint DOI
   once it exists ("is supplemented by").
5. **Publish** → Zenodo mints a versioned DOI plus a concept DOI (cite the concept DOI
   in the paper so it always resolves to the latest version).

## Route B — automated (requires a github.com mirror)

1. Push this repo to a **public github.com** repository (also solves external
   reviewer access, since github.ugent.be is not world-readable).
2. At <https://zenodo.org/account/settings/github/> flip the repo switch **on**.
3. On GitHub, create a release from tag `v1.0.0` → Zenodo archives it and mints the
   DOI automatically. Future releases are archived on publish.

## After you have the DOI

Replace the "deposited on Zenodo upon acceptance" sentence in the manuscript's
**Data availability** / **Code availability** sections with the concrete DOI, e.g.
`Archived at Zenodo: https://doi.org/10.5281/zenodo.XXXXXXX`.
