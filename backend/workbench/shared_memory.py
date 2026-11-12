"""Project-scoped FTS5 retrieval shared by every native harness."""
import re


def index_text(text):
    # Separate Han characters so unicode61 can index Chinese without extensions.
    return re.sub(r"([\u4e00-\u9fff])", r" \1 ", text)


def search(store, project_id, query, limit=20, scope="project"):
    if not query.strip():
        return store.all(
            "SELECT id,category,content,revision FROM memories WHERE project_id=? AND state='active' AND scope=? ORDER BY created_at DESC,id LIMIT ?",
            (project_id, scope, limit),
        )
    terms = re.findall(r"[\u4e00-\u9fff]+|[^\W_]+(?:_[^\W_]+)*", query[:500], re.UNICODE)
    if not terms:
        return []
    expression = ' OR '.join('"' + index_text(term).strip() + '"' for term in terms)
    return store.all(
        "SELECT m.id,m.category,m.content,m.revision FROM memory_fts JOIN memories m ON m.rowid=memory_fts.rowid "
        "WHERE memory_fts MATCH ? AND m.project_id=? AND m.state='active' AND m.scope=? "
        "ORDER BY bm25(memory_fts),m.created_at DESC,m.id LIMIT ?",
        (expression, project_id, scope, limit),
    )


def retrieve(store, project_id, query, limit=8, budget=12000, scope="project"):
    if not query.strip():
        return []
    result = []
    for row in search(store, project_id, query, limit, scope):
        if budget <= 0:
            break
        content = row['content'][:min(4000, budget)]
        result.append({**row, 'content': content})
        budget -= len(content)
    return result
