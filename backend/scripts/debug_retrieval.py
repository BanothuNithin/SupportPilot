import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import User
from app.rag import store
from app.rag.embeddings import get_embedder


async def debug_retrieval(email: str, question: str) -> None:
    async with SessionLocal() as session:
        tenant_id = await session.scalar(
            select(User.tenant_id).where(User.email == email)
        )
    if tenant_id is None:
        raise SystemExit(f"No user found with email {email!r}")

    [vector] = await get_embedder().embed([question])
    hits = await store.search(tenant_id, vector, limit=8, min_score=0.0)

    threshold = settings.rag_min_score
    print(f"Current rag_min_score: {threshold:.3f}")
    for hit in hits:
        comparison = "ABOVE" if hit.score >= threshold else "below"
        preview = hit.text[:80].replace("\n", " ")
        print(
            f"score={hit.score:.3f} {comparison}; "
            f"chunk_index={hit.chunk_index}; text={preview}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect retrieval scores for a user's tenant without changing data."
    )
    parser.add_argument("email", help="Email of a tenant user")
    parser.add_argument("question", help="Question text to embed and search")
    args = parser.parse_args()
    asyncio.run(debug_retrieval(args.email, args.question))


if __name__ == "__main__":
    main()
