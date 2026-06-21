const API_BASE_URL = window.location.origin;

// Client-Side Session Cache to optimize autocomplete and save API requests
const clientCache = {
    cache: {},
    get(key) {
        const entry = this.cache[key];
        if (entry && (Date.now() - entry.timestamp < 60000)) { // 1-minute client TTL
            return entry.data;
        }
        return null;
    },
    set(key, data) {
        this.cache[key] = {
            data: data,
            timestamp: Date.now()
        };
    }
};

// DOM Elements
const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const loader = document.getElementById("loader");
const dropdown = document.getElementById("suggestions-dropdown");
const list = document.getElementById("suggestions-list");
const apiResponse = document.getElementById("api-response");
const apiResponseText = document.getElementById("api-response-text");
const trendingList = document.getElementById("trending-list");
const refreshMetricsBtn = document.getElementById("refresh-metrics-btn");
const trendingModeToggle = document.getElementById("trending-mode-toggle");
const themeToggleBtn = document.getElementById("theme-toggle-btn");

// Diagnostics Elements
const statLatency = document.getElementById("stat-latency");
const statCache = document.getElementById("stat-cache");
const statNode = document.getElementById("stat-node");
const statCircuit = document.getElementById("stat-circuit");

// Comparison Dashboard Elements
const comparisonCard = document.getElementById("comparison-card");
const comparisonPrefixVal = document.getElementById("comparison-prefix-val");
const comparisonBasicList = document.getElementById("comparison-basic-list");
const comparisonTrendingList = document.getElementById("comparison-trending-list");

// Ingestion Metrics Elements
const metricWritesSaved = document.getElementById("metric-writes-saved");
const metricQueueSize = document.getElementById("metric-queue-size");
const metricRecovered = document.getElementById("metric-recovered");

let debounceTimeout = null;
let selectedIndex = -1;
let currentSuggestions = [];

// Event Listeners
searchInput.addEventListener("input", handleInput);
searchInput.addEventListener("keydown", handleKeydown);
searchBtn.addEventListener("click", submitSearch);
refreshMetricsBtn.addEventListener("click", fetchMetrics);
trendingModeToggle.addEventListener("change", handleInput); // Re-fetch on mode toggles

// Event delegation on suggestions dropdown list click
list.addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (li) {
        const query = li.getAttribute("data-query");
        if (query) {
            selectQuery(query);
        }
    }
});

// Floating Light/Dark Mode theme toggle
themeToggleBtn.addEventListener("click", () => {
    document.body.classList.toggle("light-theme");
    const icon = themeToggleBtn.querySelector("i");
    if (document.body.classList.contains("light-theme")) {
        icon.className = "fa-solid fa-sun";
    } else {
        icon.className = "fa-solid fa-moon";
    }
});

// Click outside dropdown to close
document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-card")) {
        closeDropdown();
    }
});

// Periodic Updates (Lazy load trending lists)
fetchTrending();
fetchMetrics();
setInterval(fetchTrending, 15000); // Poll trending list every 15s to keep list updated
setInterval(fetchMetrics, 5000); // Poll metrics list every 5s

// Debounced typing handler
function handleInput() {
    clearTimeout(debounceTimeout);
    const query = searchInput.value.trim();

    if (!query) {
        closeDropdown();
        resetStats();
        comparisonCard.classList.add("hidden");
        return;
    }

    loader.classList.remove("hidden");

    debounceTimeout = setTimeout(async () => {
        const isTrendingMode = trendingModeToggle.checked;
        const clientCacheKey = `${isTrendingMode ? 'trending' : 'basic'}:${query.toLowerCase()}`;

        // 1. Client-Side Cache Check
        const cachedPayload = clientCache.get(clientCacheKey);
        if (cachedPayload) {
            currentSuggestions = cachedPayload.details || [];
            renderSuggestions(currentSuggestions, query);
            updateDiagnostics({
                latency_ms: "0.00",
                source: "client_cache",
                cache_node: cachedPayload.cache_node || "none",
                circuit_state: cachedPayload.circuit_state || "CLOSED"
            });
            loader.classList.add("hidden");
            fetchRankingsComparison(query);
            return;
        }

        try {
            // 2. Cache Miss: Fetch suggestions from backend
            const suggestUrl = `${API_BASE_URL}/suggest?q=${encodeURIComponent(query)}&trending=${isTrendingMode}`;
            const startFetchTime = Date.now();
            const res = await fetch(suggestUrl);
            const data = await res.json();
            
            // Set client cache
            clientCache.set(clientCacheKey, data);

            currentSuggestions = data.details || [];
            renderSuggestions(currentSuggestions, query);
            updateDiagnostics(data);

            // Fetch comparisons side-by-side
            fetchRankingsComparison(query);

        } catch (err) {
            console.error("Error fetching suggestions:", err);
            updateDiagnostics({
                latency_ms: "N/A",
                source: "ERROR",
                cache_node: "N/A",
                circuit_state: "OPEN"
            });
        } finally {
            loader.classList.add("hidden");
        }
    }, 350); // 350ms debounce
}

// Bolds the matching query prefix
function highlightMatch(text, prefix) {
    if (!prefix) return escapeHTML(text);
    const escapedPrefix = prefix.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'); // regex escape
    const regex = new RegExp(`^(${escapedPrefix})`, 'i');
    return text.replace(regex, '<strong>$1</strong>');
}

// Render autocomplete dropdown list
function renderSuggestions(details, prefix) {
    list.innerHTML = "";
    selectedIndex = -1;

    if (details.length === 0) {
        closeDropdown();
        return;
    }

    details.forEach((item) => {
        const li = document.createElement("li");
        li.setAttribute("data-query", item.query);
        
        const highlightedText = highlightMatch(item.query, prefix);
        const searchCount = item.count ? item.count.toLocaleString() : "0";
        const countBadge = `<span class="badge">${searchCount} searches</span>`;
        
        li.innerHTML = `<span class="suggest-text">${highlightedText}</span>${countBadge}`;
        list.appendChild(li);
    });

    dropdown.classList.remove("hidden");
}

// Fetch side-by-side rankings for comparisons card
async function fetchRankingsComparison(prefix) {
    try {
        const compareUrl = `${API_BASE_URL}/suggest/compare?q=${encodeURIComponent(prefix)}`;
        const res = await fetch(compareUrl);
        const data = await res.json();
        
        comparisonPrefixVal.textContent = prefix;
        
        // Render basic list
        comparisonBasicList.innerHTML = "";
        const basicList = data.basic_suggestions || [];
        if (basicList.length === 0) {
            comparisonBasicList.innerHTML = `<li style="color: var(--text-muted); font-size: 0.85rem;">No matches</li>`;
        } else {
            basicList.forEach((item) => {
                const li = document.createElement("li");
                li.setAttribute("data-query", item);
                li.innerHTML = `<span>${highlightMatch(item, prefix)}</span>`;
                comparisonBasicList.appendChild(li);
            });
        }
        
        // Render trending list
        comparisonTrendingList.innerHTML = "";
        const trendingList = data.trending_suggestions || [];
        if (trendingList.length === 0) {
            comparisonTrendingList.innerHTML = `<li style="color: var(--text-muted); font-size: 0.85rem;">No matches</li>`;
        } else {
            trendingList.forEach((item) => {
                const li = document.createElement("li");
                li.setAttribute("data-query", item);
                li.innerHTML = `<span>${highlightMatch(item, prefix)}</span>`;
                comparisonTrendingList.appendChild(li);
            });
        }
        
        comparisonCard.classList.remove("hidden");
    } catch (err) {
        console.error("Failed to fetch comparative rankings:", err);
    }
}

function selectQuery(query) {
    searchInput.value = query;
    closeDropdown();
    comparisonCard.classList.add("hidden");
    submitSearch();
}

// Keyboard navigation (Up/Down/Enter)
function handleKeydown(e) {
    const items = list.getElementsByTagName("li");
    if (!items.length) return;

    if (e.key === "ArrowDown") {
        e.preventDefault();
        selectedIndex = (selectedIndex + 1) % items.length;
        updateSelection(items);
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        selectedIndex = (selectedIndex - 1 + items.length) % items.length;
        updateSelection(items);
    } else if (e.key === "Enter") {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < items.length) {
            const query = items[selectedIndex].getAttribute("data-query");
            searchInput.value = query;
            closeDropdown();
        }
        submitSearch();
    } else if (e.key === "Escape") {
        closeDropdown();
    }
}

// Highlight active items
function updateSelection(items) {
    for (let i = 0; i < items.length; i++) {
        if (i === selectedIndex) {
            items[i].classList.add("selected");
            const query = items[i].getAttribute("data-query");
            searchInput.value = query; // Sync text input
        } else {
            items[i].classList.remove("selected");
        }
    }
}

function closeDropdown() {
    dropdown.classList.add("hidden");
    selectedIndex = -1;
}

// Submit search query (POST /search)
async function submitSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    closeDropdown();
    comparisonCard.classList.add("hidden");

    try {
        const res = await fetch(`${API_BASE_URL}/search`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ query }),
        });

        if (res.ok) {
            showResponseBanner(`Searched: "${query}"`);
            
            // Wait for buffer flush
            setTimeout(() => {
                fetchTrending();
                fetchMetrics();
            }, 1000);
        }
    } catch (err) {
        console.error("Search submission failed:", err);
    }
}

// Fetch trending searches (GET /trending)
async function fetchTrending() {
    try {
        const res = await fetch(`${API_BASE_URL}/trending`);
        const data = await res.json();
        
        trendingList.innerHTML = "";
        const trending = data.trending || [];

        if (trending.length === 0) {
            trendingList.innerHTML = `<div class="trending-placeholder">No trending searches calculated yet.</div>`;
            return;
        }

        trending.forEach((item, index) => {
            const div = document.createElement("div");
            div.className = "trending-item";
            div.innerHTML = `
                <div class="trending-left">
                    <span class="trending-rank">${index + 1}</span>
                    <span class="trending-text">${escapeHTML(item.query)}</span>
                </div>
                <span class="trending-score">Score: ${item.score}</span>
            `;
            div.addEventListener("click", () => {
                searchInput.value = item.query;
                submitSearch();
            });
            trendingList.appendChild(div);
        });
    } catch (err) {
        console.error("Failed to fetch trending searches:", err);
        trendingList.innerHTML = `<div class="trending-placeholder" style="color: var(--accent-rose);">Failed to load trending data.</div>`;
    }
}

// Fetch batch writer metrics
async function fetchMetrics() {
    try {
        const res = await fetch(`${API_BASE_URL}/metrics`);
        const data = await res.json();
        
        const metrics = data.batch_writer_metrics;
        metricWritesSaved.textContent = metrics.total_raw_writes_saved.toLocaleString();
        metricQueueSize.textContent = data.queue_size.toLocaleString();
        metricRecovered.textContent = metrics.recovered_queries_count.toLocaleString();
    } catch (err) {
        console.error("Failed to fetch metrics:", err);
    }
}

// Diagnostics Panel Updater
function updateDiagnostics(data) {
    statLatency.innerHTML = `${data.latency_ms} <span class="unit">ms</span>`;
    
    // Cache status
    statCache.textContent = data.source.toUpperCase();
    statCache.className = "stat-value";
    if (data.source === "cache") {
        statCache.classList.add("hit");
    } else if (data.source === "database") {
        statCache.classList.add("miss");
    } else if (data.source === "client_cache") {
        statCache.classList.add("hit");
        statCache.textContent = "CLIENT CACHE";
    }

    // Routed node name
    statNode.textContent = data.cache_node;

    // Circuit state
    statCircuit.textContent = data.circuit_state;
    statCircuit.className = `stat-value ${data.circuit_state}`;
}

function resetStats() {
    statLatency.innerHTML = `0.00 <span class="unit">ms</span>`;
    statCache.textContent = "-";
    statCache.className = "stat-value";
    statNode.textContent = "-";
}

function showResponseBanner(message) {
    apiResponseText.textContent = message;
    apiResponse.classList.remove("hidden");
    
    setTimeout(() => {
        apiResponse.classList.add("hidden");
    }, 3000);
}

// Utility to escape html characters
function escapeHTML(str) {
    return str.replace(/[&<>'"]/g, 
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag)
    );
}
