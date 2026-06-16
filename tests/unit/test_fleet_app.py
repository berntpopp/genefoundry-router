from starlette.routing import Mount

from genefoundry_router.devtools.fake_fleet import build_fleet_app, url_map
from genefoundry_router.devtools.fakes import load_manifest


def test_fleet_app_mounts_every_backend_path():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    app = build_fleet_app(manifest)
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    assert mounts == {"/gnomad", "/gtex", "/pubtator"}


async def test_fleet_app_lifespan_enters_all_children():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    app = build_fleet_app(manifest)
    assert len(app.routes) == len(manifest.backends)  # guard against vacuous pass
    async with app.router.lifespan_context(app):
        pass  # no exception == all child lifespans entered+exited cleanly


def test_url_map_is_localhost_paths():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    urls = url_map(manifest, "127.0.0.1", 9100)
    assert urls["gnomad"] == "http://127.0.0.1:9100/gnomad/mcp"
