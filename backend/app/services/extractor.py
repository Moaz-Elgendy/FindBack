import os
import json
from app.schemas import ExtractedMemory

SYSTEM_PROMPT = """You are FindBack's memory extractor. Given raw content scraped from a saved link, return a STRICT JSON object with:
{
  "summary": "25-word faithful summary, no hallucination",
  "key_points": ["3 concise bullet takeaways"],
  "category": "one of: recipe, tutorial, tool, product, video, article, other",
  "entities": {"ingredients": [], "tech": [], "people": [], "topics": [], "products": []},
  "intent": "one of: learn, cook, buy, watch, read, other",
  "tags": ["3 short lowercase tags"],
  "title_clean": "clean human title, max 12 words"
}
Rules: Be faithful to content. If field unknown, use [] or "other". No extra keys. JSON only."""

FALLBACK = ExtractedMemory(
    summary="Saved content awaiting AI processing.",
    key_points=["Open original to view content","AI summary unavailable yet","Try searching by what you remember"],
    category="other", entities={}, intent="other", tags=["saved","pending","memory"], title_clean="Saved Memory"
)

async def extract_memory(raw_text: str, url_title: str = "") -> ExtractedMemory:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("EXTRACTOR_MODEL", "gpt-4o-mini")
    if not api_key or not raw_text or api_key.startswith("sk-...") :
        # offline/dev fallback: naive keyword category
        return _heuristic(raw_text, url_title)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        user_content = f"URL title hint: {url_title}\n\nContent (truncated):\n{raw_text[:8000]}"
        resp = await client.chat.completions.create(
            model=model, temperature=0.1,
            response_format={"type": "json_object"},
            messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":user_content}]
        )
        data = json.loads(resp.choices[0].message.content)
        return ExtractedMemory(
            summary=str(data.get("summary",""))[:300],
            key_points=list(data.get("key_points",[]))[:3],
            category=str(data.get("category","other")).lower(),
            entities=dict(data.get("entities",{})),
            intent=str(data.get("intent","other")).lower(),
            tags=[str(t).lower() for t in data.get("tags",[])][:3],
            title_clean=str(data.get("title_clean",""))[:120]
        )
    except Exception as e:
        print(f"[extractor] LLM failed: {e}")
        return _heuristic(raw_text, url_title)

def _heuristic(text: str, hint: str) -> ExtractedMemory:
    t = (hint + " " + text).lower()
    category = "article"
    if any(k in t for k in ["recipe","ingredient","cook","chicken","mushroom"]): category="recipe"
    elif any(k in t for k in ["tutorial","how to","fix","aws","tool"]): category="tutorial"
    elif any(k in t for k in ["buy","price","$"]): category="product"
    title_clean = hint[:80] if hint else text[:80].split("\n")[0][:80] or "Saved Memory"
    summary = text[:200].split(".")[0][:180] + "." if text else "Saved for later."
    return ExtractedMemory(summary=summary, key_points=[summary.strip()[:100]], category=category,
        entities={"topics":[category]}, intent="learn" if category in ("tutorial","article") else "other",
        tags=[category,"saved","memory"], title_clean=title_clean)
