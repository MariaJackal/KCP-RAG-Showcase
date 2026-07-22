from types import SimpleNamespace

from services.rerank_service import RANKING_MODEL, rerank_results


class FakeResult:
    def __init__(self, title, content="內容"):
        class _Doc:
            def __init__(self, t, c):
                self.struct_data = {"title": t, "content": c}
                self.derived_struct_data = {}

        self.document = _Doc(title, content)


class FakeRankRecord:
    def __init__(self, id, score=0.5):
        self.id = id
        self.score = score


class FakeRankResponse:
    def __init__(self, records):
        self.records = records


class FakeRankClient:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error
        self.requests = []

    def rank(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return FakeRankResponse(self.records)


def _settings(top_n=15):
    return SimpleNamespace(project_id="p1", rerank_top_n=top_n)


def test_rerank_reorders_by_response_order():
    results = [FakeResult("A"), FakeResult("B"), FakeResult("C")]
    client = FakeRankClient(records=[
        FakeRankRecord("2", 0.9), FakeRankRecord("0", 0.5), FakeRankRecord("1", 0.1),
    ])
    out = rerank_results("查詢", results, _settings(), rank_client=client)
    assert [r.document.struct_data["title"] for r in out] == ["C", "A", "B"]


def test_rerank_request_shape_and_top_n_cap():
    results = [FakeResult("A"), FakeResult("B"), FakeResult("C")]
    client = FakeRankClient(records=[FakeRankRecord("0")])
    rerank_results("深夜競速", results, _settings(top_n=50), rank_client=client)
    req = client.requests[0]
    assert req["model"] == RANKING_MODEL
    assert req["ranking_config"] == (
        "projects/p1/locations/global/rankingConfigs/default_ranking_config"
    )
    assert req["query"] == "深夜競速"
    assert req["top_n"] == 3  # 上限為結果數
    assert [r["id"] for r in req["records"]] == ["0", "1", "2"]
    assert req["records"][0]["title"] == "A"


def test_rerank_truncates_to_top_n():
    results = [FakeResult(t) for t in ("A", "B", "C", "D")]
    client = FakeRankClient(records=[FakeRankRecord("3", 0.9), FakeRankRecord("1", 0.8)])
    out = rerank_results("q", results, _settings(top_n=2), rank_client=client)
    assert [r.document.struct_data["title"] for r in out] == ["D", "B"]
    assert client.requests[0]["top_n"] == 2


def test_rerank_failure_returns_original_order():
    results = [FakeResult("A"), FakeResult("B")]
    client = FakeRankClient(error=RuntimeError("boom"))
    out = rerank_results("q", results, _settings(), rank_client=client)
    assert out == results


def test_rerank_invalid_and_duplicate_ids_ignored():
    results = [FakeResult("A"), FakeResult("B")]
    client = FakeRankClient(records=[
        FakeRankRecord("x"), FakeRankRecord("9"), FakeRankRecord("1"), FakeRankRecord("1"),
    ])
    out = rerank_results("q", results, _settings(), rank_client=client)
    assert [r.document.struct_data["title"] for r in out] == ["B"]


def test_rerank_empty_response_returns_original():
    results = [FakeResult("A"), FakeResult("B")]
    client = FakeRankClient(records=[])
    out = rerank_results("q", results, _settings(), rank_client=client)
    assert out == results


def test_rerank_skips_single_result_and_blank_query():
    single = [FakeResult("A")]
    client = FakeRankClient(records=[FakeRankRecord("0")])
    assert rerank_results("q", single, _settings(), rank_client=client) == single
    two = [FakeResult("A"), FakeResult("B")]
    assert rerank_results("  ", two, _settings(), rank_client=client) == two
    assert client.requests == []  # 皆未呼叫 API


def test_rerank_unwraps_subquery_wrapper():
    inner = FakeResult("內層")
    wrapper = SimpleNamespace(result=inner, sub_query="q1", sub_query_index=1)
    client = FakeRankClient(records=[FakeRankRecord("1"), FakeRankRecord("0")])
    out = rerank_results("q", [FakeResult("外層"), wrapper], _settings(), rank_client=client)
    assert out[0] is wrapper  # 回傳原包裝物件，只換順序
    assert client.requests[0]["records"][1]["title"] == "內層"
