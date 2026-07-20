# Awning Protector

Local web dashboard and automation engine for managing an outdoor awning based on live Tempest weather data.

## Quick Start

```bash
# 1. Copy and fill in credentials
cp .env.example .env   # or edit .env directly

# 2. Run natively (macOS dev)
pipenv install
pipenv run python main.py

# Optionally run the watchdog in a second terminal
pipenv run python watchdog.py

# 3. Or run in Docker (includes watchdog automatically)
docker compose build
docker compose up
```

Open **http://localhost:8767** in a browser.

## Features

- **Live wind compass** — SVG direction arrow updated every 3 seconds via SSE
- **Rain status** — real-time from Tempest sensor (precip type + mm/min)
- **Rain forecast** — hourly rain probability bars from OpenWeatherMap (requires API key)
- **Automation rules** — retract on rain, retract on high wind; deploy decisions are delegated to AI deploy mode
- **Real-time wind guard** — event-driven coroutine reacts to every `rapid_wind` SSE message (~3 s cadence), retracting immediately when the threshold is exceeded without waiting for the 10-second polling cycle or an AI evaluation
- **AI deploy mode** — a multi-agent Claude pipeline (wind/rain/forecast/solar workers → coordinator → orchestrator) evaluates weather conditions and decides when to deploy; runs at startup and at orchestrator-suggested intervals to minimise token usage; editable prompts via the dashboard
- **Weather data watchdog** — retracts awning if weather station goes silent for 2 minutes
- **App failure watchdog** — separate process retracts awning directly if this app crashes
- **Manual controls** — Deploy / Stop / Retract buttons with temporary override
- **Configurable thresholds** — all rule parameters editable in the UI and persisted to disk
- **Automation & weather logs** — filterable, sortable log pages at `/logs/automation-page` and `/logs/weather-page`

## Dependencies

Requires two sibling services running on the same host:
- `tempest_weather` on port 8766
- `tahoma_awning` on port 8765

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEATHER_URL` | `http://host.docker.internal:8766` | Tempest weather service URL |
| `AWNING_URL` | `http://host.docker.internal:8765` | TaHoma awning service URL |
| `UV_SENSOR_URL` | `http://uvsensor.local:8768` | Living-room UV/illuminance sensor service URL (glare worker) |
| `APP_PORT` | `8767` | Host port for this service |
| `APP_URL` | `http://localhost:8767` | Used by watchdog to reach this app |
| `OPENWEATHER_API_KEY` | _(empty)_ | OpenWeatherMap API key for forecast |
| `LATITUDE` | _(empty)_ | Your latitude (required for forecast) |
| `LONGITUDE` | _(empty)_ | Your longitude (required for forecast) |
| `ANTHROPIC_API_KEY` | _(empty)_ | Required to enable AI deploy mode |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Claude model used for AI evaluations |

## Fail-Safe Behavior

Two independent mechanisms protect the awning when something goes wrong:

1. **Weather data timeout** — if the weather station stops sending observations for 120 seconds, the automation engine retracts the awning. Normal automation resumes automatically once data flows again.

2. **App watchdog** (`watchdog.py`) — a separate process polls `/health` every 30 seconds. If the main app is unreachable for 120 seconds, the watchdog calls the `tahoma_awning` service directly to retract. When the app recovers, the watchdog logs the downtime and the automation engine resumes from its next evaluation cycle.

In Docker, `awning-watchdog` runs as its own service and restarts independently of the main app.

## Testing

```bash
pipenv run pytest tests/
```
