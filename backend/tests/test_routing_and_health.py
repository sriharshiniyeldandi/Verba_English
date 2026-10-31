"""Tests for root, health, OpenAPI/Swagger docs, CORS, and routing configuration."""
from app.database.session import _normalize_database_url


def test_root_endpoint_returns_online_status(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.json() == {
        "status": "online",
        "application": "Verba",
        "message": "Verba backend is running",
    }
    assert "X-Process-Time-Ms" in res.headers


def test_health_endpoint_returns_healthy(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}
    assert "X-Process-Time-Ms" in res.headers


def test_swagger_docs_and_openapi_schema_are_available(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text.lower()

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    schema = openapi.json()
    paths = set(schema.get("paths", {}).keys())

    expected_paths = {
        "/",
        "/health",
        "/lessons",
        "/auth/register",
        "/auth/login",
        "/auth/me",
        "/auth/forgot-password",
        "/auth/verify-reset-code",
        "/auth/reset-password",
        "/practice/attempts",
        "/practice/attempts/{attempt_id}",
        "/practice/attempts/{attempt_id}/retry",
        "/progress/summary",
        "/progress/calendar",
        "/progress/activity",
        "/progress/lessons/{lesson_id}",
        "/admin/media/lesson/{lesson_id}",
        "/admin/media/{media_id}",
        "/admin/stats/overview",
        "/admin/content-progress/summary",
        "/admin/content-progress/teachers",
        "/admin/content-progress/content",
        "/admin/content-progress/content/{lesson_id}",
        "/admin/content-progress/records",
    }
    assert expected_paths.issubset(paths)


def test_cors_allows_deployed_frontend(client):
    for origin in (
        "https://verba-spokenenglishassistant.onrender.com",
        "https://verba-frontend.onrender.com",
        "http://localhost:3000",
    ):
        res = client.options(
            "/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == origin
        assert res.headers.get("access-control-allow-credentials") == "true"
        allow_headers = (res.headers.get("access-control-allow-headers") or "").lower()
        assert "authorization" in allow_headers
        assert "content-type" in allow_headers


def test_database_url_normalizes_postgres_scheme():
    assert (
        _normalize_database_url("postgres://user:pass@host:5432/verba")
        == "postgresql://user:pass@host:5432/verba"
    )
    assert (
        _normalize_database_url("postgresql://user:pass@host:5432/verba")
        == "postgresql://user:pass@host:5432/verba"
    )
