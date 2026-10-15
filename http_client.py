"""Shared HTTP session: keep-alive pool + automatic retry of dropped connections.

Без общего Session каждый requests.get/post открывает новое TCP+TLS соединение.
При 10-20 параллельных потоках это медленно и часто заканчивается SSL EOF /
Connection reset, которые в GUI видны как «сетевая ошибка».
"""
import threading
from http.cookiejar import DefaultCookiePolicy

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Быстро падаем на установке соединения (и повторяем), но даём серверу время ответить.
CONNECT_TIMEOUT_SECONDS = 10
POOL_SIZE = 64

_session = None
_session_lock = threading.Lock()


def _with_connect_timeout(timeout):
    if isinstance(timeout, (int, float)) and timeout > CONNECT_TIMEOUT_SECONDS:
        return (CONNECT_TIMEOUT_SECONDS, timeout)
    return timeout


class _SharedSession(requests.Session):
    def request(self, method, url, **kwargs):
        if "timeout" in kwargs:
            kwargs["timeout"] = _with_connect_timeout(kwargs["timeout"])
        return super().request(method, url, **kwargs)


def _build_session():
    session = _SharedSession()
    # Сессия общая для всех потоков и аккаунтов — cookies не храним.
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    retry = Retry(
        total=3,
        connect=3,
        # Read-ошибки повторяем только для идемпотентных методов (GET/PUT/DELETE/HEAD...),
        # POST/PATCH повторяет прикладной код, чтобы не задвоить создание объектов.
        read=2,
        status=0,
        other=1,
        backoff_factor=0.5,
        raise_on_status=False,
        respect_retry_after_header=False,
    )
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=POOL_SIZE, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = _build_session()
    return _session


def request(method, url, **kwargs):
    return get_session().request(method, url, **kwargs)


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def put(url, **kwargs):
    return request("PUT", url, **kwargs)


def patch(url, **kwargs):
    return request("PATCH", url, **kwargs)


def delete(url, **kwargs):
    return request("DELETE", url, **kwargs)


def head(url, **kwargs):
    kwargs.setdefault("allow_redirects", False)
    return request("HEAD", url, **kwargs)
