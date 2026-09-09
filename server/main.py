import gzip
import json
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class MatchPointsRequest(BaseModel):
    source: list[tuple[float, float, float]]
    target: list[tuple[float, float, float]]
    radius: float

app = FastAPI(title="Skybrush API Mock Server")

# ==========================================
# 1. Skybrush Studio Server APIs
# ==========================================

@app.get("/queries/version")
def query_version():
    """Mock for retrieving the current version of the server backend."""
    return {"version": "2.40.0"}

@app.get("/queries/limits")
def query_limits():
    """Mock for retrieving server capabilities and limits."""
    return {
        "num_drones": 10000,
        "features": ["smart_rth", "advanced_matching"]
    }

@app.post("/operations/decompose")
def decompose(req: dict):
    """Mock for decomposing a set of points into multiple groups."""
    points = req.get("points", [])
    return {
        "version": 1,
        "groups": [list(range(len(points)))]
    }

@app.post("/operations/match-points")
async def match_points(req: Request):
    """Mock for calculating optimal collision-free matchings."""
    print("Content-Encoding:", req.headers.get("Content-Encoding"))
    
    if req.headers.get("Content-Encoding") == "gzip":
        body = await req.body()
        data = json.loads(gzip.decompress(body).decode("utf-8"))
    else:
        data = await req.json()
        
    source = data.get("source", [])
    target = data.get("target", [])
    radius = data.get("radius", 0.0)
    
    # Dummy mapping: straight 1-to-1 matching
    mapping = [i if i < len(source) else None for i in range(len(target))]
    return {
        "version": 1,
        "mapping": mapping,
        "clearance": 2.0
    }

@app.post("/operations/plan-transition")
def plan_transition(req: dict):
    """Mock for proposing optimal transition durations and velocities."""
    source = req.get("source", [])
    target = req.get("target", [])
    n = len(target)
    
    # Return dummy data with simple 5-second transitions
    return {
        "version": 1,
        "start_times": [0.0] * n,
        "durations": [5.0] * n,
        "mapping": [i if i < len(source) else None for i in range(n)],
        "clearance": 2.0
    }

@app.post("/operations/plan-takeoff")
def plan_takeoff(req: dict):
    """Mock for planning layered takeoff schedules."""
    points = req.get("points", [])
    return {
        "version": 1,
        "groups": [list(range(len(points)))]
    }

@app.post("/operations/plan-landing")
def plan_landing(req: dict):
    """Mock for calculating landing times and durations."""
    points = req.get("points", [])
    n = len(points)
    return {
        "version": 1,
        "start_times": [0.0] * n,
        "durations": [5.0] * n
    }

@app.post("/operations/plan-smart-rth")
def plan_smart_rth(req: dict):
    """Mock for planning safe Smart Return-to-Home routes."""
    target = req.get("target", [])
    n = len(target)
    return {
        "version": 1,
        "start_times": [0.0] * n,
        "durations": [10.0] * n,
        "inner_points": [[] for _ in range(n)]
    }

@app.post("/operations/create-static-formation")
def create_static_formation(req: dict):
    """Mock for generating 3D points and colors by sampling 2D SVG paths."""
    params = req.get("parameters", {})
    n = params.get("n", 10)
    return {
        "version": 1,
        "points": [[0.0, 0.0, 0.0] for _ in range(n)],
        "colors": [[255, 255, 255] for _ in range(n)]
    }

@app.post("/operations/render")
def render(req: dict):
    """Mock for single-format export (e.g. .skyc, .csv)."""
    # Returns empty dummy bytes
    return Response(content=b"{}", media_type="application/octet-stream")

@app.post("/operations/multi-render")
def multi_render(req: dict):
    """Mock for multi-format export."""
    return Response(content=b"{}", media_type="application/octet-stream")


# ==========================================
# 2. Skybrush Gateway APIs
# ==========================================

@app.get("/hwid")
def get_hwid():
    """Mock for retrieving the hardware ID of the local machine."""
    return Response(content=b"dummy-hwid-12345", media_type="text/plain")

@app.post("/sign")
async def sign_request(request: Request):
    """Mock for cryptographically signing request payloads."""
    # Read body to consume it, though we don't need to process it
    _body = await request.body()
    return Response(content=b"dummy-signature", media_type="text/plain")

@app.post("/task")
def create_task(req: dict):
    """Mock for creating a new background task."""
    # Returns 201 Created with a Location header for task updates
    headers = {"Location": "/task/dummy-task-id"}
    return JSONResponse(status_code=201, content={"status": "created"}, headers=headers)

@app.post("/task/{task_id}")
def update_task(task_id: str, req: dict):
    """Mock for handling task progress updates."""
    # Return false to indicate the task has not been cancelled
    return JSONResponse(content=False)

@app.delete("/task/{task_id}")
def delete_task(task_id: str):
    """Mock for deleting/canceling a background task."""
    return JSONResponse(content={"status": "deleted"})
