"""Knowledge search and answering, shared by the public API and the admin panel."""
import json
import re

import requests

from core import OLLAMA_MODEL, OLLAMA_URL, db, log


def search_knowledge(question, category=None, limit=5):
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2][:12]
    if not words:
        return []
    tsquery = " ".join(f"+{w}*" for w in words)
    with db() as cur:
        cur.execute(
            """
            SELECT id, category, title, content, tags, updated_at,
                   MATCH(title, content) AGAINST (%s IN BOOLEAN MODE) AS rank
            FROM knowledge
            WHERE MATCH(title, content) AGAINST (%s IN BOOLEAN MODE)
              AND (%s IS NULL OR category = %s)
            ORDER BY rank DESC LIMIT %s
            """,
            (tsquery, tsquery, category, category, limit),
        )
        return cur.fetchall()


def _ask_llm(question, matches):
    context = "\n\n".join(f"[{m['title']}]\n{m['content']}" for m in matches)
    prompt = (
        "You are the SPVM3 knowledge assistant. Answer using ONLY the context below. "
        "If the context does not contain the answer, say you do not have that information yet. "
        f"Keep the answer short and clear.\n\nContext:\n{context}\n\nQuestion: {question}\nAnswer:"
    )
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}, timeout=60)
    r.raise_for_status()
    return r.json()["response"].strip()


def build_answer(question, matches):
    if not matches:
        return "I don't have information about that yet.", "none"
    if OLLAMA_URL:
        try:
            return _ask_llm(question, matches), "llm"
        except Exception as exc:
            log.warning("LLM call failed, using retrieval answer: %s", exc)
    text = matches[0]["content"].strip()
    if len(text) > 700:
        text = text[:700].rsplit(" ", 1)[0] + "..."
    return text, "retrieval"


def clean_entry(data):
    category = str(data.get("category") or "general").strip()[:60]
    title = str(data.get("title") or "").strip()[:200]
    content = str(data.get("content") or "").strip()
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tags = [str(t).strip()[:40] for t in tags if str(t).strip()][:20]
    if not title or not content:
        return None
    return category, title, content, json.dumps(tags)
