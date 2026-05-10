const API = '/api';
let velocityChart = null;
let refreshTimer = null;

// ─── NAVIGATION ──────────────────────────────────────────────────────────────

document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const sec = link.dataset.section;
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    link.classList.add('active');
    document.getElementById(`section-${sec}`).classList.add('active');
    if (sec === 'velocity') loadVelocity();
    if (sec === 'gems') loadGems();
    if (sec === 'alerts') loadAlerts();
    if (sec === 'listings') loadListings();
    if (sec === 'dealers') loadDealers();
  });
});

// ─── UTILS ───────────────────────────────────────────────────────────────────

async function apiFetch(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-GB');
const fmtPrice = p => p == null ? '—' : '£' + Number(p).toLocaleString('en-GB');
const fmtDays = d => d == null ? '—' : Number(d).toFixed(1) + 'd';

function sourceBadge(src) {
  if (src === 'autotrader') return '<span class="listing-badge badge-at">AutoTrader</span>';
  return '<span class="listing-badge badge-cc">Car&amp;Classic</span>';
}

function daysColor(d) {
  if (d == null) return '';
  if (d <= 7) return 'badge badge-green';
  if (d <= 14) return 'badge badge-blue';
  if (d <= 30) return 'badge badge-orange';
  return 'badge badge-red';
}

function timeAgo(iso) {
  if (!iso) return '—';
  const secs = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (secs < 60) return 'just now';
  if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
  if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
  return Math.floor(secs / 86400) + 'd ago';
}

function conditionBadge(condition) {
  if (!condition) return '';
  const lc = condition.toLowerCase();
  if (lc.includes('high') || lc.includes('strong')) return `<span class="badge badge-green">${condition}</span>`;
  if (lc.includes('low') || lc.includes('weak')) return `<span class="badge badge-red">${condition}</span>`;
  return `<span class="badge badge-blue">${condition}</span>`;
}

// ─── LIVE DASHBOARD ──────────────────────────────────────────────────────────

async function loadDashboard() {
  try {
    const [stats, signals, spikes, activity] = await Promise.all([
      apiFetch('/live/stats'),
      apiFetch('/analytics/demand'),
      apiFetch('/analytics/demand/spikes'),
      apiFetch('/live/activity'),
    ]);

    document.getElementById('stat-active').textContent = fmt(stats.active_listings);
    document.getElementById('stat-new24').textContent = fmt(stats.new_last_24h);
    document.getElementById('stat-sold7').textContent = fmt(stats.sold_last_7d);
    document.getElementById('stat-avg-dts').textContent = stats.avg_days_to_sell ? stats.avg_days_to_sell + 'd' : '—';
    document.getElementById('stat-dealers').textContent = fmt(stats.dealers.monitored);
    document.getElementById('stat-dealer-sold').textContent = fmt(stats.dealers.sold_last_7d);

    // Scraper health cards
    document.getElementById('scraper-health').innerHTML = ['autotrader', 'carandclassic'].map(src => {
      const s = stats.scrapers[src];
      const ok = s && s.success;
      const when = s ? timeAgo(s.last_run) : 'Never';
      return `<div class="health-card ${ok ? 'ok' : 'warn'}">
        <div class="health-name">${src === 'autotrader' ? 'AutoTrader' : 'Car &amp; Classic'}</div>
        <div class="health-time">${when}</div>
        <div class="health-stats">${s ? `+${s.listings_new} new · ${s.listings_sold} sold` : 'No data'}</div>
        <div class="health-dot ${ok ? 'green' : 'red'}"></div>
      </div>`;
    }).join('');

    // Demand spikes
    const spikesEl = document.getElementById('spikes-list');
    spikesEl.innerHTML = spikes.length
      ? spikes.slice(0, 6).map(s => `
          <div class="spike-item">
            <div class="spike-name">${s.make} ${s.model}</div>
            <div class="spike-meta">${s.new_last_7d} new this week · ${fmtDays(s.avg_days_to_sell)} avg · Score ${s.demand_score}</div>
          </div>`).join('')
      : '<div class="empty-state" style="padding:20px">No spikes detected yet</div>';

    // Demand table
    document.getElementById('demand-tbody').innerHTML = signals.slice(0, 20).map(s => `
      <tr>
        <td><strong>${s.make}</strong> ${s.model}</td>
        <td><span class="badge badge-blue">${s.demand_score}</span></td>
        <td><span class="${daysColor(s.avg_days_to_sell)}">${fmtDays(s.avg_days_to_sell)}</span></td>
        <td>${s.active_count}</td>
        <td>${s.new_last_7d}</td>
        <td>${s.sold_last_7d}</td>
        <td>${s.spike_detected ? '🔥' : ''}</td>
      </tr>`).join('') || '<tr><td colspan="7" class="text-muted">Run scrapers to collect data</td></tr>';

    // Activity feed
    document.getElementById('activity-feed').innerHTML = activity.slice(0, 20).map(e => {
      const icon = e.type === 'new' ? '🟢' : '🔴';
      const label = e.type === 'new' ? 'Listed' : `Sold in ${e.days_to_sell ?? '?'}d`;
      return `<div class="activity-item">
        <span class="activity-icon">${icon}</span>
        <span class="activity-text"><strong>${e.year || ''} ${e.make || ''} ${e.model || ''}</strong>
          ${fmtPrice(e.price)} · ${e.colour || ''} · ${label}
          ${sourceBadge(e.source)}
        </span>
        <span class="activity-time">${timeAgo(e.time)}</span>
      </div>`;
    }).join('') || '<div class="empty-state">No activity yet — run scrapers</div>';

    // Update refresh indicator
    document.getElementById('last-refresh').textContent = 'Updated ' + new Date().toLocaleTimeString('en-GB');
    const dot = document.getElementById('live-dot');
    dot.classList.add('pulse');
    setTimeout(() => dot.classList.remove('pulse'), 1000);

  } catch (e) {
    console.error('Dashboard error:', e);
  }
}

async function triggerScrape(source) {
  await fetch(`${API}/scraper/run/${source}`, { method: 'POST' });
  alert(`Scrape started: ${source}. Check activity feed in a few minutes.`);
}

// Auto-refresh every 30s
function startAutoRefresh() {
  loadDashboard();
  refreshTimer = setInterval(loadDashboard, 30000);
}

// ─── VALUATION ───────────────────────────────────────────────────────────────

async function getValuation() {
  const reg = document.getElementById('val-reg').value.trim().replace(/\s+/g, '');
  const mileage = document.getElementById('val-mileage').value;
  const force = document.getElementById('val-force').checked;

  if (!reg || !mileage) { alert('Enter plate and mileage'); return; }

  const result = document.getElementById('val-result');
  result.innerHTML = '<div class="card loading">Fetching valuation from AutoTrader — this may take 15–20 seconds...</div>';

  try {
    const v = await apiFetch(`/valuation/${encodeURIComponent(reg)}?mileage=${mileage}&force_refresh=${force}`);
    const ratingColor = !v.retail_rating ? '#8892a4' :
      v.retail_rating >= 70 ? '#22c55e' : v.retail_rating >= 40 ? '#f59e0b' : '#ef4444';
    const trendSign = v.price_change_pct > 0 ? '+' : '';
    const trendColor = v.price_change_pct > 0 ? '#22c55e' : '#ef4444';

    result.innerHTML = `
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px">
          <div>
            <h2>${v.reg} &nbsp;<span style="color:var(--text-muted);font-weight:400">${v.make} ${v.model} ${v.year || ''}</span></h2>
            <div class="listing-meta">${v.colour || ''} · ${v.fuel_type || ''} · ${v.transmission || ''} · ${fmt(v.mileage)} mi</div>
          </div>
          ${v.from_cache ? `<span class="badge badge-orange">Cached ${timeAgo(v.cached_at)}</span>` : '<span class="badge badge-green">Fresh</span>'}
        </div>

        <div class="val-grid mt-2">
          <div class="val-box primary">
            <div class="val-label">Retail Valuation</div>
            <div class="val-price">${fmtPrice(v.retail_price)}</div>
          </div>
          <div class="val-box">
            <div class="val-label">Trade / Part-Ex</div>
            <div class="val-price trade">${fmtPrice(v.trade_price)}</div>
          </div>
          <div class="val-box">
            <div class="val-label">Retail Rating</div>
            <div class="val-price" style="color:${ratingColor}">${v.retail_rating != null ? v.retail_rating + '/100' : '—'}</div>
          </div>
          <div class="val-box">
            <div class="val-label">Avg Days to Sell</div>
            <div class="val-price">${v.avg_days_to_sell != null ? v.avg_days_to_sell + ' days' : '—'}</div>
          </div>
          <div class="val-box">
            <div class="val-label">Market Condition</div>
            <div style="margin-top:6px">${conditionBadge(v.market_condition) || '—'}</div>
          </div>
          <div class="val-box">
            <div class="val-label">30-day Price Trend</div>
            <div class="val-price" style="color:${trendColor}">${v.price_change_pct != null ? trendSign + v.price_change_pct + '%' : '—'}</div>
          </div>
        </div>

        ${v.retail_rating != null ? `
        <div style="margin-top:16px">
          <div style="display:flex;justify-content:space-between;font-size:.8rem;color:var(--text-muted);margin-bottom:4px">
            <span>Low demand</span><span style="color:${ratingColor};font-weight:700">${v.retail_rating}/100</span><span>High demand</span>
          </div>
          <div class="desirability-bar"><div class="desirability-marker" style="left:${v.retail_rating}%"></div></div>
        </div>` : ''}
      </div>`;
  } catch(e) {
    result.innerHTML = `<div class="card"><div class="empty-state">Could not get valuation for ${reg.toUpperCase()}.<br><small>AutoTrader may have changed their page layout or the plate is unrecognised.</small></div></div>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const vi = document.getElementById('val-reg');
  if (vi) vi.addEventListener('keypress', e => { if (e.key === 'Enter') getValuation(); });
});

// ─── DEALERS ─────────────────────────────────────────────────────────────────

let dealerSoldDays = 7;

function setDealerSoldPeriod(days, btn) {
  dealerSoldDays = days;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadDealerSoldFeed();
}

async function loadDealerSoldFeed() {
  const allSold = await apiFetch(`/dealers/sold/all?days=${dealerSoldDays}`).catch(() => []);
  renderDealerSoldCars(allSold, 'dealer-sold-grid');
}

async function loadDealers() {
  const [dealers, leaderboard, allSold] = await Promise.all([
    apiFetch('/dealers/').catch(() => []),
    apiFetch('/dealers/summary/sold?days=7').catch(() => []),
    apiFetch(`/dealers/sold/all?days=${dealerSoldDays}`).catch(() => []),
  ]);

  // Leaderboard
  document.getElementById('dealer-leaderboard').innerHTML =
    leaderboard.map(d => `
      <tr>
        <td><strong>${d.name}</strong><br><small class="text-muted">${d.location || ''}</small></td>
        <td><span class="badge badge-${d.sold_last_7d > 0 ? 'green' : 'blue'}">${d.sold_last_7d}</span></td>
        <td>${fmt(d.current_stock)}</td>
        <td>${fmtPrice(d.avg_sold_price)}</td>
        <td>${fmtDays(d.avg_days_to_sell)}</td>
      </tr>`).join('') || '<tr><td colspan="5" class="text-muted">No dealers added yet</td></tr>';

  // Dealer cards
  const container = document.getElementById('dealers-list');
  if (!dealers.length) {
    container.innerHTML = '<div class="empty-state">No dealers added. Paste an AutoTrader dealer profile URL above.</div>';
  } else {
    container.innerHTML = `<div class="dealer-grid">${dealers.map(d => `
      <div class="dealer-card">
        <div class="dealer-name">${d.name}</div>
        <div class="dealer-meta">${d.location || ''}</div>
        <div class="dealer-stats">
          <span>${fmt(d.total_stock)} in stock</span>
          <span>Scraped ${timeAgo(d.last_scraped)}</span>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn btn-sm btn-primary" onclick="viewDealerSold(${d.id}, '${d.name}')">View Sold</button>
          <button class="btn btn-sm" onclick="triggerDealerScrape(${d.id})">Scrape Now</button>
          <button class="btn btn-sm btn-danger" onclick="removeDealer(${d.id})">Remove</button>
        </div>
      </div>`).join('')}</div>`;
  }

  // All dealer sold
  renderDealerSoldCars(allSold, 'dealer-sold-grid');
}

function renderDealerSoldCars(cars, containerId) {
  const container = document.getElementById(containerId);
  if (!cars.length) {
    container.innerHTML = '<div class="empty-state">No sold data yet. Add dealers and run a scrape.</div>';
    return;
  }
  container.innerHTML = cars.map(l => `
    <div class="listing-card sold-card">
      <a href="${l.url}" target="_blank" rel="noopener">
        <div class="listing-title">${l.year || ''} ${l.make || ''} ${l.model || ''}</div>
        <div class="listing-price">${fmtPrice(l.price)}</div>
        <div class="listing-meta">
          ${l.colour ? l.colour + ' · ' : ''}${l.fuel_type ? l.fuel_type + ' · ' : ''}${l.mileage ? fmt(l.mileage) + ' mi' : ''}
        </div>
        <div class="listing-meta">${l.dealer_name || ''}</div>
        <div style="margin-top:6px">
          <span class="badge badge-red">Sold</span>
          ${l.days_to_sell != null ? `<span class="badge badge-blue">in ${l.days_to_sell}d</span>` : ''}
          <span class="listing-meta">${timeAgo(l.sold_at)}</span>
        </div>
      </a>
    </div>`).join('');
}

async function viewDealerSold(dealerId, name) {
  const sold = await apiFetch(`/dealers/${dealerId}/sold?days=7`).catch(() => []);
  const container = document.getElementById('dealer-sold-grid');
  if (!sold.length) {
    container.innerHTML = `<div class="empty-state">No sold vehicles recorded for ${name} in last 7 days</div>`;
    return;
  }
  container.innerHTML = sold.map(l => `
    <div class="listing-card sold-card">
      <a href="${l.url}" target="_blank">
        <div class="listing-title">${l.year || ''} ${l.make || ''} ${l.model || ''}</div>
        <div class="listing-price">${fmtPrice(l.price)}</div>
        <div class="listing-meta">${l.colour || ''} · ${l.mileage ? fmt(l.mileage) + ' mi' : ''}</div>
        <div style="margin-top:6px">
          <span class="badge badge-red">Sold</span>
          ${l.days_to_sell != null ? `<span class="badge badge-blue">in ${l.days_to_sell}d</span>` : ''}
        </div>
      </a>
    </div>`).join('');
}

async function addDealer() {
  const name = document.getElementById('d-name').value.trim();
  const url = document.getElementById('d-url').value.trim();
  const location = document.getElementById('d-location').value.trim();
  if (!name || !url) { alert('Name and URL are required'); return; }
  await fetch(`${API}/dealers/`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ name, autotrader_url: url, location: location || null }),
  });
  document.getElementById('d-name').value = '';
  document.getElementById('d-url').value = '';
  document.getElementById('d-location').value = '';
  loadDealers();
}

async function removeDealer(id) {
  if (!confirm('Remove this dealer?')) return;
  await fetch(`${API}/dealers/${id}`, { method: 'DELETE' });
  loadDealers();
}

async function triggerDealerScrape(id) {
  await fetch(`${API}/dealers/${id}/scrape`, { method: 'POST' });
  alert('Scrape started — check back in a few minutes');
}

async function triggerAllDealerScrape() {
  await fetch(`${API}/dealers/scrape/all`, { method: 'POST' });
  alert('Scraping all dealers — check back in a few minutes');
}

// ─── VELOCITY ────────────────────────────────────────────────────────────────

async function loadVelocity() {
  const make = document.getElementById('vel-make').value.trim();
  const model = document.getElementById('vel-model').value.trim();
  const yearMin = document.getElementById('vel-year-min').value;
  const yearMax = document.getElementById('vel-year-max').value;
  const fuel = document.getElementById('vel-fuel').value;
  let params = new URLSearchParams();
  if (make) params.set('make', make);
  if (model) params.set('model', model);
  if (yearMin) params.set('year_min', yearMin);
  if (yearMax) params.set('year_max', yearMax);
  if (fuel) params.set('fuel_type', fuel);

  const [all, fast] = await Promise.all([
    apiFetch(`/analytics/velocity?${params}`).catch(() => []),
    apiFetch('/analytics/fast-sellers?max_days=7').catch(() => []),
  ]);

  document.getElementById('fast-sellers-tbody').innerHTML =
    fast.slice(0, 10).map(m => `<tr>
      <td><strong>${m.make}</strong> ${m.model}</td><td>${m.year_band}</td>
      <td><span class="badge badge-green">${fmtDays(m.avg_days_to_sell)}</span></td>
      <td>${m.pct_sold_under_7_days.toFixed(0)}%</td>
      <td>${fmtPrice(m.avg_price)}</td><td>${m.sample_size}</td>
    </tr>`).join('') || '<tr><td colspan="6" class="text-muted">No sold data yet</td></tr>';

  document.getElementById('velocity-tbody').innerHTML =
    all.map(m => `<tr>
      <td>${m.make}</td><td>${m.model}</td><td>${m.year_band}</td><td>${m.colour || '—'}</td>
      <td><span class="${daysColor(m.avg_days_to_sell)}">${fmtDays(m.avg_days_to_sell)}</span></td>
      <td>${fmtDays(m.median_days_to_sell)}</td>
      <td>${m.pct_sold_under_7_days.toFixed(0)}%</td>
      <td>${fmtPrice(m.avg_price)}</td><td>${m.sample_size}</td>
    </tr>`).join('') || '<tr><td colspan="9" class="text-muted">No sold data yet — run scrapers first</td></tr>';

  renderVelocityChart(all.slice(0, 12));
}

function renderVelocityChart(metrics) {
  const ctx = document.getElementById('velocity-chart');
  if (!ctx) return;
  if (velocityChart) velocityChart.destroy();
  velocityChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: metrics.map(m => `${m.make} ${m.model}`),
      datasets: [{
        label: 'Avg Days to Sell',
        data: metrics.map(m => m.avg_days_to_sell),
        backgroundColor: metrics.map(m =>
          m.avg_days_to_sell <= 7 ? '#22c55e' : m.avg_days_to_sell <= 14 ? '#3b82f6' :
          m.avg_days_to_sell <= 30 ? '#f59e0b' : '#ef4444'),
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false }, title: { display: true, text: 'Avg Days to Sell', color: '#e2e8f0' } },
      scales: {
        x: { ticks: { color: '#8892a4', maxRotation: 45 }, grid: { color: '#2e3346' } },
        y: { ticks: { color: '#8892a4' }, grid: { color: '#2e3346' } },
      },
    },
  });
}

// ─── GEMS ─────────────────────────────────────────────────────────────────────

async function loadGems() {
  const make = document.getElementById('gem-make').value.trim();
  const model = document.getElementById('gem-model').value.trim();
  const yearMin = document.getElementById('gem-year-min').value;
  const yearMax = document.getElementById('gem-year-max').value;
  const discount = document.getElementById('gem-discount').value || 15;
  let params = new URLSearchParams({ discount_pct: discount });
  if (make) params.set('make', make);
  if (model) params.set('model', model);
  if (yearMin) params.set('year_min', yearMin);
  if (yearMax) params.set('year_max', yearMax);

  const gems = await apiFetch(`/analytics/gems?${params}`).catch(() => []);
  const c = document.getElementById('gems-grid');
  c.innerHTML = gems.length
    ? gems.map(g => `<div class="gem-card-item">
        <div class="gem-discount">-${g.discount_pct}% vs market</div>
        <div class="listing-title"><a href="${g.url}" target="_blank">${g.year || ''} ${g.make} ${g.model}</a></div>
        <div class="gem-price">${fmtPrice(g.price)}</div>
        <div class="gem-market">Market median: ${fmtPrice(g.market_median)}</div>
        <div class="listing-meta">${g.colour ? g.colour + ' · ' : ''}${g.mileage ? fmt(g.mileage) + ' mi · ' : ''}${g.days_live != null ? g.days_live + 'd live' : ''} ${sourceBadge(g.source)}</div>
      </div>`).join('')
    : '<div class="empty-state">No gems found yet — needs more scraped data</div>';
}

// ─── PLATE LOOKUP ─────────────────────────────────────────────────────────────

async function lookupPlate() {
  const reg = document.getElementById('plate-input').value.trim().replace(/\s+/g, '');
  if (!reg) { alert('Enter a plate'); return; }
  const result = document.getElementById('plate-result');
  result.classList.remove('hidden');
  result.innerHTML = '<div class="card loading">Looking up ' + reg.toUpperCase() + '...</div>';
  try {
    const p = await apiFetch(`/plate/${encodeURIComponent(reg)}`);
    const score = p.desirability_score;
    const sc = score >= 70 ? '#22c55e' : score >= 40 ? '#f59e0b' : '#ef4444';
    result.innerHTML = `<div class="card">
      <h2>${p.reg} — ${p.make} ${p.model}</h2>
      <div class="plate-grid mt-1">
        ${[['Year', p.year],['Colour', p.colour],['Fuel', p.fuel_type],
           ['Engine', p.engine_cc ? (p.engine_cc/1000).toFixed(1)+'L' : null],
           ['MOT', p.mot_expiry],['Tax Due', p.tax_due]].map(([l,v]) =>
          `<div class="plate-stat"><div class="plate-stat-val">${v||'—'}</div><div class="plate-stat-lbl">${l}</div></div>`).join('')}
      </div>
      <h3 style="margin:16px 0 8px">Market Desirability</h3>
      <div class="desirability-bar"><div class="desirability-marker" style="left:${score}%"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:.75rem;color:var(--text-muted)">
        <span>Low</span><span style="color:${sc};font-weight:700;font-size:1.1rem">${score}/100</span><span>High</span>
      </div>
      <div class="plate-grid mt-2">
        <div class="plate-stat"><div class="plate-stat-val" style="color:${sc}">${score}</div><div class="plate-stat-lbl">Desirability</div></div>
        <div class="plate-stat"><div class="plate-stat-val">${p.estimated_days_to_sell != null ? p.estimated_days_to_sell+'d' : '—'}</div><div class="plate-stat-lbl">Est. Days to Sell</div></div>
        <div class="plate-stat"><div class="plate-stat-val">${fmtPrice(p.median_price)}</div><div class="plate-stat-lbl">Market Median</div></div>
        <div class="plate-stat"><div class="plate-stat-val">${p.active_listings}</div><div class="plate-stat-lbl">Similar Live</div></div>
      </div>
    </div>`;
  } catch(e) {
    result.innerHTML = `<div class="card"><div class="empty-state">No data found for ${reg.toUpperCase()}</div></div>`;
  }
}
document.addEventListener('DOMContentLoaded', () => {
  const pi = document.getElementById('plate-input');
  if (pi) pi.addEventListener('keypress', e => { if (e.key === 'Enter') lookupPlate(); });
});

// ─── ALERTS ──────────────────────────────────────────────────────────────────

async function loadAlerts() {
  const alerts = await apiFetch('/alerts/').catch(() => []);
  const c = document.getElementById('alerts-list');
  c.innerHTML = alerts.length
    ? alerts.map(a => `<div class="alerts-list-item">
        <div class="alert-info"><strong>${a.make || 'Any'} ${a.model || ''}</strong>
          ${[a.year_min && a.year_max ? `${a.year_min}–${a.year_max}` : '',
             a.max_price ? 'max ' + fmtPrice(a.max_price) : '',
             a.colour || ''].filter(Boolean).join(' · ')}
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteAlert(${a.id})">Delete</button>
      </div>`).join('')
    : '<div class="empty-state">No active alerts</div>';
}

async function createAlert() {
  const data = {
    make: document.getElementById('alert-make').value.trim() || null,
    model: document.getElementById('alert-model').value.trim() || null,
    year_min: +document.getElementById('alert-year-min').value || null,
    year_max: +document.getElementById('alert-year-max').value || null,
    max_price: +document.getElementById('alert-max-price').value || null,
    max_mileage: +document.getElementById('alert-max-mileage').value || null,
    colour: document.getElementById('alert-colour').value.trim() || null,
    email: document.getElementById('alert-email').value.trim() || null,
  };
  if (!data.make && !data.model && !data.max_price) { alert('Set at least a make, model, or max price'); return; }
  await fetch(`${API}/alerts/`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  loadAlerts();
}

async function deleteAlert(id) {
  await fetch(`${API}/alerts/${id}`, { method: 'DELETE' });
  loadAlerts();
}

async function checkAlerts() {
  const matches = await apiFetch('/alerts/check').catch(() => []);
  document.getElementById('alert-matches').innerHTML = matches.length
    ? matches.map(m => `<div class="card" style="margin-bottom:10px"><strong>Alert #${m.alert_id}</strong> — ${m.matches.length} matches
        <div class="listing-grid mt-1">${m.matches.map(l => `
          <div class="listing-card"><a href="${l.url}" target="_blank">
            <div class="listing-price">${fmtPrice(l.price)}</div>
            <div class="listing-meta">${l.year || ''} ${l.colour || ''}</div>
          </a></div>`).join('')}</div></div>`).join('')
    : '<div class="text-muted">No matches for current alerts</div>';
}

// ─── LISTINGS ────────────────────────────────────────────────────────────────

async function loadListings() {
  let params = new URLSearchParams({ limit: 48 });
  const make = document.getElementById('lst-make').value.trim();
  const model = document.getElementById('lst-model').value.trim();
  if (make) params.set('make', make);
  if (model) params.set('model', model);
  const yearMin = document.getElementById('lst-year-min').value;
  const yearMax = document.getElementById('lst-year-max').value;
  if (yearMin) params.set('year_min', yearMin);
  if (yearMax) params.set('year_max', yearMax);
  const priceMax = document.getElementById('lst-price-max').value;
  if (priceMax) params.set('price_max', priceMax);
  const colour = document.getElementById('lst-colour').value.trim();
  if (colour) params.set('colour', colour);
  const source = document.getElementById('lst-source').value;
  if (source) params.set('source', source);

  const listings = await apiFetch(`/listings/?${params}`).catch(() => []);
  const c = document.getElementById('listings-grid');
  c.innerHTML = listings.length
    ? listings.map(l => `<div class="listing-card">
        <a href="${l.url}" target="_blank">
          <div class="listing-title">${l.year || ''} ${l.make || ''} ${l.model || ''}</div>
          <div class="listing-price">${fmtPrice(l.price)}</div>
          <div class="listing-meta">${l.colour ? l.colour+' · ':''} ${l.fuel_type ? l.fuel_type+' · ':''} ${l.mileage ? fmt(l.mileage)+' mi':''}</div>
          <div style="margin-top:6px">${sourceBadge(l.source)} ${l.days_live != null && l.days_live <= 7 ? '<span class="listing-badge badge-fast">Fast mover</span>':''}</div>
        </a>
      </div>`).join('')
    : '<div class="empty-state">No listings found</div>';
}

// ─── INIT ─────────────────────────────────────────────────────────────────────

startAutoRefresh();
