"""#2: 強制 JWT_SECRET_KEY 存在且不得與 APP_PASSWORD 相同。"""

import pytest

from api.auth import assert_jwt_secret_safe


def test_distinct_secret_passes(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "a-distinct-random-secret-32chars-xx")
    assert_jwt_secret_safe("the-app-password")  # 不應 raise


def test_secret_equal_app_password_raises(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "samesecret")
    with pytest.raises(RuntimeError):
        assert_jwt_secret_safe("samesecret")


def test_missing_secret_raises(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    with pytest.raises(RuntimeError):
        assert_jwt_secret_safe("whatever")
