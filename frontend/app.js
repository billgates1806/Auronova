/* ==========================================================
   AURONOVA V6 — Live API Integration
   ========================================================== */

const LOOPBACK_HOSTS = {
    '': true,
    'localhost': true,
    '127.0.0.1': true,
    '::1': true,
};
const APP_HOST = window.location.hostname || '127.0.0.1';
const API_HOST = LOOPBACK_HOSTS[APP_HOST] ? '127.0.0.1' : APP_HOST;
const API_BASE = `${window.location.protocol === 'https:' ? 'https' : 'http'}://${API_HOST}:8000`;
const DEFAULT_LOGIN_COPY = 'Read-only access - We never touch your library';
const DEFAULT_TOPBAR_SUB = 'Personal listening console';
const EMPTY_CARD_HTML = (message) => `
    <div class="feat-inner" style="background:#1E2620; display:flex; align-items:center; justify-content:center; padding:40px; border:1px dashed rgba(255,255,255,0.1);">
        <div style="color:#8A847A; font-family:'IBM Plex Mono'; font-size:12px; text-align:center;">
            ${message}
        </div>
    </div>
`;

/* ==========================================================
   AUTHENTICATION & ROUTING
   ========================================================== */
function getToken() { return localStorage.getItem('auronova_token'); }
function setToken(token) { localStorage.setItem('auronova_token', token); }
function allSettledCompat(promises) {
    return Promise.all(promises.map((promise) =>
        Promise.resolve(promise).then(
            function(value) { return { status: 'fulfilled', value: value }; },
            function(reason) { return { status: 'rejected', reason: reason }; }
        )
    ));
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function resetState() {
    state = {
        user: null,
        artists: [],
        genres: [],
        bubble: null,
        dna: null,
        recs: [],
        mood: 'chill',
        dial: 35
    };
}

function setLoginCopy(message = DEFAULT_LOGIN_COPY) {
    const fine = document.querySelector('#screen-login .fine');
    if (fine) fine.textContent = message;
}

function setTopbarSubcopy(message = DEFAULT_TOPBAR_SUB) {
    const sub = document.getElementById('topbar-sub');
    if (sub) sub.textContent = message;
}

function showLoginScreen(message = DEFAULT_LOGIN_COPY) {
    const loginScreen = document.getElementById('screen-login');
    const appScreen = document.getElementById('screen-app');
    if (appScreen) appScreen.classList.remove('active');
    if (loginScreen) loginScreen.classList.add('active');
    setLoginCopy(message);
}

function showAppScreen() {
    const loginScreen = document.getElementById('screen-login');
    const appScreen = document.getElementById('screen-app');
    if (loginScreen) loginScreen.classList.remove('active');
    if (appScreen) appScreen.classList.add('active');
}

function logout(message = 'Spotify session cleared. Connect again to continue.') {
    localStorage.removeItem('auronova_token');
    resetState();
    if (spotifyFallbackTimer) {
        clearTimeout(spotifyFallbackTimer);
        spotifyFallbackTimer = null;
    }
    window.history.replaceState({}, document.title, window.location.pathname);
    setTopbarSubcopy();
    showLoginScreen(message);
}

function getFirstName(displayName) {
    const name = (displayName || 'Listener').trim();
    return name.split(/\s+/)[0] || 'Listener';
}

function getArtistName(artist) {
    return (artist && (artist.artist_name || artist.name)) || 'Unknown Artist';
}

function getArtistGenres(artist) {
    if (artist && Array.isArray(artist.genres) && artist.genres.length > 0) return artist.genres;
    if (artist && artist.genre) return [artist.genre];
    return ['Unknown'];
}

function getArtistHours(artist) {
    const hours = artist && artist.estimated_hours != null
        ? artist.estimated_hours
        : artist && artist.hrs != null
            ? artist.hrs
            : 0;
    return Math.round(hours);
}

function getTrackTitle(track) {
    return (track && (track.title || track.track_name || track.name)) || 'Unknown Track';
}

function getTrackArtist(track) {
    return (track && (track.artist || track.artist_name)) || 'Unknown Artist';
}

function getTrackExplanation(track) {
    return (track && (track.explanation || track.why)) || 'Based on your listening history';
}

function getDayPart() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Morning';
    if (hour < 17) return 'Afternoon';
    if (hour < 21) return 'Evening';
    return 'Night';
}

function updateContextBanner() {
    const chips = document.querySelectorAll('#ctx-banner .ctx-chip');
    if (chips[0]) {
        chips[0].textContent = `◷ ${getDayPart()}`;
    }
    if (chips[1]) {
        const activeMood = document.querySelector('.mood.active');
        chips[1].textContent = activeMood ? activeMood.textContent.trim() : '😌 Chill';
    }
}

function getTopGenreEntry() {
    return state.genres && state.genres.length > 0 ? state.genres[0] : null;
}

function getTopArtistEntry() {
    return state.artists && state.artists.length > 0 ? state.artists[0] : null;
}

function getMoodProfileLabel() {
    const labels = {
        energetic: 'high-drive',
        chill: 'slow-burn',
        focused: 'precision',
        melancholy: 'after-hours',
    };
    return labels[state.mood] || 'balanced';
}

function computeMutationIndex() {
    if (!state.genres || state.genres.length === 0) return '0.00';
    const total = state.genres.reduce((sum, item) => sum + item.hrs, 0) || 1;
    const dominantShare = state.genres[0].hrs / total;
    const diversityBoost = Math.min(state.genres.length / 8, 1);
    return (Math.max(0.12, (1 - dominantShare) * 0.7 + diversityBoost * 0.3) * 100).toFixed(1) + '%';
}

function computeSignalDensity() {
    const artistCount = state.artists ? state.artists.length : 0;
    const genreCount = state.genres ? state.genres.length : 0;
    return `${Math.max(artistCount * 6, genreCount * 9)} nodes`;
}

function formatCompactCount(value) {
    const numeric = Number(value) || 0;
    return numeric.toLocaleString();
}

function inferMoodAxis() {
    const genreNames = (state.genres || []).map((item) => item.name.toLowerCase());
    if (genreNames.some((name) => ['ambient', 'lofi', 'acoustic', 'chill'].some((hint) => name.includes(hint)))) {
        return 'Quiet-focus';
    }
    if (genreNames.some((name) => ['dance', 'house', 'electro', 'hyperpop', 'hip hop'].some((hint) => name.includes(hint)))) {
        return 'High-voltage';
    }
    if (genreNames.some((name) => ['folk', 'sad', 'soul', 'blues', 'indie'].some((hint) => name.includes(hint)))) {
        return 'Warm dusk';
    }
    return 'Wide-spectrum';
}

function renderDNADetails() {
    const topGenre = getTopGenreEntry();
    const topArtist = getTopArtistEntry();
    const regions = state.dna && state.dna.regions ? state.dna.regions : [];
    const primaryRegion = regions[0] && regions[0].name ? regions[0].name : 'No dominant region yet';
    const secondaryRegion = regions[1] && regions[1].name ? regions[1].name : 'Still forming';
    const firstName = getFirstName(state.user && state.user.display_name ? state.user.display_name : 'Listener');
    const genreLabel = topGenre ? topGenre.name : 'Unclassified';
    const artistLabel = topArtist ? getArtistName(topArtist) : 'Unknown';

    const topGenreEl = document.getElementById('dna-top-genre');
    if (topGenreEl) topGenreEl.textContent = genreLabel;

    const topArtistEl = document.getElementById('dna-top-artist');
    if (topArtistEl) topArtistEl.textContent = artistLabel;

    const densityEl = document.getElementById('dna-density');
    if (densityEl) densityEl.textContent = computeSignalDensity();

    const mutationEl = document.getElementById('dna-mutation');
    if (mutationEl) mutationEl.textContent = computeMutationIndex();

    const notesEl = document.getElementById('dna-notes');
    if (notesEl) {
        const secondGenre = state.genres[1] ? state.genres[1].name : null;
        const totalHours = Math.round(state.genres.reduce((sum, item) => sum + item.hrs, 0));
        notesEl.textContent = `${firstName}'s listening map is anchored by ${genreLabel}, with ${artistLabel} acting as the primary gravity well. ${secondGenre ? `${secondGenre} keeps bending the pattern outward, which is why the profile resists collapsing into a single lane.` : 'The signal is still sparse, but the profile is beginning to define a clear center of taste.'} The strongest region currently resolves as ${primaryRegion.toLowerCase()}${secondaryRegion !== 'Still forming' ? `, with ${secondaryRegion.toLowerCase()} trailing behind as the secondary pull.` : '.'} Across roughly ${totalHours} logged hours, the overall posture reads as ${inferMoodAxis().toLowerCase()} with a ${getMoodProfileLabel()} bias.`;
    }

    const primaryEl = document.getElementById('dna-region-primary');
    if (primaryEl) primaryEl.textContent = primaryRegion;

    const secondaryEl = document.getElementById('dna-region-secondary');
    if (secondaryEl) secondaryEl.textContent = secondaryRegion;

    const moodAxisEl = document.getElementById('dna-mood-axis');
    if (moodAxisEl) moodAxisEl.textContent = inferMoodAxis();

    const collectorEl = document.getElementById('dna-collector-note');
    if (collectorEl) {
        collectorEl.textContent = topGenre
            ? `${Math.round(topGenre.hrs)}h logged in ${genreLabel.toLowerCase()}`
            : 'Awaiting more listening history';
    }

    renderDNASequence();
}

function hexToRgba(hex, alpha) {
    const normalized = String(hex || '').replace('#', '').trim();
    if (normalized.length !== 6) {
        return `rgba(107, 143, 113, ${alpha})`;
    }
    const r = parseInt(normalized.slice(0, 2), 16);
    const g = parseInt(normalized.slice(2, 4), 16);
    const b = parseInt(normalized.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function stableUnit(seedA, seedB) {
    const raw = Math.sin(seedA * 127.1 + seedB * 311.7) * 43758.5453123;
    return raw - Math.floor(raw);
}

function getGenreDistribution(sourceGenres) {
    const rows = Array.isArray(sourceGenres) ? sourceGenres : state.genres;
    const enriched = (rows || []).map(function(item, index) {
        const rawValue = item && item.hrs != null
            ? item.hrs
            : item && item.hours != null
                ? item.hours
                : item && item.count != null
                    ? item.count
                    : 0;
        return {
            rank: index + 1,
            name: item && (item.name || item.genre) ? (item.name || item.genre) : 'Unknown',
            hrs: Number(rawValue) || 0,
            pct: item && item.percentage != null ? Number(item.percentage) || 0 : 0,
            color: item && item.color ? item.color : COLORS[index % COLORS.length],
            unit: item && item.count != null && item.hours == null && item.hrs == null ? 'tracks' : 'h'
        };
    }).filter(function(item) {
        return item.hrs > 0 || item.pct > 0;
    });

    const total = enriched.reduce(function(sum, item) { return sum + item.hrs; }, 0) || 1;

    return enriched
        .map(function(item) {
            return {
                rank: item.rank,
                name: item.name,
                hrs: item.hrs,
                pct: item.pct > 0 ? item.pct : (item.hrs / total) * 100,
                color: item.color,
                unit: item.unit
            };
        })
        .sort(function(a, b) { return b.pct - a.pct; })
        .map(function(item, index) {
            item.rank = index + 1;
            return item;
        });
}

function describeGenreSpread(rows) {
    if (!rows || rows.length === 0) return 'Pending';
    if (rows.length === 1) return 'Mono-field';

    const entropy = rows.reduce(function(sum, row) {
        const share = Math.max(row.pct / 100, 0.0001);
        return sum - share * Math.log(share);
    }, 0);
    const normalized = entropy / Math.log(rows.length);

    if (normalized > 0.82) return 'Panoramic';
    if (normalized > 0.62) return 'Balanced';
    if (normalized > 0.4) return 'Focused';
    return 'Narrow-band';
}

function renderGenreLedger(message) {
    const rows = getGenreDistribution();
    const totalHours = Math.round(rows.reduce(function(sum, row) { return sum + row.hrs; }, 0));
    const top = rows[0] || null;

    const totalEl = document.getElementById('genre-total-hours');
    if (totalEl) {
        totalEl.textContent = message || (rows.length > 0 ? `${totalHours}h cross-referenced` : 'Awaiting sync');
    }

    const dominantEl = document.getElementById('genre-dominant');
    if (dominantEl) {
        dominantEl.textContent = top ? `${top.name} · ${Math.round(top.pct)}%` : 'Pending';
    }

    const spreadEl = document.getElementById('genre-spread');
    if (spreadEl) {
        spreadEl.textContent = rows.length > 0 ? `${describeGenreSpread(rows)} · ${rows.length} lanes` : 'Pending';
    }

    const ledgerEl = document.getElementById('genre-ledger');
    if (!ledgerEl) return;

    if (rows.length === 0) {
        ledgerEl.innerHTML = `<div class="genre-ledger-row"><div class="genre-ledger-top"><div class="genre-ledger-main"><span class="genre-ledger-name">${escapeHtml(message || 'No genre data yet')}</span></div></div></div>`;
        return;
    }

    ledgerEl.innerHTML = rows.slice(0, 6).map(function(row) {
        const fillWidth = Math.max(Math.min(row.pct, 100), 6);
        const valueLabel = row.unit === 'tracks'
            ? `${Math.round(row.hrs)} tracks`
            : `${Math.round(row.hrs)}h`;

        return `
            <div class="genre-ledger-row">
                <div class="genre-ledger-top">
                    <div class="genre-ledger-main">
                        <span class="genre-ledger-rank">${String(row.rank).padStart(2, '0')}</span>
                        <span class="genre-ledger-name">${escapeHtml(row.name)}</span>
                    </div>
                    <div class="genre-ledger-values">
                        <span class="genre-ledger-hours">${valueLabel}</span>
                        <span class="genre-ledger-pct">${Math.round(row.pct)}%</span>
                    </div>
                </div>
                <div class="genre-ledger-track">
                    <div class="genre-ledger-fill" style="width:${fillWidth}%; background:linear-gradient(90deg, ${row.color}, ${hexToRgba(row.color, 0.28)}); box-shadow:0 0 18px ${hexToRgba(row.color, 0.2)};"></div>
                </div>
            </div>
        `;
    }).join('');
}

function getDNABreakdownRows() {
    if (state.dna && Array.isArray(state.dna.genre_breakdown) && state.dna.genre_breakdown.length > 0) {
        return getGenreDistribution(state.dna.genre_breakdown);
    }
    return getGenreDistribution();
}

function renderDNASequence() {
    const rows = getDNABreakdownRows();
    const regions = state.dna && state.dna.regions ? state.dna.regions : [];

    const metaEl = document.getElementById('dna-sequence-meta');
    if (metaEl) {
        metaEl.textContent = rows.length > 0 ? `${rows.length} strands · ${regions.length} mapped regions` : 'Awaiting profile data';
    }

    const orbitBiasEl = document.getElementById('dna-orbit-bias');
    if (orbitBiasEl) {
        orbitBiasEl.textContent = regions[0] ? `${regions[0].name} ${Math.round(regions[0].percentage)}%` : 'Pending';
    }

    const rareStrandEl = document.getElementById('dna-rare-strand');
    if (rareStrandEl) {
        const lastRow = rows.length > 0 ? rows[rows.length - 1] : null;
        rareStrandEl.textContent = lastRow ? `${lastRow.name} ${Math.round(lastRow.pct)}%` : 'Pending';
    }

    const regionCountEl = document.getElementById('dna-region-count');
    if (regionCountEl) {
        regionCountEl.textContent = `${regions.length || 0} clusters`;
    }

    const listEl = document.getElementById('dna-sequence');
    if (!listEl) return;

    if (rows.length === 0) {
        listEl.innerHTML = '<div class="dna-sequence-row"><div class="dna-sequence-top"><span class="dna-sequence-name">Awaiting enough listening data to form a sequence.</span></div></div>';
        return;
    }

    listEl.innerHTML = rows.slice(0, 6).map(function(row) {
        const valueLabel = row.unit === 'tracks'
            ? `${Math.round(row.hrs)} tracks`
            : `${Math.round(row.hrs)}h`;
        return `
            <div class="dna-sequence-row">
                <div class="dna-sequence-top">
                    <span class="dna-sequence-name">${escapeHtml(row.name)}</span>
                    <span class="dna-sequence-pct">${Math.round(row.pct)}%</span>
                </div>
                <div class="dna-sequence-track">
                    <div class="dna-sequence-fill" style="width:${Math.max(Math.min(row.pct, 100), 5)}%; background:linear-gradient(90deg, ${row.color}, ${hexToRgba(row.color, 0.24)}); box-shadow:0 0 16px ${hexToRgba(row.color, 0.18)};"></div>
                </div>
                <div class="genre-ledger-copy">${valueLabel}</div>
            </div>
        `;
    }).join('');
}

function isAngleInSector(angle, start, end) {
    const normalized = angle < 0 ? angle + Math.PI * 2 : angle;
    const normStart = start < 0 ? start + Math.PI * 2 : start;
    const normEnd = end < 0 ? end + Math.PI * 2 : end;
    if (normStart <= normEnd) {
        return normalized >= normStart && normalized < normEnd;
    }
    return normalized >= normStart || normalized < normEnd;
}

function drawClusterContour(ctx, clusterPoints, cx, cy, color, seedOffset) {
    if (!clusterPoints || clusterPoints.length < 3) return;

    const polar = clusterPoints.map(function(point) {
        const dx = point.px - cx;
        const dy = point.py - cy;
        return {
            angle: Math.atan2(dy, dx),
            dist: Math.sqrt(dx * dx + dy * dy)
        };
    });

    const averageDist = polar.reduce(function(sum, sample) { return sum + sample.dist; }, 0) / polar.length;
    const contour = [];
    const sectorCount = 18;

    for (let i = 0; i < sectorCount; i += 1) {
        const start = (Math.PI * 2 * i) / sectorCount - Math.PI;
        const end = (Math.PI * 2 * (i + 1)) / sectorCount - Math.PI;
        let sectorMax = 0;

        polar.forEach(function(sample) {
            if (isAngleInSector(sample.angle, start, end)) {
                sectorMax = Math.max(sectorMax, sample.dist);
            }
        });

        const mid = start + ((end - start) / 2);
        const radius = Math.max(
            averageDist * 0.72,
            (sectorMax || averageDist) * (0.92 + stableUnit(seedOffset, i + 1) * 0.28) + 18
        );

        contour.push({
            x: cx + Math.cos(mid) * radius,
            y: cy + Math.sin(mid) * radius
        });
    }

    if (contour.length < 3) return;

    const startMidX = (contour[0].x + contour[contour.length - 1].x) / 2;
    const startMidY = (contour[0].y + contour[contour.length - 1].y) / 2;

    ctx.beginPath();
    ctx.moveTo(startMidX, startMidY);
    for (let i = 0; i < contour.length; i += 1) {
        const current = contour[i];
        const next = contour[(i + 1) % contour.length];
        const midX = (current.x + next.x) / 2;
        const midY = (current.y + next.y) / 2;
        ctx.quadraticCurveTo(current.x, current.y, midX, midY);
    }
    ctx.closePath();

    const maxDist = polar.reduce(function(maxValue, sample) { return Math.max(maxValue, sample.dist); }, averageDist);
    const fill = ctx.createRadialGradient(cx, cy, averageDist * 0.2, cx, cy, maxDist + 28);
    fill.addColorStop(0, hexToRgba(color, 0.34));
    fill.addColorStop(0.65, hexToRgba(color, 0.16));
    fill.addColorStop(1, hexToRgba(color, 0.04));
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.strokeStyle = hexToRgba(color, 0.34);
    ctx.lineWidth = 1.1;
    ctx.stroke();
}

function renderFeatureMessage(message) {
    const featEl = document.getElementById('feat');
    if (featEl) featEl.innerHTML = EMPTY_CARD_HTML(escapeHtml(message).replace(/\n/g, '<br>'));
}

function renderGenrePlaceholder(message) {
    const canvas = document.getElementById('genre-canvas');
    if (!canvas) return;

    const container = canvas.parentElement;
    const width = container ? Math.max(container.clientWidth - 8, 320) : 320;
    const height = Math.min(width * 0.78, 420);
    const ctx = setupHiDPI(canvas, width, height);
    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, '#eff4ef');
    bg.addColorStop(1, '#dde8e3');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
    ctx.font = '12px "IBM Plex Mono"';
    ctx.fillStyle = '#5a615a';
    ctx.textAlign = 'center';
    ctx.fillText(message, width / 2, height / 2);
    renderGenreLedger(message);
}

function drawRoundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
}

async function apiReq(endpoint, method='GET', body=null) {
    const token = getToken();
    if (!token) {
        throw new Error('Missing auth token');
    }

    const opts = { method, headers: { 'Authorization': `Bearer ${token}` } };
    if (body && method !== 'GET') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    let res;
    try {
        res = await fetch(`${API_BASE}${endpoint}`, opts);
    } catch (error) {
        throw new Error(`Network error: ${error.message}`);
    }

    let payload = null;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.indexOf('application/json') !== -1) {
        try {
            payload = await res.json();
        } catch (_error) {
            payload = null;
        }
    }

    if (res.status === 401) {
        logout('Spotify session expired. Connect again to continue.');
        throw new Error('Unauthorized');
    }
    if (!res.ok) {
        throw new Error(payload && payload.detail ? payload.detail : `API error: ${res.status}`);
    }
    return payload;
}

function handleAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    const authError = params.get('auth_error');
    if (token) {
        setToken(token);
        // Clean URL
        window.history.replaceState({}, document.title, window.location.pathname);
        navigateTo('dashboard');
    } else if (authError) {
        window.history.replaceState({}, document.title, window.location.pathname);
        showLoginScreen(authError);
    } else if (getToken()) {
        navigateTo('dashboard');
    } else {
        showLoginScreen();
    }
    updateContextBanner();
}

function loginWithSpotify() {
    setLoginCopy('Redirecting to Spotify...');
    window.location.assign(`${API_BASE}/auth/login?frontend_origin=${encodeURIComponent(window.location.origin)}`);
}

/* ==========================================================
   STATE
   ========================================================== */
let state = {
    user: null,
    artists: [],
    genres: [],
    bubble: null,
    dna: null,
    recs: [],
    mood: 'chill',
    dial: 35
};

const COLORS = ['#6B8F71', '#8B6F5E', '#9B9A5B', '#C4A882', '#7B9E87', '#6B7B5A', '#9B8B6B', '#A89880'];

/* ==========================================================
   HIDPI CANVAS SETUP
   ========================================================== */
function setupHiDPI(canvas, w, h) {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    return ctx;
}

/* ==========================================================
   NAVIGATION
   ========================================================== */
async function navigateTo(pg) {
    if (!getToken()) {
        showLoginScreen('Connect with Spotify to open your dashboard.');
        return;
    }

    showAppScreen();
    document.querySelectorAll('.pg').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('pg-' + pg);
    if (el) { 
        el.classList.add('active'); 
        el.querySelectorAll('.s').forEach(e => { e.style.animation='none'; e.offsetHeight; e.style.animation=''; }); 
    }
    document.querySelectorAll('.dock-btn').forEach(b => b.classList.remove('active'));
    const db = document.querySelector(`[data-p="${pg}"]`);
    if (db) db.classList.add('active');

    // Load data based on page
    if (pg === 'dashboard' && !state.user) {
        await fetchDashboardData();
    }
    if (pg === 'discover' && state.recs.length === 0) {
        await fetchRecommendations();
    }
    if (pg === 'dna' && !state.user) {
        await fetchDashboardData();
    }
    if (pg === 'dna' && !state.dna) {
        await fetchDNA();
    }
}

/* ==========================================================
   DATA FETCHING
   ========================================================== */
async function fetchDashboardData() {
    try {
        const [meRet, artistsRet, genreRet] = await allSettledCompat([
            apiReq('/me/profile'),
            apiReq('/me/top-artists'),
            apiReq('/me/genre-hours')
        ]);

        if (meRet.status !== 'fulfilled' || !meRet.value) {
            throw new Error('Unable to load Spotify profile');
        }

        const me = meRet.value;
        const artists = artistsRet.status === 'fulfilled' ? artistsRet.value : { artists: [] };
        const genRet = genreRet.status === 'fulfilled' ? genreRet.value : { genres: {}, bubble: null };

        state.user = me;
        state.artists = artists.artists || artists || [];
        state.bubble = genRet.bubble || null;
        
        // Format genres and assign colors
        const rawGenres = genRet.genres || {};
        state.genres = Object.entries(rawGenres)
            .map(([name, hrs], i) => ({ name, hrs: Math.round(hrs), color: COLORS[i % COLORS.length] }))
            .sort((a,b) => b.hrs - a.hrs).slice(0, 8); // top 8

        // Update UI
        const firstName = getFirstName(me.display_name);
        const greetingEl = document.getElementById('dashboard-greeting');
        if (greetingEl) greetingEl.innerHTML = `Good ${getDayPart()},<br>${escapeHtml(firstName)}.`;

        const avatarEl = document.querySelector('.avatar');
        if (avatarEl) avatarEl.textContent = firstName.substring(0,2).toUpperCase();
        updateContextBanner();
        
        const artistCount = artists.artist_count != null
            ? Number(artists.artist_count) || 0
            : state.artists.length || 0;
        const totalHours = genRet.total_hours != null
            ? Math.round(Number(genRet.total_hours) || 0)
            : Math.round(state.genres.reduce(function(sum, item) { return sum + item.hrs; }, 0));
        const coreGenreCount = genRet.core_genre_count != null
            ? Number(genRet.core_genre_count) || 0
            : state.genres.length || 0;
        const currentYear = new Date().getFullYear();
        const hoursBasis = genRet.hours_basis === 'live_spotify_estimate'
            ? 'Live Spotify estimate'
            : 'Personal listening console';
        setTopbarSubcopy(totalHours > 0 ? `${hoursBasis} · ${totalHours}h in ${currentYear}` : DEFAULT_TOPBAR_SUB);

        const statsEl = document.getElementById('dashboard-stats');
        if (statsEl) {
            statsEl.innerHTML = `
                <div class="sf"><span class="sf-n">${totalHours}h</span><span class="sf-l">${currentYear} est. hours</span></div>
                <div class="sf"><span class="sf-n">${formatCompactCount(artistCount)}</span><span class="sf-l">artists mapped</span></div>
                <div class="sf"><span class="sf-n">${formatCompactCount(coreGenreCount)}</span><span class="sf-l">core genres</span></div>
            `;
        }

        // Handle Bubble Alert natively
        const bubbleAlert = document.getElementById('bubble-alert');
        if (genRet.bubble && genRet.bubble.bubble) {
            bubbleAlert.style.display = 'flex';
            bubbleAlert.querySelector('.alert-text').innerHTML = `You've been in a <strong>${escapeHtml(genRet.bubble.genre)} bubble</strong> — `;
        } else {
            bubbleAlert.style.display = 'none';
        }

        if (artistCount === 0) {
            renderFeatureMessage('Not enough Spotify history yet.\nStart listening on this account to build your Auronova profile.');
            const gridEl = document.getElementById('a-grid');
            if (gridEl) gridEl.innerHTML = '';
            renderGenrePlaceholder(state.genres.length === 0 ? 'NO GENRE DATA' : 'SYNCING GENRE MAP');
        } else {
            renderFeat();
            renderGrid();
            if (state.genres.length > 0) {
                setTimeout(renderGenre, 150);
            } else {
                renderGenrePlaceholder('GENRE MAP PENDING');
            }
        }

        if (artistsRet.status === 'rejected') {
            console.error('Top artists fetch failed', artistsRet.reason);
        }
        if (genreRet.status === 'rejected') {
            console.error('Genre hours fetch failed', genreRet.reason);
        }
    } catch(e) {
        console.error("Dashboard fetch failed", e);
        setTopbarSubcopy();
        renderFeatureMessage('Unable to load your Spotify profile right now.\nReconnect and try again.');
        renderGenrePlaceholder('CONNECTION REQUIRED');
    }
}

async function fetchRecommendations() {
    try {
        const data = await apiReq(`/recommendations?mood=${state.mood}&discovery=${state.dial}`);
        state.recs = data.recommendations || data; // Handle backend wrapping
        renderCrate();
    } catch(e) {
        console.error("Recs fetch failed", e);
        state.recs = [];
        const el = document.getElementById('crate');
        if (el) {
            el.innerHTML = `<div class="rec" style="grid-column:1/-1;text-align:center;color:#8A847A;">Unable to build recommendations right now.<br>${escapeHtml(e.message || 'Try syncing Spotify and retrying.')}</div>`;
        }
    }
}

async function fetchDNA() {
    try {
        if (!state.user || state.genres.length === 0) {
            await fetchDashboardData();
        }
        const dna = await apiReq('/me/music-dna');
        state.dna = dna;
        setTimeout(renderDNA, 80);
        renderTags();
    } catch(e) {
        console.error("DNA fetch failed", e);
        state.dna = { points: [], regions: [], genre_breakdown: [] };
        renderDNA();
        renderTags();
    }
}

/* ==========================================================
   UI RENDERING — DASHBOARD
   ========================================================== */
function renderFeat() {
    if(!state.artists || state.artists.length===0) return;
    const a = state.artists[0];
    const name = escapeHtml(getArtistName(a));
    const genres = getArtistGenres(a).map(escapeHtml);
    const hrs = getArtistHours(a) || 45;
    const bg = 'linear-gradient(135deg,#3D5C44,#2A3D2E)';
    
    document.getElementById('feat').innerHTML = `
        <div class="feat-inner" style="background:${bg}">
            <div class="feat-av" style="background:rgba(0,0,0,0.15)">${name.substring(0,2).toUpperCase()}</div>
            <div class="feat-info">
                <div class="feat-badge">★ MOST LISTENED</div>
                <div class="feat-name">${name}</div>
                <div class="feat-genre">${genres[0] || 'Unknown'}</div>
                <div class="feat-hrs">${hrs} hours</div>
            </div>
        </div>
    `;
}

function renderGrid() {
    if(!state.artists || state.artists.length < 2) return;
    document.getElementById('a-grid').innerHTML = state.artists.slice(1, 5).map((a, i) => {
        const color = COLORS[(i+1) % COLORS.length];
        const name = escapeHtml(getArtistName(a));
        const genres = getArtistGenres(a).map(escapeHtml);
        const hrs = getArtistHours(a) || (30 - i * 5);
        const clickAttr = a.spotify_url ? `onclick="window.location.href='${a.spotify_url}'"` : '';
        return `
        <div class="ag" style="animation:slideR 0.4s ease ${i*0.06}s both" ${clickAttr}>
            <div class="ag-stripe" style="background:${color}"></div>
            <div class="ag-body">
                <span class="ag-rank">#${i+2}</span>
                <div class="ag-init">${name.substring(0,2).toUpperCase()}</div>
                <div class="ag-name">${name}</div>
                <div class="ag-genre">${genres[0] || 'Artist'}</div>
                <div class="ag-hrs">${hrs}h</div>
            </div>
        </div>
        `;
    }).join('');
}

function renderGenre() {
    if (!state.genres || state.genres.length === 0) {
        renderGenrePlaceholder('NO GENRE DATA');
        return;
    }

    const rows = getGenreDistribution();
    const canvas = document.getElementById('genre-canvas');
    if (!canvas) return;

    const container = canvas.parentElement;
    const W = Math.max(container.clientWidth - 8, 320);
    const H = Math.min(Math.max(rows.length * 52 + 130, 320), 430);
    const ctx = setupHiDPI(canvas, W, H);

    const background = ctx.createLinearGradient(0, 0, W, H);
    background.addColorStop(0, '#eef4ef');
    background.addColorStop(1, '#d7e3dd');
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, W, H);

    for (let y = 0; y < H; y += 12) {
        ctx.strokeStyle = 'rgba(17,17,15,0.025)';
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(W, y + 0.5);
        ctx.stroke();
    }

    const left = 24;
    const labelWidth = 122;
    const right = 22;
    const top = 64;
    const rowHeight = 24;
    const rowGap = 18;
    const chartX = left + labelWidth;
    const chartWidth = W - chartX - right;
    const endpoints = [];

    for (let tick = 0; tick <= 4; tick += 1) {
        const x = chartX + (chartWidth * tick / 4);
        ctx.strokeStyle = 'rgba(17,17,15,0.07)';
        ctx.beginPath();
        ctx.moveTo(x, top - 18);
        ctx.lineTo(x, H - 24);
        ctx.stroke();
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.fillStyle = '#6b726c';
        ctx.textAlign = tick === 4 ? 'right' : 'center';
        ctx.fillText(`${tick * 25}%`, x, top - 26);
    }

    ctx.font = '600 11px "IBM Plex Mono"';
    ctx.fillStyle = '#4b534c';
    ctx.textAlign = 'left';
    ctx.fillText('Hours mapped to your current genre spread', left, 28);
    ctx.font = '400 10px "IBM Plex Mono"';
    ctx.fillStyle = '#7a827a';
    ctx.fillText('share of listening time', left, 44);

    rows.forEach(function(row, index) {
        const y = top + index * (rowHeight + rowGap);
        const barWidth = chartWidth * Math.max(row.pct / 100, 0.03);
        const barY = y + 2;
        const centerY = barY + rowHeight / 2;
        const labelY = y + 11;
        const valueColor = index === 0 ? '#11110f' : '#3e453f';

        ctx.font = '700 11px "Space Grotesk"';
        ctx.fillStyle = '#171713';
        ctx.textAlign = 'left';
        ctx.fillText(row.name, left, labelY);
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.fillStyle = '#737973';
        ctx.fillText(`${Math.round(row.hrs)}h logged`, left, labelY + 15);

        drawRoundedRect(ctx, chartX, barY, chartWidth, rowHeight, 12);
        ctx.fillStyle = 'rgba(255,255,255,0.46)';
        ctx.fill();

        ctx.save();
        ctx.shadowColor = hexToRgba(row.color, 0.26);
        ctx.shadowBlur = 18;
        drawRoundedRect(ctx, chartX, barY, barWidth, rowHeight, 12);
        const fill = ctx.createLinearGradient(chartX, barY, chartX + barWidth, barY);
        fill.addColorStop(0, row.color);
        fill.addColorStop(1, hexToRgba(row.color, 0.32));
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.restore();

        ctx.beginPath();
        ctx.arc(chartX + barWidth, centerY, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = row.color;
        ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.6)';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.font = '700 10px "IBM Plex Mono"';
        ctx.textAlign = 'right';
        ctx.fillStyle = valueColor;
        ctx.fillText(`${Math.round(row.pct)}%`, W - 18, labelY + 1);
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.fillStyle = '#6f756f';
        ctx.fillText(index === 0 ? 'dominant share' : 'active share', W - 18, labelY + 16);

        endpoints.push({ x: chartX + barWidth, y: centerY });
    });

    if (endpoints.length > 1) {
        ctx.beginPath();
        ctx.moveTo(endpoints[0].x, endpoints[0].y);
        for (let i = 1; i < endpoints.length; i += 1) {
            const prev = endpoints[i - 1];
            const current = endpoints[i];
            const controlX = (prev.x + current.x) / 2;
            ctx.quadraticCurveTo(controlX, prev.y, current.x, current.y);
        }
        ctx.strokeStyle = 'rgba(17,17,15,0.12)';
        ctx.lineWidth = 1.4;
        ctx.stroke();
    }

    if (state.bubble && state.bubble.bubble) {
        const chipWidth = 184;
        const chipX = W - chipWidth - 16;
        const chipY = 14;
        drawRoundedRect(ctx, chipX, chipY, chipWidth, 24, 12);
        ctx.fillStyle = 'rgba(17,17,15,0.88)';
        ctx.fill();
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.fillStyle = '#eef4ef';
        ctx.textAlign = 'left';
        ctx.fillText(`bubble detected: ${state.bubble.genre}`, chipX + 12, chipY + 16);
    }

    renderGenreLedger();
}

/* ==========================================================
   UI RENDERING — DISCOVER
   ========================================================== */
const DIAL_HINTS = {
    0:'Songs from your most‑played artists', 20:'Very similar artists', 35:'Related artists in genre family',
    50:'Mix of familiar and discoveries', 65:'Neighboring genres', 80:'Venturing into unknown', 100:'Completely uncharted'
};

async function pickMood(btn) {
    document.querySelectorAll('.mood').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.mood = btn.dataset.m;
    document.querySelector('#ctx-banner .ctx-chip:last-child').textContent = btn.textContent.trim();
    
    // Refresh crate
    const crate = document.getElementById('crate');
    if (crate) { crate.style.opacity = '0.5'; }
    await fetchRecommendations();
    if (crate) { crate.style.opacity = '1'; }
}

let dialTimer;
function moveDial(v) {
    const numericValue = parseInt(v, 10) || 0;
    document.getElementById('dial-val').innerHTML = numericValue + '<span class="dial-pct">%</span>';
    const ks = Object.keys(DIAL_HINTS).map(Number).sort((a,b)=>a-b);
    let cl = ks[0];
    for (const k of ks) if (Math.abs(k-numericValue) <= Math.abs(cl-numericValue)) cl = k;
    document.getElementById('dial-hint').textContent = DIAL_HINTS[cl];

    const settingsDial = document.getElementById('settings-discovery');
    if (settingsDial && settingsDial.value !== String(numericValue)) {
        settingsDial.value = String(numericValue);
    }

    state.dial = numericValue;
    
    // Debounce the API call
    clearTimeout(dialTimer);
    dialTimer = setTimeout(async () => {
        const crate = document.getElementById('crate');
        if (crate) crate.style.opacity = '0.5';
        await fetchRecommendations();
        if (crate) crate.style.opacity = '1';
    }, 500);
}

function renderCrate() {
    const el = document.getElementById('crate');
    if (!state.recs || state.recs.length === 0) {
        el.innerHTML = `<div class="rec" style="grid-column:1/-1;text-align:center;color:#8A847A;">No recommendations found for this mix.<br>Try adjusting the dial or syncing your Spotify.</div>`;
        return;
    }
    
    el.innerHTML = state.recs.map((r, i) => {
        const c = COLORS[i % COLORS.length];
        const title = escapeHtml(getTrackTitle(r));
        const artist = escapeHtml(getTrackArtist(r));
        const expl = escapeHtml(getTrackExplanation(r));
        
        return `
        <div class="rec" style="--rlc:${c}; animation:slideR 0.3s ease ${i*0.04}s both" data-id="${r.id || r.track_id}">
            <div class="rec-top">
                <button class="rec-play" onclick="openTrackFromButton(this);event.stopPropagation()">↗</button>
                <div class="rec-meta">
                    <div class="rec-title">${title}</div>
                    <div class="rec-sub">${artist}</div>
                </div>
            </div>
            <div class="rec-why">${expl}</div>
            <div class="rec-acts">
                <button class="rbtn" onclick="actOnSong(this, '${r.id || r.track_id}', 'love');event.stopPropagation()"><span class="icon">♡</span> Save</button>
                <button class="rbtn" onclick="actOnSong(this, '${r.id || r.track_id}', 'playlist');event.stopPropagation()">＋ Playlist</button>
                <button class="rbtn" onclick="actOnSong(this, '${r.id || r.track_id}', 'skip');event.stopPropagation()">✕ Skip</button>
            </div>
        </div>
        `;
    }).join('');
}

async function actOnSong(btn, trackId, action) {
    if(!trackId) return;
    try {
        if (action === 'love') {
            await apiReq('/spotify/save-track', 'PUT', { track_id: trackId });
            await apiReq('/feedback', 'POST', { track_id: trackId, action: 'love' });
            btn.classList.add('loved'); 
            btn.innerHTML = '<span class="icon">♥</span> Saved';
            
            const sk = btn.parentElement.querySelector('button:last-child');
            if(sk) { sk.classList.remove('skipped'); sk.textContent = '✕ Skip'; btn.closest('.rec').style.opacity = ''; }
        } else if (action === 'playlist') {
            const playlistName = window.prompt('Playlist name', 'Auronova Queue');
            if (!playlistName) return;

            const result = await apiReq('/spotify/create-playlist', 'POST', {
                name: playlistName,
                track_ids: [trackId],
            });
            btn.textContent = '✓ Added';
            if (result && result.playlist_url) {
                window.open(result.playlist_url, '_blank');
            }
        } else if (action === 'skip') {
            await apiReq('/feedback', 'POST', { track_id: trackId, action: 'skip' });
            btn.classList.add('skipped'); 
            btn.textContent = '✕ Skipped';
            btn.closest('.rec').style.opacity = '0.35';
            
            const lv = btn.parentElement.querySelector('button:nth-child(1)');
            if (lv) { lv.classList.remove('loved'); lv.innerHTML = '<span class="icon">♡</span> Save'; }
        }
    } catch (e) { console.error("Action failed", e); }
}

let spotifyFallbackTimer = null;

function getSpotifyTrackUrl(track) {
    const trackId = track && (track.track_id || track.id);
    return trackId ? `https://open.spotify.com/track/${trackId}` : '';
}

function getSpotifyTrackUri(track) {
    const trackId = track && (track.track_id || track.id);
    return trackId ? `spotify:track:${trackId}` : '';
}

function openTrackInSpotify(track) {
    const webUrl = getSpotifyTrackUrl(track);
    const appUrl = getSpotifyTrackUri(track);
    if (!webUrl || !appUrl) return;

    if (spotifyFallbackTimer) {
        clearTimeout(spotifyFallbackTimer);
        spotifyFallbackTimer = null;
    }

    let pageHidden = false;
    const onVisibilityChange = function() {
        if (document.hidden) {
            pageHidden = true;
        }
    };

    document.addEventListener('visibilitychange', onVisibilityChange, { once: true });
    window.location.href = appUrl;

    spotifyFallbackTimer = window.setTimeout(() => {
        if (!pageHidden) {
            window.location.href = webUrl;
        }
    }, 900);
}

function openTrackFromButton(btn) {
    const parent = btn.closest('.rec');
    const trackId = parent.dataset.id;
    const track = state.recs.find(r => r.track_id === trackId || r.id === trackId);
    if (!track) return;

    btn.classList.add('on');
    btn.textContent = '↗';
    openTrackInSpotify(track);
    window.setTimeout(() => {
        btn.classList.remove('on');
        btn.textContent = '↗';
    }, 1200);
}

async function shuffleCrate() {
    await fetchRecommendations();
}

/* ==========================================================
   MUSIC DNA
   ========================================================== */
function renderDNA() {
    const canvas = document.getElementById('dna-canvas');
    if (!canvas) return;

    const container = canvas.parentElement;
    const size = Math.min(container.clientWidth - 8, 640);
    const ctx = setupHiDPI(canvas, size, size);

    if (!state.dna || !state.genres || state.genres.length === 0) {
        ctx.clearRect(0, 0, size, size);
        ctx.font = '12px "IBM Plex Mono"';
        ctx.fillStyle = '#55524C';
        ctx.textAlign = 'center';
        ctx.fillText('AWAITING DATA', size / 2, size / 2);
        renderDNASequence();
        return;
    }

    const S = size;
    const cx = S / 2, cy = S / 2;
    const points = state.dna.points || [];
    const regions = state.dna.regions || [];

    const backdrop = ctx.createLinearGradient(0, 0, S, S);
    backdrop.addColorStop(0, '#f2f5ef');
    backdrop.addColorStop(1, '#e0e8e1');
    ctx.fillStyle = backdrop;
    ctx.fillRect(0, 0, S, S);

    const vignette = ctx.createRadialGradient(cx, cy, S * 0.08, cx, cy, S * 0.62);
    vignette.addColorStop(0, 'rgba(255,255,255,0)');
    vignette.addColorStop(1, 'rgba(17,17,15,0.06)');
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, S, S);

    for (let y = 18; y < S; y += 18) {
        ctx.strokeStyle = 'rgba(17,17,15,0.03)';
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(S, y + 0.5);
        ctx.stroke();
    }
    for (let x = 20; x < S; x += 20) {
        ctx.strokeStyle = 'rgba(17,17,15,0.025)';
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, S);
        ctx.stroke();
    }

    ctx.strokeStyle = 'rgba(17,17,15,0.08)';
    ctx.strokeRect(10.5, 10.5, S - 21, S - 21);
    ctx.strokeRect(20.5, 20.5, S - 41, S - 41);

    const mappedPoints = points.map((point) => ({
        ...point,
        px: 56 + point.x * (S - 112),
        py: 84 + point.y * (S - 168),
    }));

    const clustersByColor = {};
    mappedPoints.forEach(function(point) {
        const key = point.color || '#6B8F71';
        if (!clustersByColor[key]) {
            clustersByColor[key] = [];
        }
        clustersByColor[key].push(point);
    });

    for (let ring = S * 0.15; ring <= S * 0.37; ring += 34) {
        ctx.strokeStyle = 'rgba(17,17,15,0.07)';
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 8]);
        ctx.beginPath();
        ctx.arc(cx, cy, ring, 0, Math.PI * 2);
        ctx.stroke();
    }
    ctx.setLineDash([]);

    ctx.strokeStyle = 'rgba(17,17,15,0.08)';
    ctx.beginPath();
    ctx.moveTo(cx, 26);
    ctx.lineTo(cx, S - 26);
    ctx.moveTo(26, cy);
    ctx.lineTo(S - 26, cy);
    ctx.stroke();

    regions.forEach(function(region, index) {
        const rx = 56 + region.centroid_x * (S - 112);
        const ry = 84 + region.centroid_y * (S - 168);
        const clusterPoints = clustersByColor[region.color || '#6B8F71'] || [];

        if (clusterPoints.length > 2) {
            drawClusterContour(ctx, clusterPoints, rx, ry, region.color || '#6B8F71', index + 1);
        }

        const radius = 36 + region.percentage * 0.95;
        const glow = ctx.createRadialGradient(rx, ry, 0, rx, ry, radius);
        glow.addColorStop(0, hexToRgba(region.color || '#6B8F71', 0.26));
        glow.addColorStop(1, hexToRgba(region.color || '#6B8F71', 0));
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(rx, ry, radius, 0, Math.PI * 2);
        ctx.fill();
    });

    for (let i = 0; i < mappedPoints.length; i += 1) {
        const point = mappedPoints[i];
        const neighbor = mappedPoints[(i + 5) % mappedPoints.length];
        if (!neighbor) continue;
        const dx = point.px - neighbor.px;
        const dy = point.py - neighbor.py;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < S * 0.16 && point.color === neighbor.color) {
            ctx.strokeStyle = hexToRgba(point.color || '#6B8F71', 0.13);
            ctx.lineWidth = 0.9;
            ctx.beginPath();
            ctx.moveTo(point.px, point.py);
            ctx.lineTo(neighbor.px, neighbor.py);
            ctx.stroke();
        }
    }

    mappedPoints.forEach((point) => {
        ctx.beginPath();
        ctx.arc(point.px, point.py, point.cluster === 0 ? 3.6 : 2.35, 0, Math.PI * 2);
        ctx.fillStyle = point.color || '#6B8F71';
        ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.6)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
    });

    const n = Math.min(state.genres.length, 8);
    const step = (Math.PI * 2) / Math.max(n, 1);
    const maxHrs = Math.max(...state.genres.map(g => g.hrs), 1);

    state.genres.slice(0, n).forEach((g, i) => {
        const a = step * i - Math.PI / 2;
        const ring = S * 0.33 + (g.hrs / maxHrs) * 38;
        ctx.strokeStyle = hexToRgba(g.color, 0.42);
        ctx.lineWidth = 1.2;
        ctx.setLineDash([10, 8]);
        ctx.beginPath();
        ctx.arc(cx, cy, ring, a - 0.22, a + 0.22);
        ctx.stroke();
        ctx.setLineDash([]);

        const lx = cx + Math.cos(a) * (ring + 26);
        const ly = cy + Math.sin(a) * (ring + 26);
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.fillStyle = '#505652';
        ctx.textAlign = 'center';
        ctx.fillText(g.name.toUpperCase(), lx, ly);
    });

    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, 70);
    core.addColorStop(0, 'rgba(17,17,15,0.95)');
    core.addColorStop(0.6, 'rgba(17,17,15,0.35)');
    core.addColorStop(1, 'rgba(17,17,15,0)');
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, 72, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.arc(cx, cy, 30, 0, Math.PI * 2);
    ctx.fillStyle = '#f4f4ee';
    ctx.fill();
    ctx.strokeStyle = 'rgba(17,17,15,0.1)';
    ctx.stroke();

    const topGenre = getTopGenreEntry();
    ctx.textAlign = 'center';
    ctx.fillStyle = '#11110f';
    ctx.font = '700 14px "Space Grotesk"';
    ctx.fillText(getFirstName(state.user && state.user.display_name ? state.user.display_name : 'Listener'), cx, cy - 4);
    ctx.fillStyle = '#6f756f';
    ctx.font = '500 8px "IBM Plex Mono"';
    ctx.fillText(topGenre ? topGenre.name.toUpperCase() : 'SIGNAL', cx, cy + 14);

    regions.slice(0, 3).forEach((region, index) => {
        const rx = S - 194;
        const ry = 30 + index * 38;
        ctx.fillStyle = 'rgba(255,255,255,0.82)';
        drawRoundedRect(ctx, rx, ry, 164, 28, 10);
        ctx.fill();
        ctx.fillStyle = region.color || '#6B8F71';
        drawRoundedRect(ctx, rx + 10, ry + 18, Math.max(region.percentage, 10), 4, 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(rx + 10, ry + 9, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = region.color || '#6B8F71';
        ctx.fill();
        ctx.fillStyle = '#313530';
        ctx.font = '500 8px "IBM Plex Mono"';
        ctx.textAlign = 'left';
        ctx.fillText(region.name.toUpperCase(), rx + 20, ry + 12);
        ctx.textAlign = 'right';
        ctx.fillText(`${Math.round(region.percentage)}%`, rx + 150, ry + 12);
    });

    for (let i = 0; i < 45; i++) {
        const a = stableUnit(points.length + 17, i + 1) * Math.PI * 2;
        const d = 54 + stableUnit(i + 21, points.length + 9) * (S * 0.34);
        const x = cx + Math.cos(a) * d;
        const y = cy + Math.sin(a) * d;
        ctx.beginPath();
        ctx.arc(x, y, 0.7 + stableUnit(i + 7, 42) * 1.4, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(17,17,15,${0.04 + stableUnit(91, i + 5) * 0.08})`;
        ctx.fill();
    }

    renderDNADetails();
}

function renderTags() {
    if (!state.dna) return;

    const tags = state.dna.genre_tags || (state.dna.genre_breakdown || []).map((item) => item.genre);
    if (tags.length === 0) {
        document.getElementById('dna-tags').innerHTML = '<span style="color:#55524C; font-family:\'IBM Plex Mono\'; font-size:11px;">NO TAGS YET</span>';
    } else {
        document.getElementById('dna-tags').innerHTML = tags.slice(0, 6).map((g, i) => {
            const color = COLORS[i % COLORS.length];
            return `<span class="dna-tag" style="color:${color};background:${color}14;border:1px solid ${color}26">${g}</span>`;
        }).join('');
    }
    
    const regions = state.dna.taste_regions || (state.dna.regions || []).reduce(function(acc, region) {
        acc[region.name] = region.percentage;
        return acc;
    }, {});
    if (Object.keys(regions).length === 0) {
        document.querySelector('.region-list').innerHTML = '';
    } else {
        document.querySelector('.region-list').innerHTML = Object.entries(regions)
            .slice(0, 4)
            .map(([region, pct], i) => {
                const color = COLORS[i % COLORS.length];
                return `<span class="rl" style="--rc:${color}"><b class="rdot"></b>${region} — ${Math.round(pct)}%</span>`;
            }).join('');
    }

    const firstName = getFirstName(state.user && state.user.display_name ? state.user.display_name : 'Your');
    const totalHrs = state.genres.reduce((sum, g) => sum + g.hrs, 0);
    document.querySelector('.dna-wm-name').textContent = `${firstName}'s Music DNA`;
    document.querySelector('.dna-wm-sub').textContent = `${totalHrs} hrs · ${state.artists.length * 10}+ artists · ${state.genres.length} genres`;
    renderDNADetails();
}

function shareDNA() {
    const btn = document.querySelector('.btn-share');
    const orig = btn.textContent;
    const notesEl = document.getElementById('dna-notes');
    const summary = notesEl ? notesEl.textContent : 'Auronova Music DNA';

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(summary).then(() => {
            btn.textContent = '✓ Dossier copied';
            setTimeout(() => { btn.textContent = orig; }, 1800);
        }).catch(() => {
            btn.textContent = '✓ Ready to share';
            setTimeout(() => { btn.textContent = orig; }, 1800);
        });
    } else {
        btn.textContent = '✓ Ready to share';
        setTimeout(() => { btn.textContent = orig; }, 1800);
    }
}

/* ==========================================================
   SETTINGS / SYNC
   ========================================================== */
async function syncSpotify(btn) {
    const orig = btn.textContent;
    const syncCopy = document.getElementById('sync-status-copy');
    btn.textContent = 'Syncing...';
    btn.disabled = true;
    if (syncCopy) syncCopy.textContent = 'Refreshing your Spotify library and recommendation graph';
    try {
        await apiReq('/spotify/sync', 'POST');
        btn.textContent = 'Sync Complete!';
        if (syncCopy) syncCopy.textContent = 'Latest Spotify signals loaded successfully';
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
        // Refresh dashboard
        state.user = null;
        state.artists = [];
        state.genres = [];
        state.recs = [];
        state.dna = null;
        await fetchDashboardData();
    } catch(e) {
        console.error("Sync failed", e);
        btn.textContent = 'Error';
        if (syncCopy) syncCopy.textContent = e.message || 'Sync failed';
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
    }
}

/* ==========================================================
   INIT
   ========================================================== */
window.onload = function() {
    resetState();
    handleAuthCallback();
    updateContextBanner();
    
    // Attach event listeners dynamically to index.html buttons
    const loginBtn = document.querySelector('#screen-login .btn-cta');
    if (loginBtn) {
        loginBtn.onclick = loginWithSpotify;
    }
    
    const syncBtn = document.getElementById('settings-sync-btn');
    if (syncBtn) {
        syncBtn.onclick = function() { syncSpotify(this); }
    }

    const settingsDiscovery = document.getElementById('settings-discovery');
    if (settingsDiscovery) {
        settingsDiscovery.value = String(state.dial);
        settingsDiscovery.oninput = function() { moveDial(this.value); };
    }

    const logoutBtn = document.getElementById('settings-logout-btn');
    if (logoutBtn) {
        logoutBtn.onclick = logout;
    }
};
