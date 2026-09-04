"""
pgvector RAG Service
Stores AI-generated embeddings of financial summaries in Supabase pgvector,
enabling semantic search over company data.

This enhances AI summaries with relevant peer comparisons and sector context.
"""
import json
import logging
import os
from typing import Optional

import asyncpg
import numpy as np

log = logging.getLogger("rag_service")

# Embedding dimensions for text-embedding-004 (Google)
EMBED_DIM = 768


async def get_embedding(text: str, api_key: str) -> Optional[list[float]]:
    """Get embedding vector from Google's text-embedding-004 model."""
    import httpx
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
    payload = {
        "model": "models/text-embedding-004",
        "content": {"parts": [{"text": text[:2000]}]},  # max 2000 chars
        "taskType": "RETRIEVAL_DOCUMENT",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()["embedding"]["values"]
        except Exception as e:
            log.warning(f"Embedding failed: {e}")
    return None


async def store_embedding(
    conn,
    nse_symbol: str,
    content_type: str,
    text_content: str,
    embedding: list[float],
) -> bool:
    """Store embedding in stock_embeddings table."""
    try:
        # Convert to pgvector format
        vec_str = f"[{','.join(str(v) for v in embedding)}]"
        await conn.execute("""
            INSERT INTO stock_embeddings (nse_symbol, content_type, text_content, embedding, created_at)
            VALUES ($1, $2, $3, $4::vector, NOW())
            ON CONFLICT (nse_symbol, content_type)
            DO UPDATE SET text_content = EXCLUDED.text_content,
                          embedding = EXCLUDED.embedding,
                          created_at = NOW()
        """, nse_symbol, content_type, text_content[:4000], vec_str)
        return True
    except Exception as e:
        log.warning(f"Store embedding failed for {nse_symbol}: {e}")
        return False


async def similarity_search(
    conn,
    query_embedding: list[float],
    limit: int = 5,
    content_type: Optional[str] = None,
    exclude_symbol: Optional[str] = None,
) -> list[dict]:
    """Find most similar stocks by embedding cosine similarity."""
    vec_str = f"[{','.join(str(v) for v in query_embedding)}]"
    where_clauses = []
    params = [vec_str, limit]

    if content_type:
        params.append(content_type)
        where_clauses.append(f"content_type = ${len(params)}")
    if exclude_symbol:
        params.append(exclude_symbol)
        where_clauses.append(f"nse_symbol != ${len(params)}")

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    try:
        rows = await conn.fetch(f"""
            SELECT nse_symbol, content_type, text_content,
                   1 - (embedding <=> $1::vector) as similarity
            FROM stock_embeddings
            {where}
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """, *params)
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"Similarity search failed: {e}")
        return []


class RAGContext:
    """Build RAG context for AI prompt augmentation."""

    def __init__(self, conn, api_key: str):
        self.conn = conn
        self.api_key = api_key

    async def build_context(self, symbol: str, query_text: str, max_tokens: int = 1000) -> str:
        """Find relevant context from stored embeddings for a symbol's AI prompt."""
        # Get query embedding
        embedding = await get_embedding(query_text, self.api_key)
        if not embedding:
            return ""  # Graceful degradation

        # Find similar stocks
        similar = await similarity_search(
            self.conn,
            embedding,
            limit=3,
            exclude_symbol=symbol,
        )

        if not similar:
            return ""

        context_parts = []
        total_chars = 0
        max_chars = max_tokens * 4  # ~4 chars per token

        for s in similar:
            snippet = f"[{s['nse_symbol']}] {s['text_content'][:300]}"
            if total_chars + len(snippet) > max_chars:
                break
            context_parts.append(snippet)
            total_chars += len(snippet)

        if not context_parts:
            return ""

        return "\n\nRelated companies for context:\n" + "\n---\n".join(context_parts)

    async def index_summary(self, symbol: str, summary_text: str) -> bool:
        """Index an AI summary for future RAG retrieval."""
        embedding = await get_embedding(summary_text, self.api_key)
        if not embedding:
            return False
        return await store_embedding(
            self.conn, symbol, "ai_summary", summary_text, embedding
        )

    async def index_fundamentals(self, symbol: str, ratios: dict) -> bool:
        """Index fundamental data as searchable text."""
        text = f"{symbol} fundamentals: " + " | ".join(
            f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
            for k, v in ratios.items()
            if v is not None
        )
        embedding = await get_embedding(text, self.api_key)
        if not embedding:
            return False
        return await store_embedding(
            self.conn, symbol, "fundamentals", text, embedding
        )
