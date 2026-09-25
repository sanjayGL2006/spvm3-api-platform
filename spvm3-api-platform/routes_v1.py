"""The public API that API keys unlock."""
from flask import Blueprint, g, request
import os
import sys
import subprocess
import pandas as pd

from core import api_key_required, db, fail, ok
from knowledge import build_answer, search_knowledge

bp = Blueprint("v1", __name__, url_prefix="/v1")


def _entry(row):
    return {"id": row["id"], "category": row["category"], "title": row["title"], "content": row["content"],
            "tags": row["tags"], "updated_at": row["updated_at"]}


def _limit(default=5, maximum=20):
    try:
        return min(max(int(request.args.get("limit", default)), 1), maximum)
    except ValueError:
        return default


@bp.get("/whoami")
@api_key_required()
def whoami():
    k = g.key
    return ok({"key_prefix": k["key_prefix"], "environment": k["environment"], "scopes": k["scopes"],
               "expires_at": k["expires_at"], "rate_limit_per_min": k["rate_limit_per_min"]})


@bp.get("/categories")
@api_key_required("knowledge:read")
def categories():
    with db() as cur:
        cur.execute("SELECT category, count(*) AS entries FROM knowledge GROUP BY category ORDER BY category")
        return ok({"categories": cur.fetchall()})


@bp.get("/knowledge/search")
@api_key_required("knowledge:read")
def search():
    q = (request.args.get("q") or "").strip()[:200]
    category = (request.args.get("category") or "").strip()[:60] or None
    limit = _limit()
    if q:
        rows = search_knowledge(q, category, limit)
    else:
        with db() as cur:
            cur.execute("SELECT id, category, title, content, tags, updated_at FROM knowledge "
                        "WHERE (%s IS NULL OR category = %s) ORDER BY updated_at DESC LIMIT %s",
                        (category, category, limit))
            rows = cur.fetchall()
    return ok({"results": [_entry(r) for r in rows]})


@bp.get("/knowledge/<int:entry_id>")
@api_key_required("knowledge:read")
def get_entry(entry_id):
    with db() as cur:
        cur.execute("SELECT id, category, title, content, tags, updated_at FROM knowledge WHERE id = %s", (entry_id,))
        row = cur.fetchone()
    if row is None:
        return fail("NOT_FOUND", "Entry not found.", 404)
    return ok(_entry(row))


@bp.post("/ask")
@api_key_required("ask")
def ask():
    question = str((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question:
        return fail("INVALID_INPUT", "'question' is required.", 400)
    if len(question) > 500:
        return fail("INVALID_INPUT", "Question is too long (max 500 characters).", 400)
    matches = search_knowledge(question, None, 4)
    answer, source = build_answer(question, matches)
    return ok({"answer": answer, "source": source, "sources": [{"id": m["id"], "title": m["title"]} for m in matches]})


@bp.post("/auto_learn")
@api_key_required("ask")
def auto_learn():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    label = str(data.get("label") or "").strip()
    
    if not text or not label:
        return fail("INVALID_INPUT", "'text' and 'label' are required.", 400)
        
    dataset_dir = os.path.join(os.path.dirname(__file__), "dataset", "archive")
    os.makedirs(dataset_dir, exist_ok=True)
    
    csv_file = os.path.join(dataset_dir, "auto_learned_data.csv")
    
    df = pd.DataFrame([{"text": text, "label": label}])
    # Append to CSV, if file doesn't exist write header
    df.to_csv(csv_file, mode='a', header=not os.path.exists(csv_file), index=False)
    
    # Trigger training script asynchronously
    script_path = os.path.join(os.path.dirname(__file__), "train_model.py")
    subprocess.Popen([sys.executable, script_path])
    
    return ok({"message": "Data saved and model retraining started in background."})
