# Awning Protector — Project Guidelines

## Purpose
Local web dashboard and automation engine for managing an outdoor awning based on live weather data.
Consumes the `tempest_weather` service (port 8766), `tahoma_awning` service (port 8765), and the
`uv_sensor` service (port 8768, a living-room glare sensor) for AI evaluations.

## Stack
- Python 3.9, FastAPI, uvicorn, httpx, pydantic
- Dependency management: `pipenv`
- Frontend: vanilla HTML/CSS/JS (served as static files by FastAPI)

## Architecture

```
app/
  api.py         # FastAPI app, all routes, SSE fan-out
  weather.py     # WeatherClient — SSE subscriber + OpenWeatherMap forecast + staleness tracking
  uv_sensor.py   # UVSensorClient — SSE subscriber for the living-room glare sensor + staleness tracking
  awning.py      # AwningClient — HTTP calls to tahoma_awning service
  automation.py  # AutomationEngine — background rule evaluator (10s loop)
  ai_agent.py    # AIEngine — event-driven scheduling loop; _Claude wrapper; prompt load/save
  ai_pipeline.py # Worker/coordinator/orchestrator multi-agent pipeline (run_ai_pipeline)
  ai_tools.py    # Tool implementations for the AI agent (weather, awning, logging)
  config.py      # Settings load/save (data/config.json); includes AIConfig
  log_store.py   # In-memory automation and weather log ring buffers
prompts/
  wind_worker.md.j2       # Wind risk assessment worker (Jinja2)
  rain_worker.md.j2       # Rain risk assessment worker (Jinja2)
  forecast_worker.md.j2   # Forecast risk assessment worker (Jinja2)
  solar_worker.md.j2      # Solar/deploy-benefit assessment worker (Jinja2)
  glare_worker.md.j2      # Living-room glare assessment worker (Jinja2)
  coordinator.md.j2       # Synthesizes worker assessments into a brief (Jinja2)
  orchestrator.md.j2      # Final deploy/retract decision + next-eval timing (Jinja2)
static/
  index.html           # Dashboard
  style.css            # Dashboard styles
  logs.css             # Shared log page styles
  app.js               # SSE consumer, wind compass SVG, config UI, AI status panel
  automation_log.html  # Filterable/sortable automation log
  weather_log.html     # Filterable/sortable weather log
  kiosk.html            # Touch kiosk dashboard, fixed 720x1280 (Pi Touch Display 2)
  kiosk.css             # Kiosk styles; drops decorative padding at exact 720x1280 viewport
  kiosk.js              # Kiosk state/rendering, swipeable screens, QR code linking to LAN hostname
watchdog.py      # Standalone app-failure watchdog (polls /health, retracts on timeout)
main.py          # uvicorn entry point (port 8767)
deploy/
  awning-protector.service  # systemd unit for production Pi deployment
  awning-watchdog.service   # systemd unit for the watchdog, runs alongside the main service
scripts/
  cicd_update.py            # CI/CD polling script (stdlib only)
  run_cicd.sh                # executable wrapper; sets ENVIRONMENT=production
  run_cicd_boot.sh           # @reboot wrapper; bypasses CICD_INTERVAL_MINUTES
```

## API Endpoints

| Method | Path | Action |
|--------|------|--------|
| GET | `/health` | Health check |
| GET | `/` | Web dashboard |
| GET | `/kiosk` | Touch kiosk dashboard (720×1280, Pi Touch Display 2) |
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
| GET | `/ai/status` | AI engine state (last eval, next eval, report text) |
| GET | `/ai/prompts/{name}` | Retrieve prompt template (`wind`, `rain`, `forecast`, `solar`, `coordinator`, `orchestrator`) |
| PUT | `/ai/prompts/{name}` | Update and persist prompt template |
| POST | `/ai/evaluate` | Trigger an immediate AI evaluation |

## Automation Rules (priority order)
1. Weather data stale > 120s → undeploy (resumes when data returns)
2. Rain detected (`precip_type != 0` or `rain_prev_min_mm > 0`) → undeploy
3. Wind avg > `max_wind_mph` → undeploy
4. **AI mode** (`ai.ai_enabled = true`) → all deployment decisions delegated to `AIEngine`; rules 1–3 still run as safety checks

There is no rule-based deploy path — deploy decisions rely entirely on AI deploy mode (rule 4).
With AI mode disabled, the engine only ever retracts (rules 1–3); it never deploys on its own.

## AI Agent (`AIEngine` + multi-agent pipeline)

`app/ai_agent.py` owns scheduling: the `_Claude` wrapper, prompt template load/save, and the
`AIEngine` evaluation loop. `app/ai_pipeline.py` (`run_ai_pipeline`) implements the actual
decision-making as a worker → coordinator → orchestrator pipeline (ported from
`notebooks/multi_agent_sandbox.ipynb`):

1. **Workers** (`run_wind_worker`, `run_rain_worker`, `run_forecast_worker`, `run_solar_worker`,
   `run_glare_worker`) — small, tool-free Claude calls that each return a compact JSON assessment
   from pre-formatted data. Wind/rain/forecast return `{"risk": ..., "reasoning": ...}`; solar and
   glare are deploy-benefit workers with a stubbed `"risk"` field (not a hazard signal).
2. **Fast path** — if the wind or rain worker reports `risk` of `moderate`/`high`, the forecast,
   solar, and glare workers and the coordinator call are skipped entirely; a `MUST_RETRACT` brief is
   synthesized directly in code and handed to the orchestrator. This trims token usage when an
   immediate retract is obviously correct.
3. **Coordinator** (`run_coordinator`) — one Claude call that synthesizes the five worker
   assessments (skipped on the fast path) into a concise plain-text brief.
4. **Orchestrator** (`run_orchestrator`) — the only pipeline stage with tool access
   (`action_tool_schemas` / `execute_action_tool`: deploy/retract/log/status). It reads the brief,
   takes action, and emits a final `ORCHESTRATOR REPORT` that also states
   `Next Eval In: <seconds>` — folding next-eval timing into this single call rather than running
   a separate timing agent. When conditions are exceptionally safe (see below), a sixth
   `deploy_for_glare` tool is added to this run's tool list.

`AIEngine` scheduling behavior:
- On startup (when `ai_enabled = true`), runs immediately.
- After each evaluation, sleeps for exactly `next_eval_seconds` — parsed from the orchestrator's
  `Next Eval In:` line via `_parse_next_eval` (regex) — using `asyncio.Event`-driven sleep, no CPU polling.
- `POST /ai/evaluate` calls `trigger_immediate()` which sets the event to wake the sleep early. `trigger_immediate()` is a no-op when `ai_enabled = false`.
- `PUT /config` calls `notify_config_changed()` which wakes the loop immediately so it re-checks `ai_enabled` — disabling AI takes effect within seconds rather than waiting for the current sleep interval to expire.
- If an evaluation is in-flight when AI is disabled, the result is discarded and no next evaluation is scheduled.
- `AIEngine.run()` gathers `_eval_loop()` (the scheduling loop above) with `_glare_guard()`, a
  real-time task that reacts to `uv_sensor_client`'s SSE stream and calls `trigger_immediate()` on
  the rising edge of illuminance crossing `glare_lux_threshold` — so a glare event is evaluated
  promptly instead of waiting out the scheduled sleep (mirrors `AutomationEngine._wind_guard()`).

### Glare worker and extended deployment (living-room UV/illuminance sensor)
- `app/uv_sensor.py` (`UVSensorClient`) subscribes to the `uv_sensor` service's SSE stream
  (`UV_SENSOR_URL`, default `http://uvsensor.local:8768`) the same way `WeatherClient` does for
  weather; a stale/unreachable reading (> 30s, `GLARE_STALE_SECONDS`) causes the glare worker to be
  skipped for that evaluation — the rest of the pipeline is unaffected.
- The glare worker only reports whether illuminance is above `AIConfig.glare_lux_threshold`
  (default 2000 lux) — it does not decide whether extended deployment is safe.
- `deploy_for_glare` (`app/ai_tools.py`) — a tool that incrementally extends the awning, polling
  `/uv/latest` after each step, stopping once the reading drops below the threshold or
  `AIConfig.max_extended_deployment_seconds` (default 15s) is reached; that cap is enforced
  server-side regardless of what value the LLM passes.
- This tool is only added to the orchestrator's tool list for a given run (`app/ai_pipeline.py`,
  `glare_eligible`) when: not on the fast path, the glare sensor isn't stale, `glare_detected` is
  true, wind risk == `none`, rain risk == `none`, **and** the presence gate passes — the LLM cannot
  invoke it otherwise. Presence gate: `home == true`, or (`home == false` and `risk_tolerance == 5`),
  from the structured `home`/`risk_tolerance` fields on `UserGuidance` (`app/config.py`,
  `get_active_presence()`) — set from the same kiosk guidance screen as the existing free-text
  guidance (`static/kiosk.js`). Defaults are the conservative end (`home=False`,
  `risk_tolerance=1`) so the gate stays closed until guidance is explicitly set.
- `_next_eval_at` is cleared when AI is disabled; re-enabling triggers an immediate evaluation.
- All six prompt templates live in `prompts/` and can be overridden at runtime via `PUT /ai/prompts/{name}` (rendered with `_build_system_blocks`, a generic Jinja2 + prompt-cache system-block builder shared by every stage).
- Requires `ANTHROPIC_API_KEY` in `.env`; model defaults to `claude-haiku-4-5` (override with `CLAUDE_MODEL`).

### Structured error reporting (`app/error_report.py`)
Failures in the pipeline (`app/ai_pipeline.py`) and action tools (`app/ai_tools.py`) emit a
single-line JSON error report via `emit_error_report` to the logger — `{error_code, message,
task_id, agent_id, input_snapshot, retry_eligible, suggested_action, occurred_at}` (shape per the
global error-reporting rule; `input_snapshot` is sanitized: sensitive keys redacted, long strings
truncated, binary omitted). A per-run `task_id` (uuid) is threaded through every stage; `agent_id`
is the stage/tool name (`wind-worker`, `coordinator`, `orchestrator`, `tool:<name>`). Reported
cases: worker Claude-API failures (`DEPENDENCY_UNAVAILABLE`, re-raised), worker JSON parse /
missing-`risk` assessments (`PARSE_ERROR`/`VALIDATION_FAILED`, previously silent), coordinator/
orchestrator API failures, orchestrator tool failures (fed back to the model as an `ERROR:` tool
result instead of aborting the run), and the orchestrator tool loop exceeding `MAX_TOOL_ITERATIONS`
(`MAX_RETRIES_EXCEEDED`). `deploy_awning`/`retract_awning` now `raise_for_status()` instead of
reporting success on a non-OK awning-service response.

## Fail-Safe Mechanisms

### Weather data timeout (in-app)
`WeatherClient` tracks `_last_obs_at` on every `obs_st` event. `AutomationEngine._evaluate()` reads `seconds_since_last_obs` each cycle. If > `WEATHER_TIMEOUT_S` (120s), it issues undeploy and logs `weather_timeout`. Normal evaluation resumes automatically once data flows again.

### Real-time wind guard (in-app)
`AutomationEngine._wind_guard()` runs concurrently with the 10-second polling loop via `asyncio.gather()`. It blocks on `WeatherClient.wait_for_wind_data()`, which is signalled on every `obs_st` and `rapid_wind` SSE message (~3 s cadence). When wind exceeds `max_wind_mph` and the awning is not already retracted, it calls `awning_client.undeploy()` immediately — no polling delay, no AI evaluation cycle wait. The 10-second loop still owns all logging and state bookkeeping.

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

### Native (Raspberry Pi production)
Runs as two systemd services (`awning-protector`, `awning-watchdog`) with auto-deploy on new
commits via `scripts/cicd_update.py`, and boots straight into Chromium kiosk mode showing
`/kiosk` on the Touch Display 2 (`deploy/kiosk-autostart`, appended to `~/.config/labwc/autostart`).
See [README-PI.md](README-PI.md) for full setup and the
[CI/CD](#cicd-raspberry-pi-production-only) section below for the auto-deploy mechanism.

## Testing
```bash
pipenv run pytest tests/
```

## Environment Variables (.env)

```
WEATHER_URL=http://host.docker.internal:8766
AWNING_URL=http://host.docker.internal:8765
UV_SENSOR_URL=http://uvsensor.local:8768   # living-room glare sensor for AI evaluations
APP_PORT=8767
APP_URL=http://localhost:8767        # watchdog uses this; overridden in docker-compose
ANTHROPIC_API_KEY=                   # required for AI deploy mode
CLAUDE_MODEL=claude-haiku-4-5        # optional — override Claude model for AI evaluations
CICD_DEPLOY_MODE=systemd             # Pi production only — systemd | docker
CICD_GIT_BRANCH=main                 # Pi production only — branch to poll
CICD_INTERVAL_MINUTES=15             # Pi production only — polling interval for CI/CD script
CICD_SERVICE_NAME=awning-protector   # Pi production only — service restarted on deploy
CICD_KIOSK_URL=http://localhost:8767/kiosk   # Pi production only — hard-reloads this kiosk tab (CDP) after each deploy; unset skips it
CICD_KIOSK_DEBUG_PORT=9222                   # Pi production only — must match --remote-debugging-port in deploy/kiosk-autostart
```

> On macOS, `host.docker.internal` resolves automatically.
> On Linux, `extra_hosts: ["host.docker.internal:host-gateway"]` in docker-compose.yml handles this.
> In Docker, `APP_URL` is set to `http://awning-protector:8767` by the compose file so the watchdog reaches the main container by service name.

## CI/CD (Raspberry Pi production only)

`scripts/cicd_update.py` polls GitHub for new commits on `CICD_GIT_BRANCH` and automatically
deploys. It only runs when `ENVIRONMENT=production` is set — safe to run accidentally in dev.

**Sudoers prerequisite** — the cron job restarts the service non-interactively, so this entry is
required in `/etc/sudoers.d/awning-protector`:
```
mcdomx ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart awning-protector
```

**Cron entries (Pi, as `mcdomx`):**
```
* * * * * ENVIRONMENT=production /usr/bin/python3 /home/mcdomx/awning_protector/scripts/cicd_update.py
@reboot /home/mcdomx/awning_protector/scripts/run_cicd_boot.sh
```

**Manual trigger:**
```bash
./scripts/run_cicd.sh
```

**Pause / resume without editing cron:**
```bash
touch .cicd_disabled   # pause
rm .cicd_disabled      # resume
```

**Logs:** `logs/cicd.log`

**Key behaviour:**
- Cron fires every minute; the script gates on `CICD_INTERVAL_MINUTES` via `logs/.last_run` — most fires are silent no-ops
- `@reboot` bypasses the interval gate so commits that landed while the Pi was off deploy immediately on next boot
- On new commits: `git pull` → `pipenv install` → `systemctl restart awning-protector` → kiosk reload (if `CICD_KIOSK_URL` set)
- Only restarts `awning-protector`; if a deploy changes `watchdog.py`, restart `awning-watchdog` manually
- Full setup steps: see [README-PI.md](README-PI.md)

**Kiosk reload** — `systemctl restart` reloads the Python backend, but the kiosk's Chromium tab
(launched once at boot, see [Kiosk Boot Mode](README-PI.md#9-kiosk-boot-mode)) never re-navigates
on its own; a deploy that only changes static files (`kiosk.css`/`kiosk.js`) would otherwise sit
stale until the next reboot even though the backend already restarted with the new commit. When
`CICD_KIOSK_URL` is set, `reload_kiosk_if_configured()` (`scripts/cicd_update.py`) hard-reloads
that tab over the Chrome DevTools Protocol debug port opened by `deploy/kiosk-autostart`
(`--remote-debugging-port`, bound to `127.0.0.1`). Failure to reach the debug port (e.g. kiosk not
running) only logs a warning — it never fails the deploy.
