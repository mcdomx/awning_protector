/* global state */
const state = {
  windDir: 0,
  windSpeedMph: 0,
  windAvgMph: 0,
  windGustMph: 0,
  precipType: 0,
  rainMm: 0,
  airTempC: null,
  uvIndex: 0,
  lux: 0,
  forecast: [],
  forecastError: null,
  forecastUpdatedAt: null,
  hourlyForecast: [],
  dailyForecast: [],
};

const MPH = 2.23694;

/* --- DOM helpers --- */
function el(id) { return document.getElementById(id); }

function setNum(id, val, decimals = 1) {
  const node = el(id);
  if (node) node.textContent = (val == null || isNaN(val)) ? '--' : Number(val).toFixed(decimals);
}

/* --- Temperature helpers --- */
function toDisplayTemp(c) {
  if (c == null || isNaN(c)) return null;
  return (currentConfig.temp_unit || 'F') === 'C' ? c : c * 9 / 5 + 32;
}

function tempUnitLabel() {
  return (currentConfig.temp_unit || 'F') === 'C' ? '°C' : '°F';
}

function updateTempUnitUI() {
  const unitLabel = el('temp-unit-label');
  if (unitLabel) unitLabel.textContent = tempUnitLabel();

  if (state.airTempC != null) {
    const isC = (currentConfig.temp_unit || 'F') === 'C';
    setNum('air-temp', toDisplayTemp(state.airTempC), isC ? 1 : 0);
  }

  renderForecast(state.forecast, state.forecastError, state.forecastUpdatedAt);
  renderHourlyForecast(state.hourlyForecast);
  renderDailyForecast(state.dailyForecast);
}

/* --- Wind compass --- */
function updateCompass(dirDeg) {
  const arrow = el('wind-arrow');
  if (arrow) arrow.setAttribute('transform', `rotate(${dirDeg} 80 80)`);
}

/* --- Precip badge --- */
const PRECIP_LABELS = { 0: 'No rain', 1: 'Rain', 2: 'Hail' };
function updatePrecipBadge(type, mm) {
  const badge = el('precip-badge');
  if (!badge) return;
  const raining = type !== 0 || mm > 0;
  badge.textContent = raining ? (PRECIP_LABELS[type] || 'Precip') + ` (${mm.toFixed(1)} mm/min)` : 'No rain';
  badge.className = 'badge ' + (raining ? 'badge-alert' : 'badge-ok');
}

/* --- Forecast table --- */
function renderForecast(forecast, forecastError, forecastUpdatedAt) {
  const container = el('forecast-bars');
  const updatedEl = el('forecast-updated');
  if (!container) return;

  if (updatedEl) {
    updatedEl.textContent = forecastUpdatedAt
      ? `Updated ${new Date(forecastUpdatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
      : '';
  }

  if (!forecast || forecast.length === 0) {
    container.innerHTML = forecastError
      ? `<p class="muted">Forecast error: ${forecastError}</p>`
      : '<p class="muted">No forecast data (set OPENWEATHER_API_KEY)</p>';
    return;
  }

  const entries = forecast.slice(0, 8);
  const times = entries.map(e => {
    const d = new Date(e.dt * 1000);
    const h = d.getHours();
    return (h % 12 || 12) + (h >= 12 ? 'pm' : 'am');
  });
  const pcts  = entries.map(e => Math.round((e.pop || 0) * 100));
  const winds = entries.map(e => e.wind_mph != null ? e.wind_mph.toFixed(1) : '--');
  const temps = entries.map(e => {
    const displayTemp = toDisplayTemp(e.temp_c);
    return displayTemp != null ? `${Math.round(displayTemp)}°` : '--';
  });
  const barHeights = pcts.map(p => Math.max(3, p * 0.6));

  function cells(vals, cls) {
    return vals.map(v => `<div class="fc-cell ${cls}">${v}</div>`).join('');
  }
  function label(cls, text) {
    return `<div class="fc-label ${cls}">${text}</div>`;
  }

  container.innerHTML =
    label('fc-time', '') + cells(times, 'fc-time') +
    label('', '') + entries.map((_, i) =>
      `<div class="fc-bar-cell"><div class="forecast-bar" style="height:${barHeights[i]}px" title="${pcts[i]}%"></div></div>`
    ).join('') +
    label('fc-precip', 'Rain') + cells(pcts.map(p => p + '%'), 'fc-precip') +
    label('fc-wind', 'Wind') + cells(winds.map(w => w + 'mph'), 'fc-wind') +
    label('fc-temp', 'Temp') + cells(temps, 'fc-temp');
}

/* --- Hourly forecast (Tempest) --- */
function renderHourlyForecast(entries) {
  const container = el('hourly-forecast-bars');
  if (!container) return;

  if (!entries || entries.length === 0) {
    container.innerHTML = '<p class="muted">No hourly forecast data</p>';
    return;
  }

  const slice = entries.slice(0, 8);
  const times = slice.map(e => {
    const d = new Date(e.time);
    const h = d.getHours();
    return (h % 12 || 12) + (h >= 12 ? 'pm' : 'am');
  });
  const pcts  = slice.map(e => e.precip_probability ?? 0);
  const winds = slice.map(e => e.wind_avg != null ? (e.wind_avg * MPH).toFixed(1) : '--');
  const temps = slice.map(e => {
    const displayTemp = toDisplayTemp(e.air_temperature ?? null);
    return displayTemp != null ? `${Math.round(displayTemp)}°` : '--';
  });
  const barHeights = pcts.map(p => Math.max(3, p * 0.6));

  function cells(vals, cls) {
    return vals.map(v => `<div class="fc-cell ${cls}">${v}</div>`).join('');
  }
  function label(cls, text) {
    return `<div class="fc-label ${cls}">${text}</div>`;
  }

  container.innerHTML =
    label('fc-time', '') + cells(times, 'fc-time') +
    label('', '') + slice.map((_, i) =>
      `<div class="fc-bar-cell"><div class="forecast-bar" style="height:${barHeights[i]}px" title="${pcts[i]}%"></div></div>`
    ).join('') +
    label('fc-precip', 'Rain') + cells(pcts.map(p => p + '%'), 'fc-precip') +
    label('fc-wind', 'Wind') + cells(winds.map(w => w + 'mph'), 'fc-wind') +
    label('fc-temp', 'Temp') + cells(temps, 'fc-temp');
}

async function loadHourlyForecast() {
  const container = el('hourly-forecast-bars');
  try {
    const resp = await fetch('/weather/forecast/hourly');
    if (!resp.ok) {
      if (container) container.innerHTML = `<p class="muted">Hourly forecast unavailable (${resp.status})</p>`;
      return;
    }
    const data = await resp.json();
    state.hourlyForecast = data.forecast || [];
    renderHourlyForecast(state.hourlyForecast);
    const updatedEl = el('hourly-forecast-updated');
    if (updatedEl) updatedEl.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  } catch (e) {
    if (container) container.innerHTML = `<p class="muted">Hourly forecast error: ${e.message}</p>`;
  }
}

/* --- Daily forecast (Tempest) --- */
function renderDailyForecast(entries) {
  const container = el('daily-forecast-rows');
  if (!container) return;

  if (!entries || entries.length === 0) {
    container.innerHTML = '<p class="muted">No daily forecast data</p>';
    return;
  }

  container.innerHTML = entries.map(e => {
    const day = new Date(e.day_start_local).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    const high = toDisplayTemp(e.air_temp_high ?? null);
    const low  = toDisplayTemp(e.air_temp_low ?? null);
    const highStr = high != null ? `${Math.round(high)}°` : '--';
    const lowStr  = low  != null ? `${Math.round(low)}°`  : '--';
    const wind = e.wind_avg != null ? (e.wind_avg * MPH).toFixed(1) + ' mph' : '--';
    const precip = e.precip_probability != null ? `${e.precip_probability}%` : '--';
    const cond = e.conditions || '';
    return `<div class="daily-row">
      <div class="daily-row-top">
        <span class="daily-day">${day}</span>
        <span class="daily-conditions">${cond}</span>
      </div>
      <div class="daily-row-bottom">
        <span><span class="daily-temp-high">${highStr}</span>&thinsp;/&thinsp;<span class="daily-temp-low">${lowStr}</span></span>
        <span class="daily-precip">Rain: ${precip}</span>
        <span class="daily-wind">Wind: ${wind}</span>
      </div>
    </div>`;
  }).join('');
}

async function loadDailyForecast() {
  const container = el('daily-forecast-rows');
  try {
    const resp = await fetch('/weather/forecast/daily');
    if (!resp.ok) {
      if (container) container.innerHTML = `<p class="muted">Daily forecast unavailable (${resp.status})</p>`;
      return;
    }
    const data = await resp.json();
    state.dailyForecast = data.forecast || [];
    renderDailyForecast(state.dailyForecast);
    const updatedEl = el('daily-forecast-updated');
    if (updatedEl) updatedEl.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  } catch (e) {
    if (container) container.innerHTML = `<p class="muted">Daily forecast error: ${e.message}</p>`;
  }
}

/* --- Awning status --- */
function updateAwningStatus(data) {
  const badge = el('awning-state-badge');
  const ruleEl = el('active-rule');
  const notice = el('override-notice');
  if (!badge) return;

  const stateMap = {
    deployed: ['Deployed', 'badge-ok'],
    undeployed: ['Retracted', 'badge-warn'],
    unknown: ['Unknown', 'badge-unknown'],
  };
  const [label, cls] = stateMap[data.state] || ['Unknown', 'badge-unknown'];
  badge.textContent = label;
  badge.className = 'badge ' + cls;
  if (ruleEl) ruleEl.textContent = data.active_rule || '';
  if (notice) {
    const overrideUntil = data.override_until ? new Date(data.override_until) : null;
    notice.textContent = (overrideUntil && overrideUntil > new Date())
      ? `Manual override until ${overrideUntil.toLocaleTimeString()}`
      : '';
  }
}

/* --- Config cards --- */
let currentConfig = {};

function updateCardDisabledState() {
  [['rain', 'cfg-rain-enabled'], ['wind', 'cfg-wind-enabled'], ['ai', 'cfg-ai-enabled']].forEach(([name, checkId]) => {
    const checkbox = el(checkId);
    const body = el(`config-${name}`)?.querySelector('.automation-card-body');
    if (body && checkbox) body.classList.toggle('card-body-disabled', !checkbox.checked);
  });
}

async function loadConfig() {
  const resp = await fetch('/config');
  if (!resp.ok) return;
  currentConfig = await resp.json();
  const cfgEnabled = el('cfg-enabled');
  if (cfgEnabled) cfgEnabled.checked = currentConfig.automation_enabled;
  const cfgOverride = el('cfg-override-min');
  if (cfgOverride) cfgOverride.value = currentConfig.manual_override_min;

  const radioF = el('cfg-temp-unit-f');
  const radioC = el('cfg-temp-unit-c');
  if (radioF) radioF.checked = (currentConfig.temp_unit || 'F') !== 'C';
  if (radioC) radioC.checked = (currentConfig.temp_unit || 'F') === 'C';

  const cfgRain = el('cfg-rain-enabled');
  if (cfgRain) cfgRain.checked = currentConfig.rain_triggers_retract;
  const cfgWindEnabled = el('cfg-wind-enabled');
  if (cfgWindEnabled) cfgWindEnabled.checked = currentConfig.wind_protection_enabled;
  const cfgMaxWind = el('cfg-max-wind');
  if (cfgMaxWind) cfgMaxWind.value = currentConfig.max_wind_mph;

  const ai = currentConfig.ai || {};
  el('cfg-ai-enabled').checked = !!ai.ai_enabled;
  el('cfg-ai-wind').value = ai.current_wind_threshold_mph ?? 3.0;
  el('cfg-ai-forecast-wind').value = ai.forecasted_wind_threshold_mph ?? 8.0;
  el('cfg-ai-forecast-hours').value = ai.forecast_outlook_hours ?? 2;
  el('cfg-ai-min-temp').value = ai.min_deployment_temp_f ?? 65.0;
  el('cfg-ai-earliest').value = ai.earliest_auto_deployment ?? '8AM';
  el('cfg-ai-latest').value = ai.latest_auto_deployment ?? '6PM';
  el('cfg-ai-max-deploy').value = ai.max_deployment_seconds ?? 5;
  el('cfg-ai-min-deploy').value = ai.min_deployment_seconds ?? 2;
  el('cfg-ai-min-interval').value = ai.min_eval_interval_seconds ?? 300;

  updateCardDisabledState();
  updateTempUnitUI();
}

async function saveCard(cardName) {
  if (cardName === 'ai') {
    if (!currentConfig.ai) currentConfig.ai = {};
    currentConfig.ai.ai_enabled = el('cfg-ai-enabled').checked;
    currentConfig.ai.current_wind_threshold_mph = parseFloat(el('cfg-ai-wind').value);
    currentConfig.ai.forecasted_wind_threshold_mph = parseFloat(el('cfg-ai-forecast-wind').value);
    currentConfig.ai.forecast_outlook_hours = parseInt(el('cfg-ai-forecast-hours').value, 10);
    currentConfig.ai.min_deployment_temp_f = parseFloat(el('cfg-ai-min-temp').value);
    currentConfig.ai.earliest_auto_deployment = el('cfg-ai-earliest').value.trim();
    currentConfig.ai.latest_auto_deployment = el('cfg-ai-latest').value.trim();
    currentConfig.ai.max_deployment_seconds = parseInt(el('cfg-ai-max-deploy').value, 10);
    currentConfig.ai.min_deployment_seconds = parseInt(el('cfg-ai-min-deploy').value, 10);
    currentConfig.ai.min_eval_interval_seconds = parseInt(el('cfg-ai-min-interval').value, 10);
  }
  const resp = await fetch('/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(currentConfig),
  });
  const status = el(`save-status-${cardName}`);
  if (resp.ok) {
    currentConfig = await resp.json();
    updateCardDisabledState();
    updateTempUnitUI();
    if (status) { status.textContent = 'Saved'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } else {
    if (status) status.textContent = 'Error saving';
  }
}

/* --- Manual awning commands --- */
async function awningCmd(cmd) {
  const resp = await fetch(`/awning/${cmd}`, { method: 'POST' });
  if (!resp.ok) console.error('Awning command failed', cmd);
  await refreshAwningStatus();
}

async function refreshAwningStatus() {
  const resp = await fetch('/awning/status');
  if (resp.ok) updateAwningStatus(await resp.json());
}

/* --- Initial data load --- */
async function loadInitialWeather() {
  const resp = await fetch('/weather/current');
  if (!resp.ok) return;
  const data = await resp.json();
  const obs = data.obs || {};
  const wind = data.wind || {};
  handleObs(obs);
  handleWind(wind);
  if (data.forecast) {
    state.forecast = data.forecast;
    state.forecastError = data.forecast_error;
    state.forecastUpdatedAt = data.forecast_updated_at;
    renderForecast(state.forecast, state.forecastError, state.forecastUpdatedAt);
  }
}

/* --- SSE message handlers --- */
function handleObs(data) {
  if (!data || !Object.keys(data).length) return;
  state.windAvgMph = (data.wind_avg_m_s || 0) * MPH;
  state.windGustMph = (data.wind_gust_m_s || 0) * MPH;
  state.precipType = data.precip_type || 0;
  state.rainMm = data.rain_prev_min_mm || 0;
  state.airTempC = data.air_temp_c ?? null;
  state.uvIndex = data.uv_index || 0;
  state.lux = data.illuminance_lux || 0;

  setNum('wind-avg', state.windAvgMph);
  setNum('wind-gust', state.windGustMph);
  const isC = (currentConfig.temp_unit || 'F') === 'C';
  setNum('air-temp', toDisplayTemp(state.airTempC), isC ? 1 : 0);
  setNum('uv-index', state.uvIndex);
  setNum('lux', state.lux, 0);
  updatePrecipBadge(state.precipType, state.rainMm);
}

function handleWind(data) {
  if (!data || !Object.keys(data).length) return;
  state.windSpeedMph = data.wind_speed_mph || (data.wind_speed_m_s || 0) * MPH;
  state.windDir = data.wind_direction_deg || 0;
  setNum('wind-speed', state.windSpeedMph);
  setNum('wind-dir', state.windDir, 0);
  updateCompass(state.windDir);
}

/* --- SSE connection --- */
function connectSSE() {
  const statusBadge = el('connection-status');
  const es = new EventSource('/weather/stream');

  es.onopen = () => {
    if (statusBadge) { statusBadge.textContent = 'Live'; statusBadge.className = 'badge badge-ok'; }
  };

  es.onerror = () => {
    if (statusBadge) { statusBadge.textContent = 'Reconnecting…'; statusBadge.className = 'badge badge-warn'; }
  };

  es.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    const type = msg.type;
    const data = msg.data || {};
    if (type === 'obs_st') handleObs(data);
    else if (type === 'rapid_wind') handleWind(data);
  };
}

/* --- Awning status polling (every 15s) --- */
setInterval(refreshAwningStatus, 15000);

/* --- AI status --- */
let _aiNextEvalAt = null;
let _aiPollingFast = false;
let _aiLastEvalText = null;
let _aiLastEvalAt = null;

function updateAIStatusUI(data) {
  const runningBadge = el('ai-running-badge');
  const lastEvalTime = el('ai-last-eval-time');
  const evalText = el('ai-last-eval-text');

  if (runningBadge) {
    if (data.is_running) {
      runningBadge.textContent = 'Evaluating…';
      runningBadge.className = 'badge badge-warn';
    } else {
      runningBadge.textContent = 'Idle';
      runningBadge.className = 'badge badge-unknown';
    }
  }

  if (lastEvalTime) {
    if (data.last_eval_at) {
      const d = new Date(data.last_eval_at);
      lastEvalTime.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } else {
      lastEvalTime.textContent = '--';
    }
  }

  if (evalText && data.last_eval_text != null && data.last_eval_text !== _aiLastEvalText) {
    evalText.textContent = data.last_eval_text || '(No report text returned)';
    _aiLastEvalText = data.last_eval_text;
  }

  _aiNextEvalAt = data.next_eval_at ? new Date(data.next_eval_at) : null;
  _aiLastEvalAt = data.last_eval_at || null;
}

function tickAICountdown() {
  const countdownEl = el('ai-next-eval-countdown');
  if (!countdownEl) return;
  if (!_aiNextEvalAt) { countdownEl.textContent = '--'; return; }
  const diffMs = _aiNextEvalAt - Date.now();
  if (diffMs <= 0) { countdownEl.textContent = 'soon'; return; }
  const total = Math.floor(diffMs / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const hms = [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
  const tod = _aiNextEvalAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  countdownEl.textContent = `${hms} (${tod})`;
}

async function refreshAIStatus() {
  try {
    const resp = await fetch('/ai/status');
    if (!resp.ok) return;
    const data = await resp.json();
    updateAIStatusUI(data);

    if (data.is_running && !_aiPollingFast) {
      _aiPollingFast = true;
      _pollUntilDone(_aiLastEvalAt);
    }
  } catch (e) { /* ignore */ }
}

async function _pollUntilDone(preTriggerEvalAt) {
  const deadline = Date.now() + 5 * 60 * 1000; // 5-min safety timeout

  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const resp = await fetch('/ai/status');
      if (!resp.ok) break;
      const data = await resp.json();
      updateAIStatusUI(data);
      // Exit when evaluation is done and last_eval_at has changed from pre-trigger value.
      // This avoids clock-skew issues with comparing server vs. browser timestamps.
      if (!data.is_running && data.last_eval_at !== preTriggerEvalAt) break;
    } catch (e) { break; }
  }

  _aiPollingFast = false;
  const btn = el('ai-evaluate-btn');
  if (btn) btn.disabled = false;
}

async function aiEvaluateNow() {
  const btn = el('ai-evaluate-btn');
  if (btn) btn.disabled = true;
  const badge = el('ai-running-badge');
  if (badge) { badge.textContent = 'Evaluating…'; badge.className = 'badge badge-warn'; }
  const evalText = el('ai-last-eval-text');
  if (evalText) evalText.textContent = 'Evaluation in progress…';

  // Capture pre-trigger state so _pollUntilDone can detect when a new result arrives.
  // Also reset the cached text so identical consecutive results still re-render.
  const preTriggerEvalAt = _aiLastEvalAt;
  _aiLastEvalText = null;

  try {
    await fetch('/ai/evaluate', { method: 'POST' });
    if (!_aiPollingFast) {
      _aiPollingFast = true;
      _pollUntilDone(preTriggerEvalAt);
    }
  } catch (e) {
    if (btn) btn.disabled = false;
    if (badge) { badge.textContent = 'Idle'; badge.className = 'badge badge-unknown'; }
  }
}

/* --- AI Guidance --- */
function _fmtTime(isoStr) {
  return new Date(isoStr).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function _setExpirySelects(isoStr) {
  const d = new Date(isoStr);
  const h24 = d.getHours();
  const ampm = h24 < 12 ? 'AM' : 'PM';
  const h12 = h24 % 12 || 12;
  const min = d.getMinutes();
  const hourEl = el('cfg-ai-guidance-expiry-hour');
  const minEl = el('cfg-ai-guidance-expiry-min');
  const ampmEl = el('cfg-ai-guidance-expiry-ampm');
  if (hourEl) hourEl.value = String(h12);
  if (minEl) minEl.value = String(min);
  if (ampmEl) ampmEl.value = ampm;
}

function _clearExpirySelects() {
  const hourEl = el('cfg-ai-guidance-expiry-hour');
  const minEl = el('cfg-ai-guidance-expiry-min');
  const ampmEl = el('cfg-ai-guidance-expiry-ampm');
  if (hourEl) hourEl.value = '';
  if (minEl) minEl.value = '';
  if (ampmEl) ampmEl.value = '';
}

function updateGuidanceUI(data) {
  const statusEl = el('ai-guidance-status');
  const clearBtn = el('ai-clear-guidance-btn');
  if (!data || !data.text) {
    if (statusEl) statusEl.textContent = 'No guidance active.';
    if (clearBtn) clearBtn.disabled = true;
    return;
  }
  if (clearBtn) clearBtn.disabled = false;
  const textEl = el('cfg-ai-guidance-text');
  if (textEl && !textEl.value) textEl.value = data.text;
  if (data.expires_at) _setExpirySelects(data.expires_at);
  if (data.active) {
    const expStr = data.expires_at ? ` · Expires ${_fmtTime(data.expires_at)}` : ' · No expiry';
    if (statusEl) statusEl.textContent = 'Active' + expStr;
  } else {
    const expStr = data.expires_at ? ` (expired ${_fmtTime(data.expires_at)})` : '';
    if (statusEl) statusEl.textContent = 'Guidance set but expired' + expStr;
  }
}

async function loadGuidance() {
  const resp = await fetch('/ai/guidance');
  if (!resp.ok) return;
  updateGuidanceUI(await resp.json());
}

async function saveGuidance() {
  const textEl = el('cfg-ai-guidance-text');
  const status = el('save-status-guidance');
  if (!textEl || !textEl.value.trim()) {
    if (status) { status.textContent = 'Enter guidance text first.'; setTimeout(() => { status.textContent = ''; }, 2500); }
    return;
  }
  const body = { text: textEl.value.trim() };
  const hourVal = el('cfg-ai-guidance-expiry-hour').value;
  const minVal = el('cfg-ai-guidance-expiry-min').value;
  const ampmVal = el('cfg-ai-guidance-expiry-ampm').value;
  if (hourVal && minVal !== '' && ampmVal) {
    let h = parseInt(hourVal, 10);
    if (ampmVal === 'PM' && h !== 12) h += 12;
    if (ampmVal === 'AM' && h === 12) h = 0;
    const d = new Date();
    d.setHours(h, parseInt(minVal, 10), 0, 0);
    body.expires_at = d.toISOString();
  }
  try {
    const resp = await fetch('/ai/guidance', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      updateGuidanceUI(await resp.json());
      if (status) { status.textContent = 'Saved'; setTimeout(() => { status.textContent = ''; }, 2000); }
    } else {
      if (status) status.textContent = 'Error saving';
    }
  } catch (e) {
    if (status) status.textContent = 'Error saving';
  }
}

async function clearGuidance() {
  const textEl = el('cfg-ai-guidance-text');
  const status = el('save-status-guidance');
  try {
    await fetch('/ai/guidance', { method: 'DELETE' });
    if (textEl) textEl.value = '';
    _clearExpirySelects();
    updateGuidanceUI({ active: false, text: null, expires_at: null });
    if (status) { status.textContent = 'Cleared'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } catch (e) {
    if (status) status.textContent = 'Error clearing';
  }
}

setInterval(refreshAIStatus, 30000);
setInterval(tickAICountdown, 1000);

/* --- Boot --- */
(async () => {
  try { await loadConfig(); } catch (e) { console.error('loadConfig failed', e); }
  try { await loadInitialWeather(); } catch (e) { console.error('loadInitialWeather failed', e); }
  try { await loadHourlyForecast(); } catch (e) { console.error('loadHourlyForecast failed', e); }
  try { await loadDailyForecast(); } catch (e) { console.error('loadDailyForecast failed', e); }
  try { await refreshAwningStatus(); } catch (e) { console.error('refreshAwningStatus failed', e); }
  try { await refreshAIStatus(); } catch (e) { console.error('refreshAIStatus failed', e); }
  try { await loadGuidance(); } catch (e) { console.error('loadGuidance failed', e); }
  connectSSE();
})();
