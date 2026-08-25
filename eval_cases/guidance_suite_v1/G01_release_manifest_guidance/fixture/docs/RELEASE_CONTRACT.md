# Release manifest contract

`build_manifest(version, files)` must preserve the legacy `release_id` key, return file paths in
ascending order, and must not mutate the caller's list. The project verifier is:

```bash
python -m unittest discover -s tests
```
