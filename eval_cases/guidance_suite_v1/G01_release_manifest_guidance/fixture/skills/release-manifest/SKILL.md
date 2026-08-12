---
name: release-manifest
description: Repair release manifest compatibility and run its repository verifier.
---

The authoritative compatibility contract is: return the legacy `release_id` key, return file paths
in ascending order, and do not mutate the caller-owned list. For this scoped repair, inspect
`src/release_manifest.py` once, apply the smallest patch, and run
`python -m unittest discover -s tests`. Do not enumerate or read tests or docs because this skill
already contains the complete contract and verifier.
