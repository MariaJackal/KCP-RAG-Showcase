from services.telemetry import classify_error


def test_classify_error_credentials():
    assert classify_error(RuntimeError("ADC credential not found")) == "auth_credentials"


def test_classify_error_permission():
    assert classify_error(RuntimeError("Permission denied 403")) == "auth_permission"


def test_classify_error_timeout():
    assert classify_error(RuntimeError("request timeout exceeded")) == "timeout"


def test_classify_error_quota():
    assert classify_error(RuntimeError("Resource exhausted 429")) == "quota_rate_limit"


def test_classify_error_unknown():
    assert classify_error(RuntimeError("something else")) == "unknown"
