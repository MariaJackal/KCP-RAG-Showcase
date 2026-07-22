from conftest import MockResponse
from services.answer_service import generate_refined_answer


class _SeqModel:
    def __init__(self, items):
        self.items = list(items)

    def generate_content(self, *args, **kwargs):
        if not self.items:
            raise RuntimeError("no more items")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return MockResponse(item)


class _MapLike:
    """Mimic protobuf map wrappers that support indexing but not .get()."""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def items(self):
        return self._data.items()


class _Result:
    def __init__(self, struct_data=None):
        data = _MapLike(
            {
                "title": "文件A",
                "snippets": [_MapLike({"snippet": "這是一段可用的檢索內容"})],
                "extractive_answers": [],
                "extractive_segments": [],
            }
        )

        class _Doc:
            def __init__(self, d, structured):
                self.derived_struct_data = d
                self.struct_data = structured

        self.document = _Doc(data, struct_data)


def test_generate_refined_answer_supports_maplike_derived_struct_data():
    rewriter = _SeqModel(["0"])
    answer_model = _SeqModel(["**結論:**\nA\n**注意事項:**\nC\n**法規依據:**\nB"])

    out = generate_refined_answer("q", "q", [_Result()], rewriter, answer_model)

    assert "結論" in out


def test_generate_refined_answer_supports_maplike_struct_data():
    rewriter = _SeqModel(["0"])
    answer_model = _SeqModel(["**結論:**\nA\n**注意事項:**\nC\n**法規依據:**\nB"])

    out = generate_refined_answer(
        "q",
        "q",
        [
            _Result(
                struct_data=_MapLike(
                    {
                        "law_name": "道路交通管理處罰條例",
                        "display_name": "第 3 條",
                        "embedding_text": "道路交通管理處罰條例 第 3 條 內容",
                    }
                )
            )
        ],
        rewriter,
        answer_model,
    )

    assert "結論" in out
