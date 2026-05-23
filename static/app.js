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

  const settingLabel = el('temp-unit-setting-label');
  if (settingLabel) settingLabel.textContent = tempUnitLabel();

  const minTempInput = el('cfg-min-temp');
  if (minTempInput && currentConfig.min_temp_c != null) {
    const isC = (currentConfig.temp_unit || 'F') === 'C';
    const displayVal = toDisplayTemp(currentConfig.min_temp_c);
    minTempInput.value = isC ? Number(displayVal).toFixed(1) : Math.round(displayVal);
    minTempInput.min = isC ? 0 : 32;
    minTempInput.max = isC ? 50 : 120;
    minTempInput.step = isC ? 0.5 : 1;
  }

  if (state.airTempC != null) {
    const isC = (currentConfig.temp_unit || 'F') === 'C';
    setNum('air-temp', toDisplayTemp(state.airTempC), isC ? 1 : 0);
  }

  renderForecast(state.forecast, state.forecastError, state.forecastUpdatedAt);
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
    notice.textContent = data.override_until
      ? `Manual override until ${new Date(data.override_until).toLocaleTimeString()}`
      : '';
  }
}

/* --- Config cards --- */
let currentConfig = {};

function updateCardDisabledState() {
  [['rain', 'cfg-rain-enabled'], ['wind', 'cfg-wind-enabled'], ['sunny', 'cfg-sunny-enabled']].forEach(([name, checkId]) => {
    const checkbox = el(checkId);
    const body = el(`config-${name}`)?.querySelector('.automation-card-body');
    if (body && checkbox) body.classList.toggle('card-body-disabled', !checkbox.checked);
  });
}

async function loadConfig() {
  const resp = await fetch('/config');
  if (!resp.ok) return;
  currentConfig = await resp.json();
  el('cfg-enabled').checked = currentConfig.automation_enabled;
  el('cfg-override-min').value = currentConfig.manual_override_min;

  const radioF = el('cfg-temp-unit-f');
  const radioC = el('cfg-temp-unit-c');
  if (radioF) radioF.checked = (currentConfig.temp_unit || 'F') !== 'C';
  if (radioC) radioC.checked = (currentConfig.temp_unit || 'F') === 'C';

  el('cfg-rain-enabled').checked = currentConfig.rain_triggers_retract;
  el('cfg-wind-enabled').checked = currentConfig.wind_protection_enabled;
  el('cfg-max-wind').value = currentConfig.max_wind_mph;
  el('cfg-sunny-enabled').checked = currentConfig.sunny_deploy_enabled;
  el('cfg-sunny-lux').value = currentConfig.sunny_lux_threshold;
  el('cfg-sunny-wind').value = currentConfig.sunny_wind_max_mph;
  el('cfg-deploy-dur').value = currentConfig.deploy_duration_s;
  el('cfg-deploy-dwell').value = currentConfig.sunny_deploy_dwell_s;
  // min_temp_c display set by updateTempUnitUI below
  updateCardDisabledState();
  updateTempUnitUI();
}

async function saveCard(cardName) {
  if (cardName === 'general') {
    currentConfig.automation_enabled = el('cfg-enabled').checked;
    currentConfig.manual_override_min = parseInt(el('cfg-override-min').value, 10);
    currentConfig.temp_unit = el('cfg-temp-unit-c').checked ? 'C' : 'F';
  } else if (cardName === 'rain') {
    currentConfig.rain_triggers_retract = el('cfg-rain-enabled').checked;
  } else if (cardName === 'wind') {
    currentConfig.wind_protection_enabled = el('cfg-wind-enabled').checked;
    currentConfig.max_wind_mph = parseFloat(el('cfg-max-wind').value);
  } else if (cardName === 'sunny') {
    currentConfig.sunny_deploy_enabled = el('cfg-sunny-enabled').checked;
    currentConfig.sunny_lux_threshold = parseInt(el('cfg-sunny-lux').value, 10);
    currentConfig.sunny_wind_max_mph = parseFloat(el('cfg-sunny-wind').value);
    currentConfig.deploy_duration_s = parseInt(el('cfg-deploy-dur').value, 10);
    // convert display unit input back to Celsius for storage
    const inputVal = parseFloat(el('cfg-min-temp').value);
    currentConfig.min_temp_c = (currentConfig.temp_unit || 'F') === 'C'
      ? inputVal
      : (inputVal - 32) * 5 / 9;
    currentConfig.sunny_deploy_dwell_s = parseInt(el('cfg-deploy-dwell').value, 10);
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

/* --- Boot --- */
(async () => {
  try { await loadConfig(); } catch (e) { console.error('loadConfig failed', e); }
  try { await loadInitialWeather(); } catch (e) { console.error('loadInitialWeather failed', e); }
  try { await refreshAwningStatus(); } catch (e) { console.error('refreshAwningStatus failed', e); }
  connectSSE();
})();
