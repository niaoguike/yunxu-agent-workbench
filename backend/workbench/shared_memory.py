"""Project-scoped retrieval shared by every native harness."""
import re


def retrieve(store, project_id, query, limit=8, budget=12000, scope="project"):
    tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2}", query.lower()))
    if not tokens:
        return []
    rows = store.all(
        "SELECT id,category,content,revision FROM memories WHERE project_id=? AND state='active' AND scope=? ORDER BY created_at DESC,id",
        (project_id, scope),
    )
    ranked = sorted(
        ((sum(token in row['content'].lower() for token in tokens), index, row)
         for index, row in enumerate(rows)),
        key=lambda item: (-item[0], item[1]),
    )
    result = []
    for score, _, row in ranked:
        if not score or len(result) >= limit or budget <= 0:
            break
        content = row['content'][:min(4000, budget)]
        result.append({**row, 'content': content})
        budget -= len(content)
    return result
