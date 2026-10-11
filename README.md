# 🚦 TrueTraffic AI
**Live demo:** https://truetraffic-ai-1.onrender.com

**AI-assisted road-safety and route intelligence for Visakhapatnam.**
Instead of one "fastest" answer, TrueTraffic AI gives three options - **Fastest, Safest and Recommended** - scored with a live risk model built from 50,000 historical accident records, and explains the trade-off in plain language.

Built for the APAC GenAI Hackathon 2026 (Urban Mobility track), then reworked into a deployable app.

## Features

- **Real-road routing** - candidate routes follow actual streets (OSRM) from the exact start to the exact destination; each is scored against the live hotspot risk model, then Fastest / Safest / Recommended (60/40 safety/speed) are chosen. All three are drawn on the map. If OSRM is unreachable the app falls back to the old built-in road model (`backend/route_engine.py`).
- **Live risk model** - base road risk + time of day, road-specific peak hours, weather, weekend, and admin events (`backend/risk_model.py`).
- **AI explanations** - Gemini first, Groq second, and a rule-based fallback, so the app never breaks if an API key is missing or rate-limited.
- **Accident dashboard** - hourly trend, weather, severity, most dangerous roads (cached pandas aggregation, optional BigQuery).
- **Human-in-the-loop admin panel** - authenticated admins can fully close a road (routes avoid it) or raise its risk (VIP movement, road work, floods...).

## Architecture

```
Browser (static HTML/JS, Leaflet, Chart.js)
        │  same-origin /api/*
Flask app (gunicorn)
 ├─ risk_model.py    live risk scoring + event windows
 ├─ route_engine.py  road graph + Dijkstra (3 cost functions)
 ├─ ai_agent.py      Gemini → Groq → rule-based explanation
 ├─ bigquery_client.py  optional (USE_BIGQUERY=true)
 └─ data/            roads, accidents CSV, admin events
```

Flask serves both the API and the frontend, so there is **one service to deploy** and no CORS or URL configuration.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # then edit: ADMIN_PASSWORD, and optionally GEMINI_API_KEY / GROQ_API_KEY
python -m backend.app       # http://localhost:5000
pytest -q                   # run the tests
```

## Deploy (Render, free tier)

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo (it reads `render.yaml`).
3. When prompted, set `ADMIN_PASSWORD` and (optionally) `GEMINI_API_KEY` / `GROQ_API_KEY`.
4. Deploy. Health check: `/api/health`.

Any platform that runs `gunicorn backend.app:app` works (Railway, Fly.io, Heroku via `Procfile`).

> **Note:** admin-created events are stored in `data/special_events.json`; on free hosts with an ephemeral disk they reset on redeploy. Fine for a demo; use a database for production.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/roads` | road network + locations |
| GET | `/api/risk?hour=&weather=&day=` | live risk per road |
| POST | `/api/routes` | `{origin, destination, hour, weather, day}` → 3 routes + AI text |
| GET | `/api/stats` | dashboard statistics |
| GET | `/api/events` | active admin events |
| POST | `/api/admin/login` | `{password}` → bearer token |
| POST/DELETE | `/api/events`, `/api/events/<i>`, `/api/events/clear` | admin only |

## Limitations (honest notes)

- The road network is a small hand-curated set of 10 roads, so routes are a model, not turn-by-turn navigation. To go further, swap the graph builder for OpenStreetMap / Google Routes data.
- The accident data is synthetic (`benchmark/generate_data.py`). Replace it with real records before drawing real-world conclusions.
- The RAPIDS/cuDF benchmark only runs on an NVIDIA GPU; no GPU numbers are claimed here.

## Security

Never commit `.env`. API keys and the admin password come from environment variables only.

## Location data
`data/vizag_roads.json` coordinates were corrected from Wikipedia infobox / OpenStreetMap-derived
sources (RK Beach 17.7142, 83.3236; NAD Junction 17.7424, 83.2366; MVP Colony 17.7414, 83.3387;
Hanumanthawaka 17.7602, 83.3258; Kommadi 17.8428, 83.3215; ...). "NH16 Bypass" and the hotspot
polylines are approximate. To change a place, edit its `lat`/`lng` (right-click the spot in Google
Maps to copy them). `tests/test_road_router.py::test_real_data_coordinates_are_sane` guards against
bad values.
