# Support agent HTTP service. Contract: deploy/agent/agent.yaml (port 8000, GET /healthz).
from fastapi import FastAPI

app = FastAPI(title="FT2 Support Agent")


# Readiness probe target
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
