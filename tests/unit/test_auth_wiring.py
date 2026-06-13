from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


def test_server_built_with_none_auth():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    server = build_server(
        s,
        [BackendDef(name="hgnc", url_env="X", namespace="hgnc", enabled=False)],
        enable_search=False,
    )
    assert server.auth is None


def test_server_built_with_jwt_auth(monkeypatch):
    captured = {}

    def fake_build_auth(settings):
        captured["mode"] = settings.GF_AUTH_MODE
        return object()  # stand-in auth provider

    monkeypatch.setattr("genefoundry_router.server.build_auth", fake_build_auth)
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="jwt")
    server = build_server(
        s,
        [BackendDef(name="hgnc", url_env="X", namespace="hgnc", enabled=False)],
        enable_search=False,
    )
    assert captured["mode"] == "jwt"
    assert server.auth is not None
