from services.timing import measure_ms


def test_measure_ms_returns_result_and_non_negative_time():
    result, elapsed_ms = measure_ms(lambda x, y: x + y, 2, 3)
    assert result == 5
    assert elapsed_ms >= 0
