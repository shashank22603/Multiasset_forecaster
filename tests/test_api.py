from fastapi.testclient import TestClient

from timesfm_lab.api import create_app


def test_dashboard_api_exposes_coverage_and_unknowns(store):
    with TestClient(create_app(store)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        overview = client.get("/api/overview").json()
        assert overview["registered"] == 6
        assert overview["promotion_allowed"] is False
        series = client.get("/api/series", params={"asset_id": "COM:GOLD", "limit": 3}).json()
        assert len(series["bars"]) == 3
        assert series["bars"][0]["volume"] is None
        assert client.get("/api/series", params={"asset_id": "UNKNOWN"}).status_code == 404
        assert client.get("/api/runs/not-found").status_code == 404


def test_jobs_reject_invalid_and_cross_origin_requests(store):
    with TestClient(create_app(store)) as client:
        assert client.post("/api/jobs", json={"horizon": 64, "context": 32}).status_code == 422
        assert client.post("/api/jobs", json={"model": "naive"}).status_code == 422
        assert client.post("/api/jobs", headers={"Origin": "https://unrelated.example"}, json={}).status_code == 403


def test_stale_forecast_job_surfaces_failure(store):
    import time
    with TestClient(create_app(store)) as client:
        response = client.post("/api/jobs", json={"model": "naive", "experiment": "stock_only"})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "ERROR":
                break
            time.sleep(.02)
        assert job["status"] == "ERROR"
        assert "stale" in job["error"]
