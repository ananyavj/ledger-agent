"""Load the contracts into Postgres for search (the "indexing" half of RAG).

Run from the project root, AFTER seed_data:   python -m scripts.ingest_contracts

For each contract in data/contracts/:
  1. Split it into chunks, one per numbered section (structure-aware chunking).
  2. Prefix each chunk with the buyer's name and section title, so a chunk still
     makes sense on its own when the agent reads it out of context.
  3. Embed all chunks locally and upsert them into contract_chunks.

Safe to re-run: (source_file, chunk_index) is unique, so re-running updates rows
in place, and chunks left over from an older, longer version are deleted.
"""
import re
import time
from pathlib import Path

from app.db import query, transaction
from app.embeddings import embed_passages, to_pgvector
from scripts.seed_data import CONTRACTS_DIR, slugify


def chunk_contract(text: str, buyer_name: str) -> list[str]:
    """Split on '## ' headings. The title and opening paragraph become chunk 0."""
    chunks = []
    for part in re.split(r"\n(?=## )", text.strip()):
        lines = part.strip().splitlines()
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        chunks.append(f"Contract with {buyer_name}. Section: {title}\n{body}".strip())
    return chunks


def main() -> None:
    started = time.perf_counter()
    buyers_by_slug = {slugify(b["name"]): b for b in query("SELECT id, name FROM buyers")}
    files = sorted(CONTRACTS_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"No contracts in {CONTRACTS_DIR}. Run `python -m scripts.seed_data` first.")

    # 1-2. Chunk every contract, remembering which buyer and file each chunk came from
    records = []   # (buyer_id, file name, chunk index, text)
    skipped = []
    for path in files:
        buyer = buyers_by_slug.get(Path(path).stem)
        if buyer is None:
            skipped.append(path.name)   # a contract for a buyer we don't know: report, don't guess
            continue
        for i, chunk in enumerate(chunk_contract(path.read_text(encoding="utf-8"), buyer["name"])):
            records.append((buyer["id"], path.name, i, chunk))

    # 3. Embed in one batch (much faster than one call per chunk)
    print(f"Embedding {len(records)} chunks from {len(files) - len(skipped)} contracts "
          f"(the first run downloads the model, ~70 MB)...")
    vectors = embed_passages([r[3] for r in records])

    with transaction() as conn:
        for (buyer_id, file_name, index, text), vector in zip(records, vectors):
            conn.execute(
                """
                INSERT INTO contract_chunks (buyer_id, source_file, chunk_index, content, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                ON CONFLICT (source_file, chunk_index)
                DO UPDATE SET buyer_id = EXCLUDED.buyer_id, content = EXCLUDED.content,
                              embedding = EXCLUDED.embedding
                """,
                (buyer_id, file_name, index, text, to_pgvector(vector)),
            )
        # Remove chunks from an older version of a contract that had more sections
        for file_name in {r[1] for r in records}:
            count = sum(1 for r in records if r[1] == file_name)
            conn.execute("DELETE FROM contract_chunks WHERE source_file = %s AND chunk_index >= %s",
                         (file_name, count))

    print(f"Stored {len(records)} chunks in {time.perf_counter() - started:.1f}s")
    if skipped:
        print(f"Skipped (no matching buyer): {skipped}")


if __name__ == "__main__":
    main()
