const API = '/api';
let velocityChart = null;

// ─── NAVIGATION ─────────────────────────────────────────────────────────────

document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const section = link.dataset.section;
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    link.classList.add('active');
    document.getElementById(`section-${section}`).classList.add('active');
    if (section === 'velocity') loadVelocity();
    if (section === 'gems') loadGems();
    if (section === 'alerts') loadAlerts();
    if (section === 'listings') loadListings();
  });
});

// ─── UTILITIES ──────────────────────────────────────────────────────────────

async function apiFetch(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function fmt(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-GB');
}

function fmtPrice(p) {
  if (p == null) return '—';
  return '£' + Number(p).toLocaleString('en-GB');
}

function fmtDays(d) {
  if (d == null) return '—';
  return d.toFixed(1) + 'd';
}

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

// ─── DASHBOARD ──────────────────────────────────────────────────────────────

async function loadDashboard() {
  try {
    const [signals, spikes, recent, scraperStatus, gems] = await Promise.all([
      apiFetch('/analytics/demand'),
      apiFetch('/analytics/demand/spikes'),
      apiFetch('/listings/recent?hours=24&limit=12'),
      apiFetch('/scraper/status'),
      apiFetch('/analytics/gems?discount_pct=15&limit=20').catch(() => []),
    ]);

    // Stats
    const totalActive = signals.reduce((s, x) => s + x.active_count, 0);
    const totalNew = signals.reduce((s, x) => s + x.new_last_24h, 0);
    const totalSold7 = signals.reduce((s, x) => s + x.sold_last_7d, 0);
    const allDts = signals.filter(s => s.avg_days_to_sell).map(s => s.avg_days_to_sell);
    const avgDts = allDts.length ? (allDts.reduce((a, b) => a + b, 0) / allDts.length).toFixed(1) : '—';

    document.getElementById('stat-active').textContent = fmt(totalActive);
    document.getElementById('stat-new24').textContent = fmt(totalNew);
    document.getElementById('stat-sold7').textContent = fmt(totalSold7);
    document.getElementById('stat-avg-dts').textContent = avgDts !== '—' ? avgDts + 'd' : '—';
    document.getElementById('stat-spikes').textContent = spikes.length;
    document.getElementById('stat-gems').textContent = gems.length || '0';

    // Demand table (top 15)
    const tbody = document.getElementById('demand-tbody');
    tbody.innerHTML = signals.slice(0, 15).map(s => `
      <tr>
        <td><strong>${s.make}</strong> ${s.model}</td>
        <td><span class="badge badge-blue">${s.demand_score}</span></td>
        <td><span class="${daysColor(s.avg_days_to_sell)}">${fmtDays(s.avg_days_to_sell)}</span></td>
        <td>${s.active_count}</td>
        <td>${s.spike_detected ? '🔥' : ''}</td>
      </tr>`).join('');

    // Spikes
    const spikesEl = document.getElementById('spikes-list');
    if (spikes.length === 0) {
      spikesEl.innerHTML = '<div class="empty-state">No spikes detected</div>';
    } else {
      spikesEl.innerHTML = spikes.slice(0, 8).map(s => `
        <div class="spike-item">
          <div class="spike-name">${s.make} ${s.model}</div>
          <div class="spike-meta">${s.new_last_7d} new listings this week · ${fmtDays(s.avg_days_to_sell)} avg to sell · Score ${s.demand_score}</div>
        </div>`).join('');
    }

    // Recent listings
    renderListingCards(recent, 'recent-listings');

    // Scraper status
    const stbody = document.getElementById('scraper-tbody');
    stbody.innerHTML = scraperStatus.slice(0, 6).map(r => `
      <tr>
        <td>${r.source}</td>
        <td>${r.started_at ? new Date(r.started_at).toLocaleString('en-GB') : '—'}</td>
        <td>${fmt(r.listings_new)}</td>
        <td>${fmt(r.listings_sold)}</td>
        <td><span class="badge ${r.success ? 'badge-green' : 'badge-red'}">${r.success ? 'OK' : 'Error'}</span></td>
      </tr>`).join('');

    document.getElementById('db-status').textContent = 'Live';
    document.getElementById('db-status').classList.add('ok');

  } catch (e) {
    console.error('Dashboard load error:', e);
    document.getElementById('db-status').textContent = 'Error';
  }
}

async function triggerScrape(source) {
  try {
    await fetch(`${API}/scraper/run/${source}`, { method: 'POST' });
    alert(`Scrape started for: ${source}. Check status in a few minutes.`);
  } catch (e) {
    alert('Failed to trigger scrape: ' + e.message);
  }
}

// ─── VELOCITY ───────────────────────────────────────────────────────────────

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

  const [allMetrics, fastSellers] = await Promise.all([
    apiFetch(`/analytics/velocity?${params}`).catch(() => []),
    apiFetch(`/analytics/fast-sellers?max_days=7`).catch(() => []),
  ]);

  // Fast sellers table
  document.getElementById('fast-sellers-tbody').innerHTML =
    fastSellers.slice(0, 10).map(m => `
      <tr>
        <td><strong>${m.make}</strong> ${m.model}</td>
        <td>${m.year_band}</td>
        <td><span class="badge badge-green">${fmtDays(m.avg_days_to_sell)}</span></td>
        <td>${m.pct_sold_under_7_days.toFixed(0)}%</td>
        <td>${fmtPrice(m.avg_price)}</td>
        <td>${m.sample_size}</td>
      </tr>`).join('') || '<tr><td colspan="6" class="text-muted">Not enough sold data yet</td></tr>';

  // Full velocity table
  document.getElementById('velocity-tbody').innerHTML =
    allMetrics.map(m => `
      <tr>
        <td>${m.make}</td>
        <td>${m.model}</td>
        <td>${m.year_band}</td>
        <td>${m.colour || '—'}</td>
        <td><span class="${daysColor(m.avg_days_to_sell)}">${fmtDays(m.avg_days_to_sell)}</span></td>
        <td>${fmtDays(m.median_days_to_sell)}</td>
        <td>${m.pct_sold_under_7_days.toFixed(0)}%</td>
        <td>${fmtPrice(m.avg_price)}</td>
        <td>${m.sample_size}</td>
      </tr>`).join('') || '<tr><td colspan="9" class="text-muted">No sold data yet — run scrapers to collect data</td></tr>';

  // Chart
  renderVelocityChart(allMetrics.slice(0, 12));
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
          m.avg_days_to_sell <= 7 ? '#22c55e' :
          m.avg_days_to_sell <= 14 ? '#3b82f6' :
          m.avg_days_to_sell <= 30 ? '#f59e0b' : '#ef4444'
        ),
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: { display: true, text: 'Avg Days to Sell by Model', color: '#e2e8f0' },
      },
      scales: {
        x: { ticks: { color: '#8892a4', maxRotation: 45 }, grid: { color: '#2e3346' } },
        y: { ticks: { color: '#8892a4' }, grid: { color: '#2e3346' } },
      },
    },
  });
}

// ─── GEMS ────────────────────────────────────────────────────────────────────

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
  const container = document.getElementById('gems-grid');

  if (!gems.length) {
    container.innerHTML = '<div class="empty-state">No gems found at this discount level yet.<br>Run scrapers to collect more data.</div>';
    return;
  }

  container.innerHTML = gems.map(g => `
    <div class="gem-card-item">
      <div class="gem-discount">-${g.discount_pct}% vs market</div>
      <div class="listing-title"><a href="${g.url}" target="_blank">${g.year || ''} ${g.make} ${g.model}</a></div>
      <div class="gem-price">${fmtPrice(g.price)}</div>
      <div class="gem-market">Market median: ${fmtPrice(g.market_median)}</div>
      <div class="listing-meta">
        ${g.colour ? g.colour + ' · ' : ''}${g.mileage ? fmt(g.mileage) + ' mi · ' : ''}
        Listed ${g.days_live != null ? g.days_live + 'd ago' : ''}
        ${sourceBadge(g.source)}
      </div>
    </div>`).join('');
}

// ─── PLATE LOOKUP ────────────────────────────────────────────────────────────

async function lookupPlate() {
  const reg = document.getElementById('plate-input').value.trim().replace(/\s+/g, '');
  if (!reg) { alert('Enter a registration plate'); return; }

  const result = document.getElementById('plate-result');
  result.classList.remove('hidden');
  result.innerHTML = '<div class="loading">Looking up ' + reg.toUpperCase() + '...</div>';

  try {
    const p = await apiFetch(`/plate/${encodeURIComponent(reg)}`);
    const score = p.desirability_score;
    const scoreColor = score >= 70 ? '#22c55e' : score >= 40 ? '#f59e0b' : '#ef4444';

    result.innerHTML = `
      <div class="card">
        <h2 class="card-title">${p.reg} — ${p.make} ${p.model}</h2>
        <div class="plate-grid">
          <div class="plate-stat">
            <div class="plate-stat-val">${p.year || '—'}</div>
            <div class="plate-stat-lbl">Year</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.colour || '—'}</div>
            <div class="plate-stat-lbl">Colour</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.fuel_type || '—'}</div>
            <div class="plate-stat-lbl">Fuel</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.engine_cc ? (p.engine_cc/1000).toFixed(1)+'L' : '—'}</div>
            <div class="plate-stat-lbl">Engine</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.mot_expiry || '—'}</div>
            <div class="plate-stat-lbl">MOT Expiry</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.tax_due || '—'}</div>
            <div class="plate-stat-lbl">Tax Due</div>
          </div>
        </div>

        <h3 style="margin:20px 0 10px">Market Desirability</h3>
        <div class="desirability-bar">
          <div class="desirability-marker" style="left:${score}%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:.75rem;color:var(--text-muted)">
          <span>Low</span><span style="color:${scoreColor};font-weight:700;font-size:1.1rem">${score}/100</span><span>High</span>
        </div>

        <div class="plate-grid" style="margin-top:16px">
          <div class="plate-stat">
            <div class="plate-stat-val" style="color:${scoreColor}">${score}</div>
            <div class="plate-stat-lbl">Desirability Score</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.estimated_days_to_sell != null ? p.estimated_days_to_sell + 'd' : '—'}</div>
            <div class="plate-stat-lbl">Est. Days to Sell</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${fmtPrice(p.median_price)}</div>
            <div class="plate-stat-lbl">Market Median</div>
          </div>
          <div class="plate-stat">
            <div class="plate-stat-val">${p.active_listings}</div>
            <div class="plate-stat-lbl">Similar Listed Now</div>
          </div>
        </div>

        ${p.comparable_listings.length ? `
        <h3 style="margin:20px 0 10px">Comparable Listings</h3>
        <div class="listing-grid">
          ${p.comparable_listings.map(l => `
            <div class="listing-card">
              <a href="${l.url}" target="_blank">
                <div class="listing-title">${l.year || ''} ${p.make} ${p.model}</div>
                <div class="listing-price">${fmtPrice(l.price)}</div>
                <div class="listing-meta">${l.colour || ''} · ${l.mileage ? fmt(l.mileage)+' mi' : ''}</div>
                ${sourceBadge(l.source)}
              </a>
            </div>`).join('')}
        </div>` : ''}
      </div>`;

  } catch (e) {
    result.innerHTML = `<div class="card"><div class="empty-state">
      No data found for ${reg.toUpperCase()}.<br>
      <small>DVLA key may not be set, or this plate hasn't been scraped yet.</small>
    </div></div>`;
  }
}

document.getElementById('plate-input').addEventListener('keypress', e => {
  if (e.key === 'Enter') lookupPlate();
});

// ─── ALERTS ──────────────────────────────────────────────────────────────────

async function loadAlerts() {
  const alerts = await apiFetch('/alerts/').catch(() => []);
  const container = document.getElementById('alerts-list');
  if (!alerts.length) {
    container.innerHTML = '<div class="empty-state">No active alerts</div>';
    return;
  }
  container.innerHTML = alerts.map(a => `
    <div class="alerts-list-item">
      <div class="alert-info">
        <strong>${a.make || 'Any'} ${a.model || ''}</strong>
        ${[
          a.year_min && a.year_max ? `${a.year_min}–${a.year_max}` : a.year_min ? `from ${a.year_min}` : '',
          a.max_price ? `max ${fmtPrice(a.max_price)}` : '',
          a.max_mileage ? `max ${fmt(a.max_mileage)}mi` : '',
          a.colour || '',
        ].filter(Boolean).join(' · ')}
        ${a.email ? `<br><small class="text-muted">${a.email}</small>` : ''}
      </div>
      <button class="btn btn-danger" style="font-size:.75rem;padding:4px 10px" onclick="deleteAlert(${a.id})">Delete</button>
    </div>`).join('');
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
  if (!data.make && !data.model && !data.max_price) {
    alert('Set at least a make, model, or max price');
    return;
  }
  await fetch(`${API}/alerts/`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  loadAlerts();
}

async function deleteAlert(id) {
  await fetch(`${API}/alerts/${id}`, { method: 'DELETE' });
  loadAlerts();
}

async function checkAlerts() {
  const matches = await apiFetch('/alerts/check').catch(() => []);
  const container = document.getElementById('alert-matches');
  if (!matches.length) {
    container.innerHTML = '<div class="text-muted">No matches for current alerts</div>';
    return;
  }
  container.innerHTML = matches.map(m => `
    <div class="card" style="margin-bottom:12px">
      <strong>Alert #${m.alert_id}</strong> — ${m.matches.length} match(es)
      <div class="listing-grid mt-1">
        ${m.matches.map(l => `
          <div class="listing-card">
            <a href="${l.url}" target="_blank">
              <div class="listing-title">${l.year || ''}</div>
              <div class="listing-price">${fmtPrice(l.price)}</div>
              <div class="listing-meta">${l.colour || ''}</div>
            </a>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

// ─── LISTINGS ────────────────────────────────────────────────────────────────

async function loadListings() {
  let params = new URLSearchParams({ limit: 48 });
  const make = document.getElementById('lst-make').value.trim();
  const model = document.getElementById('lst-model').value.trim();
  const yearMin = document.getElementById('lst-year-min').value;
  const yearMax = document.getElementById('lst-year-max').value;
  const priceMax = document.getElementById('lst-price-max').value;
  const colour = document.getElementById('lst-colour').value.trim();
  const source = document.getElementById('lst-source').value;

  if (make) params.set('make', make);
  if (model) params.set('model', model);
  if (yearMin) params.set('year_min', yearMin);
  if (yearMax) params.set('year_max', yearMax);
  if (priceMax) params.set('price_max', priceMax);
  if (colour) params.set('colour', colour);
  if (source) params.set('source', source);

  const listings = await apiFetch(`/listings/?${params}`).catch(() => []);
  renderListingCards(listings, 'listings-grid');
}

function renderListingCards(listings, containerId) {
  const container = document.getElementById(containerId);
  if (!listings.length) {
    container.innerHTML = '<div class="empty-state">No listings found</div>';
    return;
  }
  container.innerHTML = listings.map(l => `
    <div class="listing-card">
      <a href="${l.url}" target="_blank" rel="noopener">
        <div class="listing-title">${l.year || ''} ${l.make || ''} ${l.model || ''} ${l.variant ? '<small>'+l.variant+'</small>' : ''}</div>
        <div class="listing-price">${fmtPrice(l.price)}</div>
        <div class="listing-meta">
          ${l.colour ? l.colour + ' · ' : ''}
          ${l.fuel_type ? l.fuel_type + ' · ' : ''}
          ${l.mileage ? fmt(l.mileage) + ' mi · ' : ''}
          ${l.location || ''}
        </div>
        <div style="margin-top:6px">
          ${sourceBadge(l.source)}
          ${l.days_live != null && l.days_live <= 7 ? '<span class="listing-badge badge-fast">Fast mover</span>' : ''}
          ${l.days_live != null ? '<span class="listing-meta">' + l.days_live + 'd live</span>' : ''}
        </div>
      </a>
    </div>`).join('');
}

// ─── INIT ────────────────────────────────────────────────────────────────────

loadDashboard();
