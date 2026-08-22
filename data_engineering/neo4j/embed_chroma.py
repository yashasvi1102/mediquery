"""
Day 27: Embed patient summaries into Chroma for semantic retrieval.

Uses all-MiniLM-L6-v2 (free, local, no API key).
Creates a persistent Chroma collection at project_root/chroma_db/.

Usage (from project root):
    python data_engineering/neo4j/embed_chroma.py

Depends on Day 26 (patient_summaries.parquet must exist).
"""

import sys
import time
from pathlib import Path

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
SUMMARIES_PATH = _PROJECT_ROOT / "data_generation" / "parsed" / "patient_summaries.parquet"
CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")

BATCH_SIZE = 500  # Chroma add() batch limit
COLLECTION_NAME = "patient_summaries"


def main():
    print("Day 27: Embedding patient summaries into Chroma")
    start = time.time()

    # ---------------------------------------------------------------------------
    # Check dependencies
    # ---------------------------------------------------------------------------
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        print("ERROR: chromadb not installed. Run:")
        print("  pip install chromadb sentence-transformers")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Load summaries
    # ---------------------------------------------------------------------------
    if not SUMMARIES_PATH.exists():
        print(f"ERROR: Summaries not found at {SUMMARIES_PATH}")
        print("Run generate_summaries.py first (Day 26).")
        sys.exit(1)

    df = pd.read_parquet(str(SUMMARIES_PATH))
    print(f"  Loaded {len(df):,} summaries from {SUMMARIES_PATH.name}")

    # ---------------------------------------------------------------------------
    # Initialize Chroma
    # ---------------------------------------------------------------------------
    print(f"  Chroma DB path: {CHROMA_PATH}")

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    print("  Embedding model: all-MiniLM-L6-v2 (384 dimensions)")

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    # Delete existing collection if re-running (idempotent)
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"  Deleted existing '{COLLECTION_NAME}' collection (re-run)")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"description": "MediQuery patient summaries for semantic retrieval"}
    )
    print(f"  Created collection '{COLLECTION_NAME}'")

    # ---------------------------------------------------------------------------
    # Embed in batches
    # ---------------------------------------------------------------------------
    print(f"\n  Embedding {len(df):,} summaries (batch size {BATCH_SIZE})...")
    embed_start = time.time()

    ids = df["patient_id"].tolist()
    documents = df["summary_text"].tolist()

    total_batches = (len(df) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(df), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch_ids = ids[i : i + BATCH_SIZE]
        batch_docs = documents[i : i + BATCH_SIZE]

        collection.add(
            ids=batch_ids,
            documents=batch_docs,
        )

        pct = min(100, int(batch_num / total_batches * 100))
        elapsed = time.time() - embed_start
        print(f"\r  Embedding: batch {batch_num}/{total_batches} ({pct}%) — {elapsed:.1f}s", end="", flush=True)

    elapsed = time.time() - embed_start
    print(f"\r  Embedding: {len(df):,} summaries in {elapsed:.1f}s" + " " * 30)

    # ---------------------------------------------------------------------------
    # Verify
    # ---------------------------------------------------------------------------
    print(f"\n  Collection count: {collection.count()}")
    assert collection.count() == len(df), (
        f"Count mismatch: {collection.count()} vs {len(df)}"
    )

    # ---------------------------------------------------------------------------
    # Test queries
    # ---------------------------------------------------------------------------
    print("\n=== Semantic search verification ===")
    test_queries = [
        "elderly diabetic patient with heart problems",
        "young patient with frequent emergency visits",
        "patient with multiple chronic conditions and many medications",
        "deceased patient with cancer history",
        "patient with hypertension and kidney disease",
    ]

    for query in test_queries:
        results = collection.query(
            query_texts=[query],
            n_results=3,
        )
        print(f"\n  Query: \"{query}\"")
        for j, (doc_id, doc, distance) in enumerate(zip(
            results["ids"][0],
            results["documents"][0],
            results["distances"][0],
        )):
            # Show first 150 chars of the matching summary
            preview = doc[:150].replace("\n", " ")
            print(f"    [{j+1}] dist={distance:.3f} | {doc_id[:12]}... | {preview}...")

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    total_elapsed = time.time() - start
    print(f"\n=== Day 27 complete in {total_elapsed:.1f}s ===")
    print(f"  Collection: {COLLECTION_NAME}")
    print(f"  Documents:  {collection.count():,}")
    print(f"  Model:      all-MiniLM-L6-v2")
    print(f"  Chroma DB:  {CHROMA_PATH}")
    print(f"\n  Add 'chroma_db/' to .gitignore (like mediquery.duckdb).")
    print(f"  Ready for Day 28 (query router).")


if __name__ == "__main__":
    main()