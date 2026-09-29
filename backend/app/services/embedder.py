import os

def memory_string(title_clean: str, summary: str, key_points: list, entities: dict) -> str:
    parts = []
    if title_clean: parts.append(title_clean)
    if summary: parts.append(summary)
    if key_points: parts.extend(key_points)
    for vals in (entities or {}).values():
        if isinstance(vals, list): parts.extend([str(v) for v in vals])
    text = " ".join(parts).strip()
    return text[:6000] if text else (summary or title_clean or "saved memory")

async def embed_text(text: str) -> list[float] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    if not api_key or api_key.startswith("sk-..."):
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.embeddings.create(model=model, input=text[:8000])
        return resp.data[0].embedding
    except Exception as e:
        print(f"[embedder] failed: {e}")
        return None

def chunk_text(text: str, size: int = 800, overlap: int = 160) -> list[str]:
    words = text.split()
    if len(words) <= size: return [text]
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i+size]))
        if i + size >= len(words): break
        i += size - overlap
    return chunks
