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
  hourlyForecast: [],
  dailyForecast: [],
};

const MPH = 2.23694;
let currentConfig = {};

/* --- DOM helpers --- */
function el(id) { return document.getElementById(id); }

function setNum(id, val, decimals = 1) {
  const node = el(id);
  if (node) node.textContent = (val == null || isNaN(val)) ? '--' : Number(val).toFixed(decimals);
}

function setNumIds(ids, val, decimals = 1) {
  ids.forEach(id => setNum(id, val, decimals));
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
  const raining = type !== 0 || mm > 0;
  const text = raining ? (PRECIP_LABELS[type] || 'Precip') + ` (${mm.toFixed(1)} mm/min)` : 'No rain';
  const cls = 'badge ' + (raining ? 'badge-alert' : 'badge-ok');
  ['precip-badge', 'awn-precip-badge'].forEach(id => {
    const badge = el(id);
    if (!badge) return;
    badge.textContent = text;
    badge.className = cls;
  });
}

/* --- Hourly forecast (stacked rows) --- */
function renderHourlyForecast(entries) {
  const container = el('hourly-forecast-rows');
  if (!container) return;

  if (!entries || entries.length === 0) {
    container.innerHTML = '<p class="muted">No hourly forecast data</p>';
    return;
  }

  container.innerHTML = entries.slice(0, 8).map(e => {
    const d = new Date(e.time);
    const h = d.getHours();
    const time = (h % 12 || 12) + (h >= 12 ? 'pm' : 'am');
    const pct = e.precip_probability ?? 0;
    const wind = e.wind_avg != null ? (e.wind_avg * MPH).toFixed(1) + ' mph' : '--';
    const displayTemp = toDisplayTemp(e.air_temperature ?? null);
    const temp = displayTemp != null ? `${Math.round(displayTemp)}°` : '--';
    return `<div class="hourly-row">
      <span class="hourly-time">${time}</span>
      <span class="hourly-rain">${pct}%</span>
      <span class="hourly-wind">${wind}</span>
      <span class="hourly-temp">${temp}</span>
    </div>`;
  }).join('');
}

/* --- Wind & rain forecast strip (awning screen, horizontal layout) --- */
function renderAwningHourlyStrip(entries) {
  const container = el('awning-hourly-strip');
  if (!container) return;

  if (!entries || entries.length === 0) {
    container.innerHTML = '<p class="muted">No forecast data</p>';
    return;
  }

  const windIcon = '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M3 8h11a3 3 0 1 0-3-3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M3 14h15a3 3 0 1 1-3 3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>';
  const rainIcon = '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M12 3c4 5 6 8 6 11a6 6 0 1 1-12 0c0-3 2-6 6-11z" stroke="currentColor" stroke-width="2" fill="none"/></svg>';

  container.innerHTML = entries.slice(0, 6).map(e => {
    const d = new Date(e.time);
    const h = d.getHours();
    const time = (h % 12 || 12) + (h >= 12 ? 'pm' : 'am');
    const pct = e.precip_probability ?? 0;
    const wind = e.wind_avg != null ? (e.wind_avg * MPH).toFixed(1) : '--';
    return `<div class="awning-hour-col">
      <span class="awning-hour-time">${time}</span>
      <div class="stat-row">${windIcon}<span class="awning-hour-value">${wind}</span></div>
      <div class="stat-row">${rainIcon}<span class="awning-hour-value">${pct}%</span></div>
    </div>`;
  }).join('');
}

async function loadHourlyForecast() {
  const container = el('hourly-forecast-rows');
  try {
    const resp = await fetch('/weather/forecast/hourly');
    if (!resp.ok) {
      if (container) container.innerHTML = `<p class="muted">Hourly forecast unavailable (${resp.status})</p>`;
      const stripErr = el('awning-hourly-strip');
      if (stripErr) stripErr.innerHTML = `<p class="muted">Forecast unavailable (${resp.status})</p>`;
      return;
    }
    const data = await resp.json();
    state.hourlyForecast = data.forecast || [];
    renderHourlyForecast(state.hourlyForecast);
    renderAwningHourlyStrip(state.hourlyForecast);
    const updatedEl = el('hourly-forecast-updated');
    if (updatedEl) updatedEl.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  } catch (e) {
    if (container) container.innerHTML = `<p class="muted">Hourly forecast error: ${e.message}</p>`;
    const stripErr = el('awning-hourly-strip');
    if (stripErr) stripErr.innerHTML = `<p class="muted">Forecast error: ${e.message}</p>`;
  }
}

/* --- Daily forecast --- */
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
    const precip = e.precip_probability != null ? `${e.precip_probability}%` : '--';
    const cond = e.conditions || '';
    return `<div class="daily-row">
      <span class="daily-day">${day}</span>
      <span class="daily-rain">${precip}</span>
      <span class="daily-temp">${highStr}&thinsp;/&thinsp;${lowStr}</span>
      <span class="daily-conditions">${conditionIcon(cond)}<span>${cond}</span></span>
    </div>`;
  }).join('');
}

/* --- Condition icon lookup ---
   The Tempest API only returns a free-text `conditions` description (no
   structured icon code), so icon selection is done by keyword match. */
const CONDITION_ICONS = [
  [/thunder|storm/i, '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M19.35 8.04A7.49 7.49 0 0 0 12 2a7.49 7.49 0 0 0-7.35 6.04A5.5 5.5 0 0 0 6 19h13a5.5 5.5 0 0 0 .35-10.96z" stroke="currentColor" stroke-width="2" fill="none"/><path d="M13 18l-3 2h3l-2 2" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'],
  [/snow|flurries|sleet/i, '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M19.35 8.04A7.49 7.49 0 0 0 12 2a7.49 7.49 0 0 0-7.35 6.04A5.5 5.5 0 0 0 6 19h13a5.5 5.5 0 0 0 .35-10.96z" stroke="currentColor" stroke-width="2" fill="none"/><g stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="8" y1="20" x2="8" y2="22"/><line x1="7" y1="21" x2="9" y2="21"/><line x1="16" y1="20" x2="16" y2="22"/><line x1="15" y1="21" x2="17" y2="21"/></g></svg>'],
  [/rain|shower|drizzle/i, '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M19.35 8.04A7.49 7.49 0 0 0 12 2a7.49 7.49 0 0 0-7.35 6.04A5.5 5.5 0 0 0 6 19h13a5.5 5.5 0 0 0 .35-10.96z" stroke="currentColor" stroke-width="2" fill="none"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="20" x2="8" y2="22"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="16" y1="20" x2="16" y2="22"/></g></svg>'],
  [/fog|mist|haze/i, '<svg class="stat-icon" viewBox="0 0 24 24"><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="8" x2="20" y2="8"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="16" x2="20" y2="16"/></g></svg>'],
  [/wind/i, '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M3 8h11a3 3 0 1 0-3-3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M3 14h15a3 3 0 1 1-3 3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'],
  [/cloud|overcast/i, '<svg class="stat-icon" viewBox="0 0 24 24"><path d="M19 18H6a4 4 0 1 1 .9-7.93A5.5 5.5 0 0 1 21 9.5 3.5 3.5 0 0 1 19 18z" stroke="currentColor" stroke-width="2" fill="none" stroke-linejoin="round"/></svg>'],
  [/clear|sunny/i, '<svg class="stat-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2" fill="none"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.9" y1="4.9" x2="6.3" y2="6.3"/><line x1="17.7" y1="17.7" x2="19.1" y2="19.1"/><line x1="4.9" y1="19.1" x2="6.3" y2="17.7"/><line x1="17.7" y1="6.3" x2="19.1" y2="4.9"/></g></svg>'],
];
const CONDITION_ICON_DEFAULT = CONDITION_ICONS[5][1]; // cloud as fallback

function conditionIcon(text) {
  const match = CONDITION_ICONS.find(([re]) => re.test(text));
  return match ? match[1] : CONDITION_ICON_DEFAULT;
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

async function awningCmd(cmd) {
  const resp = await fetch(`/awning/${cmd}`, { method: 'POST' });
  if (!resp.ok) console.error('Awning command failed', cmd);
  await refreshAwningStatus();
}

async function refreshAwningStatus() {
  const resp = await fetch('/awning/status');
  if (resp.ok) updateAwningStatus(await resp.json());
}

/* --- Config (curated AI settings) --- */
async function loadConfig() {
  const resp = await fetch('/config');
  if (!resp.ok) return;
  currentConfig = await resp.json();
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
  el('cfg-ai-min-interval').value = Math.round((ai.min_eval_interval_seconds ?? 300) / 60);
  el('cfg-ai-max-interval').value = Math.round((ai.max_eval_interval_seconds ?? 4500) / 60);
  updateTempUnitUI();
}

async function saveAISettings() {
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
  currentConfig.ai.min_eval_interval_seconds = parseInt(el('cfg-ai-min-interval').value, 10) * 60;
  currentConfig.ai.max_eval_interval_seconds = parseInt(el('cfg-ai-max-interval').value, 10) * 60;

  const resp = await fetch('/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(currentConfig),
  });
  const status = el('save-status-ai');
  if (resp.ok) {
    currentConfig = await resp.json();
    if (status) { status.textContent = 'Saved'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } else {
    if (status) status.textContent = 'Error saving';
  }
}

/* --- Initial weather load --- */
async function loadInitialWeather() {
  const resp = await fetch('/weather/current');
  if (!resp.ok) return;
  const data = await resp.json();
  handleObs(data.obs || {});
  handleWind(data.wind || {});
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

  setNumIds(['wind-avg', 'awn-wind-avg'], state.windAvgMph);
  setNumIds(['wind-gust', 'awn-wind-gust'], state.windGustMph);
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
  setNumIds(['wind-speed', 'awn-wind-speed'], state.windSpeedMph);
  setNum('wind-dir', state.windDir, 0);
  updateCompass(state.windDir);
}

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
  const deadline = Date.now() + 5 * 60 * 1000;

  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const resp = await fetch('/ai/status');
      if (!resp.ok) break;
      const data = await resp.json();
      updateAIStatusUI(data);
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

setInterval(refreshAIStatus, 30000);
setInterval(tickAICountdown, 1000);

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

function _setDefaultExpiry() {
  _setExpirySelects(new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString());
}

function _radioValue(name) {
  const checked = document.querySelector(`input[name="${name}"]:checked`);
  return checked ? checked.value : null;
}

function _setRadioValue(name, value) {
  const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
  if (input) input.checked = true;
}

const RISK_PHRASES = {
  '1': 'Take no risks — retract proactively at the slightest concern',
  '2': 'Take a low level of risk — favor caution over keeping the awning deployed',
  '3': 'Take a moderate, balanced level of risk',
  '4': 'Take a higher level of risk — favor keeping the awning deployed when conditions are borderline',
  '5': 'Take the maximum acceptable risk — only retract for clear, immediate danger',
};

function _buildGuidanceText(location, risk) {
  const locPhrase = location === 'home'
    ? "I'm home and can monitor conditions"
    : "I'm away and cannot react quickly";
  return `${locPhrase}. ${RISK_PHRASES[risk]} (risk level ${risk}/5).`;
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
  if (data.expires_at) _setExpirySelects(data.expires_at);
  if (data.active) {
    const expStr = data.expires_at ? ` · Expires ${_fmtTime(data.expires_at)}` : ' · No expiry';
    if (statusEl) statusEl.textContent = data.text + ' · Active' + expStr;
  } else {
    const expStr = data.expires_at ? ` (expired ${_fmtTime(data.expires_at)})` : '';
    if (statusEl) statusEl.textContent = data.text + ' · Guidance set but expired' + expStr;
  }
}

async function loadGuidance() {
  const resp = await fetch('/ai/guidance');
  if (!resp.ok) return;
  updateGuidanceUI(await resp.json());
}

async function saveGuidance() {
  const status = el('save-status-guidance');
  const location = _radioValue('ai-guidance-location');
  const risk = _radioValue('ai-guidance-risk');
  if (!location || !risk) {
    if (status) { status.textContent = 'Select an option in both groups.'; setTimeout(() => { status.textContent = ''; }, 2500); }
    return;
  }
  const body = { text: _buildGuidanceText(location, risk) };
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
  const status = el('save-status-guidance');
  try {
    await fetch('/ai/guidance', { method: 'DELETE' });
    _setRadioValue('ai-guidance-location', 'home');
    _setRadioValue('ai-guidance-risk', '3');
    _setDefaultExpiry();
    updateGuidanceUI({ active: false, text: null, expires_at: null });
    if (status) { status.textContent = 'Cleared'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } catch (e) {
    if (status) status.textContent = 'Error clearing';
  }
}

/* --- QR code --- */
function renderQRCode() {
  const container = el('qr-code');
  const urlEl = el('qr-url');
  if (!container) return;
  const url = window.location.origin + '/';
  if (urlEl) urlEl.textContent = url;
  container.innerHTML = '';
  const qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();
  container.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 4 });
}

/* --- Swipe dot indicator --- */
function setupScreenDots() {
  const screens = el('screens');
  const dots = document.querySelectorAll('#dots .dot');
  if (!screens || !dots.length) return;

  let scrollTimeout = null;
  screens.addEventListener('scroll', () => {
    if (scrollTimeout) clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      const idx = Math.round(screens.scrollLeft / screens.clientWidth);
      dots.forEach((d, i) => d.classList.toggle('active', i === idx));
    }, 80);
  });
}

/* --- Boot --- */
(async () => {
  setupScreenDots();
  try { renderQRCode(); } catch (e) { console.error('renderQRCode failed', e); }
  try { await loadConfig(); } catch (e) { console.error('loadConfig failed', e); }
  try { await loadInitialWeather(); } catch (e) { console.error('loadInitialWeather failed', e); }
  try { await loadHourlyForecast(); } catch (e) { console.error('loadHourlyForecast failed', e); }
  try { await loadDailyForecast(); } catch (e) { console.error('loadDailyForecast failed', e); }
  try { await refreshAwningStatus(); } catch (e) { console.error('refreshAwningStatus failed', e); }
  try { await refreshAIStatus(); } catch (e) { console.error('refreshAIStatus failed', e); }
  _setDefaultExpiry();
  try { await loadGuidance(); } catch (e) { console.error('loadGuidance failed', e); }
  connectSSE();
})();
