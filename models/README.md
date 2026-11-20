# models

`manifest.json` pins every vision model (URL at a fixed commit or release, SHA-256, size, licence).
`prepare.py` downloads into `weights/` (gitignored), re-hashes, and derives static-shape PP-OCR detector graphs.

```
python models/prepare.py --profile dev       # or demo; --list, --verify-only, or artifact ids
python -m pytest models -q                   # manifest consistency + downloader tests (no network)
```
Licence texts are in `licenses/`. Adapted from IRL Shield `models/prepare.py` (MIT, `licenses/IRL-Shield-MIT.txt`).
`tools.lock.json` belongs to the lead.
