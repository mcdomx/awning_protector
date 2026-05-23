# Awning Protector

Local web dashboard and automation engine for managing an outdoor awning based on live Tempest weather data.

## Quick Start

```bash
# 1. Copy and fill in credentials
cp .env.example .env   # or edit .env directly

# 2. Run natively (macOS dev)
pipenv install
pipenv run python main.py

# 3. Or run in Docker
docker compose build
docker compose up
```

Open **http://localhost:8767** in a browser.

## Features

- **Live wind compass** — SVG direction arrow updated every 3 seconds via SSE
- **Rain status** — real-time from Tempest sensor (precip type + mm/min)
- **Rain forecast** — hourly rain probability bars from OpenWeatherMap (requires API key)
- **Automation rules** — retract on rain, retract on high wind, deploy when sunny & calm
- **Manual controls** — Deploy / Stop / Retract buttons with temporary override
- **Configurable thresholds** — all rule parameters editable in the UI and persisted to disk

## Dependencies

Requires two sibling services running on the same host:
- `tempest_weather` on port 8766
- `tahoma_awning` on port 8765

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEATHER_URL` | `http://host.docker.internal:8766` | Tempest weather service URL |
| `AWNING_URL` | `http://host.docker.internal:8765` | TaHoma awning service URL |
| `APP_PORT` | `8767` | Host port for this service |
| `OPENWEATHER_API_KEY` | _(empty)_ | OpenWeatherMap API key for forecast |
| `LATITUDE` | _(empty)_ | Your latitude (required for forecast) |
| `LONGITUDE` | _(empty)_ | Your longitude (required for forecast) |

## Testing

```bash
pipenv run pytest tests/
```
