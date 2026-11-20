"""Load the contracts into Postgres for search (the "indexing" half of RAG).

Run from the project root, AFTER seed_data:   python -m scripts.ingest_contracts

For each contract in data/contracts/:
  1. Split it into chunks, one per numbered section (structure-aware chunking).
  2. Prefix each chunk with the buyer's name and section title, so a chunk still
     makes sense on its own when the agent reads it out of context.
  3. Embed all chunks locally and upsert them into contract_chunks.

Safe to re-run: each contract's old chunks are replaced in one transaction.
Uses the same chunking as uploads in the app (app/contracts.py). Demo workspace only.
"""
import time
from pathlib import Path

from app.contracts import chunk_contract, store_contract
from app.db import query
from scripts.seed_data import CONTRACTS_DIR, DEMO_WORKSPACE_ID, slugify


def main() -> None:
    started = time.perf_counter()
    buyers_by_slug = {slugify(b["name"]): b for b in query(
        "SELECT id, name FROM buyers WHERE workspace_id = %(w)s", {"w": DEMO_WORKSPACE_ID})}
    files = sorted(CONTRACTS_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"No contracts in {CONTRACTS_DIR}. Run `python -m scripts.seed_data` first.")

    print(f"Embedding {len(files)} contracts (the first run downloads the model, ~70 MB)...")
    total, skipped = 0, []
    for path in files:
        buyer = buyers_by_slug.get(Path(path).stem)
        if buyer is None:
            skipped.append(path.name)   # a contract for a buyer we don't know: report, don't guess
            continue
        chunks = chunk_contract(path.read_text(encoding="utf-8"), buyer["name"])
        total += store_contract(buyer["id"], path.name, chunks)   # replaces any earlier version

    print(f"Stored {total} chunks in {time.perf_counter() - started:.1f}s")
    if skipped:
        print(f"Skipped (no matching buyer): {skipped}")


if __name__ == "__main__":
    main()
