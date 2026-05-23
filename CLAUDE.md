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
  weather.py     # WeatherClient — SSE subscriber + OpenWeatherMap forecast
  awning.py      # AwningClient — HTTP calls to tahoma_awning service
  automation.py  # AutomationEngine — background rule evaluator (10s loop)
  config.py      # Settings load/save (data/config.json)
static/
  index.html     # Dashboard
  style.css
  app.js         # SSE consumer, wind compass SVG, config UI
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

## Automation Rules (priority order)
1. Rain detected (`precip_type != 0` or `rain_prev_min_mm > 0`) → undeploy
2. Wind avg > `max_wind_mph` → undeploy
3. Sunny (`illuminance_lux > sunny_lux_threshold`) + calm (`wind_avg < sunny_wind_max_mph`) + no rain → deploy for `deploy_duration_s` seconds, then stop

## Running

### Native (macOS dev)
```bash
pipenv install
pipenv run python main.py
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
OPENWEATHER_API_KEY=   # optional — for rain forecast %
LATITUDE=
LONGITUDE=
```

> On macOS, `host.docker.internal` resolves automatically.
> On Linux, `extra_hosts: ["host.docker.internal:host-gateway"]` in docker-compose.yml handles this.
