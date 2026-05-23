# Awning Protector — Project Guidelines

## Purpose
Local web dashboard and automation engine for managing an outdoor awning based on live weather data.
Consumes the `tempest_weather` service (port 8766) and `tahoma_awning` service (port 8765).

## Stack
- Python 3.9, FastAPI, uvicorn, httpx, pydantic
- Dependency management: `pipenv`
- Frontend: vanilla HTML/CSS/JS (served as static files by FastAPI)

## Architecture

```
app/
  api.py         # FastAPI app, all routes, SSE fan-out
  weather.py     # WeatherClient — SSE subscriber + OpenWeatherMap forecast + staleness tracking
  awning.py      # AwningClient — HTTP calls to tahoma_awning service
  automation.py  # AutomationEngine — background rule evaluator (10s loop)
  config.py      # Settings load/save (data/config.json)
  log_store.py   # In-memory automation and weather log ring buffers
static/
  index.html           # Dashboard
  style.css            # Dashboard styles
  logs.css             # Shared log page styles
  app.js               # SSE consumer, wind compass SVG, config UI
  automation_log.html  # Filterable/sortable automation log
  weather_log.html     # Filterable/sortable weather log
watchdog.py      # Standalone app-failure watchdog (polls /health, retracts on timeout)
main.py          # uvicorn entry point (port 8767)
```

## API Endpoints

| Method | Path | Action |
|--------|------|--------|
| GET | `/health` | Health check |
| GET | `/` | Web dashboard |
| GET | `/weather/current` | Latest obs + rapid_wind + forecast snapshot |
| GET | `/weather/stream` | SSE proxy to browsers |
| GET | `/awning/status` | State + automation status |
| POST | `/awning/deploy` | Manual deploy + set override |
| POST | `/awning/undeploy` | Manual undeploy + set override |
| POST | `/awning/stop` | Manual stop + set override |
| GET | `/config` | Current automation config |
| PUT | `/config` | Update and persist config |
| GET | `/logs/automation` | Automation log entries (JSON) |
| GET | `/logs/weather` | Weather log entries (JSON) |
| GET | `/logs/automation-page` | Automation log UI |
| GET | `/logs/weather-page` | Weather log UI |

## Automation Rules (priority order)
1. Weather data stale > 120s → undeploy (resumes when data returns)
2. Rain detected (`precip_type != 0` or `rain_prev_min_mm > 0`) → undeploy
3. Wind avg > `max_wind_mph` → undeploy
4. Sunny (`illuminance_lux > sunny_lux_threshold`) + calm (`wind_avg < sunny_wind_max_mph`) + warm + no rain → deploy for `deploy_duration_s` seconds, then stop

## Fail-Safe Mechanisms

### Weather data timeout (in-app)
`WeatherClient` tracks `_last_obs_at` on every `obs_st` event. `AutomationEngine._evaluate()` reads `seconds_since_last_obs` each cycle. If > `WEATHER_TIMEOUT_S` (120s), it issues undeploy and logs `weather_timeout`. Normal evaluation resumes automatically once data flows again.

### App watchdog (external process)
`watchdog.py` polls `APP_URL/health` every 30s. If unreachable for >= `FAILURE_TIMEOUT_S` (120s), it calls `AWNING_URL/awning/undeploy` directly, bypassing the main app. On recovery it resets and logs the downtime. In Docker it runs as the `awning-watchdog` service.

## Running

### Native (macOS dev)
```bash
pipenv install
pipenv run python main.py
# in a second terminal:
pipenv run python watchdog.py
```

### Docker
```bash
docker compose build
docker compose up
```

Dashboard: http://localhost:8767

## Testing
```bash
pipenv run pytest tests/
```

## Environment Variables (.env)

```
WEATHER_URL=http://host.docker.internal:8766
AWNING_URL=http://host.docker.internal:8765
APP_PORT=8767
APP_URL=http://localhost:8767        # watchdog uses this; overridden in docker-compose
OPENWEATHER_API_KEY=                 # optional — for rain forecast %
LATITUDE=
LONGITUDE=
```

> On macOS, `host.docker.internal` resolves automatically.
> On Linux, `extra_hosts: ["host.docker.internal:host-gateway"]` in docker-compose.yml handles this.
> In Docker, `APP_URL` is set to `http://awning-protector:8767` by the compose file so the watchdog reaches the main container by service name.
