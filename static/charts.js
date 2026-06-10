// Charts page — real-time weather trend chart with automation markers + forecast tail.
// Self-contained: does not share app.js (which is bound to the dashboard DOM).

const MPH = 2.23694;
const FORECAST_HORIZON_MS = 4 * 3600 * 1000; // forecast dotted tail = next 4 hours
const MAX_POINTS = 3000;                      // per-series in-memory cap
const FORECAST_REFRESH_MS = 5 * 60 * 1000;
const EVENTS_REFRESH_MS = 30 * 1000;

const COLORS = {
  windAvg: '#38bdf8',
  windGust: '#3b82f6',
  temp: '#ef4444',
  uv: '#f59e0b',
  lux: '#22c55e',
  event: '#a855f7',
};

// Mirrors the maps in weather_log.html for tooltip labels.
const AUTOMATION_LABELS = {
  rain_protection: 'Rain Protection',
  wind_protection: 'Wind Protection',
  wind_guard: 'Wind Guard',
  weather_timeout: 'Weather Timeout',
  manual_override: 'Manual Override',
  ai_agent: 'AI Agent',
};
const ACTION_LABELS = { deploy: 'Deploy', undeploy: 'Retract', stop: 'Stop' };

let chart = null;
let tempUnit = 'F';

function el(id) { return document.getElementById(id); }

function toDisplayTemp(c) {
  if (c == null) return null;
  return tempUnit === 'C' ? c : c * 9 / 5 + 32;
}

function tempAxisLabel() {
  return tempUnit === 'C' ? 'Temp (°C)' : 'Temp (°F)';
}

// ---- Dataset indices (fixed order in buildChart) ----
const DS = { windAvg: 0, windGust: 1, temp: 2, uv: 3, lux: 4, windFc: 5, tempFc: 6, events: 7 };

function buildChart() {
  Chart.defaults.color = '#94a3b8';
  Chart.defaults.borderColor = '#2a2d3a';
  Chart.defaults.font.family = 'system-ui, sans-serif';

  const lineCommon = {
    pointRadius: 0,
    pointHitRadius: 8,
    borderWidth: 2,
    tension: 0.25,
    spanGaps: true,
  };

  const data = {
    datasets: [
      { label: 'Wind avg (mph)', data: [], yAxisID: 'yWind', borderColor: COLORS.windAvg, backgroundColor: COLORS.windAvg, ...lineCommon },
      { label: 'Wind gust (mph)', data: [], yAxisID: 'yWind', borderColor: COLORS.windGust, backgroundColor: COLORS.windGust, ...lineCommon },
      { label: tempAxisLabel(), data: [], yAxisID: 'yTemp', borderColor: COLORS.temp, backgroundColor: COLORS.temp, ...lineCommon },
      { label: 'UV index', data: [], yAxisID: 'yUv', borderColor: COLORS.uv, backgroundColor: COLORS.uv, ...lineCommon },
      { label: 'Lux', data: [], yAxisID: 'yLux', borderColor: COLORS.lux, backgroundColor: COLORS.lux, hidden: true, ...lineCommon },
      { label: 'Wind forecast', data: [], yAxisID: 'yWind', borderColor: COLORS.windAvg, backgroundColor: COLORS.windAvg, borderDash: [5, 5], pointRadius: 2, borderWidth: 2, spanGaps: true },
      { label: 'Temp forecast', data: [], yAxisID: 'yTemp', borderColor: COLORS.temp, backgroundColor: COLORS.temp, borderDash: [5, 5], pointRadius: 2, borderWidth: 2, spanGaps: true },
      { label: 'Automation event', type: 'scatter', data: [], yAxisID: 'yEvents', borderColor: COLORS.event, backgroundColor: COLORS.event, pointRadius: 6, pointHoverRadius: 8, pointStyle: 'rectRot', showLine: false },
    ],
  };

  const axisRight = (id, title, extra = {}) => ({
    type: 'linear', position: 'right', title: { display: true, text: title },
    grid: { drawOnChartArea: false }, ...extra,
  });

  chart = new Chart(el('trend-chart'), {
    type: 'line',
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'nearest', intersect: true },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'PP pp' },
          ticks: { maxRotation: 0, autoSkip: true },
        },
        yWind: { type: 'linear', position: 'left', beginAtZero: true, title: { display: true, text: 'Wind (mph)' } },
        yTemp: axisRight('yTemp', tempAxisLabel()),
        yUv: axisRight('yUv', 'UV index', { suggestedMin: 0, suggestedMax: 12 }),
        yLux: axisRight('yLux', 'Lux', { beginAtZero: true, display: false }),
        yEvents: { type: 'linear', display: false, min: 0, max: 1 },
      },
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              if (ctx.datasetIndex === DS.events) {
                const m = ctx.raw.meta || {};
                const name = AUTOMATION_LABELS[m.automation_name] || m.automation_name || 'Event';
                const action = ACTION_LABELS[m.action_taken] || m.action_taken;
                const lines = [name + (action ? ' → ' + action : '')];
                if (m.rule_description) lines.push(m.rule_description);
                return lines;
              }
              const v = ctx.parsed.y;
              return `${ctx.dataset.label}: ${v == null ? '—' : v.toFixed(1)}`;
            },
          },
        },
      },
    },
  });
}

function trim(arr) {
  if (arr.length > MAX_POINTS) arr.splice(0, arr.length - MAX_POINTS);
}

async function loadConfig() {
  try {
    const resp = await fetch('/config');
    if (resp.ok) {
      const cfg = await resp.json();
      tempUnit = cfg.temp_unit || 'F';
      chart.data.datasets[DS.temp].label = tempAxisLabel();
      chart.options.scales.yTemp.title.text = tempAxisLabel();
    }
  } catch (e) { console.error('config load failed', e); }
}

async function loadHistory() {
  try {
    const [wResp, aResp] = await Promise.all([fetch('/logs/weather'), fetch('/logs/automation')]);
    if (wResp.ok) {
      const rows = (await wResp.json()).slice().reverse(); // newest-first -> chronological
      const ds = chart.data.datasets;
      ds[DS.windAvg].data = rows.map(e => ({ x: new Date(e.timestamp), y: e.wind_avg_mph }));
      ds[DS.windGust].data = rows.map(e => ({ x: new Date(e.timestamp), y: e.wind_gust_mph }));
      ds[DS.temp].data = rows.map(e => ({ x: new Date(e.timestamp), y: toDisplayTemp(e.air_temp_c) }));
      ds[DS.uv].data = rows.map(e => ({ x: new Date(e.timestamp), y: e.uv_index }));
      ds[DS.lux].data = rows.map(e => ({ x: new Date(e.timestamp), y: e.lux }));
    }
    if (aResp.ok) {
      const events = (await aResp.json()).filter(e => e.triggered);
      chart.data.datasets[DS.events].data = events.map(e => ({ x: new Date(e.timestamp), y: 0.5, meta: e }));
    }
  } catch (e) { console.error('history load failed', e); }
}

async function loadForecast() {
  try {
    const resp = await fetch('/weather/current');
    if (!resp.ok) return;
    const snap = await resp.json();
    const obs = snap.obs || {};
    const now = Date.now();
    const cutoff = now + FORECAST_HORIZON_MS;
    const fc = (snap.forecast || []).filter(f => f.dt * 1000 > now && f.dt * 1000 <= cutoff);

    // Anchor the dashed line to the current reading at "now" so it connects visually.
    const windAnchor = obs.wind_avg_m_s != null ? obs.wind_avg_m_s * MPH : null;
    const tempAnchor = toDisplayTemp(obs.air_temp_c);

    const windFc = [];
    const tempFc = [];
    if (windAnchor != null) windFc.push({ x: now, y: windAnchor });
    if (tempAnchor != null) tempFc.push({ x: now, y: tempAnchor });
    for (const f of fc) {
      const x = f.dt * 1000;
      if (f.wind_mph != null) windFc.push({ x, y: f.wind_mph });
      if (f.temp_c != null) tempFc.push({ x, y: toDisplayTemp(f.temp_c) });
    }
    chart.data.datasets[DS.windFc].data = windFc;
    chart.data.datasets[DS.tempFc].data = tempFc;
    chart.update('none');
  } catch (e) { console.error('forecast load failed', e); }
}

async function refreshEvents() {
  try {
    const resp = await fetch('/logs/automation');
    if (!resp.ok) return;
    const events = (await resp.json()).filter(e => e.triggered);
    chart.data.datasets[DS.events].data = events.map(e => ({ x: new Date(e.timestamp), y: 0.5, meta: e }));
    chart.update('none');
  } catch (e) { console.error('events refresh failed', e); }
}

function applyWindow() {
  const windowMs = parseInt(el('time-window').value, 10);
  const now = Date.now();
  chart.options.scales.x.min = now - windowMs;
  // Show the forecast tail only when the window is wide enough to reach it.
  chart.options.scales.x.max = windowMs <= 900000 ? now : now + FORECAST_HORIZON_MS;
}

function pushLiveObs(data) {
  if (!data || !Object.keys(data).length) return;
  const x = new Date();
  const ds = chart.data.datasets;
  if (data.wind_avg_m_s != null) { ds[DS.windAvg].data.push({ x, y: data.wind_avg_m_s * MPH }); trim(ds[DS.windAvg].data); }
  if (data.wind_gust_m_s != null) { ds[DS.windGust].data.push({ x, y: data.wind_gust_m_s * MPH }); trim(ds[DS.windGust].data); }
  if (data.air_temp_c != null) { ds[DS.temp].data.push({ x, y: toDisplayTemp(data.air_temp_c) }); trim(ds[DS.temp].data); }
  if (data.uv_index != null) { ds[DS.uv].data.push({ x, y: data.uv_index }); trim(ds[DS.uv].data); }
  if (data.illuminance_lux != null) { ds[DS.lux].data.push({ x, y: data.illuminance_lux }); trim(ds[DS.lux].data); }
  applyWindow();
  chart.update('none');
}

function setLuxVisible(visible) {
  chart.data.datasets[DS.lux].hidden = !visible;
  chart.options.scales.yLux.display = visible;
  chart.update();
}

function connectSSE() {
  const badge = el('connection-status');
  const es = new EventSource('/weather/stream');
  es.onopen = () => { if (badge) { badge.textContent = 'Live'; badge.className = 'badge badge-ok'; } };
  es.onerror = () => { if (badge) { badge.textContent = 'Reconnecting…'; badge.className = 'badge badge-warn'; } };
  es.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    if (msg.type === 'obs_st') pushLiveObs(msg.data || {});
  };
}

el('time-window').addEventListener('change', () => { applyWindow(); chart.update(); });
el('show-lux').addEventListener('change', (e) => setLuxVisible(e.target.checked));

(async () => {
  buildChart();
  await loadConfig();
  await loadHistory();
  await loadForecast();
  applyWindow();
  chart.update();
  connectSSE();
  setInterval(loadForecast, FORECAST_REFRESH_MS);
  setInterval(refreshEvents, EVENTS_REFRESH_MS);
})();
