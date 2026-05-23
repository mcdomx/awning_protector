# Project Plan

## Sprint 1 — Foundation (complete)
- [x] Project structure and directory layout
- [x] `app/config.py` — settings model, load/save
- [x] `app/weather.py` — SSE subscriber + forecast client
- [x] `app/awning.py` — HTTP client with state tracking
- [x] `app/automation.py` — rule engine background task
- [x] `app/api.py` — FastAPI routes and SSE fan-out
- [x] `main.py` — uvicorn entry point
- [x] `static/` — dashboard (compass, rain panel, config UI)
- [x] `Pipfile`, `Dockerfile`, `docker-compose.yml`
- [x] Unit tests for config, automation rules, awning client

## Sprint 2 — Enhancements (future)
- [ ] `/awning/my` endpoint for partial preset position
- [ ] Historical log of automation actions (SQLite or CSV)
- [ ] Wind alert sound/notification
- [ ] Humidity and temperature display
- [ ] Dark/light theme toggle
