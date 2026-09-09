# Skybrush API Mock Server

This is a FastAPI mock server implementing the APIs used by the Skybrush Studio Blender plugin. It provides stubbed responses for both the **Skybrush Studio Server API** and the **Skybrush Gateway API**.

## Setup

1. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the server using `uvicorn`:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

## Implemented APIs

### Skybrush Studio Server APIs
- `GET /queries/version`
- `GET /queries/limits`
- `POST /operations/decompose`
- `POST /operations/match-points`
- `POST /operations/plan-transition`
- `POST /operations/plan-takeoff`
- `POST /operations/plan-landing`
- `POST /operations/plan-smart-rth`
- `POST /operations/create-static-formation`
- `POST /operations/render`
- `POST /operations/multi-render`

### Skybrush Gateway APIs
- `GET /hwid`
- `POST /sign`
- `POST /task`
- `POST /task/{task_id}`
- `DELETE /task/{task_id}`

These mock endpoints are designed to return minimal valid JSON and octet-stream responses matching the schema required by the plugin's `api.py`, `studio.py`, `gateway.py`, and `types.py` files.
