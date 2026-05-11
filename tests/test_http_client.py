from app.collectors import http_client
from app.collectors.http_client import AntiBanError, HttpClient


class DummyResponse:
    def __init__(self, text="<html>ok</html>", status_code=200) -> None:
        self.status_code = status_code
        self.text = text


class DummySession:
    def __init__(self, response=None) -> None:
        self.response = response or DummyResponse()
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class CacheSpy:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


def test_kwork_cookie_is_sent_only_for_kwork(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(http_client.config, "KWORK_COOKIE", "sessionid=test-cookie")
    monkeypatch.setattr(http_client, "_sleep_before_request", lambda: None)
    monkeypatch.setattr(http_client, "get_cached_page", lambda url: None)
    monkeypatch.setattr(http_client, "cache_page", lambda *args, **kwargs: None)

    client = HttpClient(session=session)

    client.get_html("https://kwork.ru/categories/test", source="kwork")
    client.get_html("https://funpay.com/lots/test", source="funpay")

    assert session.calls[0][1]["headers"]["Cookie"] == "sessionid=test-cookie"
    assert session.calls[0][1]["headers"]["Referer"] == "https://kwork.ru/seller"
    assert session.calls[0][1]["headers"]["Accept-Language"].startswith("ru-RU")
    assert session.calls[1][1]["headers"] == {}


def test_empty_kwork_cookie_is_not_sent(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(http_client.config, "KWORK_COOKIE", None)
    monkeypatch.setattr(http_client, "_sleep_before_request", lambda: None)
    monkeypatch.setattr(http_client, "get_cached_page", lambda url: None)
    monkeypatch.setattr(http_client, "cache_page", lambda *args, **kwargs: None)

    HttpClient(session=session).get_html("https://kwork.ru/categories/test", source="kwork")

    assert "Cookie" not in session.calls[0][1]["headers"]
    assert session.calls[0][1]["headers"]["Sec-Fetch-Mode"] == "navigate"


def test_yandex_smartcaptcha_raises_antiban_before_cache(monkeypatch):
    session = DummySession(
        response=DummyResponse(
            '<html><div class="smart-captcha">Подтвердите, что вы не робот</div></html>'
        )
    )
    cache_spy = CacheSpy()
    monkeypatch.setattr(http_client.config, "KWORK_COOKIE", "sessionid=test-cookie")
    monkeypatch.setattr(http_client, "_sleep_before_request", lambda: None)
    monkeypatch.setattr(http_client, "get_cached_page", lambda url: None)
    monkeypatch.setattr(http_client, "cache_page", cache_spy)

    try:
        HttpClient(session=session).get_html("https://kwork.ru/categories/test", source="kwork")
    except AntiBanError as error:
        assert "Yandex SmartCaptcha" in str(error)
    else:
        raise AssertionError("AntiBanError was not raised")

    assert cache_spy.calls == []
