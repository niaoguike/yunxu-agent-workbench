import json
import os
import re
import urllib.request


def voyage(texts, input_type):
    request = urllib.request.Request(
        'https://api.voyageai.com/v1/embeddings',
        data=json.dumps(dict(input=texts, model='voyage-3.5-lite', input_type=input_type, output_dimension=512)).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['VOYAGE_API_KEY'], 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)['data']
    return [row['embedding'] for row in sorted(data, key=lambda row: row['index'])]


class HistorySearch:
    def __init__(self, store, embed=None):
        self.store = store
        self.embed = embed or voyage
        with store.transaction() as db:
            db.executescript('''
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(content, content=messages, content_rowid=rowid, tokenize='trigram');
                CREATE TRIGGER IF NOT EXISTS history_insert AFTER INSERT ON messages BEGIN
                  INSERT INTO history_fts(rowid,content) VALUES(new.rowid,new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS history_delete AFTER DELETE ON messages BEGIN
                  INSERT INTO history_fts(history_fts,rowid,content) VALUES('delete',old.rowid,old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS history_update AFTER UPDATE ON messages BEGIN
                  INSERT INTO history_fts(history_fts,rowid,content) VALUES('delete',old.rowid,old.content);
                  INSERT INTO history_fts(rowid,content) VALUES(new.rowid,new.content);
                END;
            ''')
            db.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")

    def search(self, project_id, query, limit=20):
        query = query.strip()
        if not query or len(query) > 2000 or not 1 <= limit <= 100:
            raise ValueError('查询长度需为 1–2000，结果数需为 1–100')
        candidates = self.store.all('SELECT m.rowid AS rid,m.*,t.title FROM messages m JOIN tasks t ON t.id=m.task_id WHERE t.project_id=?', (project_id,))
        allowed = {row['rid']: row for row in candidates}
        expression = ' OR '.join('"' + word.replace('"', '""') + '"' for word in re.findall(r'\w+', query))
        keyword = []
        if expression:
            keyword = self.store.all('SELECT m.rowid AS rid FROM history_fts JOIN messages m ON m.rowid=history_fts.rowid JOIN tasks t ON t.id=m.task_id WHERE history_fts MATCH ? AND t.project_id=? ORDER BY bm25(history_fts),m.rowid LIMIT ?', (expression, project_id, limit * 4))
        rankings = [[row['rid'] for row in keyword]]
        mode = 'keyword'
        error = None
        if os.environ.get('VOYAGE_API_KEY') or self.embed is not voyage:
            try:
                import sqlite_vec
                with self.store.transaction() as db:
                    db.enable_load_extension(True)
                    try:
                        sqlite_vec.load(db)
                    finally:
                        db.enable_load_extension(False)
                    db.execute('CREATE TABLE IF NOT EXISTS history_vectors(message_id TEXT PRIMARY KEY,content TEXT NOT NULL,embedding BLOB NOT NULL)')
                vectors = {}
                for start in range(0, len(candidates), 64):
                    batch = candidates[start:start+64]
                    missing = []
                    for row in batch:
                        cached = self.store.one('SELECT embedding FROM history_vectors WHERE message_id=? AND content=?', (row['id'], row['content']))
                        if cached:
                            vectors[row['rid']] = cached['embedding']
                        else:
                            missing.append(row)
                    if missing:
                        embeddings = self.embed([row['content'] for row in missing], 'document')
                        if len(embeddings) != len(missing):
                            raise ValueError('Invalid embedding response')
                        for row, vector in zip(missing, embeddings):
                            blob = sqlite_vec.serialize_float32(vector)
                            vectors[row['rid']] = blob
                            self.store.execute('INSERT OR REPLACE INTO history_vectors VALUES(?,?,?)', (row['id'], row['content'], blob))
                query_vector = sqlite_vec.serialize_float32(self.embed([query], 'query')[0])
                # Scope before ranking so other projects never consume the candidate budget.
                with self.store.transaction() as db:
                    ranked = sorted(vectors, key=lambda rid: (db.execute('SELECT vec_distance_cosine(?,?)', (vectors[rid], query_vector)).fetchone()[0], rid))
                rankings.append(ranked[:limit*4])
                mode = 'hybrid'
            except Exception:
                error = '语义检索暂不可用，已返回关键词结果'
        scores = {}
        for ranking in rankings:
            for rank, rid in enumerate(ranking, 1):
                scores[rid] = scores.get(rid, 0) + 1 / (60 + rank)
        results = [dict(allowed[rid], score=scores[rid]) for rid in sorted(scores, key=lambda rid: (-scores[rid], rid))[:limit]]
        for row in results:
            row.pop('rid')
        return dict(results=results, mode=mode, warning=error)
