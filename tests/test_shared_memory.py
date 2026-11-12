from workbench.shared_memory import retrieve


def test_scoped_ranked_bounded_retrieval():
    class Store:
        def all(self, sql, args):
            assert args == ('project', 'project')
            assert 'WHERE project_id=?' in sql
            return [dict(id='old', category='project', content='unrelated', revision=1),
                    dict(id='hit', category='project', content='codex testing', revision=2)]
    assert retrieve(Store(), 'project', 'codex', budget=5) == [
        dict(id='hit', category='project', content='codex', revision=2)]
    assert retrieve(Store(), 'project', '') == []
