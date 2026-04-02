async function loadPlatformStatus() {
    const statusFields = {
        app: document.getElementById("status-app"),
        version: document.getElementById("status-version"),
        hostname: document.getElementById("status-hostname"),
        time: document.getElementById("status-time"),
    };
    const statusError = document.getElementById("status-error");

    if (!statusFields.app || !statusFields.version || !statusFields.hostname || !statusFields.time || !statusError) {
        return;
    }

    try {
        const response = await fetch("/api/status");

        if (!response.ok) {
            throw new Error("Status request failed");
        }

        const data = await response.json();
        statusFields.app.textContent = data.app;
        statusFields.version.textContent = data.version;
        statusFields.hostname.textContent = data.hostname;
        statusFields.time.textContent = data.time;
        statusError.hidden = true;
    } catch (error) {
        Object.values(statusFields).forEach((field) => {
            field.textContent = "--";
        });
        statusError.hidden = false;
    }
}

let currentNodes = [];
let currentEditNodeId = null;
let currentServices = [];
let currentNodeDashboardPayload = { anchors: [], discovered: [] };
let keepNodeModalOpenAfterSave = false;
let dashboardRefreshTimer = null;
let topologyPingTimer = null;
let topologyLastUpdatedAt = null;
let topologyLastUpdatedTimer = null;
let nodeDashboardEventSource = null;
const dashboardOrderStorageKey = "smp-dashboard-order";
const anchorListOrderStorageKey = "smp-anchor-list-order";
const dashboardRefreshStorageKey = "smp-dashboard-refresh-seconds";
const themeModeStorageKey = "smp-theme-mode";
const themeOverlayStorageKey = "smp-theme-overlay";
const pinnedNodesStorageKey = "smp-main-dashboard-node-ids";
const pinnedServicesStorageKey = "smp-main-dashboard-service-ids";
const topologyEditModeStorageKey = "smp-topology-edit-mode";
const topologyLayoutStorageKey = "smp-topology-layout-overrides";
const topologyStateLogLayoutStorageKey = "smp-topology-state-log-layout";
const topologyLinkAnchorStorageKey = "smp-topology-link-anchors";
const TOPOLOGY_LOCATIONS = ["HSMC", "Cloud", "Episodic"];
const TOPOLOGY_UNITS = ["DIV HQ", "1BCT", "2BCT", "3BCT", "CAB/DIVARTY", "Sustainment"];
const TOPOLOGY_LOCATION_ALIASES = {
    hsmc: "HSMC",
    cloud: "Cloud",
    azure: "Cloud",
    episodic: "Episodic",
    epis: "Episodic",
};
let topologyPayload = null;
let topologySubmapDetail = null;
let topologyDiscoveryPayload = { anchors: [], discovered: [], relationships: [], summary: {} };
let topologyNodeDashboardPayload = { anchors: [], discovered: [] };
let topologyDashboardServicesPayload = { summary: {}, services: [] };
let topologyResizeListenerBound = false;
let topologyRouteListenerBound = false;
let topologyEditorStateLoaded = false;
let topologyEditorStateSaveTimer = null;
let topologyRefreshTimer = null;
const topologyLinkStatsCache = new Map();
const topologyNetworkStateLog = (() => {
    try {
        const raw = localStorage.getItem("smp-topology-state-log");
        if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
                return parsed.slice(0, 100);
            }
        }
    } catch (e) { /* ignore */ }
    return [];
})();
const topologyPreviousNodeStates = new Map();
const topologyPreviousLinkStates = new Map();
// Cache verified DN counts in localStorage so they survive page refreshes
const _submapDnCountCache = (() => {
    const STORAGE_KEY = "smp-submap-dn-counts";
    let _data = {};
    try { _data = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch (_e) { /* ignore */ }
    return {
        get(mapViewId) { return _data[mapViewId] || null; },
        set(mapViewId, value) {
            _data[mapViewId] = value;
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(_data)); } catch (_e) { /* ignore */ }
        },
    };
})();
const topologyState = {
    activeLocations: new Set(TOPOLOGY_LOCATIONS),
    activeUnits: new Set(TOPOLOGY_UNITS),
    view: "backbone+l2",
    selectedKind: null,
    selectedId: null,
    focusUnit: null,
    editMode: false,
    layoutOverrides: {},
    dragging: null,
    selectedEntityIds: new Set(),
    demoMode: "off",
    demoMenuOpen: false,
    demoSnapshot: null,
    isFullscreen: false,
    stateLogExpanded: false,
    stateLogSelected: false,
    stateLogLayout: null,
    linkAnchorAssignments: {},
    activeLinkHandleTarget: null,
    drawerLayout: null,
    drawerDragging: null,
    pinnedTooltipId: null,
    pinnedLinkTooltipId: null,
    pinnedLinkNodeId: null,
    _prevDnStates: {},
    _flashTimers: [],
};

const statusPriority = {
    online: 0,
    degraded: 1,
    offline: 2,
    disabled: 3,
};

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function normalizeTopologyLocation(location) {
    if (!location) {
        return null;
    }

    const normalized = String(location).trim();
    return TOPOLOGY_LOCATION_ALIASES[normalized.toLowerCase()] || normalized;
}

function normalizeTopologyUnit(unit) {
    if (!unit) {
        return null;
    }

    const normalized = String(unit).trim();
    return TOPOLOGY_UNITS.includes(normalized) ? normalized : null;
}

function getTopologyDiscoveryCounts() {
    const summary = topologyDiscoveryPayload?.summary;
    if (summary && typeof summary === "object") {
        return {
            total: Number(summary.total_discovered || 0),
            byLocation: new Map(Object.entries(summary.by_location || {})),
            byLocationUnit: new Map(Object.entries(summary.by_location_unit || {})),
            byUnit: new Map(Object.entries(summary.by_unit || {})),
        };
    }
    const counts = {
        total: 0,
        byLocation: new Map(),
        byLocationUnit: new Map(),
        byUnit: new Map(),
    };
    const discovered = Array.isArray(topologyDiscoveryPayload?.discovered) ? topologyDiscoveryPayload.discovered : [];

    discovered.forEach((row) => {
        const location = normalizeTopologyLocation(row.location) || "--";
        const unit = String(row.unit || "--");
        counts.total += 1;
        counts.byLocation.set(location, (counts.byLocation.get(location) || 0) + 1);
        counts.byLocationUnit.set(`${location}::${unit}`, (counts.byLocationUnit.get(`${location}::${unit}`) || 0) + 1);
        counts.byUnit.set(unit, (counts.byUnit.get(unit) || 0) + 1);
    });

    return counts;
}

function formatTopologyRelationshipKind(kind) {
    const normalized = String(kind || "").trim().toLowerCase();
    if (normalized === "surfaced_by") {
        return "Surfaced By";
    }
    return normalized ? normalized.replace(/_/g, " ") : "Relationship";
}

function getTopologyDiscoveryRelationships() {
    const relationships = Array.isArray(topologyDiscoveryPayload?.relationships)
        ? topologyDiscoveryPayload.relationships
        : [];

    return relationships
        .filter((relationship) => {
            const targetUnit = normalizeTopologyUnit(relationship.target_unit);
            const targetLocation = normalizeTopologyLocation(relationship.target_location);

            if (topologyState.focusUnit && targetUnit && topologyState.focusUnit !== targetUnit) {
                return false;
            }
            if (targetUnit && !topologyState.activeUnits.has(targetUnit)) {
                return false;
            }
            if (targetLocation && !topologyState.activeLocations.has(targetLocation)) {
                return false;
            }
            return true;
        })
        .sort((left, right) => {
            const leftLevel = Number(left.discovered_level || 99);
            const rightLevel = Number(right.discovered_level || 99);
            if (leftLevel !== rightLevel) {
                return leftLevel - rightLevel;
            }
            return `${left.source_name || left.source_site_id} ${left.target_name || left.target_site_id}`
                .localeCompare(`${right.source_name || right.source_site_id} ${right.target_name || right.target_site_id}`);
        });
}

function getTopologyDiscoveryRelationshipRows() {
    const discoveredRows = Array.isArray(topologyDiscoveryPayload?.discovered)
        ? topologyDiscoveryPayload.discovered
        : [];
    const discoveredBySiteId = new Map(
        discoveredRows.map((row) => [String(row.site_id || ""), row]),
    );
    const grouped = new Map();

    getTopologyDiscoveryRelationships().forEach((relationship) => {
        const targetSiteId = String(relationship.target_site_id || "").trim();
        if (!targetSiteId) {
            return;
        }
        const discovered = discoveredBySiteId.get(targetSiteId) || {};
        const existing = grouped.get(targetSiteId) || {
            site_id: targetSiteId,
            site_name: discovered.site_name || relationship.target_name || `Site ${targetSiteId}`,
            host: discovered.host || "--",
            version: discovered.version || "--",
            location: normalizeTopologyLocation(discovered.location || relationship.target_location) || "--",
            unit: normalizeTopologyUnit(discovered.unit || relationship.target_unit) || "--",
            discovered_level: Number(discovered.discovered_level || relationship.discovered_level || 2),
            ping: discovered.ping || "Down",
            detail_url: `/nodes/discovered/${encodeURIComponent(targetSiteId)}`,
            surfaced_by_non_anchor_names: [],
            surfaced_by_names: Array.isArray(discovered.surfaced_by_names) ? [...discovered.surfaced_by_names] : [],
        };

        const sourceName = String(relationship.source_name || relationship.source_site_id || "").trim();
        if (sourceName && !existing.surfaced_by_names.includes(sourceName)) {
            existing.surfaced_by_names.push(sourceName);
        }
        if (String(relationship.source_row_type || "anchor").trim() !== "anchor" && sourceName && !existing.surfaced_by_non_anchor_names.includes(sourceName)) {
            existing.surfaced_by_non_anchor_names.push(sourceName);
        }
        grouped.set(targetSiteId, existing);
    });

    return Array.from(grouped.values()).sort((left, right) => {
        const leftLevel = Number(left.discovered_level || 99);
        const rightLevel = Number(right.discovered_level || 99);
        if (leftLevel !== rightLevel) {
            return leftLevel - rightLevel;
        }
        return String(left.site_name || left.site_id).localeCompare(String(right.site_name || right.site_id));
    });
}

function formatTopologyUnitSource(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "anchor") {
        return "Anchor-derived";
    }
    if (normalized === "dn_lineage") {
        return "DN lineage";
    }
    if (normalized === "fallback") {
        return "Fallback";
    }
    if (normalized === "ambiguous") {
        return "Ambiguous";
    }
    if (normalized === "unresolved") {
        return "Unresolved";
    }
    return normalized ? normalized : "Unknown";
}

function renderTopologyStateLogMarkup(events, options = {}) {
    const limit = Number.isFinite(options.limit) ? options.limit : events.length;
    const compact = options.compact === true;
    const selectedEvents = events.slice(0, limit);

    if (!selectedEvents.length) {
        return `<p class="table-message">No recent ping or RTT state changes are available yet.</p>`;
    }

    if (compact) {
        return selectedEvents.map((event) => `
            <div class="topology-state-log-preview-line">
                <span class="topology-state-log-severity topology-state-log-severity-${escapeHtml(event.severity)}" aria-hidden="true"></span>
                <span class="topology-state-log-preview-text">${escapeHtml(event.headline)}</span>
                <span class="topology-state-log-time">${escapeHtml(formatDashboardTimestamp(event.timestamp))}</span>
            </div>
        `).join("");
    }

    return selectedEvents.map((event) => `
        <article class="topology-state-log-item">
            <span class="topology-state-log-severity topology-state-log-severity-${escapeHtml(event.severity)}" aria-hidden="true"></span>
            <div class="topology-state-log-main">
                <div class="topology-state-log-headline">
                    ${event.detailUrl ? `<button type="button" class="node-list-name-button" data-node-detail-url="${escapeHtml(event.detailUrl)}">${escapeHtml(event.headline)}</button>` : escapeHtml(event.headline)}
                </div>
                <div class="topology-state-log-meta">
                    ${event.meta.map((value) => `<span class="topology-state-log-chip">${escapeHtml(String(value))}</span>`).join("")}
                </div>
            </div>
            <div class="topology-state-log-time">${escapeHtml(formatDashboardTimestamp(event.timestamp))}</div>
        </article>
    `).join("");
}

function pushNetworkStateEvent(event) {
    event.timestamp = event.timestamp || new Date().toISOString();
    topologyNetworkStateLog.unshift(event);
    if (topologyNetworkStateLog.length > 100) {
        topologyNetworkStateLog.length = 100;
    }
    try {
        localStorage.setItem("smp-topology-state-log", JSON.stringify(topologyNetworkStateLog));
    } catch (e) { /* ignore storage failures */ }
}

function detectNodeStateChanges() {
    const entities = getTopologyEntities();
    for (const entity of entities) {
        if (!entity.inventory_node_id) {
            continue;
        }
        const key = String(entity.inventory_node_id);
        const name = entity.inventory_name || entity.name || entity.id;
        const pingState = String(entity.ping_state || "unknown").toLowerCase();
        const latencyMs = typeof entity.latency_ms === "number" ? entity.latency_ms : null;
        const prev = topologyPreviousNodeStates.get(key);

        if (prev) {
            if (prev.pingState !== pingState) {
                if (pingState === "down" || pingState === "miss") {
                    pushNetworkStateEvent({
                        severity: "down",
                        kind: "ping-down",
                        headline: `Ping down — ${name}`,
                        meta: [entity.location || "--", entity.unit || "--", entity.site_id || "--"],
                        detailUrl: entity.inventory_node_id ? `/nodes/${entity.inventory_node_id}` : "",
                    });
                } else if (prev.pingState === "down" || prev.pingState === "miss") {
                    pushNetworkStateEvent({
                        severity: "up",
                        kind: "ping-up",
                        headline: `Ping restored — ${name}`,
                        meta: [entity.location || "--", entity.unit || "--", entity.site_id || "--"],
                        detailUrl: entity.inventory_node_id ? `/nodes/${entity.inventory_node_id}` : "",
                    });
                } else {
                    pushNetworkStateEvent({
                        severity: "info",
                        kind: "ping-change",
                        headline: `Ping ${prev.pingState} → ${pingState} — ${name}`,
                        meta: [entity.location || "--", entity.unit || "--"],
                        detailUrl: entity.inventory_node_id ? `/nodes/${entity.inventory_node_id}` : "",
                    });
                }
            }
            if (latencyMs !== null && prev.latencyMs !== null && prev.latencyMs !== latencyMs) {
                const delta = latencyMs - prev.latencyMs;
                const pctChange = prev.latencyMs > 0 ? Math.abs(delta / prev.latencyMs) : 0;
                if (pctChange >= 0.5 || Math.abs(delta) >= 50) {
                    pushNetworkStateEvent({
                        severity: delta > 0 ? "warn" : "up",
                        kind: "rtt-change",
                        headline: `RTT ${prev.latencyMs} → ${latencyMs} ms — ${name}`,
                        meta: [entity.location || "--", entity.unit || "--"],
                        detailUrl: entity.inventory_node_id ? `/nodes/${entity.inventory_node_id}` : "",
                    });
                }
            }
        }
        topologyPreviousNodeStates.set(key, { pingState, latencyMs });
    }
}

function detectLinkStateChanges() {
    const links = topologyPayload?.links ?? [];
    for (let i = 0; i < links.length; i++) {
        const link = links[i];
        if (link.kind !== "authored" || !link.status_node_id) {
            continue;
        }
        const linkId = getTopologyLinkId(link, i);
        const currentStatus = getEffectiveTopologyLinkStatus(link, i);
        const prev = topologyPreviousLinkStates.get(linkId);
        if (prev && prev !== currentStatus) {
            const entities = getTopologyEntities();
            const fromE = entities.find((e) => e.id === link.from);
            const toE = entities.find((e) => e.id === link.to);
            const fromName = fromE?.name || link.from;
            const toName = toE?.name || link.to;
            pushNetworkStateEvent({
                severity: currentStatus === "down" ? "down" : currentStatus === "degraded" ? "warn" : "up",
                kind: "link-change",
                headline: `Link ${prev} → ${currentStatus} — ${fromName} ↔ ${toName}`,
                meta: [fromName, toName],
                detailUrl: "",
            });
        }
        topologyPreviousLinkStates.set(linkId, currentStatus);
    }
}

function getTopologyStateLogEvents() {
    return topologyNetworkStateLog.slice(0, 40);
}

function renderTopologyStateLog() {
    const container = document.getElementById("topology-state-log");
    const preview = document.getElementById("topology-state-log-preview-list");
    const previewButton = document.getElementById("topology-state-log-preview");
    const flyout = document.getElementById("topology-state-log-flyout");
    const count = document.getElementById("topology-state-log-count");
    if (!container || !preview || !previewButton || !flyout || !count) {
        return;
    }

    const events = getTopologyStateLogEvents();
    count.textContent = String(events.length);
    preview.innerHTML = renderTopologyStateLogMarkup(events, { limit: 4, compact: true });
    container.innerHTML = renderTopologyStateLogMarkup(events, { limit: 40, compact: false });
    flyout.hidden = true;
    previewButton.hidden = true;
    applyTopologyStateLogLayout();

    if (!container.dataset.bound) {
        container.dataset.bound = "true";
        container.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }

            const detailButton = target.closest("[data-node-detail-url]");
            if (detailButton instanceof HTMLElement) {
                event.preventDefault();
                event.stopPropagation();
                openNodeDetail(detailButton.getAttribute("data-node-detail-url"));
            }
        });
    }

    if (!previewButton.dataset.bound) {
        previewButton.dataset.bound = "true";
        previewButton.addEventListener("click", (event) => {
            if (topologyState.editMode) {
                event.preventDefault();
                event.stopPropagation();
                topologyState.stateLogSelected = true;
                clearTopologyEntitySelection();
                const layer = document.getElementById("topology-node-layer");
                if (layer) {
                    syncTopologyEntitySelectionStyles(layer);
                } else {
                    updateTopologyEditStatus();
                }
                applyTopologyStateLogLayout();
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            topologyState.stateLogExpanded = true;
            renderTopologyStateLog();
        });
    }

    const closeButton = document.getElementById("topology-state-log-close");
    if (closeButton && closeButton.dataset.bound !== "true") {
        closeButton.dataset.bound = "true";
        closeButton.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            topologyState.stateLogExpanded = false;
            renderTopologyStateLog();
        });
    }
}

function getTopologyDiscoveryCount(entity, discoveryCounts) {
    if (entity.level === 0) {
        return discoveryCounts.byLocation.get(entity.location) || 0;
    }

    if (entity.level === 1) {
        return discoveryCounts.byLocationUnit.get(`${entity.location}::${entity.unit}`) || 0;
    }

    if (entity.level === 2) {
        return discoveryCounts.byUnit.get(entity.unit) || 0;
    }

    return 0;
}

function getTopologyLayoutScale() {
    return topologyState.isFullscreen ? 1.08 : 1;
}

function getTopologyBubbleSize(entity, discoveredCount) {
    const layoutScale = getTopologyLayoutScale();
    if (entity.kind === "services-cloud") {
        const total = Number(entity.service_summary?.total || 0);
        return Math.round(Math.max(164, Math.min(228, (168 + total * 6) * layoutScale)));
    }

    const scale = Math.sqrt(Math.max(discoveredCount, 0));
    if (entity.level === 0) {
        return Math.round(Math.max(154, Math.min(228, (142 + scale * 12) * layoutScale)));
    }

    if (entity.level === 1) {
        return Math.round(Math.max(104, Math.min(156, (94 + scale * 7) * layoutScale)));
    }

    return Math.round(Math.max(146, Math.min(220, (122 + scale * 10) * layoutScale)));
}

function getTopologyClusterStatusCounts() {
    const counts = {
        totalByUnit: new Map(),
        upByUnit: new Map(),
    };

    const discoveredRows = topologyDiscoveryPayload?.discovered ?? [];
    discoveredRows.forEach((row) => {
        if (!row || typeof row !== "object") {
            return;
        }

        const unit = String(row.resolved_unit || row.unit || "").trim();
        if (!unit || unit === "--") {
            return;
        }

        counts.totalByUnit.set(unit, (counts.totalByUnit.get(unit) || 0) + 1);
        const ping = String(row.ping || "").trim().toLowerCase();
        if (ping === "up") {
            counts.upByUnit.set(unit, (counts.upByUnit.get(unit) || 0) + 1);
        }
    });

    return counts;
}

function hashTopologyString(value) {
    let hash = 0;
    const input = String(value || "");
    for (let index = 0; index < input.length; index += 1) {
        hash = ((hash << 5) - hash + input.charCodeAt(index)) | 0;
    }
    return Math.abs(hash);
}

function getTopologyDemoRatio(seedKey, minRatio, maxRatio) {
    const range = Math.max(maxRatio - minRatio, 0);
    const hash = hashTopologyString(seedKey) % 1000;
    return minRatio + (hash / 1000) * range;
}

function buildTopologyDemoSnapshot(mode) {
    if (!topologyPayload || mode === "off") {
        return null;
    }

    const entities = getTopologyEntities();
    const clusterCounts = getTopologyClusterStatusCounts();
    const entityStatusById = new Map();
    const linkStatusById = new Map();
    const clusterUpByUnit = new Map();

    if (mode === "all-down") {
        entities.forEach((entity) => {
            entityStatusById.set(entity.id, "down");
            if (entity.level === 2) {
                clusterUpByUnit.set(entity.unit, 0);
            }
        });
        (topologyPayload.links ?? []).forEach((link, index) => {
            linkStatusById.set(getTopologyLinkId(link, index), "down");
        });
        return { entityStatusById, linkStatusById, clusterUpByUnit };
    }

    if (mode === "all-up") {
        const scriptedUpCounts = {
            "DIV HQ": 50,
            "1BCT": 7,
            "2BCT": 25,
            "3BCT": 12,
            "CAB/DIVARTY": 55,
            "Sustainment": 60,
        };
        entities.forEach((entity) => {
            entityStatusById.set(entity.id, "healthy");
            if (entity.level === 2) {
                clusterUpByUnit.set(
                    entity.unit,
                    scriptedUpCounts[entity.unit] ?? Math.max(1, Math.round(getTopologyDemoRatio(`all-up:${entity.unit}`, 20, 60))),
                );
            }
        });
        (topologyPayload.links ?? []).forEach((link, index) => {
            linkStatusById.set(getTopologyLinkId(link, index), "healthy");
        });
        return { entityStatusById, linkStatusById, clusterUpByUnit };
    }

    const mixClusterUpCounts = {
        "DIV HQ": 44,
        "1BCT": 3,
        "2BCT": 23,
        "3BCT": 19,
        "CAB/DIVARTY": 41,
        "Sustainment": 36,
    };
    const mixLvl0StatusByLocation = {
        Cloud: "healthy",
        HSMC: "healthy",
        Episodic: "degraded",
    };
    const mixLvl1StatusByKey = {
        "Cloud::DIV HQ": "healthy",
        "Cloud::1BCT": "degraded",
        "Cloud::2BCT": "healthy",
        "Cloud::3BCT": "healthy",
        "Cloud::CAB/DIVARTY": "healthy",
        "Cloud::Sustainment": "healthy",
        "HSMC::DIV HQ": "healthy",
        "HSMC::1BCT": "healthy",
        "HSMC::2BCT": "healthy",
        "HSMC::3BCT": "healthy",
        "HSMC::CAB/DIVARTY": "healthy",
        "HSMC::Sustainment": "healthy",
        "Episodic::DIV HQ": "degraded",
        "Episodic::1BCT": "degraded",
        "Episodic::2BCT": "degraded",
        "Episodic::3BCT": "degraded",
        "Episodic::CAB/DIVARTY": "degraded",
        "Episodic::Sustainment": "degraded",
    };

    entities.forEach((entity) => {
        let status = "healthy";
        if (entity.level === 0) {
            status = mixLvl0StatusByLocation[entity.location] || "healthy";
        } else if (entity.level === 1) {
            status = mixLvl1StatusByKey[`${entity.location}::${entity.unit}`] || "healthy";
        } else if (entity.level === 2) {
            const total = clusterCounts.totalByUnit.get(entity.unit) || 0;
            const upCount = mixClusterUpCounts[entity.unit] ?? 0;
            status = upCount <= 0 ? "down" : upCount < total ? "degraded" : "healthy";
            clusterUpByUnit.set(entity.unit, upCount);
        }
        entityStatusById.set(entity.id, status);
    });
    (topologyPayload.links ?? []).forEach((link, index) => {
        const fromStatus = entityStatusById.get(link.from) || "healthy";
        const toStatus = entityStatusById.get(link.to) || "healthy";
        let status = "healthy";
        if (fromStatus === "down" || toStatus === "down") {
            status = link.kind === "cluster" ? "degraded" : "down";
        } else if (fromStatus === "degraded" || toStatus === "degraded") {
            status = "degraded";
        } else if (link.kind === "peer" && hashTopologyString(`mix-peer:${getTopologyLinkId(link, index)}`) % 6 === 0) {
            status = "degraded";
        }

        if (link.kind === "peer") {
            const fromEntity = entities.find((entity) => entity.id === link.from);
            const toEntity = entities.find((entity) => entity.id === link.to);
            const peerLocations = [fromEntity?.location, toEntity?.location].filter(Boolean);
            if (peerLocations.includes("Episodic")) {
                status = status === "down" ? "down" : "degraded";
            } else if (peerLocations.includes("Cloud") && hashTopologyString(`mix-cloud-peer:${getTopologyLinkId(link, index)}`) % 7 === 0 && status === "healthy") {
                status = "degraded";
            }
        }

        if (link.kind === "uplink" || link.kind === "cluster") {
            const targetEntity = entities.find((entity) => entity.id === link.to);
            if (targetEntity?.location === "Episodic") {
                status = status === "down" ? "down" : "degraded";
            }
            if (targetEntity?.unit === "1BCT" && targetEntity?.location === "Cloud") {
                status = link.kind === "uplink" ? "down" : "degraded";
            }
        }

        linkStatusById.set(getTopologyLinkId(link, index), status);
    });
    return { entityStatusById, linkStatusById, clusterUpByUnit };
}

function setTopologyDemoMode(mode) {
    const normalized = ["off", "all-up", "all-down", "mix"].includes(mode) ? mode : "off";
    topologyState.demoMode = normalized;
    topologyState.demoSnapshot = buildTopologyDemoSnapshot(normalized);
    topologyState.demoMenuOpen = false;
    if (topologyEditorStateLoaded) {
        queueTopologyEditorStateSave();
    }
}

function getEffectiveTopologyEntityStatus(entity) {
    return topologyState.demoSnapshot?.entityStatusById?.get(entity.id) || entity.status || "neutral";
}

function getEffectiveTopologyLinkStatus(link, index) {
    const demoStatus = topologyState.demoSnapshot?.linkStatusById?.get(getTopologyLinkId(link, index));
    if (demoStatus) {
        return demoStatus;
    }
    if (link.kind === "authored" && link.status_node_id) {
        const statusEntity = getTopologyStatusNodeEntity(link.status_node_id);
        if (statusEntity) {
            return computeTopologyLinkStatusFromNode(statusEntity);
        }
    }
    return link.status || "neutral";
}

function getTopologyStatusNodeEntity(inventoryNodeId) {
    const entities = getTopologyEntities();
    return entities.find((e) => e.inventory_node_id === inventoryNodeId || e.inventory_node_id === String(inventoryNodeId)) || null;
}

function computeTopologyLinkStatusFromNode(entity) {
    const pingState = String(entity.ping_state || "").toLowerCase();
    if (pingState === "down" || pingState === "miss" || entity.ping_ok === false) {
        return "down";
    }
    const latency = entity.latency_ms;
    const avg = entity.avg_latency_ms;
    if (typeof latency === "number" && typeof avg === "number" && avg > 0) {
        if (latency > avg * 1.5) {
            return "degraded";
        }
    }
    return "healthy";
}

function getEffectiveTopologyClusterUpCount(unit, fallbackCount) {
    if (!topologyState.demoSnapshot?.clusterUpByUnit) {
        return fallbackCount;
    }
    return topologyState.demoSnapshot.clusterUpByUnit.get(unit) ?? fallbackCount;
}

function getEffectiveTopologyClusterIconStatus(discoveredCount, upCount) {
    if (topologyState.demoMode === "all-up") {
        return "up";
    }
    if (topologyState.demoMode === "all-down") {
        return "down";
    }
    return getTopologyClusterIconStatus(discoveredCount, upCount);
}

function getTopologyLinkStatusScore(status) {
    switch (String(status || "").toLowerCase()) {
        case "healthy":
        case "up":
        case "online":
            return 3;
        case "degraded":
            return 2;
        case "down":
        case "offline":
            return 1;
        default:
            return 0;
    }
}

function getTopologyClusterHealthIconCount(upCount) {
    if (upCount <= 9) {
        return 1;
    }
    if (upCount <= 19) {
        return 2;
    }
    return 3;
}

function getTopologyIconStatus(status) {
    const normalized = String(status || "neutral").trim().toLowerCase();
    if (normalized === "healthy") {
        return "up";
    }
    if (normalized === "degraded") {
        return "degraded";
    }
    if (normalized === "down") {
        return "down";
    }
    return "neutral";
}

function getTopologyClusterIconStatus(discoveredCount, upCount) {
    if (discoveredCount <= 0 || upCount <= 0) {
        return "down";
    }
    if (upCount < discoveredCount) {
        return "degraded";
    }
    return "up";
}

function getTopologyClusterFooterMarkup(discoveredCount, upCount) {
    const iconCount = getTopologyClusterHealthIconCount(upCount);
    const iconStatus = getEffectiveTopologyClusterIconStatus(discoveredCount, upCount);
    return `
        <span class="topology-cluster-footer" aria-label="${upCount} reachable nodes">
            <span class="topology-cluster-health-chip">${upCount}</span>
            <span class="topology-cluster-health-icons" aria-hidden="true">
                ${Array.from({ length: iconCount }, () => `
                    <span class="topology-cluster-health-icon topology-cluster-health-icon-${iconStatus}">
                        <svg viewBox="0 0 64 64" focusable="false">
                            <circle cx="32" cy="32" r="27" class="topology-node-icon-ring"></circle>
                            <path d="M20 24v15l12 7 12-7" class="topology-node-icon-stroke"></path>
                            <path d="M32 17v29" class="topology-node-icon-stroke"></path>
                            <path d="M32 31l11-8" class="topology-node-icon-stroke"></path>
                            <circle cx="20" cy="24" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="20" cy="39" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="32" cy="17" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="32" cy="31" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="32" cy="46" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="44" cy="23" r="3.2" class="topology-node-icon-node"></circle>
                            <circle cx="44" cy="38" r="3.2" class="topology-node-icon-node"></circle>
                        </svg>
                    </span>
                `).join("")}
            </span>
        </span>
    `;
}

function getSavedTopologyEditMode() {
    try {
        return window.localStorage.getItem(topologyEditModeStorageKey) === "true";
    } catch (error) {
        return false;
    }
}

function saveTopologyEditMode(editMode) {
    try {
        window.localStorage.setItem(topologyEditModeStorageKey, editMode ? "true" : "false");
    } catch (error) {
        // Ignore storage failures.
    }
}

function hasLocalTopologyEditorState() {
    return Object.keys(getSavedTopologyLayoutOverrides()).length > 0
        || Boolean(getSavedTopologyStateLogLayout())
        || Object.keys(getSavedTopologyLinkAnchorAssignments()).length > 0;
}

function getSavedTopologyLayoutOverrides() {
    try {
        const raw = window.localStorage.getItem(topologyLayoutStorageKey);
        if (!raw) {
            return {};
        }

        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
        return {};
    }
}

function getSavedTopologyStateLogLayout() {
    try {
        const raw = window.localStorage.getItem(topologyStateLogLayoutStorageKey);
        if (!raw) {
            return null;
        }
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : null;
    } catch (error) {
        return null;
    }
}

function queueTopologyEditorStateSave() {
    if (!topologyEditorStateLoaded) {
        return;
    }
    if (topologyEditorStateSaveTimer) {
        window.clearTimeout(topologyEditorStateSaveTimer);
    }
    topologyEditorStateSaveTimer = window.setTimeout(() => {
        topologyEditorStateSaveTimer = null;
        void persistTopologyEditorState();
    }, 220);
}

function buildTopologyEditorStatePayload() {
    return {
        layout_overrides: topologyState.layoutOverrides || {},
        state_log_layout: topologyState.stateLogLayout || null,
        link_anchor_assignments: topologyState.linkAnchorAssignments || {},
        demo_mode: topologyState.demoMode || "off",
    };
}

async function persistTopologyEditorState() {
    const payload = buildTopologyEditorStatePayload();
    try {
        await apiRequest("/api/topology/editor-state", {
            method: "PUT",
            body: JSON.stringify(payload),
        });
    } catch (error) {
        // Keep local fallback if the backend save fails.
    }
}

function saveTopologyStateLogLayout() {
    try {
        window.localStorage.setItem(
            topologyStateLogLayoutStorageKey,
            JSON.stringify(topologyState.stateLogLayout || {}),
        );
    } catch (error) {
        // Ignore storage failures.
    }
    queueTopologyEditorStateSave();
}

function getSavedTopologyLinkAnchorAssignments() {
    try {
        const raw = window.localStorage.getItem(topologyLinkAnchorStorageKey);
        if (!raw) {
            return {};
        }
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
        return {};
    }
}

function saveTopologyLinkAnchorAssignments() {
    try {
        window.localStorage.setItem(
            topologyLinkAnchorStorageKey,
            JSON.stringify(topologyState.linkAnchorAssignments || {}),
        );
    } catch (error) {
        // Ignore storage failures.
    }
    queueTopologyEditorStateSave();
}

function saveTopologyLayoutOverrides() {
    try {
        window.localStorage.setItem(topologyLayoutStorageKey, JSON.stringify(topologyState.layoutOverrides || {}));
    } catch (error) {
        // Ignore storage failures.
    }
    queueTopologyEditorStateSave();
}

function clearTopologyLayoutOverrides() {
    topologyState.layoutOverrides = {};
    saveTopologyLayoutOverrides();
}

function getTopologyAnchorPointDefinitions() {
    return [
        { key: "n", x: 0.5, y: 0 },
        { key: "ne", x: 0.82, y: 0.12 },
        { key: "e", x: 1, y: 0.5 },
        { key: "se", x: 0.82, y: 0.88 },
        { key: "s", x: 0.5, y: 1 },
        { key: "sw", x: 0.18, y: 0.88 },
        { key: "w", x: 0, y: 0.5 },
        { key: "nw", x: 0.18, y: 0.12 },
    ];
}

function getTopologyConnectedAnchorMap() {
    const connected = new Map();
    (topologyPayload?.links ?? []).forEach((link, index) => {
        const linkId = getTopologyLinkId(link, index);
        const assignment = topologyState.linkAnchorAssignments?.[linkId] || {};
        const linkStatus = getEffectiveTopologyLinkStatus(link, index) || "neutral";
        const sourceAnchor = assignment.source || link.source_anchor || null;
        const targetAnchor = assignment.target || link.target_anchor || null;
        if (sourceAnchor && link.from) {
            const sourceKey = `${link.from}::${sourceAnchor}`;
            const currentSourceStatus = connected.get(sourceKey);
            if (
                !currentSourceStatus
                || getTopologyLinkStatusScore(linkStatus) > getTopologyLinkStatusScore(currentSourceStatus)
            ) {
                connected.set(sourceKey, linkStatus);
            }
        }
        if (targetAnchor && link.to) {
            const targetKey = `${link.to}::${targetAnchor}`;
            const currentTargetStatus = connected.get(targetKey);
            if (
                !currentTargetStatus
                || getTopologyLinkStatusScore(linkStatus) > getTopologyLinkStatusScore(currentTargetStatus)
            ) {
                connected.set(targetKey, linkStatus);
            }
        }
    });
    return connected;
}

/**
 * Build a map of entity::anchorKey -> worst discovery link status.
 * Unlike getTopologyConnectedAnchorMap (which uses highest-score-wins for
 * authored links), this uses worst-wins so a single down discovery link
 * turns the AP dot red even if other links on the same AP are healthy.
 */
function getDiscoveryWorstAnchorMap() {
    const worst = new Map();
    const scoreWorst = (status) => {
        switch (String(status || "").toLowerCase()) {
            case "down": case "offline": return 3;
            case "degraded": return 2;
            case "healthy": case "up": case "online": return 1;
            default: return 0;
        }
    };
    (topologyPayload?.links ?? []).forEach((link, index) => {
        if (link.kind !== "discovery") return;
        const linkStatus = getEffectiveTopologyLinkStatus(link, index) || "neutral";
        const assignment = topologyState.linkAnchorAssignments?.[getTopologyLinkId(link, index)] || {};
        const sourceAnchor = assignment.source || link.source_anchor || null;
        const targetAnchor = assignment.target || link.target_anchor || null;
        if (sourceAnchor && link.from) {
            const key = `${link.from}::${sourceAnchor}`;
            const cur = worst.get(key);
            if (!cur || scoreWorst(linkStatus) > scoreWorst(cur)) {
                worst.set(key, linkStatus);
            }
        }
        if (targetAnchor && link.to) {
            const key = `${link.to}::${targetAnchor}`;
            const cur = worst.get(key);
            if (!cur || scoreWorst(linkStatus) > scoreWorst(cur)) {
                worst.set(key, linkStatus);
            }
        }
    });
    return worst;
}

function getTopologyLinkAnchorAssignment(link, index) {
    const linkId = getTopologyLinkId(link, index);
    const assignment = topologyState.linkAnchorAssignments?.[linkId] || {};
    return {
        source: assignment.source || null,
        target: assignment.target || null,
    };
}

function setTopologyLinkAnchorAssignment(linkId, side, anchorKey) {
    topologyState.linkAnchorAssignments = {
        ...(topologyState.linkAnchorAssignments || {}),
        [linkId]: {
            ...(topologyState.linkAnchorAssignments?.[linkId] || {}),
            [side]: anchorKey,
        },
    };
    saveTopologyLinkAnchorAssignments();
}

function getTopologyStateLogLayout() {
    const stage = document.getElementById("topology-stage");
    const stageWidth = Math.max(stage?.clientWidth || 1200, 600);
    const stageHeight = Math.max(stage?.clientHeight || 940, 500);
    const saved = topologyState.stateLogLayout || {};
    const width = Number.isFinite(saved.width) ? saved.width : Math.min(860, stageWidth - 32);
    const height = Number.isFinite(saved.height) ? saved.height : 132;
    const maxLeft = Math.max(8, stageWidth - width - 8);
    const maxBottom = Math.max(8, stageHeight - height - 8);
    const left = Number.isFinite(saved.left) ? saved.left : 16;
    const bottom = Number.isFinite(saved.bottom) ? saved.bottom : 16;
    return {
        left: Math.min(maxLeft, Math.max(8, left)),
        bottom: Math.min(maxBottom, Math.max(8, bottom)),
        width: Math.max(320, Math.min(width, stageWidth - 16)),
        height: Math.max(110, Math.min(height, stageHeight - 16)),
    };
}

function applyTopologyStateLogLayout() {
    const preview = document.getElementById("topology-state-log-preview");
    const flyout = document.getElementById("topology-state-log-flyout");
    const stage = document.getElementById("topology-stage");
    if (!(preview instanceof HTMLElement) || !(flyout instanceof HTMLElement) || !(stage instanceof HTMLElement)) {
        return;
    }
    const layout = getTopologyStateLogLayout();
    preview.style.left = `${layout.left}px`;
    preview.style.bottom = `${layout.bottom}px`;
    preview.style.width = `${layout.width}px`;
    preview.style.minHeight = `${layout.height}px`;
    preview.classList.toggle("is-selected", topologyState.editMode && topologyState.stateLogSelected);

    flyout.style.left = `${layout.left}px`;
    flyout.style.bottom = `${layout.bottom}px`;
    flyout.style.width = `${Math.min(layout.width + 120, stage.clientWidth - 16)}px`;
    flyout.style.maxHeight = `${Math.max(220, stage.clientHeight - 32)}px`;
    flyout.classList.toggle("is-selected", topologyState.editMode && topologyState.stateLogSelected);
}

function setTopologyEditMode(editMode) {
    topologyState.editMode = Boolean(editMode);
    saveTopologyEditMode(topologyState.editMode);
    if (topologyState.editMode) {
        topologyState.pinnedTooltipId = null;
    }
    if (!topologyState.editMode) {
        clearTopologyEntitySelection();
        syncTopologySelectionBox(null);
        topologyState.demoMenuOpen = false;
        topologyState.stateLogSelected = false;
        setTopologyActiveLinkHandleTarget(null);
    }

    const stage = document.getElementById("topology-stage");
    const layer = document.getElementById("topology-node-layer");
    const button = document.getElementById("topology-layout-edit-toggle");
    const resetButton = document.getElementById("topology-layout-reset");
    if (stage) {
        stage.classList.toggle("is-editing", topologyState.editMode);
    }
    if (button) {
        button.textContent = topologyState.editMode ? "Lock Map" : "Edit Map";
        button.setAttribute("aria-pressed", topologyState.editMode ? "true" : "false");
    }
    if (resetButton) {
        resetButton.hidden = !topologyState.editMode;
    }
    const createSubmapButton = document.getElementById("topology-create-submap-button");
    if (createSubmapButton) {
        createSubmapButton.hidden = !topologyState.editMode;
    }
    const addNodeButton = document.getElementById("submap-add-node-button");
    if (addNodeButton) {
        addNodeButton.hidden = !topologyState.editMode;
    }
    if (!topologyState.editMode) {
        const addNodePanel = document.getElementById("submap-add-node-panel");
        if (addNodePanel) {
            addNodePanel.hidden = true;
        }
    }
    // Show all discovery links in edit mode; hide when leaving (unless pinned)
    if (topologyState.editMode) {
        revealAllDiscoveryLinks();
    } else {
        hideAllDiscoveryLinks();
        if (topologyState.pinnedLinkNodeId) {
            revealDiscoveryLinksForEntity(topologyState.pinnedLinkNodeId);
        }
    }
    if (layer) {
        syncTopologyEntitySelectionStyles(layer);
    }
    updateTopologyEditStatus();
    applyTopologyStateLogLayout();
}

function getTopologyBaseLayout(entity) {
    const position = getTopologyNodePosition(entity);
    const discoveryCounts = getTopologyDiscoveryCounts();
    const discoveredCount = getTopologyDiscoveryCount(entity, discoveryCounts);
    return {
        x: position.x,
        y: position.y,
        size: getTopologyBubbleSize(entity, discoveredCount),
    };
}

function getTopologyEntityLayout(entity) {
    const baseLayout = getTopologyBaseLayout(entity);
    const override = topologyState.layoutOverrides?.[entity.id];
    const logicalLayout = !override ? baseLayout : {
        x: Number.isFinite(override.x) ? override.x : baseLayout.x,
        y: Number.isFinite(override.y) ? override.y : baseLayout.y,
        size: Number.isFinite(override.size) ? override.size : baseLayout.size,
    };
    if (!topologyState.isFullscreen) {
        return logicalLayout;
    }

    const stage = document.getElementById("topology-stage");
    const stageWidth = Math.max(stage?.clientWidth || 1200, 600);
    const centerX = stageWidth / 2;
    const horizontalScale = 1;
    const verticalScale = 1.35;
    const sizeScale = getTopologyLayoutScale();

    return {
        x: centerX + ((logicalLayout.x - centerX) * horizontalScale),
        y: logicalLayout.y * verticalScale,
        size: logicalLayout.size * sizeScale,
    };
}

function formatTopologyMetricValue(value, suffix = "") {
    if (typeof value === "number" && Number.isFinite(value)) {
        return `${Math.round(value)}${suffix}`;
    }
    const normalized = String(value ?? "").trim();
    return normalized || "--";
}

function getTopologyAnchorStatusReason(entity) {
    const rttState = String(entity?.rtt_state || "").trim().toLowerCase();
    const rttText = typeof entity.latency_ms === "number" && Number.isFinite(entity.latency_ms)
        ? `${Math.round(entity.latency_ms)} ms`
        : null;

    if (rttState === "good") {
        return rttText
            ? `Green: RTT is healthy on Node Dashboard (${rttText}).`
            : "Green: RTT is healthy on Node Dashboard.";
    }

    if (rttState === "warn") {
        return rttText
            ? `Yellow: RTT is degraded on Node Dashboard (${rttText}).`
            : "Yellow: RTT is degraded on Node Dashboard.";
    }

    if (rttState === "down") {
        return "Red: RTT is down on Node Dashboard.";
    }

    return "Neutral: Node Dashboard RTT state is not available yet.";
}

function getTopologyAnchorTooltipMarkup(entity) {
    if (!entity || entity.level === 2 || entity.kind === "services-cloud" || !entity.inventory_node_id) {
        return "";
    }

    const nodeId = entity.site_id || entity.inventory_node_id || "--";
    const rttText = typeof entity.latency_ms === "number" && Number.isFinite(entity.latency_ms)
        ? `${Math.round(entity.latency_ms)} ms`
        : "--";
    const wanTxText = formatRate(entity.wan_tx_bps || 0);
    const wanRxText = formatRate(entity.wan_rx_bps || 0);
    const lanTxText = formatRate(entity.lan_tx_bps || 0);
    const lanRxText = formatRate(entity.lan_rx_bps || 0);
    const wanTxTotal = String(entity.wan_tx_total || "--").trim() || "--";
    const wanRxTotal = String(entity.wan_rx_total || "--").trim() || "--";
    const lanTxTotal = String(entity.lan_tx_total || "--").trim() || "--";
    const lanRxTotal = String(entity.lan_rx_total || "--").trim() || "--";
    const cpuText = typeof entity.cpu_avg === "number" && Number.isFinite(entity.cpu_avg)
        ? `${Math.round(entity.cpu_avg)}%`
        : "--";
    const versionText = String(entity.version || "--").trim() || "--";
    const statusReason = getTopologyAnchorStatusReason(entity);

    return `
        <span class="topology-node-tooltip" role="tooltip">
            <strong class="topology-node-tooltip-title">${escapeHtml(entity.inventory_name || entity.name || "Node")}</strong>
            <span class="topology-node-tooltip-reason">${escapeHtml(statusReason)}</span>
            <span class="topology-node-tooltip-grid">
                <span class="topology-node-tooltip-label">Node ID</span>
                <span class="topology-node-tooltip-value">${escapeHtml(String(nodeId))}</span>
                <span class="topology-node-tooltip-label">RTT</span>
                <span class="topology-node-tooltip-value" data-tooltip-rtt>${escapeHtml(rttText)}</span>
                <span class="topology-node-tooltip-label">WAN TX / RX</span>
                <span class="topology-node-tooltip-value">${escapeHtml(`${wanTxText} / ${wanRxText}`)}</span>
                <span class="topology-node-tooltip-label">LAN TX / RX</span>
                <span class="topology-node-tooltip-value">${escapeHtml(`${lanTxText} / ${lanRxText}`)}</span>
                <span class="topology-node-tooltip-label">WAN Total</span>
                <span class="topology-node-tooltip-value">${escapeHtml(`↑${wanTxTotal} / ↓${wanRxTotal}`)}</span>
                <span class="topology-node-tooltip-label">LAN Total</span>
                <span class="topology-node-tooltip-value">${escapeHtml(`↑${lanTxTotal} / ↓${lanRxTotal}`)}</span>
                <span class="topology-node-tooltip-label">CPU</span>
                <span class="topology-node-tooltip-value">${escapeHtml(cpuText)}</span>
                <span class="topology-node-tooltip-label">Version</span>
                <span class="topology-node-tooltip-value">${escapeHtml(versionText)}</span>
            </span>
        </span>
    `;
}

function getTopologyDiscoveredTooltipMarkup(entity) {
    if (!entity || entity.kind !== "discovered") {
        return "";
    }

    const siteId = entity.site_id || entity.node_id || "--";
    const hostIp = entity.host || "--";
    const rttState = String(entity.rtt_state || "unknown").toLowerCase();
    const rttText = typeof entity.latency_ms === "number" && Number.isFinite(entity.latency_ms)
        ? `${Math.round(entity.latency_ms)} ms`
        : "--";
    const avgRttText = typeof entity.avg_latency_ms === "number" && Number.isFinite(entity.avg_latency_ms)
        ? `${Math.round(entity.avg_latency_ms)} ms`
        : "--";
    const txText = entity.tx_display || "--";
    const rxText = entity.rx_display || "--";
    const sourceName = entity.source_name || "--";

    let statusReason;
    if (rttState === "good") {
        statusReason = rttText !== "--"
            ? `Green: Ping is healthy (${rttText}).`
            : "Green: Ping is healthy.";
    } else if (rttState === "warn") {
        statusReason = rttText !== "--"
            ? `Yellow: Ping is degraded (${rttText}).`
            : "Yellow: Ping is degraded.";
    } else if (rttState === "down") {
        statusReason = "Red: Ping is down.";
    } else {
        statusReason = "Ping state not available yet.";
    }

    return `
        <span class="topology-node-tooltip" role="tooltip">
            <strong class="topology-node-tooltip-title">${escapeHtml(entity.name || siteId)}</strong>
            <span class="topology-node-tooltip-reason">${escapeHtml(statusReason)}</span>
            <span class="topology-node-tooltip-grid">
                <span class="topology-node-tooltip-label">Site ID</span>
                <span class="topology-node-tooltip-value">${escapeHtml(String(siteId))}</span>
                <span class="topology-node-tooltip-label">Host</span>
                <span class="topology-node-tooltip-value">${escapeHtml(hostIp)}</span>
                <span class="topology-node-tooltip-label">RTT</span>
                <span class="topology-node-tooltip-value" data-tooltip-rtt>${escapeHtml(rttText)}</span>
                <span class="topology-node-tooltip-label">Avg RTT</span>
                <span class="topology-node-tooltip-value">${escapeHtml(avgRttText)}</span>
                <span class="topology-node-tooltip-label">TX / RX</span>
                <span class="topology-node-tooltip-value">${escapeHtml(`${txText} / ${rxText}`)}</span>
                <span class="topology-node-tooltip-label">Owner AN</span>
                <span class="topology-node-tooltip-value">${escapeHtml(sourceName)}</span>
            </span>
        </span>
    `;
}

function getTopologyEntityLabel(entity) {
    const override = topologyState.layoutOverrides?.[entity.id];
    const overrideLabel = typeof override?.label === "string" ? override.label.trim() : "";
    if (overrideLabel) {
        return overrideLabel;
    }
    if (entity.kind === "services-cloud") {
        return "Services";
    }
    if (entity.level === 2) {
        return entity.unit || "Edge Nodes";
    }
    return entity.name || entity.unit || entity.id;
}

function setTopologyEntityLayout(entityId, nextLayout, options = {}) {
    topologyState.layoutOverrides = {
        ...(topologyState.layoutOverrides || {}),
        [entityId]: {
            ...(topologyState.layoutOverrides?.[entityId] || {}),
            x: Math.round(nextLayout.x),
            y: Math.round(nextLayout.y),
            size: Math.round(nextLayout.size),
        },
    };

    if (options.persist !== false) {
        saveTopologyLayoutOverrides();
    }
}

function removeTopologyEntityLayout(entityId) {
    if (!topologyState.layoutOverrides?.[entityId]) {
        return;
    }

    const nextOverrides = { ...(topologyState.layoutOverrides || {}) };
    delete nextOverrides[entityId];
    topologyState.layoutOverrides = nextOverrides;
    saveTopologyLayoutOverrides();
}

function setTopologyEntityLabel(entityId, nextLabel) {
    const existing = topologyState.layoutOverrides?.[entityId] || {};
    const normalized = String(nextLabel || "").trim();
    topologyState.layoutOverrides = {
        ...(topologyState.layoutOverrides || {}),
        [entityId]: {
            ...existing,
            ...(Number.isFinite(existing.x) ? { x: existing.x } : {}),
            ...(Number.isFinite(existing.y) ? { y: existing.y } : {}),
            ...(Number.isFinite(existing.size) ? { size: existing.size } : {}),
            ...(normalized ? { label: normalized } : {}),
        },
    };
    if (!normalized) {
        delete topologyState.layoutOverrides[entityId].label;
        if (Object.keys(topologyState.layoutOverrides[entityId]).length === 0) {
            delete topologyState.layoutOverrides[entityId];
        }
    }
    saveTopologyLayoutOverrides();
}

function clearTopologyEntityLabel(entityId) {
    const existing = topologyState.layoutOverrides?.[entityId];
    if (!existing || typeof existing !== "object" || !("label" in existing)) {
        return;
    }
    const nextOverride = { ...existing };
    delete nextOverride.label;
    topologyState.layoutOverrides = { ...(topologyState.layoutOverrides || {}) };
    if (Object.keys(nextOverride).length) {
        topologyState.layoutOverrides[entityId] = nextOverride;
    } else {
        delete topologyState.layoutOverrides[entityId];
    }
    saveTopologyLayoutOverrides();
}

function applyTopologyEntityStyles(button, layout) {
    button.style.left = `${layout.x}px`;
    button.style.top = `${layout.y}px`;
    button.style.setProperty("--topology-bubble-size", `${layout.size}px`);
}

function clearTopologyEntitySelection() {
    topologyState.selectedEntityIds = new Set();
}

function applyTopologyDrawerPosition() {
    return;
}

function wireTopologyDrawerDrag() {
    return;
}

function updateTopologyEditStatus() {
    const hint = document.getElementById("topology-edit-hint");
    const status = document.getElementById("topology-selection-status");
    const clearButton = document.getElementById("topology-selection-clear");
    const demoControl = document.getElementById("topology-demo-control");
    const demoToggle = document.getElementById("topology-demo-toggle");
    const demoMenu = document.getElementById("topology-demo-menu");
    const selectedCount = topologyState.selectedEntityIds.size;
    if (hint) {
        hint.hidden = !topologyState.editMode;
    }
    if (status) {
        status.hidden = !topologyState.editMode;
        status.textContent = `${selectedCount} selected`;
    }
    if (clearButton) {
        clearButton.hidden = !topologyState.editMode || selectedCount === 0;
    }
    if (demoControl) {
        demoControl.hidden = !topologyState.editMode;
    }
    if (demoToggle) {
        const label = topologyState.demoMode === "off"
            ? "Demo"
            : `Demo: ${topologyState.demoMode === "all-up" ? "All Up" : topologyState.demoMode === "all-down" ? "All Down" : "Mix"}`;
        demoToggle.textContent = label;
        demoToggle.setAttribute("aria-expanded", topologyState.demoMenuOpen ? "true" : "false");
    }
    if (demoMenu) {
        demoMenu.hidden = !topologyState.editMode || !topologyState.demoMenuOpen;
        demoMenu.querySelectorAll("[data-topology-demo-mode]").forEach((option) => {
            option.classList.toggle("is-active", option.getAttribute("data-topology-demo-mode") === topologyState.demoMode);
        });
    }

    const fullscreenButton = document.getElementById("topology-fullscreen-toggle");
    if (fullscreenButton) {
        fullscreenButton.textContent = topologyState.isFullscreen ? "Exit Full Screen" : "Full Screen";
        fullscreenButton.setAttribute("aria-pressed", topologyState.isFullscreen ? "true" : "false");
    }
}

function syncTopologyFullscreenState() {
    const card = document.querySelector(".topology-stage-card");
    topologyState.isFullscreen = Boolean(document.fullscreenElement && card && document.fullscreenElement === card);
    if (card instanceof HTMLElement) {
        card.classList.toggle("is-fullscreen", topologyState.isFullscreen);
    }
    updateTopologyEditStatus();
}

async function toggleTopologyFullscreen() {
    const card = document.querySelector(".topology-stage-card");
    if (!(card instanceof HTMLElement) || !document.fullscreenEnabled) {
        return;
    }

    try {
        if (document.fullscreenElement === card) {
            await document.exitFullscreen();
        } else {
            await card.requestFullscreen();
        }
    } catch (error) {
        // Ignore fullscreen failures and keep the existing layout.
    }
}

function syncTopologyEntitySelectionStyles(layer) {
    if (!(layer instanceof HTMLElement)) {
        return;
    }
    layer.querySelectorAll("[data-topology-id]").forEach((button) => {
        const entityId = button.getAttribute("data-topology-id") || "";
        button.classList.toggle("is-multi-selected", topologyState.selectedEntityIds.has(entityId));
    });
    updateTopologyEditStatus();
}

function getTopologySelectionRect(drag, event) {
    const stageBounds = getTopologyStageBounds();
    if (!stageBounds) {
        return null;
    }
    const startX = drag.stageStartX;
    const startY = drag.stageStartY;
    const currentX = event.clientX - stageBounds.left;
    const currentY = event.clientY - stageBounds.top;
    return {
        left: Math.min(startX, currentX),
        top: Math.min(startY, currentY),
        width: Math.abs(currentX - startX),
        height: Math.abs(currentY - startY),
    };
}

function syncTopologySelectionBox(rect) {
    const selectionBox = document.getElementById("topology-selection-box");
    if (!(selectionBox instanceof HTMLElement)) {
        return;
    }
    if (!rect) {
        selectionBox.hidden = true;
        return;
    }
    selectionBox.hidden = false;
    selectionBox.style.left = `${rect.left}px`;
    selectionBox.style.top = `${rect.top}px`;
    selectionBox.style.width = `${rect.width}px`;
    selectionBox.style.height = `${rect.height}px`;
}

function wireTopologyStateLogEditor() {
    const preview = document.getElementById("topology-state-log-preview");
    const flyout = document.getElementById("topology-state-log-flyout");
    const stage = document.getElementById("topology-stage");
    if (!(preview instanceof HTMLElement) || !(flyout instanceof HTMLElement) || !(stage instanceof HTMLElement)) {
        return;
    }

    if (!preview.querySelector("[data-topology-state-log-resize]")) {
        preview.insertAdjacentHTML("beforeend", '<span class="topology-resize-handle topology-state-log-resize" data-topology-state-log-resize="true" aria-hidden="true"></span>');
    }
    if (!flyout.querySelector("[data-topology-state-log-resize]")) {
        flyout.insertAdjacentHTML("beforeend", '<span class="topology-resize-handle topology-state-log-resize" data-topology-state-log-resize="true" aria-hidden="true"></span>');
    }

    if (preview.dataset.stateLogEditorBound === "true") {
        return;
    }
    preview.dataset.stateLogEditorBound = "true";

    const handlePointerMove = (event) => {
        const drag = topologyState.dragging;
        if (!drag || event.pointerId !== drag.pointerId || drag.kind !== "state-log") {
            return;
        }
        const deltaX = event.clientX - drag.startX;
        const deltaY = event.clientY - drag.startY;
        const nextLayout = { ...drag.startLayout };
        if (drag.mode === "resize") {
            nextLayout.width = Math.max(320, Math.round(drag.startLayout.width + deltaX));
            nextLayout.height = Math.max(110, Math.round(drag.startLayout.height + deltaY));
        } else {
            nextLayout.left = Math.max(8, Math.round(drag.startLayout.left + deltaX));
            nextLayout.bottom = Math.max(8, Math.round(drag.startLayout.bottom - deltaY));
        }
        topologyState.stateLogLayout = nextLayout;
        applyTopologyStateLogLayout();
        event.preventDefault();
    };

    const handlePointerEnd = (event) => {
        const drag = topologyState.dragging;
        if (!drag || event.pointerId !== drag.pointerId || drag.kind !== "state-log") {
            return;
        }
        try {
            drag.element?.releasePointerCapture?.(drag.pointerId);
        } catch (error) {
            // Ignore capture failures.
        }
        topologyState.dragging = null;
        saveTopologyStateLogLayout();
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerEnd);
        window.removeEventListener("pointercancel", handlePointerEnd);
        event.preventDefault();
    };

    const handlePointerDown = (event) => {
        if (!topologyState.editMode) {
            return;
        }
        const target = event.target;
        if (!(target instanceof Element) || !target.closest("#topology-state-log-preview, #topology-state-log-flyout")) {
            return;
        }
        const activeElement = topologyState.stateLogExpanded ? flyout : preview;
        const resizeHandle = target.closest("[data-topology-state-log-resize]");
        topologyState.stateLogSelected = true;
        clearTopologyEntitySelection();
        const layer = document.getElementById("topology-node-layer");
        if (layer) {
            syncTopologyEntitySelectionStyles(layer);
        } else {
            updateTopologyEditStatus();
        }
        const startLayout = getTopologyStateLogLayout();
        topologyState.dragging = {
            kind: "state-log",
            mode: resizeHandle ? "resize" : "drag",
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLayout,
            element: activeElement,
        };
        try {
            activeElement.setPointerCapture(event.pointerId);
        } catch (error) {
            // Ignore capture failures.
        }
        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", handlePointerEnd);
        window.addEventListener("pointercancel", handlePointerEnd);
        applyTopologyStateLogLayout();
        event.preventDefault();
        event.stopPropagation();
    };

    preview.addEventListener("pointerdown", handlePointerDown);
    flyout.addEventListener("pointerdown", handlePointerDown);
}

function nudgeTopologySelection(deltaX, deltaY) {
    if (!topologyState.editMode || !topologyState.selectedEntityIds.size) {
        return;
    }
    const entities = getTopologyEntities();
    const entityMap = new Map(entities.map((entity) => [entity.id, entity]));
    topologyState.selectedEntityIds.forEach((entityId) => {
        const entity = entityMap.get(entityId);
        if (!entity) {
            return;
        }
        const layout = getTopologyEntityLayout(entity);
        setTopologyEntityLayout(
            entityId,
            {
                ...layout,
                x: layout.x + deltaX,
                y: layout.y + deltaY,
            },
            { persist: false },
        );
    });
    saveTopologyLayoutOverrides();

    // Persist DN positions to DB after nudge
    topologyState.selectedEntityIds.forEach((entityId) => {
        if (entityId.startsWith("dn-")) {
            const siteId = entityId.slice(3);
            const lo = topologyState.layoutOverrides?.[entityId];
            if (lo) {
                fetch(`/api/topology/maps/discovered-nodes/${encodeURIComponent(siteId)}/position`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ x: Math.round(lo.x), y: Math.round(lo.y) }),
                }).catch(() => {});
            }
        }
    });

    renderTopologyStage();
}

function getTopologyStageBounds() {
    const stage = document.getElementById("topology-stage");
    return stage ? stage.getBoundingClientRect() : null;
}

function wireTopologyLayoutEditor(stage, layer, entityMap) {
    stage._topologyEditorLayer = layer;
    stage._topologyEditorEntityMap = entityMap;

    if (stage.dataset.layoutEditorBound === "true") {
        return;
    }

    stage.dataset.layoutEditorBound = "true";

    const getEditorContext = () => ({
        layer: stage._topologyEditorLayer instanceof HTMLElement ? stage._topologyEditorLayer : layer,
        entityMap: stage._topologyEditorEntityMap instanceof Map ? stage._topologyEditorEntityMap : entityMap,
    });

    const clearDragListeners = () => {
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerEnd);
        window.removeEventListener("pointercancel", handlePointerEnd);
    };

    const handlePointerMove = (event) => {
        const drag = topologyState.dragging;
        if (!drag || event.pointerId !== drag.pointerId) {
            return;
        }

        if (drag.mode === "select") {
            const rect = getTopologySelectionRect(drag, event);
            if (!rect) {
                return;
            }
            topologyState.selectedEntityIds = new Set(
                drag.visibleEntityIds.filter((entityId) => {
                    const { entityMap } = getEditorContext();
                    const entity = entityMap.get(entityId);
                    if (!entity) {
                        return false;
                    }
                    const layout = getTopologyEntityLayout(entity);
                    return (
                        layout.x >= rect.left &&
                        layout.x <= rect.left + rect.width &&
                        layout.y >= rect.top &&
                        layout.y <= rect.top + rect.height
                    );
                }),
            );
            syncTopologySelectionBox(rect);
            syncTopologyEntitySelectionStyles(layer);
            event.preventDefault();
            return;
        }

        const deltaX = event.clientX - drag.startX;
        const deltaY = event.clientY - drag.startY;
        const nextLayout = { ...drag.startLayout };

        if (drag.mode === "resize") {
            const nextSize = Math.max(88, Math.round(drag.startLayout.size + Math.max(deltaX, deltaY)));
            const offset = (nextSize - drag.startLayout.size) / 2;
            nextLayout.x = drag.startLayout.x + offset;
            nextLayout.y = drag.startLayout.y + offset;
            nextLayout.size = nextSize;
        } else {
            nextLayout.x = drag.startLayout.x + deltaX;
            nextLayout.y = drag.startLayout.y + deltaY;
        }

        if (drag.mode === "drag-group") {
            drag.entities.forEach((item) => {
                const groupLayout = {
                    ...item.startLayout,
                    x: item.startLayout.x + deltaX,
                    y: item.startLayout.y + deltaY,
                    size: item.startLayout.size,
                };
                setTopologyEntityLayout(item.entityId, groupLayout, { persist: false });
                applyTopologyEntityStyles(item.element, groupLayout);
            });
        } else {
            setTopologyEntityLayout(drag.entityId, nextLayout, { persist: false });
            applyTopologyEntityStyles(drag.element, nextLayout);
        }
        drawTopologyLinks(getEditorContext().entityMap);
        event.preventDefault();
    };

    const handlePointerEnd = (event) => {
        const drag = topologyState.dragging;
        if (!drag || event.pointerId !== drag.pointerId) {
            return;
        }

        try {
            drag.element?.releasePointerCapture?.(drag.pointerId);
        } catch (error) {
            // Ignore capture failures.
        }

        syncTopologySelectionBox(null);
        saveTopologyLayoutOverrides();

        // Persist DN positions to DB after drag
        if (drag.mode === "drag" || drag.mode === "drag-group") {
            const idsToSave = drag.mode === "drag-group"
                ? (drag.entities || []).map((e) => e.entityId)
                : [drag.entityId];
            idsToSave.forEach((eid) => {
                if (eid && eid.startsWith("dn-")) {
                    const siteId = eid.slice(3);
                    const lo = topologyState.layoutOverrides?.[eid];
                    if (lo) {
                        fetch(`/api/topology/maps/discovered-nodes/${encodeURIComponent(siteId)}/position`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ x: Math.round(lo.x), y: Math.round(lo.y) }),
                        }).catch(() => {});
                    }
                }
            });
        }

        topologyState.dragging = null;
        topologyState._lastDragEndTime = Date.now();
        clearDragListeners();
        event.preventDefault();
    };

    document.addEventListener("click", () => {
        if (topologyState.editMode || !topologyState.pinnedTooltipId) {
            return;
        }
        topologyState.pinnedTooltipId = null;
        renderTopologyStage();
    });

    stage.addEventListener("pointerdown", (event) => {
        if (!topologyState.editMode) {
            return;
        }

        const { layer, entityMap } = getEditorContext();

        const target = event.target;
        if (target instanceof Element && target.closest("#topology-state-log-preview, #topology-state-log-flyout")) {
            return;
        }
        const link = target instanceof Element ? target.closest("[data-topology-link-id]") : null;
        if (link) {
            return;
        }
        const button = target instanceof Element ? target.closest("[data-topology-id]") : null;
        if (!(button instanceof HTMLElement)) {
            const stageBounds = getTopologyStageBounds();
            if (!stageBounds) {
                return;
            }
            topologyState.stateLogSelected = false;
            setTopologyActiveLinkHandleTarget(null);
            topologyState.selectedKind = null;
            topologyState.selectedId = null;
            topologyState.dragging = {
                mode: "select",
                pointerId: event.pointerId,
                stageStartX: event.clientX - stageBounds.left,
                stageStartY: event.clientY - stageBounds.top,
                element: layer,
                visibleEntityIds: Array.from(layer.querySelectorAll("[data-topology-id]"))
                    .map((item) => item.getAttribute("data-topology-id") || "")
                    .filter(Boolean),
            };
            clearTopologyEntitySelection();
            syncTopologyEntitySelectionStyles(layer);
            syncTopologySelectionBox({
                left: topologyState.dragging.stageStartX,
                top: topologyState.dragging.stageStartY,
                width: 0,
                height: 0,
            });
            window.addEventListener("pointermove", handlePointerMove);
            window.addEventListener("pointerup", handlePointerEnd);
            window.addEventListener("pointercancel", handlePointerEnd);
            event.preventDefault();
            return;
        }

        const entityId = button.getAttribute("data-topology-id");
        const entity = entityMap.get(entityId || "");
        if (!entity) {
            return;
        }
        topologyState.stateLogSelected = false;

        if (event.shiftKey) {
            if (topologyState.selectedEntityIds.has(entityId)) {
                topologyState.selectedEntityIds.delete(entityId);
            } else {
                topologyState.selectedEntityIds.add(entityId);
            }
            syncTopologyEntitySelectionStyles(layer);
            event.preventDefault();
            event.stopPropagation();
            return;
        }

        const resizeHandle = target instanceof Element ? target.closest("[data-topology-resize-handle]") : null;
        const startLayout = getTopologyEntityLayout(entity);
        if (!resizeHandle) {
            if (!topologyState.selectedEntityIds.has(entityId)) {
                topologyState.selectedEntityIds = new Set([entityId]);
            }
            syncTopologyEntitySelectionStyles(layer);
        }
        topologyState.selectedKind = "entity";
        topologyState.selectedId = entityId;

        const dragEntities = !resizeHandle && topologyState.selectedEntityIds.size > 1
            ? Array.from(topologyState.selectedEntityIds)
                .map((selectedEntityId) => {
                    const selectedEntity = entityMap.get(selectedEntityId);
                    const selectedElement = layer.querySelector(`[data-topology-id="${CSS.escape(selectedEntityId)}"]`);
                    if (!selectedEntity || !(selectedElement instanceof HTMLElement)) {
                        return null;
                    }
                    return {
                        entityId: selectedEntityId,
                        element: selectedElement,
                        startLayout: getTopologyEntityLayout(selectedEntity),
                    };
                })
                .filter(Boolean)
            : null;

        topologyState.dragging = {
            mode: resizeHandle ? "resize" : dragEntities ? "drag-group" : "drag",
            entityId,
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLayout,
            element: button,
            entities: dragEntities,
        };

        try {
            button.setPointerCapture(event.pointerId);
        } catch (error) {
            // Ignore capture failures.
        }

        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", handlePointerEnd);
        window.addEventListener("pointercancel", handlePointerEnd);
        event.preventDefault();
        event.stopPropagation();
    });
}

function setTopologyUnitFocus(unit) {
    const normalizedUnit = normalizeTopologyUnit(unit);
    topologyState.focusUnit = normalizedUnit;
    if (normalizedUnit) {
        topologyState.activeUnits = new Set([normalizedUnit]);
    } else {
        topologyState.activeUnits = new Set(TOPOLOGY_UNITS);
    }
}

function updateTopologyUnitRoute(unit) {
    const url = new URL(window.location.href);
    if (unit) {
        url.searchParams.set("unit", unit);
    } else {
        url.searchParams.delete("unit");
    }
    window.history.pushState({ unit: unit || null }, "", url);
}

function safeStart(loader, label) {
    try {
        const result = loader();
        if (result && typeof result.catch === "function") {
            result.catch((error) => {
                console.error(`Startup loader failed: ${label}`, error);
            });
        }
    } catch (error) {
        console.error(`Startup loader failed: ${label}`, error);
    }
}

function getSavedThemeMode() {
    try {
        return window.localStorage.getItem(themeModeStorageKey) || "system";
    } catch (error) {
        return "system";
    }
}

function getSavedThemeOverlay() {
    try {
        return window.localStorage.getItem(themeOverlayStorageKey) || "off";
    } catch (error) {
        return "off";
    }
}

function saveThemeMode(mode) {
    try {
        window.localStorage.setItem(themeModeStorageKey, mode);
    } catch (error) {
        // Ignore storage failures and keep the app usable.
    }
}

function saveThemeOverlay(overlay) {
    try {
        window.localStorage.setItem(themeOverlayStorageKey, overlay);
    } catch (error) {
        // Ignore storage failures and keep the app usable.
    }
}

function getResolvedTheme(mode) {
    if (mode === "light" || mode === "dark" || mode === "vader") {
        return mode;
    }

    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyThemeMode(mode = getSavedThemeMode()) {
    const resolvedTheme = getResolvedTheme(mode);
    const overlay = getSavedThemeOverlay();
    document.documentElement.setAttribute("data-theme", resolvedTheme);
    document.documentElement.setAttribute("data-theme-mode", mode);
    if (overlay === "4id") {
        document.documentElement.setAttribute("data-theme-overlay", overlay);
    } else {
        document.documentElement.removeAttribute("data-theme-overlay");
    }
    updateThemeControlUi(mode, overlay);
}

function updateThemeControlUi(mode = getSavedThemeMode(), overlay = getSavedThemeOverlay()) {
    const button = document.getElementById("theme-mode-button");
    const menu = document.getElementById("theme-mode-menu");
    if (button) {
        const label = {
            system: "System",
            light: "Light",
            dark: "Dark",
            vader: "Vader",
        }[mode] || mode;
        button.textContent = `Theme: ${label}${overlay === "4id" ? " + 4ID" : ""}`;
    }
    if (menu) {
        menu.querySelectorAll("[data-theme-mode]").forEach((item) => {
            item.setAttribute("aria-pressed", item.getAttribute("data-theme-mode") === mode ? "true" : "false");
        });
        menu.querySelectorAll("[data-theme-overlay]").forEach((item) => {
            item.setAttribute("aria-pressed", item.getAttribute("data-theme-overlay") === overlay ? "true" : "false");
        });
    }
}

function setThemeMenuOpen(open) {
    const menu = document.getElementById("theme-mode-menu");
    if (menu) {
        menu.hidden = !open;
    }
}

function mountThemeControl() {
    const topbar = document.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement) || topbar.querySelector(".theme-mode-control")) {
        return;
    }

    const control = document.createElement("div");
    control.className = "theme-mode-control";
    control.innerHTML = `
        <button type="button" class="button-secondary theme-mode-button" id="theme-mode-button" aria-haspopup="true" aria-expanded="false">
            Theme: System
        </button>
        <div class="theme-mode-menu" id="theme-mode-menu" hidden>
            <button type="button" data-theme-mode="system">System</button>
            <button type="button" data-theme-mode="light">Light</button>
            <button type="button" data-theme-mode="dark">Dark</button>
            <button type="button" data-theme-mode="vader">Vader</button>
            <div class="theme-mode-menu-divider" role="presentation"></div>
            <button type="button" class="theme-overlay-button" data-theme-overlay="4id">4ID Overlay</button>
        </div>
    `;
    topbar.appendChild(control);

    const button = document.getElementById("theme-mode-button");
    const menu = document.getElementById("theme-mode-menu");
    if (!button || !menu) {
        return;
    }

    button.addEventListener("click", (event) => {
        event.stopPropagation();
        const open = menu.hidden;
        setThemeMenuOpen(open);
        button.setAttribute("aria-expanded", open ? "true" : "false");
    });

    menu.addEventListener("click", (event) => {
        event.stopPropagation();
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
            return;
        }
        const mode = target.getAttribute("data-theme-mode");
        const overlay = target.getAttribute("data-theme-overlay");
        if (!mode && !overlay) {
            return;
        }
        if (mode) {
            saveThemeMode(mode);
            applyThemeMode(mode);
        } else if (overlay) {
            const nextOverlay = getSavedThemeOverlay() === overlay ? "off" : overlay;
            saveThemeOverlay(nextOverlay);
            applyThemeMode(getSavedThemeMode());
        }
        setThemeMenuOpen(false);
        button.setAttribute("aria-expanded", "false");
    });

    document.addEventListener("pointerdown", (event) => {
        const target = event.target;
        if (!(target instanceof Node)) {
            return;
        }
        if (!control.contains(target)) {
            setThemeMenuOpen(false);
            button.setAttribute("aria-expanded", "false");
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setThemeMenuOpen(false);
            button.setAttribute("aria-expanded", "false");
        }
    });

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", () => {
        if (getSavedThemeMode() === "system") {
            applyThemeMode("system");
        }
    });

    applyThemeMode();
}

function sortNodesForDisplay(nodes) {
    return [...nodes].sort((left, right) => {
        const leftPriority = statusPriority[left.status] ?? 99;
        const rightPriority = statusPriority[right.status] ?? 99;

        if (leftPriority !== rightPriority) {
            return leftPriority - rightPriority;
        }

        return left.name.localeCompare(right.name);
    });
}

function getSavedDashboardOrder() {
    try {
        const raw = window.localStorage.getItem(dashboardOrderStorageKey);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed)
            ? parsed.map((value) => Number(value)).filter((value) => Number.isFinite(value))
            : [];
    } catch (error) {
        return [];
    }
}

function saveDashboardOrder(nodeIds) {
    try {
        window.localStorage.setItem(dashboardOrderStorageKey, JSON.stringify(nodeIds));
    } catch (error) {
        // Ignore storage failures and keep the dashboard usable.
    }
}

function getSavedAnchorListOrder() {
    try {
        const raw = window.localStorage.getItem(anchorListOrderStorageKey);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.map((value) => String(value)) : [];
    } catch (error) {
        return [];
    }
}

function saveAnchorListOrder(pinKeys) {
    try {
        window.localStorage.setItem(anchorListOrderStorageKey, JSON.stringify(pinKeys));
    } catch (error) {
        // Ignore storage failures and keep the dashboard usable.
    }
}

function sortAnchorListRows(rows) {
    const savedOrder = getSavedAnchorListOrder();
    const orderIndex = new Map(savedOrder.map((key, index) => [key, index]));

    return [...rows].sort((left, right) => {
        const leftKey = String(left.pin_key || `anchor:${left.id}`);
        const rightKey = String(right.pin_key || `anchor:${right.id}`);
        const leftSaved = orderIndex.get(leftKey);
        const rightSaved = orderIndex.get(rightKey);

        if (leftSaved !== undefined && rightSaved !== undefined) {
            return leftSaved - rightSaved;
        }
        if (leftSaved !== undefined) {
            return -1;
        }
        if (rightSaved !== undefined) {
            return 1;
        }

        return String(left.site_name || left.name || "").localeCompare(String(right.site_name || right.name || ""));
    });
}

function updateAnchorListOrderFromDom() {
    const list = document.getElementById("anchor-node-list");
    if (!list) {
        return;
    }
    const keys = Array.from(list.querySelectorAll(".node-list-row[data-node-pin-key]"))
        .map((row) => String(row.getAttribute("data-node-pin-key") || ""))
        .filter(Boolean);
    saveAnchorListOrder(keys);
}

function attachAnchorRowDragAndDrop(row) {
    if (row.dataset.dragBound === "true") {
        return;
    }
    row.dataset.dragBound = "true";

    row.addEventListener("dragstart", (event) => {
        row.classList.add("dragging");
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", row.dataset.nodePinKey || "");
        }
    });

    row.addEventListener("dragend", () => {
        row.classList.remove("dragging");
        document.querySelectorAll(".node-list-row.drag-over").forEach((item) => item.classList.remove("drag-over"));
        updateAnchorListOrderFromDom();
    });

    row.addEventListener("dragover", (event) => {
        event.preventDefault();
        const list = row.parentElement;
        const draggingRow = list?.querySelector(".node-list-row.dragging");

        if (!(list instanceof HTMLElement) || !(draggingRow instanceof HTMLElement) || draggingRow === row) {
            return;
        }

        const rect = row.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;

        document.querySelectorAll(".node-list-row.drag-over").forEach((item) => {
            if (item !== row) {
                item.classList.remove("drag-over");
            }
        });
        row.classList.add("drag-over");

        if (before) {
            list.insertBefore(draggingRow, row);
        } else {
            list.insertBefore(draggingRow, row.nextSibling);
        }
    });

    row.addEventListener("dragleave", () => {
        row.classList.remove("drag-over");
    });

    row.addEventListener("drop", (event) => {
        event.preventDefault();
        row.classList.remove("drag-over");
        updateAnchorListOrderFromDom();
    });
}

function sortDashboardNodes(nodes) {
    const savedOrder = getSavedDashboardOrder();
    const orderIndex = new Map(savedOrder.map((id, index) => [id, index]));

    return [...nodes].sort((left, right) => {
        const leftSaved = orderIndex.get(Number(left.id));
        const rightSaved = orderIndex.get(Number(right.id));

        if (leftSaved !== undefined && rightSaved !== undefined) {
            return leftSaved - rightSaved;
        }

        if (leftSaved !== undefined) {
            return -1;
        }

        if (rightSaved !== undefined) {
            return 1;
        }

        const leftSite = (left.site || "").toLowerCase();
        const rightSite = (right.site || "").toLowerCase();

        if (leftSite !== rightSite) {
            return leftSite.localeCompare(rightSite);
        }

        const leftName = (left.name || "").toLowerCase();
        const rightName = (right.name || "").toLowerCase();

        if (leftName !== rightName) {
            return leftName.localeCompare(rightName);
        }

        return Number(left.id) - Number(right.id);
    });
}

function updateDashboardOrderFromDom() {
    const grid = document.getElementById("nodeGrid");

    if (!grid) {
        return;
    }

    const nodeIds = Array.from(grid.querySelectorAll(".node-card[data-node-id]"))
        .map((card) => Number(card.getAttribute("data-node-id")))
        .filter((id) => Number.isFinite(id));

    saveDashboardOrder(nodeIds);
}

function attachDashboardDragAndDrop(card) {
    card.addEventListener("dragstart", (event) => {
        card.classList.add("dragging");
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", card.dataset.nodeId || "");
        }
    });

    card.addEventListener("dragend", () => {
        card.classList.remove("dragging");
        document.querySelectorAll(".node-card.drag-over").forEach((item) => item.classList.remove("drag-over"));
        updateDashboardOrderFromDom();
    });

    card.addEventListener("dragover", (event) => {
        event.preventDefault();
        const grid = card.parentElement;
        const draggingCard = grid?.querySelector(".node-card.dragging");

        if (!(grid instanceof HTMLElement) || !(draggingCard instanceof HTMLElement) || draggingCard === card) {
            return;
        }

        const rect = card.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;

        document.querySelectorAll(".node-card.drag-over").forEach((item) => {
            if (item !== card) {
                item.classList.remove("drag-over");
            }
        });
        card.classList.add("drag-over");

        if (before) {
            grid.insertBefore(draggingCard, card);
        } else {
            grid.insertBefore(draggingCard, card.nextSibling);
        }
    });

    card.addEventListener("dragleave", () => {
        card.classList.remove("drag-over");
    });

    card.addEventListener("drop", (event) => {
        event.preventDefault();
        card.classList.remove("drag-over");
        updateDashboardOrderFromDom();
    });
}

function formatLastChecked(timestamp) {
    if (!timestamp) {
        return "Not checked";
    }

    return new Date(timestamp).toLocaleTimeString();
}

function formatDashboardTimestamp(timestamp) {
    if (!timestamp) {
        return "No recent check";
    }

    return new Date(timestamp).toLocaleTimeString();
}

function formatRate(bps) {
    if (!bps) {
        return "0 bps";
    }

    if (bps >= 1e6) {
        return `${(bps / 1e6).toFixed(1)} Mbps`;
    }

    if (bps >= 1e3) {
        return `${(bps / 1e3).toFixed(1)} Kbps`;
    }

    return `${bps} bps`;
}

function formatCpuPercent(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
        return "--";
    }

    return `${value.toFixed(1)}%`;
}

function getPinnedIds(storageKey) {
    try {
        const raw = window.localStorage.getItem(storageKey);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed)
            ? parsed.map((value) => Number(value)).filter((value) => Number.isFinite(value))
            : [];
    } catch (error) {
        return [];
    }
}

function savePinnedIds(storageKey, ids) {
    try {
        window.localStorage.setItem(storageKey, JSON.stringify(ids));
    } catch (error) {
        // Ignore storage failures and keep the app usable.
    }
}

function getPinnedNodeIds() {
    return getPinnedNodeKeys()
        .map((value) => {
            const match = /^anchor:(\d+)$/.exec(String(value));
            return match ? Number(match[1]) : null;
        })
        .filter((value) => Number.isFinite(value));
}

function getPinnedNodeKeys() {
    try {
        const raw = window.localStorage.getItem(pinnedNodesStorageKey);
        const parsed = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) {
            return [];
        }
        return parsed.map((value) => {
            if (typeof value === "number") {
                return `anchor:${value}`;
            }
            const normalized = String(value);
            return /^\d+$/.test(normalized) ? `anchor:${normalized}` : normalized;
        });
    } catch (error) {
        return [];
    }
}

function getPinnedServiceIds() {
    return getPinnedIds(pinnedServicesStorageKey);
}

function getPinnedTopologyServices() {
    const services = Array.isArray(topologyDashboardServicesPayload?.services)
        ? topologyDashboardServicesPayload.services
        : [];
    const pinnedIds = new Set(
        getPinnedServiceIds()
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value)),
    );

    if (!services.length || !pinnedIds.size) {
        return [];
    }

    return services.filter((service) => pinnedIds.has(Number(service.id)));
}

function getTopologyServiceCloudSummary() {
    const services = getPinnedTopologyServices();
    const summary = {
        total: services.length,
        healthy: 0,
        degraded: 0,
        down: 0,
        status: "neutral",
        services,
    };

    if (!services.length) {
        return summary;
    }

    const normalizedStatuses = services.map((service) => String(service.status || "unknown").trim().toLowerCase());
    normalizedStatuses.forEach((status) => {
        if (status === "healthy") {
            summary.healthy += 1;
        } else if (status === "degraded") {
            summary.degraded += 1;
        } else {
            summary.down += 1;
        }
    });

    if (normalizedStatuses.every((status) => status === "healthy")) {
        summary.status = "healthy";
    } else if (normalizedStatuses.every((status) => ["failed", "unknown", "disabled"].includes(status))) {
        summary.status = "down";
    } else {
        summary.status = "degraded";
    }

    return summary;
}

function buildTopologyServiceCloudEntity() {
    const summary = getTopologyServiceCloudSummary();
    return {
        id: "services-cloud",
        kind: "services-cloud",
        name: "Services",
        location: "Cloud",
        level: 0,
        unit: "Services",
        status: summary.status,
        service_summary: summary,
        metrics_text: summary.total
            ? `${summary.total} pinned service check${summary.total === 1 ? "" : "s"} from the main dashboard watchlist.`
            : "Pin service checks from the Services Dashboard to bind this cloud status.",
    };
}

function togglePinnedNodeId(nodeId) {
    const current = new Set(getPinnedNodeIds());
    if (current.has(nodeId)) {
        current.delete(nodeId);
    } else {
        current.add(nodeId);
    }
    savePinnedIds(pinnedNodesStorageKey, Array.from(current));
}

function togglePinnedNodeKey(nodeKey) {
    const current = new Set(getPinnedNodeKeys());
    if (current.has(nodeKey)) {
        current.delete(nodeKey);
    } else {
        current.add(nodeKey);
    }
    savePinnedIds(pinnedNodesStorageKey, Array.from(current));
}

function togglePinnedServiceId(serviceId) {
    const current = new Set(getPinnedServiceIds());
    if (current.has(serviceId)) {
        current.delete(serviceId);
    } else {
        current.add(serviceId);
    }
    savePinnedIds(pinnedServicesStorageKey, Array.from(current));
}

function openNode(nodeId) {
    window.location.href = `/nodes/${nodeId}`;
}

function openNodeDetail(detailUrl) {
    if (!detailUrl) {
        return;
    }
    window.location.href = detailUrl;
}

window.openNode = openNode;

async function copySshCommand(host, username = "") {
    const sshTarget = username ? `${username}@${host}` : host;
    const sshCommand = `ssh ${sshTarget}`;
    await navigator.clipboard.writeText(sshCommand);
}

function openWebForNode(host, webPort, webScheme = "https") {
    window.open(`${webScheme}://${host}:${webPort}`, "_blank", "noopener");
}

function getDashboardRefreshSeconds() {
    const raw = window.localStorage.getItem(dashboardRefreshStorageKey);
    const seconds = Number(raw);

    if ([10, 30, 60, 300, 1800, 3600].includes(seconds)) {
        return seconds;
    }

    return 60;
}

function setDashboardRefreshSeconds(seconds) {
    window.localStorage.setItem(dashboardRefreshStorageKey, String(seconds));
}

function formatRefreshLabel(seconds) {
    if (seconds === 10) {
        return "10 sec";
    }
    if (seconds === 30) {
        return "30 sec";
    }
    if (seconds === 60) {
        return "1 min";
    }
    if (seconds === 300) {
        return "5 min";
    }
    if (seconds === 1800) {
        return "30 min";
    }
    if (seconds === 3600) {
        return "1 hr";
    }
    return `${seconds} sec`;
}

function updateDashboardRefreshButton() {
    const refreshButton = document.getElementById("dashboard-refresh-button");

    if (!refreshButton) {
        return;
    }

    refreshButton.textContent = formatRefreshLabel(getDashboardRefreshSeconds());
}

function setDashboardRefreshMenuOpen(isOpen) {
    const dashboardRefreshMenu = document.getElementById("dashboard-refresh-menu");

    if (!dashboardRefreshMenu) {
        return;
    }

    dashboardRefreshMenu.hidden = !isOpen;
}

function showDashboardFeedback(message) {
    const lastUpdated = document.getElementById("dashboard-last-updated");

    if (!lastUpdated) {
        return;
    }

    lastUpdated.textContent = message;
}

function buildNodeDashboardRequestUrl(basePath) {
    const url = new URL(basePath, window.location.origin);
    url.searchParams.set("window_seconds", String(getDashboardRefreshSeconds()));
    return `${url.pathname}${url.search}`;
}

function disconnectNodeDashboardStream() {
    if (nodeDashboardEventSource) {
        nodeDashboardEventSource.close();
        nodeDashboardEventSource = null;
    }
}

function connectNodeDashboardStream() {
    disconnectNodeDashboardStream();
}

function applyDashboardRefreshInterval() {
    if (dashboardRefreshTimer) {
        window.clearInterval(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
    }

    const seconds = getDashboardRefreshSeconds();
    updateDashboardRefreshButton();
    setDashboardRefreshMenuOpen(false);

    if (seconds > 0 && document.getElementById("anchor-node-list") && document.getElementById("discovered-node-list")) {
        dashboardRefreshTimer = window.setInterval(() => {
            loadNodeDashboard();
        }, seconds * 1000);
        return;
    }

    if (seconds > 0 && document.getElementById("nodeGrid")) {
        dashboardRefreshTimer = window.setInterval(() => {
            loadNodeDashboard();
            loadMainDashboard();
            loadServicesDashboard();
        }, seconds * 1000);
        return;
    }

    if (seconds > 0 && document.getElementById("mainNodeGrid")) {
        dashboardRefreshTimer = window.setInterval(() => {
            loadMainDashboard();
        }, seconds * 1000);
        return;
    }

    if (seconds > 0 && document.getElementById("dashboardServicesBody")) {
        dashboardRefreshTimer = window.setInterval(() => {
            loadServicesDashboard();
        }, seconds * 1000);
        return;
    }

    if (seconds > 0 && document.getElementById("topology-root")) {
        dashboardRefreshTimer = window.setInterval(() => {
            refreshTopologyPage();
        }, seconds * 1000);
        return;
    }

    if (seconds > 0 && document.getElementById("node-detail-root")) {
        dashboardRefreshTimer = window.setInterval(() => {
            loadNodeDetailPage();
        }, seconds * 1000);
    }
}

function applyTopologyRefreshInterval() {
    if (topologyRefreshTimer) {
        window.clearInterval(topologyRefreshTimer);
        topologyRefreshTimer = null;
    }
    startTopologyTimers();
}

function statusCell(node) {
    return `
        <div class="status-stack">
            <span class="status-badge ${node.status}">${node.status}</span>
        </div>
    `;
}

function getDashboardServiceIcon(iconName) {
    if (iconName === "web") {
        return `
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <circle cx="12" cy="12" r="8.5"></circle>
                <path d="M3.5 12h17"></path>
                <path d="M12 3.5a13 13 0 0 1 0 17"></path>
                <path d="M12 3.5a13 13 0 0 0 0 17"></path>
            </svg>
        `;
    }

    return `
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <rect x="6.5" y="10.5" width="11" height="9" rx="2"></rect>
            <path d="M9 10.5V8.5a3 3 0 0 1 6 0v2"></path>
        </svg>
    `;
}

function dashboardServiceItem(label, iconName, action, node) {
    const iconMarkup = getDashboardServiceIcon(iconName);

    if (action === "open-web") {
        return `
            <a
                class="dashboard-service-pill"
                data-dashboard-link="true"
                href="${node.web_scheme || "https"}://${node.host}:${node.web_port}"
                target="_blank"
                rel="noopener"
                title="${label}"
                draggable="false"
            >
                <span class="dashboard-service-glyph" aria-hidden="true">${iconMarkup}</span>
                <span>${label}</span>
            </a>
        `;
    }

    return `
        <button
            type="button"
            class="dashboard-service-pill"
            data-dashboard-action="${action}"
            data-host="${node.host}"
            data-ssh-username="${node.ssh_username || ""}"
            data-web-port="${node.web_port}"
            data-web-scheme="${node.web_scheme || "https"}"
            title="${label}"
            draggable="false"
        >
            <span class="dashboard-service-glyph" aria-hidden="true">${iconMarkup}</span>
            <span>${label}</span>
        </button>
    `;
}

function serviceItem(label, icon, isOk, action, nodeId) {
    const stateClass = isOk ? "ok" : "down";
    return `
        <button
            type="button"
            class="service-link ${stateClass}"
            data-action="${action}"
            data-id="${nodeId}"
            title="${label}"
        >
            <span class="service-icon">${icon}</span>
            <span>${label}</span>
        </button>
    `;
}

function servicesCell(node) {
    return `
        <div class="service-list">
            ${serviceItem("Web", "\uD83C\uDF10", node.web_ok, "open-web", node.id)}
            ${serviceItem("SSH", "\uD83D\uDD10", node.ssh_ok, "ssh", node.id)}
        </div>
    `;
}

function showFeedback(message) {
    const feedback = document.getElementById("nodes-feedback");

    if (!feedback) {
        return;
    }

    feedback.textContent = message;
    feedback.hidden = false;
}

function clearFeedback() {
    const feedback = document.getElementById("nodes-feedback");

    if (!feedback) {
        return;
    }

    feedback.hidden = true;
    feedback.textContent = "";
}

function showServicesFeedback(message) {
    const feedback = document.getElementById("services-feedback");

    if (!feedback) {
        return;
    }

    feedback.textContent = message;
    feedback.hidden = false;
}

function clearServicesFeedback() {
    const feedback = document.getElementById("services-feedback");

    if (!feedback) {
        return;
    }

    feedback.hidden = true;
    feedback.textContent = "";
}

function renderNormalizedTelemetrySummary(nodeName, normalized, rawTelemetry = null) {
    const panel = document.getElementById("telemetry-panel");
    const title = document.getElementById("telemetry-title");
    const summary = document.getElementById("telemetry-summary");
    const error = document.getElementById("telemetry-error");

    if (!panel || !title || !summary || !error) {
        return;
    }

    const items = [
        ["Latency", normalized.latency_ms != null ? `${normalized.latency_ms} ms` : "--"],
        ["Tx Rate", formatRate(normalized.tx_bps)],
        ["Rx Rate", formatRate(normalized.rx_bps)],
        ["CPU Avg", normalized.cpu_avg != null ? `${normalized.cpu_avg.toFixed(1)}%` : "--"],
        ["Discovered Sites", normalized.discovered_sites ?? 0],
        ["Active", normalized.is_active ? "Yes" : "No"],
    ];

    if (rawTelemetry && typeof rawTelemetry === "object") {
        if (Array.isArray(rawTelemetry.rxTunnelLock)) {
            items.push(["Tunnel Locks", rawTelemetry.rxTunnelLock.join(", ")]);
        }

        if (rawTelemetry.mateTunnelFeedback) {
            items.push(["Mate Feedback", rawTelemetry.mateTunnelFeedback]);
        }
    }

    title.textContent = `Telemetry: ${nodeName}`;
    summary.innerHTML = items
        .map(
            ([key, value]) => `
                <div class="telemetry-item">
                    <span class="telemetry-key">${key}</span>
                    <span class="telemetry-value">${value}</span>
                </div>
            `,
        )
        .join("");
    error.hidden = true;
    panel.hidden = false;
}

function showTelemetryError(message = "Unable to retrieve telemetry") {
    const panel = document.getElementById("telemetry-panel");
    const error = document.getElementById("telemetry-error");
    const summary = document.getElementById("telemetry-summary");

    if (!panel || !error || !summary) {
        return;
    }

    summary.innerHTML = "";
    error.textContent = message;
    error.hidden = false;
    panel.hidden = false;
}

function resetNodeForm() {
    const form = document.getElementById("node-form");
    const formTitle = document.getElementById("node-form-title");
    const submitButton = document.getElementById("node-submit-button");
    const cancelButton = document.getElementById("node-cancel-button");
    const formError = document.getElementById("node-form-error");
    const saveAddAnotherButton = document.getElementById("node-save-add-another-button");
    const deleteButton = document.getElementById("node-delete-button");

    if (!form) {
        return;
    }

    form.reset();
    currentEditNodeId = null;
    document.getElementById("node-id").value = "";
    document.getElementById("node-web-port").value = "443";
    document.getElementById("node-ssh-port").value = "22";
    document.getElementById("node-ping-enabled").checked = true;
    document.getElementById("node-ping-interval").value = "15";
    const topologyRoot = document.getElementById("topology-root");
    const defaultLevel = topologyRoot && topologyState.focusUnit ? "1" : "0";
    const defaultUnit = topologyRoot ? (topologyState.focusUnit || (defaultLevel === "0" ? "AGG" : "DIV HQ")) : "AGG";
    const nodeIdField = document.getElementById("node-node-id");
    const includeInTopologyField = document.getElementById("node-include-in-topology");
    const topologyLevelField = document.getElementById("node-topology-level");
    const topologyUnitField = document.getElementById("node-topology-unit");
    const enabledField = document.getElementById("node-enabled");

    if (nodeIdField instanceof HTMLInputElement) {
        nodeIdField.value = "";
    }
    if (includeInTopologyField instanceof HTMLInputElement) {
        includeInTopologyField.checked = Boolean(topologyRoot);
    }
    if (topologyLevelField instanceof HTMLSelectElement || topologyLevelField instanceof HTMLInputElement) {
        topologyLevelField.value = defaultLevel;
    }
    if (topologyUnitField instanceof HTMLSelectElement || topologyUnitField instanceof HTMLInputElement) {
        topologyUnitField.value = defaultUnit;
    }
    if (enabledField instanceof HTMLInputElement) {
        enabledField.checked = true;
    }
    if (topologyRoot) {
        const locationField = document.getElementById("node-location");
        if (locationField instanceof HTMLInputElement) {
            locationField.value = topologyState.focusUnit ? "Cloud" : "Cloud";
        }
    }
    formTitle.textContent = "Add Node";
    submitButton.textContent = "Save";
    cancelButton.hidden = false;
    if (saveAddAnotherButton) {
        saveAddAnotherButton.hidden = false;
    }
    if (deleteButton) {
        deleteButton.hidden = true;
    }
    formError.hidden = true;
    formError.textContent = "Unable to save node";
    keepNodeModalOpenAfterSave = false;
    syncTopologyFormFields();
}

function getNodeModalShell() {
    return document.getElementById("node-modal-shell");
}

function openNodeModal(options = {}) {
    const modalShell = getNodeModalShell();
    if (!modalShell) {
        return;
    }
    if (options.reset !== false) {
        resetNodeForm();
    }
    modalShell.hidden = false;
    document.body.classList.add("modal-open");
}

function closeNodeModal() {
    const modalShell = getNodeModalShell();
    if (!modalShell) {
        return;
    }
    modalShell.hidden = true;
    document.body.classList.remove("modal-open");
    keepNodeModalOpenAfterSave = false;
}

function getTopologyInventoryShell() {
    return document.getElementById("topology-inventory-shell");
}

function openTopologyInventory() {
    const shell = getTopologyInventoryShell();
    if (!shell) {
        return;
    }
    shell.hidden = false;
    document.body.classList.add("modal-open");
}

function closeTopologyInventory() {
    const shell = getTopologyInventoryShell();
    if (!shell) {
        return;
    }
    shell.hidden = true;
    document.body.classList.remove("modal-open");
}

function getTopologyDetailShell() {
    return document.getElementById("topology-detail-shell");
}

function openTopologyDetail(detailUrl, title = "Node Detail") {
    if (!detailUrl) {
        return;
    }
    const shell = getTopologyDetailShell();
    const frame = document.getElementById("topology-detail-frame");
    const titleElement = document.getElementById("topology-detail-title");
    if (!shell || !(frame instanceof HTMLIFrameElement)) {
        return;
    }
    if (titleElement) {
        titleElement.textContent = title;
    }
    const embeddedDetailUrl = new URL(detailUrl, window.location.origin);
    embeddedDetailUrl.searchParams.set("embedded", "1");
    frame.src = `${embeddedDetailUrl.pathname}${embeddedDetailUrl.search}`;
    shell.hidden = false;
    document.body.classList.add("modal-open");
}

function closeTopologyDetail() {
    const shell = getTopologyDetailShell();
    const frame = document.getElementById("topology-detail-frame");
    if (!shell) {
        return;
    }
    shell.hidden = true;
    document.body.classList.remove("modal-open");
    if (frame instanceof HTMLIFrameElement) {
        frame.removeAttribute("src");
    }
}

function syncTopologyFormFields() {
    const includeCheckbox = document.getElementById("node-include-in-topology");
    const levelField = document.getElementById("node-topology-level");
    const unitField = document.getElementById("node-topology-unit");

    if (!(includeCheckbox instanceof HTMLInputElement)) {
        return;
    }

    if (
        !(
            levelField instanceof HTMLSelectElement ||
            levelField instanceof HTMLInputElement
        ) ||
        !(
            unitField instanceof HTMLSelectElement ||
            unitField instanceof HTMLInputElement
        )
    ) {
        return;
    }

    const enabled = includeCheckbox.checked;
    levelField.disabled = !enabled;
    unitField.disabled = !enabled;

    if (!enabled) {
        return;
    }

    if (levelField.value === "0") {
        unitField.value = "AGG";
    } else if (unitField.value === "AGG") {
        unitField.value = "DIV HQ";
    }
}

function populateNodeForm(nodeId) {
    const node = currentNodes.find((entry) => entry.id === nodeId);
    const saveAddAnotherButton = document.getElementById("node-save-add-another-button");
    const deleteButton = document.getElementById("node-delete-button");

    if (!node) {
        return;
    }

    document.getElementById("node-id").value = String(node.id);
    document.getElementById("node-name").value = node.name;
    document.getElementById("node-host").value = node.host;
    document.getElementById("node-web-port").value = String(node.web_port);
    document.getElementById("node-ssh-port").value = String(node.ssh_port);
    document.getElementById("node-location").value = node.location;
    const nodeIdField = document.getElementById("node-node-id");
    const includeInTopologyField = document.getElementById("node-include-in-topology");
    const topologyLevelField = document.getElementById("node-topology-level");
    const topologyUnitField = document.getElementById("node-topology-unit");
    const enabledField = document.getElementById("node-enabled");

    if (nodeIdField instanceof HTMLInputElement) {
        nodeIdField.value = node.node_id ?? "";
    }
    if (includeInTopologyField instanceof HTMLInputElement) {
        includeInTopologyField.checked = Boolean(node.include_in_topology);
    }
    if (topologyLevelField instanceof HTMLSelectElement || topologyLevelField instanceof HTMLInputElement) {
        topologyLevelField.value = String(node.topology_level ?? 0);
    }
    if (topologyUnitField instanceof HTMLSelectElement || topologyUnitField instanceof HTMLInputElement) {
        topologyUnitField.value = node.topology_unit ?? "AGG";
    }
    if (enabledField instanceof HTMLInputElement) {
        enabledField.checked = node.enabled;
    }
    document.getElementById("node-notes").value = node.notes ?? "";
    document.getElementById("node-api-username").value = node.api_username ?? "";
    document.getElementById("node-api-password").value = node.api_password ?? "";
    document.getElementById("node-api-use-https").checked = node.api_use_https;
    document.getElementById("node-ping-enabled").checked = node.ping_enabled !== false;
    document.getElementById("node-ping-interval").value = String(node.ping_interval_seconds ?? 15);
    document.getElementById("node-form-title").textContent = `Edit ${node.name}`;
    document.getElementById("node-submit-button").textContent = "Save";
    document.getElementById("node-cancel-button").hidden = false;
    if (saveAddAnotherButton) {
        saveAddAnotherButton.hidden = true;
    }
    if (deleteButton) {
        deleteButton.hidden = false;
    }
    document.getElementById("node-form-error").hidden = true;
    currentEditNodeId = node.id;
    syncTopologyFormFields();
    renderNodesTable(currentNodes);
    openNodeModal({ reset: false });
}

async function deleteCurrentNodeFromModal() {
    const nodeId = document.getElementById("node-id").value;
    const formError = document.getElementById("node-form-error");
    if (!nodeId) {
        return;
    }
    const node = currentNodes.find((entry) => String(entry.id) === String(nodeId));
    if (!window.confirm(`Delete ${node?.name || "this node"}?`)) {
        return;
    }
    try {
        await apiRequest(`/api/nodes/${nodeId}`, { method: "DELETE" });
        currentNodes = currentNodes.filter((entry) => String(entry.id) !== String(nodeId));
        removeTopologyEntityLayout(`node-${nodeId}`);
        clearTopologyEntityLabel(`node-${nodeId}`);
        currentEditNodeId = null;
        resetNodeForm();
        closeNodeModal();
        await loadNodes();
        await loadNodeDashboard();
        await loadMainDashboard();
        if (document.getElementById("topology-root")) {
            topologyState.selectedKind = null;
            topologyState.selectedId = null;
            clearTopologyEntitySelection();
            await loadTopologyPage();
        }
        showFeedback("Node deleted.");
    } catch (error) {
        formError.textContent = error.message || "Unable to delete node";
        formError.hidden = false;
    }
}

function renderNodesTable(nodes) {
    const tableBody = document.getElementById("nodes-table-body");

    if (!tableBody) {
        return;
    }

    if (nodes.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="table-message">No nodes have been added yet.</td>
            </tr>
        `;
        return;
    }

    tableBody.innerHTML = nodes
        .map(
            (node) => `
                <tr class="${currentEditNodeId === node.id ? "row-editing" : ""}">
                    <td>${node.name}</td>
                    <td>${escapeHtml(node.node_id ?? "--")}</td>
                    <td>${escapeHtml(node.version ?? "--")}</td>
                    <td>${node.host}</td>
                    <td>${node.location}</td>
                    <td>${servicesCell(node)}</td>
                    <td>${statusCell(node)}</td>
                    <td class="action-cell">
                        <button type="button" class="inventory-action-button" data-action="edit" data-id="${node.id}" title="Edit" aria-label="Edit">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                                <path d="m3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25Zm2.92 2.33H5v-.92l8.06-8.06l.92.92L5.92 19.58ZM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.3a1 1 0 0 0-1.41 0l-1.54 1.54l3.75 3.75l1.54-1.55Z"/>
                            </svg>
                        </button>
                        <button type="button" class="inventory-action-button inventory-action-button-danger" data-action="delete" data-id="${node.id}" title="Delete" aria-label="Delete">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                                <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm1 6h2v9h-2V9Zm4 0h2v9h-2V9ZM7 9h2v9H7V9Zm-1 12h12a2 2 0 0 0 2-2V8H4v11a2 2 0 0 0 2 2Z"/>
                            </svg>
                        </button>
                    </td>
                </tr>
            `,
        )
        .join("");
}

function renderDashboard(nodes, options = {}) {
    const grid = document.getElementById(options.gridId || "nodeGrid");
    const lastUpdated = document.getElementById(options.lastUpdatedId || "dashboard-last-updated");
    const nodeCount = document.getElementById(options.countId || "dashboard-node-count");
    const showPin = options.showPin ?? true;
    const pinnedKeys = new Set(options.pinnedNodeKeys ?? getPinnedNodeKeys());
    const emptyTitle = options.emptyTitle || "No nodes configured";
    const emptySubtitle = options.emptySubtitle || "Add nodes from the inventory page to populate the dashboard.";

    if (!grid || !lastUpdated || !nodeCount) {
        return;
    }

    if (nodes.length === 0) {
        setDashboardRefreshMenuOpen(false);
        grid.innerHTML = `
            <article class="node-card">
                <div class="node-header">
                    <div class="node-name">${emptyTitle}</div>
                </div>
                <div class="node-sub">${emptySubtitle}</div>
            </article>
        `;
        nodeCount.textContent = "0";
        lastUpdated.textContent = new Date().toLocaleTimeString();
        return;
    }

    const sortedNodes = sortDashboardNodes(nodes);
    setDashboardRefreshMenuOpen(false);

    grid.innerHTML = "";
    sortedNodes.forEach((node) => {
        const pinKey = node.pin_key || `anchor:${node.id}`;
        const isPinned = pinnedKeys.has(pinKey);
        const nodeName = node.name || node.site_name || node.site_id || "Unknown";
        const nodeSub = `${node.site || node.location || "--"} · ${node.host || "--"}`;
        const sitesUp = Number.isFinite(Number(node.sites_up)) ? Number(node.sites_up) : "--";
        const sitesTotal = Number.isFinite(Number(node.sites_total)) ? Number(node.sites_total) : "--";
        const cpuValue = node.cpu_avg != null ? formatCpuPercent(node.cpu_avg) : "--";
        const txValue = node.tx_display || formatRate(node.tx_bps || node.tx_rate || 0);
        const rxValue = node.rx_display || formatRate(node.rx_bps || node.rx_rate || 0);
        const latencyValue = node.latency_ms == null ? "--" : node.latency_ms;
        const card = document.createElement("article");
        card.className = `node-card node-card-${node.status}`;
        card.dataset.nodeId = String(node.id ?? node.site_id ?? pinKey);
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("draggable", "true");
        card.innerHTML = `
            <div class="node-card-glow"></div>
                <div class="node-header">
                    <div>
                    <div class="node-name">${escapeHtml(nodeName)}</div>
                    <div class="node-sub">${escapeHtml(nodeSub)}</div>
                    <div class="node-version">${node.version && node.version !== "--" ? node.version : ""}</div>
                </div>
                <div class="node-header-actions">
                    ${showPin ? `
                        <button
                            type="button"
                            class="dashboard-pin-button ${isPinned ? "pinned" : ""}"
                            data-dashboard-action="toggle-node-pin"
                            data-node-pin-key="${escapeHtml(pinKey)}"
                            title="${isPinned ? "Remove from main dashboard" : "Add to main dashboard"}"
                            draggable="false"
                        >
                            ★
                        </button>
                    ` : ""}
                    <div class="service-list service-list-dashboard">
                        ${dashboardServiceItem("Web", "web", "open-web", node)}
                        ${dashboardServiceItem("SSH", "ssh", "ssh", node)}
                    </div>
                </div>
            </div>

            <div class="node-strip">
                <span class="metric-chip metric-chip-rtt ping-${escapeHtml(String(getNodeListRttState(latencyValue, node)))}">
                    <span class="ping-dot ${node.ping_ok ? "up" : "down"}" title="${node.ping_ok ? "Ping reachable" : "Ping unreachable"}"></span>
                    <span class="metric-chip-label">RTT</span>
                    <span class="metric-chip-value">${escapeHtml(String(latencyValue))}${latencyValue === "--" ? "" : " ms"}</span>
                </span>
            </div>

              <div class="node-metrics">
                  <div class="metric-block">
                      <span class="metric-label">Sites</span>
                      <strong>${escapeHtml(String(sitesUp))}/${escapeHtml(String(sitesTotal))}</strong>
                  </div>
                  <div class="metric-block">
                      <span class="metric-label">CPU</span>
                      <strong>${escapeHtml(String(cpuValue))}</strong>
                  </div>
                  <div class="metric-block metric-block-traffic">
                      <span class="metric-label">Tx / Rx</span>
                      <strong>${escapeHtml(String(txValue))} / ${escapeHtml(String(rxValue))}</strong>
                  </div>
              </div>
          `;
        card.addEventListener("click", (event) => {
            const target = event.target;

            if (target instanceof Element && target.closest(".service-link, .dashboard-service-pill, .dashboard-view-button")) {
                return;
            }

            if (node.detail_url) {
                openNodeDetail(node.detail_url);
                return;
            }
            openNode(node.id);
        });
        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (node.detail_url) {
                    openNodeDetail(node.detail_url);
                    return;
                }
                openNode(node.id);
            }
        });
        card.querySelectorAll("[data-dashboard-action]").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.stopPropagation();

                const action = button.getAttribute("data-dashboard-action");
                const host = button.getAttribute("data-host") || "";
                const sshUsername = button.getAttribute("data-ssh-username") || "";
                const webPort = Number(button.getAttribute("data-web-port"));
                const webScheme = button.getAttribute("data-web-scheme") || "https";

                if (action === "open-web") {
                    openWebForNode(host, webPort, webScheme);
                    return;
                }

                if (action === "ssh") {
                    copySshCommand(host, sshUsername)
                        .then(() => {
                            showDashboardFeedback("SSH command copied");
                        })
                        .catch(() => {
                            showDashboardFeedback("Unable to copy SSH command");
                        });
                    return;
                }

                if (action === "toggle-node-pin") {
                    togglePinnedNodeKey(button.getAttribute("data-node-pin-key") || pinKey);
                    if (document.getElementById("mainNodeGrid")) {
                        loadMainDashboard();
                    } else {
                        loadNodeDashboard();
                    }
                }
            });
        });
        card.querySelectorAll("[data-dashboard-link]").forEach((link) => {
            link.addEventListener("click", (event) => {
                event.stopPropagation();
            });
        });
        attachDashboardDragAndDrop(card);
        grid.appendChild(card);
    });

    nodeCount.textContent = String(sortedNodes.length);
    lastUpdated.textContent = new Date().toLocaleTimeString();
}

function formatServiceLatency(latencyMs) {
    if (typeof latencyMs !== "number") {
        return "--";
    }

    return `${latencyMs} ms`;
}

function formatServiceLastChecked(timestamp) {
    if (!timestamp) {
        return "Pending";
    }

    return new Date(timestamp).toLocaleTimeString();
}

function renderDashboardServices(payload, options = {}) {
    const body = document.getElementById(options.bodyId || "dashboardServicesBody");
    const error = document.getElementById(options.errorId || "dashboard-services-error");
    const total = document.getElementById(options.totalId || "dashboard-services-total");
    const healthy = document.getElementById(options.healthyId || "dashboard-services-healthy");
    const degraded = document.getElementById(options.degradedId || "dashboard-services-degraded");
    const failed = document.getElementById(options.failedId || "dashboard-services-failed");
    const showPin = options.showPin ?? false;
    const pinnedIds = new Set(options.pinnedServiceIds ?? getPinnedServiceIds());
    const filterPinned = options.filterPinned ?? false;
    const emptyMessage = options.emptyMessage || "No service checks configured yet.";

    if (!body || !error || !total || !healthy || !degraded || !failed) {
        return;
    }

    if (!payload || !Array.isArray(payload.services) || payload.services.length === 0) {
        body.innerHTML = `
            <tr>
                <td colspan="${showPin ? "8" : "7"}" class="table-message">${emptyMessage}</td>
            </tr>
        `;
        total.textContent = "0";
        healthy.textContent = "0";
        degraded.textContent = "0";
        failed.textContent = "0";
        error.hidden = true;
        return;
    }

    const services = [...payload.services].sort((left, right) => {
        const priority = {
            failed: 0,
            degraded: 1,
            healthy: 2,
            unknown: 3,
            disabled: 4,
        };
        const leftPriority = priority[left.status] ?? 99;
        const rightPriority = priority[right.status] ?? 99;
        if (leftPriority !== rightPriority) {
            return leftPriority - rightPriority;
        }
        return String(left.name || "").localeCompare(String(right.name || ""));
    });
    const visibleServices = filterPinned
        ? services.filter((service) => pinnedIds.has(Number(service.id)))
        : services;

    if (!visibleServices.length) {
        body.innerHTML = `
            <tr>
                <td colspan="${showPin ? "8" : "7"}" class="table-message">${emptyMessage}</td>
            </tr>
        `;
        total.textContent = "0";
        healthy.textContent = "0";
        degraded.textContent = "0";
        failed.textContent = "0";
        error.hidden = true;
        return;
    }

    const summary = {
        total: visibleServices.length,
        healthy: visibleServices.filter((service) => service.status === "healthy").length,
        degraded: visibleServices.filter((service) => service.status === "degraded").length,
        failed: visibleServices.filter((service) => ["failed", "unknown"].includes(service.status)).length,
    };
    total.textContent = String(summary.total);
    healthy.textContent = String(summary.healthy);
    degraded.textContent = String(summary.degraded);
    failed.textContent = String(summary.failed);

    body.innerHTML = visibleServices
        .map((service) => {
            const statusClass = String(service.status || "unknown");
            const isPinned = pinnedIds.has(Number(service.id));
            return `
                <tr>
                    ${showPin ? `
                        <td>
                            <button
                                type="button"
                                class="dashboard-table-pin ${isPinned ? "pinned" : ""}"
                                data-dashboard-service-action="toggle-service-pin"
                                data-service-id="${service.id}"
                                title="${isPinned ? "Remove from main dashboard" : "Add to main dashboard"}"
                            >
                                ★
                            </button>
                        </td>
                    ` : ""}
                    <td>${service.name}</td>
                    <td>${String(service.service_type || "--").toUpperCase()}</td>
                    <td class="service-target-cell">${service.target}</td>
                    <td><span class="dashboard-service-status ${statusClass}">${statusClass}</span></td>
                    <td>${service.message ?? "--"}</td>
                    <td>${formatServiceLatency(service.latency_ms)}</td>
                    <td>${formatServiceLastChecked(service.last_checked)}</td>
                </tr>
            `;
        })
        .join("");
    error.hidden = true;
}

function renderDetailSummaryGrid(containerId, items) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    container.innerHTML = items
        .map(
            ([label, value]) => `
                <div class="detail-summary-item">
                    <span class="detail-summary-label">${label}</span>
                    <strong class="detail-summary-value">${value ?? "--"}</strong>
                </div>
            `,
        )
        .join("");
}

function renderNodeSummaryPanel(containerId, summary, node = {}) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    const txBps = Number(summary.tx_bps ?? 0);
    const rxBps = Number(summary.rx_bps ?? 0);
    const rttMs = Number(summary.latency_ms ?? 0);
    const cpuPercent = Number(summary.cpu_avg ?? 0);
    const trafficScaleMax = 1_000_000_000;
    const rttState = String(summary.rtt_state || "good");
    const latestLatencyText = typeof summary.latest_latency_ms === "number" ? `${summary.latest_latency_ms} ms` : "--";
    const baselineLatencyText = typeof summary.rtt_baseline_ms === "number" ? `${summary.rtt_baseline_ms} ms` : "--";
    const txState = getGaugeState(txBps, trafficScaleMax, { warnRatio: 0.75, downRatio: 0.9 });
    const rxState = getGaugeState(rxBps, trafficScaleMax, { warnRatio: 0.75, downRatio: 0.9 });
    const cpuState = getGaugeState(cpuPercent, 100, { warnRatio: 0.75, downRatio: 0.9 });

    container.innerHTML = `
        <div class="node-summary-panel">
            ${renderMetricGauge("TX", formatRate(txBps), txBps, trafficScaleMax, {
                className: `metric-gauge-card-traffic metric-gauge-card-${escapeHtml(txState)}`,
            })}
            ${renderMetricGauge("RX", formatRate(rxBps), rxBps, trafficScaleMax, {
                className: `metric-gauge-card-traffic metric-gauge-card-${escapeHtml(rxState)}`,
            })}
            ${renderMetricGauge("CPU", formatCpuPercent(summary.cpu_avg), cpuPercent, 100, {
                className: `metric-gauge-card-cpu metric-gauge-card-${escapeHtml(cpuState)}`,
            })}
            ${renderMetricGauge("Avg RTT", summary.latency_ms != null ? `${summary.latency_ms} ms` : "--", rttMs, Math.max(Number(summary.rtt_baseline_ms ?? summary.latency_ms ?? 200), 200), {
                className: `metric-gauge-card-rtt metric-gauge-card-${escapeHtml(rttState)}`,
                centerHint: `Latest ${latestLatencyText} · Baseline ${baselineLatencyText}`,
            })}
        </div>
    `;
}

function renderDetailHeaderActions(containerId, summary, node = {}) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    const healthState = summary.health_state ?? "--";
    const webOk = Boolean(node.web_ok);
    const sshOk = Boolean(node.ssh_ok);
    const host = node.host ?? summary.host ?? "";
    const sshUsername = node.ssh_username ?? "";
    const webPort = Number(node.web_port ?? 443);
    const webScheme = node.web_scheme ?? "https";
    const pinKey = node.pin_key || `anchor:${node.id}`;
    const isPinned = new Set(getPinnedNodeKeys()).has(pinKey);

    container.innerHTML = `
        <span class="node-summary-chip node-summary-health-chip">${healthState}</span>
        <button
            type="button"
            class="service-link node-summary-chip node-summary-service ${webOk ? "ok" : "down"}"
            data-node-summary-action="open-web"
            data-host="${escapeHtml(host)}"
            data-web-port="${Number.isFinite(webPort) ? webPort : 443}"
            data-web-scheme="${escapeHtml(webScheme)}"
            title="Open Web UI"
        >
            <span class="service-icon">🌐</span>
            <span>Web</span>
        </button>
        <button
            type="button"
            class="service-link node-summary-chip node-summary-service ${sshOk ? "ok" : "down"}"
            data-node-summary-action="ssh"
            data-host="${escapeHtml(host)}"
            data-ssh-username="${escapeHtml(sshUsername)}"
            title="Copy SSH command"
        >
            <span class="service-icon">🔐</span>
            <span>SSH</span>
        </button>
        <button
            type="button"
            class="dashboard-pin-button detail-pin-button ${isPinned ? "pinned" : ""}"
            data-node-summary-action="toggle-pin"
            data-node-pin-key="${escapeHtml(pinKey)}"
            title="${isPinned ? "Remove from main dashboard" : "Pin to main dashboard"}"
        >&#9733;</button>
    `;
}

function getGaugeState(rawValue, maxValue, options = {}) {
    const safeMax = Math.max(Number(maxValue) || 1, 1);
    const safeValue = Math.max(Number(rawValue) || 0, 0);
    const ratio = Math.min(safeValue / safeMax, 1);
    const warnRatio = Number(options.warnRatio ?? 0.75);
    const downRatio = Number(options.downRatio ?? 0.9);

    if (!Number.isFinite(ratio) || ratio <= 0) {
        return "good";
    }
    if (ratio >= downRatio) {
        return "down";
    }
    if (ratio >= warnRatio) {
        return "warn";
    }
    return "good";
}

function renderMetricGauge(label, valueText, rawValue, maxValue, options = {}) {
    const safeMax = Math.max(Number(maxValue) || 1, 1);
    const safeValue = Math.max(Number(rawValue) || 0, 0);
    const ratio = Math.min(safeValue / safeMax, 1);
    const degrees = 360 * ratio;
    const { primary, secondary } = splitMetricDisplay(valueText);
    const className = options.className ? ` ${options.className}` : "";
    const centerHint = options.centerHint ? ` title="${escapeHtml(options.centerHint)}"` : "";
    return `
        <div class="metric-gauge-card${className}">
            <span class="metric-gauge-label">${label}</span>
            <div class="metric-gauge-shell" style="--gauge-deg:${degrees}deg;">
                <div class="metric-gauge">
                    <div class="metric-gauge-center"${centerHint}>
                        <strong class="metric-gauge-value">${primary}</strong>
                        ${secondary ? `<span class="metric-gauge-unit">${secondary}</span>` : ""}
                    </div>
                </div>
            </div>
        </div>
    `;
}

function splitMetricDisplay(valueText) {
    const text = String(valueText ?? "--").trim();
    if (!text || text === "--") {
        return { primary: "--", secondary: "" };
    }

    if (text.endsWith("%")) {
        return { primary: text.slice(0, -1), secondary: "%" };
    }

    const match = text.match(/^(.+?)\s+([A-Za-z/]+)$/);
    if (match) {
        return { primary: match[1], secondary: match[2] };
    }

    return { primary: text, secondary: "" };
}

function wireNodeSummaryActions(containerId) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    container.querySelectorAll("[data-node-summary-action]").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();

            const action = button.getAttribute("data-node-summary-action");
            const host = button.getAttribute("data-host") || "";
            const sshUsername = button.getAttribute("data-ssh-username") || "";
            const webPort = Number(button.getAttribute("data-web-port"));
            const webScheme = button.getAttribute("data-web-scheme") || "https";

            if (action === "open-web") {
                openWebForNode(host, webPort, webScheme);
                return;
            }

            if (action === "ssh") {
                copySshCommand(host, sshUsername).catch(() => {});
                return;
            }

            if (action === "toggle-pin") {
                const pinKey = button.getAttribute("data-node-pin-key") || "";
                togglePinnedNodeKey(pinKey);
                const nowPinned = new Set(getPinnedNodeKeys()).has(pinKey);
                button.classList.toggle("pinned", nowPinned);
                button.title = nowPinned ? "Remove from main dashboard" : "Pin to main dashboard";
            }
        });
    });
}

function renderDetailTableBody(bodyId, rows, columns, emptyMessage, options = {}) {
    const body = document.getElementById(bodyId);
    if (!body) {
        return;
    }

    if (options.storeSource !== false) {
        body._sourceRows = Array.isArray(rows) ? [...rows] : [];
    }

    if (!rows || rows.length === 0) {
        body.innerHTML = `
            <tr>
                <td colspan="${columns.length}" class="table-message">${emptyMessage}</td>
            </tr>
        `;
        return;
    }

    body.innerHTML = rows
        .map(
            (row) => `
                <tr>
                    ${columns.map((column) => `<td>${formatDetailCell(column, row[column], row)}</td>`).join("")}
                </tr>
            `,
        )
        .join("");
}

function applyRouteFilter(bodyId, columns, emptyMessage, query) {
    const body = document.getElementById(bodyId);
    if (!body) {
        return;
    }

    const sourceRows = Array.isArray(body._sourceRows) ? body._sourceRows : [];
    const normalizedQuery = String(query ?? "").trim();

    if (!normalizedQuery) {
        renderDetailTableBody(bodyId, sourceRows, columns, emptyMessage);
        return;
    }

    let regex;
    try {
        regex = new RegExp(normalizedQuery, "i");
    } catch (error) {
        renderDetailTableBody(
            bodyId,
            [],
            columns,
            `Invalid regex: ${normalizedQuery}`,
            { storeSource: false },
        );
        return;
    }

    const filteredRows = sourceRows.filter((row) =>
        columns.some((column) => regex.test(String(row?.[column] ?? ""))),
    );

    renderDetailTableBody(
        bodyId,
        filteredRows,
        columns,
        `No routes matched "${normalizedQuery}".`,
        { storeSource: false },
    );
}

function wireRouteFilter(inputId, bodyId, columns, emptyMessage) {
    const input = document.getElementById(inputId);
    if (!input) {
        return;
    }

    const handler = () => applyRouteFilter(bodyId, columns, emptyMessage, input.value);
    input.removeEventListener("input", input._routeFilterHandler);
    input._routeFilterHandler = handler;
    input.addEventListener("input", handler);
    handler();
}

function wireSitesFilter() {
    const input = document.getElementById("detail-sites-filter");
    if (!input) {
        return;
    }

    const bodyId = "detail-tunnels-body";
    const columns = ["mate_index", "site_name", "mate_site_id", "mate_ip", "tunnel_health", "tx_rate", "rx_rate", "rtt_ms", "ping"];
    const emptyMessage = "No tunnel data available.";
    const handler = () => {
        const body = document.getElementById(bodyId);
        if (!body) {
            return;
        }
        const sourceRows = Array.isArray(body._sourceRows) ? body._sourceRows : [];
        const query = String(input.value ?? "").trim();
        if (!query) {
            renderDetailTableBody(bodyId, sourceRows, columns, emptyMessage);
            return;
        }

        let regex;
        try {
            regex = new RegExp(query, "i");
        } catch (error) {
            renderDetailTableBody(
                bodyId,
                [],
                columns,
                `Invalid regex: ${query}`,
                { storeSource: false },
            );
            return;
        }

        const filteredRows = sourceRows.filter((row) =>
            regex.test(String(row?.mate_site_id ?? "")),
        );
        renderDetailTableBody(
            bodyId,
            filteredRows,
            columns,
            `No sites matched "${query}".`,
            { storeSource: false },
        );
    };
    input.removeEventListener("input", input._sitesFilterHandler);
    input._sitesFilterHandler = handler;
    input.addEventListener("input", handler);
    handler();
}

function wireDetailLinks() {
    document.querySelectorAll("[data-detail-link=\"site-web\"]").forEach((button) => {
        button.removeEventListener("click", button._detailLinkHandler);
        const handler = (event) => {
            event.preventDefault();
            event.stopPropagation();
            const siteIp = button.getAttribute("data-site-ip") || "";
            if (!siteIp) {
                return;
            }
            openWebForNode(siteIp, 443, "https");
        };
        button._detailLinkHandler = handler;
        button.addEventListener("click", handler);
    });
}

function formatDetailCell(column, value, row = {}) {
    if (column === "tunnel_health" && Array.isArray(value)) {
        return `
            <div class="tunnel-health" title="Tun0-3">
                ${value
                    .map(
                        (status, index) => `
                            <span class="tunnel-dot tunnel-dot-${status}" title="Tun${index}: ${status}"></span>
                        `,
                    )
                    .join("")}
            </div>
        `;
    }

    if (column === "site_name") {
        const siteIp = String(row?.mate_ip ?? "").trim();
        const siteName = escapeHtml(value ?? "--");
        if (!siteIp || siteIp === "--") {
            return siteName;
        }

        return `
            <a
                href="#"
                class="detail-link-chip"
                data-detail-link="site-web"
                data-site-ip="${escapeHtml(siteIp)}"
                title="Open ${siteName} web UI"
            >
                ${siteName}
            </a>
        `;
    }

    return value ?? "--";
}

function setSectionError(id, message) {
    const element = document.getElementById(id);
    if (!element) {
        return;
    }

    if (message) {
        element.textContent = message;
        element.hidden = false;
    } else {
        element.hidden = true;
        element.textContent = "";
    }
}

function renderTopologyFilterButtons(containerId, values, stateSet, filterType) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    container.innerHTML = values
        .map(
            (value) => `
                <button
                    type="button"
                    class="topology-filter-chip ${stateSet.has(value) ? "active" : ""}"
                    data-topology-filter="${filterType}"
                    data-topology-value="${escapeHtml(value)}"
                    aria-pressed="${stateSet.has(value) ? "true" : "false"}"
                >
                    ${escapeHtml(value)}
                </button>
            `,
        )
        .join("");
}

function renderTopologyViewButtons() {
    const container = document.getElementById("topology-view-filters");
    if (!container) {
        return;
    }

    const views = [
        ["backbone", "Backbone"],
        ["backbone+l2", "Backbone + Lvl2"],
    ];
    container.innerHTML = views
        .map(
            ([value, label]) => `
                <button
                    type="button"
                    class="topology-filter-chip ${topologyState.view === value ? "active" : ""}"
                    data-topology-view="${value}"
                    aria-pressed="${topologyState.view === value ? "true" : "false"}"
                >
                    ${label}
                </button>
            `,
        )
        .join("");
}

function renderTopologyLocationHeaders() {
    const container = document.getElementById("topology-locations-header");
    if (container) {
        container.innerHTML = "";
    }
}

function getTopologyNodePosition(entity) {
    const stage = document.getElementById("topology-stage");
    if (!stage) {
        return { x: 0, y: 0 };
    }

    const width = Math.max(stage.clientWidth, 2140);
    const height = Math.max(stage.clientHeight, 940);
    const isFullscreenLayout = topologyState.isFullscreen;
    const lvl1NodeWidth = 92;
    const lvl1TouchGap = 0;
    const aggXs = {
        Cloud: width * 0.18,
        HSMC: width * 0.5,
        Episodic: width * 0.82,
    };
    const groupXs = TOPOLOGY_UNITS.reduce((accumulator, unit, index) => {
        accumulator[unit] = width * (0.04 + ((index + 0.5) / TOPOLOGY_UNITS.length) * 0.92);
        return accumulator;
    }, {});
    const locationOffsets = {
        Cloud: -(lvl1NodeWidth + lvl1TouchGap),
        HSMC: 0,
        Episodic: lvl1NodeWidth + lvl1TouchGap,
    };
    const aggYs = {
        Cloud: Math.round(height * (isFullscreenLayout ? 0.21 : 0.19)),
        HSMC: Math.round(height * (isFullscreenLayout ? 0.14 : 0.12)),
        Episodic: Math.round(height * (isFullscreenLayout ? 0.21 : 0.19)),
    };
    const lvl1Y = Math.round(height * (isFullscreenLayout ? 0.5 : 0.44));
    const lvl2Y = Math.round(height * (isFullscreenLayout ? 0.9 : 0.81));

    if (entity.kind === "services-cloud") {
        return {
            x: Math.round(width * 0.08),
            y: Math.round(height * 0.16),
        };
    }

    if (entity.level === 0) {
        return {
            x: aggXs[entity.location] ?? width / 2,
            y: aggYs[entity.location] ?? Math.round(height * 0.15),
        };
    }

    if (entity.level === 1) {
        return {
            x: (groupXs[entity.unit] ?? width / 2) + (locationOffsets[entity.location] ?? 0),
            y: lvl1Y,
        };
    }

    return {
        x: groupXs[entity.unit] ?? width / 2,
        y: lvl2Y,
    };
}

function getTopologyEntities() {
    if (!topologyPayload) {
        return [];
    }

    const anchorRows = Array.isArray(topologyNodeDashboardPayload?.anchors) ? topologyNodeDashboardPayload.anchors : [];
    const inventoryNodes = Array.isArray(currentNodes) ? currentNodes : [];
    const inventoryByNodeId = new Map(
        inventoryNodes
            .filter((row) => row && row.id != null)
            .map((row) => [String(row.id), row]),
    );
    const anchorByNodeId = new Map(
        anchorRows
            .filter((row) => row && row.id != null)
            .map((row) => [String(row.id), row]),
    );
    const anchorBySiteId = new Map(
        anchorRows
            .filter((row) => row && row.site_id != null)
            .map((row) => [String(row.site_id), row]),
    );
    const mergeDashboardAnchorState = (entity) => {
        if (!entity || entity.kind === "services-cloud" || entity.level === 2) {
            return entity;
        }
        const inventoryNode = inventoryByNodeId.get(String(entity.inventory_node_id ?? ""));
        const dashboardAnchor =
            anchorByNodeId.get(String(entity.inventory_node_id ?? "")) ||
            anchorBySiteId.get(String(entity.site_id ?? ""));
        if (!dashboardAnchor) {
            if (!inventoryNode) {
                return entity;
            }
            return {
                ...entity,
                node_id: inventoryNode.node_id || entity.node_id,
                version: inventoryNode.version || entity.version,
                host: inventoryNode.host || entity.host,
                web_port: inventoryNode.web_port ?? entity.web_port,
                web_scheme: inventoryNode.web_scheme || entity.web_scheme,
            };
        }
        return {
            ...entity,
            node_id: inventoryNode?.node_id || dashboardAnchor.site_id || entity.node_id,
            status: dashboardAnchor.status || entity.status,
            site_id: dashboardAnchor.site_id || entity.site_id,
            latency_ms: dashboardAnchor.avg_latency_ms ?? dashboardAnchor.latency_ms ?? entity.latency_ms,
            avg_latency_ms: dashboardAnchor.avg_latency_ms ?? entity.avg_latency_ms,
            latest_latency_ms: dashboardAnchor.latest_latency_ms ?? entity.latest_latency_ms,
            rtt_baseline_ms: dashboardAnchor.rtt_baseline_ms ?? entity.rtt_baseline_ms,
            rtt_deviation_pct: dashboardAnchor.rtt_deviation_pct ?? entity.rtt_deviation_pct,
            rtt_state: dashboardAnchor.rtt_state || entity.rtt_state,
            tx_bps: dashboardAnchor.tx_bps ?? entity.tx_bps,
            rx_bps: dashboardAnchor.rx_bps ?? entity.rx_bps,
            tx_display: dashboardAnchor.tx_display || entity.tx_display,
            rx_display: dashboardAnchor.rx_display || entity.rx_display,
            wan_tx_bps: dashboardAnchor.wan_tx_bps ?? entity.wan_tx_bps,
            wan_rx_bps: dashboardAnchor.wan_rx_bps ?? entity.wan_rx_bps,
            lan_tx_bps: dashboardAnchor.lan_tx_bps ?? entity.lan_tx_bps,
            lan_rx_bps: dashboardAnchor.lan_rx_bps ?? entity.lan_rx_bps,
            wan_tx_total: dashboardAnchor.wan_tx_total || entity.wan_tx_total,
            wan_rx_total: dashboardAnchor.wan_rx_total || entity.wan_rx_total,
            lan_tx_total: dashboardAnchor.lan_tx_total || entity.lan_tx_total,
            lan_rx_total: dashboardAnchor.lan_rx_total || entity.lan_rx_total,
            cpu_avg: dashboardAnchor.cpu_avg ?? entity.cpu_avg,
            version: dashboardAnchor.version || entity.version,
            web_ok: dashboardAnchor.web_ok ?? entity.web_ok,
            ssh_ok: dashboardAnchor.ssh_ok ?? entity.ssh_ok,
            ping_ok: dashboardAnchor.ping_ok ?? entity.ping_ok,
            ping_state: dashboardAnchor.ping_state || entity.ping_state,
            web_port: inventoryNode?.web_port ?? dashboardAnchor.web_port ?? entity.web_port,
            web_scheme: inventoryNode?.web_scheme || dashboardAnchor.web_scheme || entity.web_scheme,
            metrics_text: dashboardAnchor.host || inventoryNode?.host || entity.metrics_text,
        };
    };

    const authoredEntities = [
        ...((topologyPayload.lvl0_nodes ?? []).map(mergeDashboardAnchorState)),
        ...((topologyPayload.lvl1_nodes ?? []).map(mergeDashboardAnchorState)),
        ...(topologyPayload.lvl2_clusters ?? []),
        ...(topologyPayload.submaps ?? []),
    ];
    return authoredEntities.filter((entity) => Boolean(topologyState.layoutOverrides?.[entity.id]));
}

function getTopologyLinkId(link, index) {
    return link.id || `link-${index}`;
}

function isTopologyEntityVisible(entity) {
    if (entity.kind === "submap") {
        return true;
    }

    const root = document.getElementById("topology-root");
    if (root?.getAttribute("data-map-view-id")) {
        return true;
    }

    if (entity.kind === "services-cloud") {
        return topologyState.activeLocations.has("Cloud");
    }

    if (entity.level === 0) {
        return topologyState.activeLocations.has(entity.location);
    }

    if (entity.level === 1) {
        return topologyState.activeLocations.has(entity.location) && topologyState.activeUnits.has(entity.unit);
    }

    if (entity.level === 2) {
        return topologyState.view === "backbone+l2" && topologyState.activeUnits.has(entity.unit);
    }

    return true;
}

function renderTopologyStage() {
    if (topologyState.dragging) {
        return;
    }
    const linkCtxMenu = document.getElementById("topology-link-context-menu");
    if (linkCtxMenu && !linkCtxMenu.hidden) {
        return;
    }
    if (!topologyState.pinnedLinkTooltipId) {
        hideTopologyLinkTooltip(true);
    }
    const layer = document.getElementById("topology-node-layer");
    const stage = document.getElementById("topology-stage");
    const stateLogPreview = document.getElementById("topology-state-log-preview");
    const stateLogFlyout = document.getElementById("topology-state-log-flyout");
    if (!layer || !stage || !topologyPayload) {
        return;
    }

    stage.classList.toggle("is-editing", topologyState.editMode);
    renderTopologyLocationHeaders();

    const entities = getTopologyEntities();
    const visibleEntities = entities.filter(isTopologyEntityVisible);
    const entityMap = new Map(entities.map((entity) => [entity.id, entity]));
    const discoveryCounts = getTopologyDiscoveryCounts();
    const clusterStatusCounts = getTopologyClusterStatusCounts();
    const connectedAnchorMap = getTopologyConnectedAnchorMap();
    const discoveryWorstMap = getDiscoveryWorstAnchorMap();

    // NSL hidden from topology — will move to main dashboard later
    if (stateLogPreview) {
        stateLogPreview.hidden = true;
    }
    if (stateLogFlyout) {
        stateLogFlyout.hidden = true;
    }

    if (!visibleEntities.length) {
        layer.innerHTML = `
            <div class="topology-empty-state">
                <strong>Blank map ready</strong>
                <span>Click Edit Map, then Add Node to place your first Seeker icon.</span>
            </div>
        `;
        drawTopologyLinks(entityMap);
        if (typeof renderTopologyDetailsDrawer === "function") {
            renderTopologyDetailsDrawer(null);
        }
        return;
    }

    layer.innerHTML = visibleEntities
        .map((entity) => {
            const layout = getTopologyEntityLayout(entity);
            const discoveredCount = getTopologyDiscoveryCount(entity, discoveryCounts);
            const isSubmap = entity.kind === "submap";
            const isServiceCloud = entity.kind === "services-cloud";
            const isCluster = entity.level === 2;
            const isLvl1 = entity.level === 1;
            const isFocusedUnit = entity.level === 2 && topologyState.focusUnit && topologyState.focusUnit === entity.unit;
            const serviceSummary = isServiceCloud ? (entity.service_summary || getTopologyServiceCloudSummary()) : null;
            const isDiscovered = entity.kind === "discovered";
            const classes = [
                  "topology-entity",
                  isCluster ? "topology-cluster" : "topology-node",
                  isSubmap ? "topology-submap" : "",
                  isDiscovered ? "topology-discovered" : "",
                  isServiceCloud ? "topology-service-cloud" : "",
                  `topology-status-${getEffectiveTopologyEntityStatus(entity) || "neutral"}`,
                  entity.level === 0 ? "topology-node-agg" : "",
                  isLvl1 ? "topology-node-lvl1" : "",
                  isFocusedUnit ? "is-selected" : "",
                  topologyState.selectedEntityIds.has(entity.id) ? "is-multi-selected" : "",
                  topologyState.selectedKind === "entity" && topologyState.selectedId === entity.id ? "is-selected" : "",
                  topologyState.pinnedTooltipId === entity.id ? "is-tooltip-pinned" : "",
              ]
                .filter(Boolean)
                .join(" ");

            const subtitle = isSubmap
                ? "Submap"
                : isDiscovered
                ? `via ${escapeHtml(entity.source_name || "anchor")}`
                : isCluster
                ? "Edge Nodes"
                : escapeHtml(entity.node_id || entity.site_id || "--");
            const displayName = isDiscovered
                ? escapeHtml(entity.site_id || entity.node_id || entity.name)
                : escapeHtml(getTopologyEntityLabel(entity));
            const clusterUpCount = isCluster
                ? getEffectiveTopologyClusterUpCount(entity.unit, clusterStatusCounts.upByUnit.get(entity.unit) || 0)
                : 0;
            const clusterFooter = isCluster
                ? getTopologyClusterFooterMarkup(discoveredCount, clusterUpCount)
                : "";
            const nodeIcon = (!isCluster || isSubmap) ? getTopologyAnchorIconMarkup({ ...entity, status: getEffectiveTopologyEntityStatus(entity) }) : "";
            const titleText = isSubmap
                ? `Submap: ${entity.name}`
                : isServiceCloud
                ? "Services cloud"
                : isCluster ? `${entity.unit} Edge Nodes` : entity.level === 1 ? `${entity.unit} / ${entity.location}` : entity.name;
            const isAnchorNode = !isCluster && !isServiceCloud && !isSubmap && !isDiscovered && Boolean(entity.inventory_node_id);
            const hoverPanel = isServiceCloud
                ? `
                    <span class="topology-service-cloud-tooltip" role="tooltip">
                        <strong class="topology-service-cloud-tooltip-title">Pinned Services</strong>
                        ${
                            serviceSummary?.services?.length
                                ? `<span class="topology-service-cloud-tooltip-list">${serviceSummary.services.map((service) => `
                                    <span class="topology-service-cloud-tooltip-item">
                                        <span class="topology-service-cloud-tooltip-name">${escapeHtml(service.name || `Service ${service.id}`)}</span>
                                        <span class="topology-service-cloud-tooltip-status status-pill status-${escapeHtml(String(service.status || "unknown").toLowerCase())}">${escapeHtml(service.status || "unknown")}</span>
                                    </span>
                                `).join("")}</span>`
                                : '<span class="topology-service-cloud-tooltip-empty">No services pinned yet.</span>'
                        }
                    </span>
                `
                : isAnchorNode ? getTopologyAnchorTooltipMarkup(entity)
                : isDiscovered ? getTopologyDiscoveredTooltipMarkup(entity)
                : "";
            const bubbleStyle = `left:${layout.x}px; top:${layout.y}px; --topology-bubble-size:${layout.size}px;`;
            const resizeHandle = topologyState.editMode
                ? '<span class="topology-resize-handle" data-topology-resize-handle="true" aria-hidden="true"></span>'
                : "";
            const anchorPoints = getTopologyAnchorPointDefinitions().map((point) => {
                const apKey = `${entity.id}::${point.key}`;
                const authoredStatus = connectedAnchorMap.get(apKey) || null;
                const discoveryWorst = discoveryWorstMap.get(apKey) || null;
                // Use the worst discovery link status if it's "down" or "degraded"
                const connectedStatus = (discoveryWorst === "down" || discoveryWorst === "degraded")
                    ? discoveryWorst
                    : authoredStatus || discoveryWorst;
                const isConnected = Boolean(connectedStatus);
                const isTargeted = topologyState.activeLinkHandleTarget?.entityId === entity.id
                    && topologyState.activeLinkHandleTarget?.anchorKey === point.key;
                return `
                    <span
                        class="topology-anchor-point ${isConnected ? `is-connected is-connected-${connectedStatus}` : ""} ${isTargeted ? "is-targeted" : ""}"
                        data-topology-anchor-point="${point.key}"
                        style="left:${point.x * 100}%; top:${point.y * 100}%;"
                        aria-hidden="true"
                    ></span>
                `;
            }).join("");

            // DN counts: prefer discovery cache, fall back to backend payload
            const dnCache = isSubmap ? _submapDnCountCache.get(entity.map_view_id) : null;
            const dnUp = dnCache ? dnCache.dn_up : (entity.dn_up || 0);
            const dnDown = dnCache ? dnCache.dn_down : (entity.dn_down || 0);
            const dnUpNames = dnCache ? dnCache.dn_up_names : (entity.dn_up_names || []);
            const dnDownNames = dnCache ? dnCache.dn_down_names : (entity.dn_down_names || []);
            const submapDnCounters = isSubmap
                ? `<span class="topology-submap-dn-counts">${
                    dnUp > 0 ? `<span class="topology-submap-dn-up" data-dn-names="${escapeHtml(dnUpNames.join(','))}">${dnUp}</span>` : ""
                }${
                    dnDown > 0 ? `<span class="topology-submap-dn-down" data-dn-names="${escapeHtml(dnDownNames.join(','))}">${dnDown}</span>` : ""
                }</span>`
                : "";
            const entityBody = isSubmap
                ? `<span class="topology-node-name">${displayName}</span>${nodeIcon}${submapDnCounters}`
                : `${nodeIcon}<span class="topology-node-name">${displayName}</span>${(isServiceCloud || isDiscovered) ? "" : `<span class="topology-node-meta">${subtitle}</span>`}`;
            return `
                <button
                    type="button"
                    class="${classes}"
                    data-topology-id="${entity.id}"
                    aria-label="${escapeHtml(titleText)}"
                    data-topology-editable="${topologyState.editMode ? "true" : "false"}"
                    ${isSubmap && entity.map_view_id ? `data-map-view-id="${entity.map_view_id}"` : ""}
                    style="${bubbleStyle}"
                >
                    ${entityBody}
                    ${clusterFooter}
                    ${hoverPanel}
                    ${anchorPoints}
                    ${resizeHandle}
                </button>
            `;
        })
        .join("");

    const visibleIds = new Set(visibleEntities.map((entity) => entity.id));
    if (topologyState.selectedKind === "entity" && topologyState.selectedId && !visibleIds.has(topologyState.selectedId)) {
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
    }
    topologyState.selectedEntityIds = new Set(
        Array.from(topologyState.selectedEntityIds).filter((entityId) => visibleIds.has(entityId)),
    );

      // Clean up any stale DN tooltips from previous render
      document.querySelectorAll(".topology-submap-dn-tooltip").forEach((t) => t.remove());

      // DN count bubble hover tooltips
      layer.querySelectorAll(".topology-submap-dn-up, .topology-submap-dn-down").forEach((bubble) => {
          const isUp = bubble.classList.contains("topology-submap-dn-up");
          bubble.addEventListener("mouseenter", () => {
              const names = (bubble.getAttribute("data-dn-names") || "").split(",").filter(Boolean);
              if (!names.length) return;
              // Remove any existing tooltip first
              document.querySelectorAll(".topology-submap-dn-tooltip").forEach((t) => t.remove());
              const tip = document.createElement("div");
              tip.className = "topology-submap-dn-tooltip" + (isUp ? " dn-tooltip-up" : " dn-tooltip-down");
              tip.innerHTML = names.map((n) => `<div>${escapeHtml(n)}</div>`).join("");
              document.body.appendChild(tip);
              const rect = bubble.getBoundingClientRect();
              tip.style.left = `${rect.left + rect.width / 2}px`;
              tip.style.top = `${rect.top - 6}px`;
              bubble._dnTooltip = tip;
          });
          bubble.addEventListener("mouseleave", () => {
              if (bubble._dnTooltip) {
                  bubble._dnTooltip.remove();
                  bubble._dnTooltip = null;
              }
          });
      });

      layer.querySelectorAll("[data-topology-id]").forEach((button) => {
          button.querySelectorAll("[data-topology-anchor-point]").forEach((anchorPoint) => {
              anchorPoint.addEventListener("pointerdown", (event) => {
                  if (!topologyState.editMode) {
                      return;
                  }
                  const entityId = button.getAttribute("data-topology-id");
                  const anchorKey = anchorPoint.getAttribute("data-topology-anchor-point");
                  if (!entityId || !anchorKey) {
                      return;
                  }
                  event.preventDefault();
                  event.stopPropagation();

                  const svgEl = document.getElementById("topology-links");
                  const stageEl = document.getElementById("topology-stage");
                  if (!svgEl || !stageEl) {
                      return;
                  }
                  const stageR = stageEl.getBoundingClientRect();
                  const fromRect = button.getBoundingClientRect();
                  const startPt = getTopologyAnchorCoordinates(fromRect, stageR, anchorKey);

                  const rubberband = document.createElementNS("http://www.w3.org/2000/svg", "line");
                  rubberband.setAttribute("x1", String(startPt.x));
                  rubberband.setAttribute("y1", String(startPt.y));
                  rubberband.setAttribute("x2", String(startPt.x));
                  rubberband.setAttribute("y2", String(startPt.y));
                  rubberband.setAttribute("class", "topology-link topology-link-neutral");
                  rubberband.style.pointerEvents = "none";
                  svgEl.appendChild(rubberband);

                  anchorPoint.classList.add("is-targeted");

                  topologyState.dragging = {
                      kind: "link-create",
                      pointerId: event.pointerId,
                      sourceEntityId: entityId,
                      sourceAnchor: anchorKey,
                      rubberband,
                      snapTarget: null,
                  };

                  const moveHandler = (moveEvent) => {
                      const drag = topologyState.dragging;
                      if (!drag || drag.kind !== "link-create" || drag.pointerId !== moveEvent.pointerId) {
                          return;
                      }
                      const cursorX = moveEvent.clientX - stageR.left;
                      const cursorY = moveEvent.clientY - stageR.top;
                      const snapTarget = getAnyTopologyAnchorTargetFromPoint(moveEvent.clientX, moveEvent.clientY, entityId);
                      drag.snapTarget = snapTarget;
                      setAnyTopologyAnchorTargetHighlight(snapTarget);

                      if (snapTarget) {
                          const targetBubble = stageEl.querySelector(`[data-topology-id="${CSS.escape(snapTarget.entityId)}"]`);
                          if (targetBubble instanceof HTMLElement) {
                              const snapPt = getTopologyAnchorCoordinates(targetBubble.getBoundingClientRect(), stageR, snapTarget.anchorKey);
                              rubberband.setAttribute("x2", String(snapPt.x));
                              rubberband.setAttribute("y2", String(snapPt.y));
                          } else {
                              rubberband.setAttribute("x2", String(cursorX));
                              rubberband.setAttribute("y2", String(cursorY));
                          }
                      } else {
                          rubberband.setAttribute("x2", String(cursorX));
                          rubberband.setAttribute("y2", String(cursorY));
                      }
                      moveEvent.preventDefault();
                  };

                  const endHandler = async (endEvent) => {
                      const drag = topologyState.dragging;
                      window.removeEventListener("pointermove", moveHandler);
                      window.removeEventListener("pointerup", endHandler);
                      window.removeEventListener("pointercancel", endHandler);
                      rubberband.remove();
                      setAnyTopologyAnchorTargetHighlight(null);
                      anchorPoint.classList.remove("is-targeted");
                      topologyState.dragging = null;

                      if (!drag || drag.kind !== "link-create") {
                          return;
                      }

                      const finalTarget = drag.snapTarget
                          || getAnyTopologyAnchorTargetFromPoint(endEvent.clientX, endEvent.clientY, entityId);
                      if (finalTarget?.entityId && finalTarget?.anchorKey) {
                          console.log("[link-create] Creating link:", drag.sourceEntityId, drag.sourceAnchor, "→", finalTarget.entityId, finalTarget.anchorKey);
                          const created = await createTopologyLink(
                              drag.sourceEntityId,
                              drag.sourceAnchor,
                              finalTarget.entityId,
                              finalTarget.anchorKey,
                          );
                          if (created) {
                              console.log("[link-create] Link created:", created);
                              await refreshTopologyData();
                          } else {
                              console.warn("[link-create] API call failed — has the migration been run?");
                          }
                          renderTopologyStage();
                      } else {
                          console.log("[link-create] No snap target found, link not created");
                          renderTopologyStage();
                      }
                      endEvent.preventDefault();
                  };

                  window.addEventListener("pointermove", moveHandler);
                  window.addEventListener("pointerup", endHandler);
                  window.addEventListener("pointercancel", endHandler);
              });
              anchorPoint.addEventListener("click", (event) => {
                  if (!topologyState.editMode || topologyState.selectedKind !== "link" || !topologyState.selectedId) {
                      event.stopPropagation();
                      return;
                  }
                  const entityId = button.getAttribute("data-topology-id");
                  const anchorKey = anchorPoint.getAttribute("data-topology-anchor-point");
                  const selectedLink = (topologyPayload?.links ?? []).find((item, index) => getTopologyLinkId(item, index) === topologyState.selectedId);
                  if (!selectedLink || !entityId || !anchorKey) {
                      event.stopPropagation();
                      return;
                  }
                  if (selectedLink.from === entityId) {
                      setTopologyLinkAnchorAssignment(topologyState.selectedId, "source", anchorKey);
                  } else if (selectedLink.to === entityId) {
                      setTopologyLinkAnchorAssignment(topologyState.selectedId, "target", anchorKey);
                  }
                  renderTopologyStage();
                  event.preventDefault();
                  event.stopPropagation();
              });
          });
          button.addEventListener("click", (event) => {
              if (topologyState.editMode) {
                  const nextId = button.getAttribute("data-topology-id");
                  if (nextId) {
                      topologyState.selectedKind = "entity";
                      topologyState.selectedId = nextId;
                      if (!topologyState.selectedEntityIds.has(nextId)) {
                          topologyState.selectedEntityIds = new Set([nextId]);
                      }
                  }
                  syncTopologyEntitySelectionStyles(layer);
                  event.stopPropagation();
                  event.preventDefault();
                  return;
              }
              event.stopPropagation();
              const nextId = button.getAttribute("data-topology-id");
            const nextEntity = entityMap.get(nextId || "");
            const isAnchorNode = Boolean(nextEntity?.inventory_node_id) && nextEntity?.level !== 2 && nextEntity?.kind !== "services-cloud";
            // Clear any pinned link tooltip when clicking an entity
            if (topologyState.pinnedLinkTooltipId) {
                topologyState.pinnedLinkTooltipId = null;
                hideTopologyLinkTooltip();
            }
            if (nextEntity?.level === 2) {
                topologyState.pinnedTooltipId = null;
                if (topologyState.pinnedLinkNodeId) {
                    hideDiscoveryLinksForEntity(topologyState.pinnedLinkNodeId);
                    topologyState.pinnedLinkNodeId = null;
                }
                setTopologyUnitFocus(nextEntity.unit);
                updateTopologyUnitRoute(nextEntity.unit);
                topologyState.selectedKind = "entity";
                topologyState.selectedId = nextId;
                renderTopologyControls();
                renderTopologyStage();
                return;
            }
            if (isAnchorNode || nextEntity?.kind === "discovered") {
                topologyState.pinnedTooltipId = topologyState.pinnedTooltipId === nextId ? null : nextId;
                // Pin/unpin discovery links for this node
                if (topologyState.pinnedLinkNodeId === nextId) {
                    topologyState.pinnedLinkNodeId = null;
                    hideAllDiscoveryLinks();
                } else {
                    if (topologyState.pinnedLinkNodeId) {
                        hideDiscoveryLinksForEntity(topologyState.pinnedLinkNodeId);
                    }
                    topologyState.pinnedLinkNodeId = nextId;
                    revealDiscoveryLinksForEntity(nextId);
                }
                renderTopologyStage();
                return;
            }
            // Clicking a non-pinnable entity clears any pinned tooltip
            topologyState.pinnedTooltipId = null;
            if (topologyState.selectedKind === "entity" && topologyState.selectedId === nextId) {
                topologyState.selectedKind = null;
                topologyState.selectedId = null;
            } else {
                topologyState.selectedKind = "entity";
                topologyState.selectedId = nextId;
            }
            if (nextEntity?.kind === "submap") {
                syncTopologyEntitySelectionStyles(layer);
                return;
            }
            renderTopologyStage();
        });
        button.addEventListener("contextmenu", (event) => {
            const nextId = button.getAttribute("data-topology-id");
            const nextEntity = entityMap.get(nextId || "");
            if (!nextId) {
                return;
            }
            if (!topologyState.editMode) {
                if (nextEntity?.kind === "discovered" && nextEntity?.site_id) {
                    openTopologyDetail(`/nodes/discovered/${encodeURIComponent(nextEntity.site_id)}`, nextEntity.name || nextEntity.site_id || "Discovered Node");
                    event.preventDefault();
                    event.stopPropagation();
                } else if (nextEntity?.inventory_node_id) {
                    openTopologyDetail(`/nodes/${encodeURIComponent(nextEntity.inventory_node_id)}`, nextEntity.name || "Node Detail");
                    event.preventDefault();
                    event.stopPropagation();
                }
                return;
            }
            topologyState.selectedKind = "entity";
            topologyState.selectedId = nextId;
            if (!topologyState.selectedEntityIds.has(nextId)) {
                topologyState.selectedEntityIds = new Set([nextId]);
            }
            syncTopologyEntitySelectionStyles(layer);
            const root = document.getElementById("topology-root");
            if (root?.getAttribute("data-map-view-id") && nextEntity?.map_object_id) {
                event.preventDefault();
                event.stopPropagation();
                const confirmed = window.confirm(`Remove "${nextEntity.name || "this node"}" from the submap?`);
                if (confirmed) {
                    deleteSubmapObject(nextEntity.map_object_id, nextEntity.id);
                }
                return;
            }
            if (nextEntity?.kind === "submap" && nextEntity?.map_view_id) {
                event.preventDefault();
                event.stopPropagation();
                renameTopologySubmap(nextEntity);
                return;
            }
            if (nextEntity?.inventory_node_id) {
                populateNodeForm(Number(nextEntity.inventory_node_id));
                openNodeModal({ reset: false });
            }
            event.preventDefault();
            event.stopPropagation();
        });
        button.addEventListener("dblclick", (event) => {
            if (topologyState.editMode) {
                return;
            }
            const nextId = button.getAttribute("data-topology-id");
            const nextEntity = entityMap.get(nextId || "");
            if (nextEntity?.kind === "submap" && nextEntity?.map_view_id) {
                window.location.href = `/topology/maps/${nextEntity.map_view_id}`;
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            if (nextEntity?.kind === "discovered" && nextEntity?.host) {
                topologyState.selectedKind = null;
                topologyState.selectedId = null;
                openWebForNode(nextEntity.host, 443, "https");
                renderTopologyStage();
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            const isAnchorNode = Boolean(nextEntity?.inventory_node_id) && nextEntity?.level !== 2 && nextEntity?.kind !== "services-cloud";
            if (!isAnchorNode || !nextEntity?.metrics_text) {
                return;
            }
            topologyState.selectedKind = null;
            topologyState.selectedId = null;
            openWebForNode(nextEntity.metrics_text, nextEntity.web_port || 443, nextEntity.web_scheme || "https");
            renderTopologyStage();
            event.preventDefault();
            event.stopPropagation();
        });
        // Discovery link reveal on hover
        button.addEventListener("mouseenter", () => {
            const entityId = button.getAttribute("data-topology-id");
            if (!entityId || topologyState.editMode) return;
            if (topologyState.pinnedLinkNodeId && topologyState.pinnedLinkNodeId !== entityId) return;
            revealDiscoveryLinksForEntity(entityId);
        });
        button.addEventListener("mouseleave", () => {
            const entityId = button.getAttribute("data-topology-id");
            if (!entityId || topologyState.editMode) return;
            if (topologyState.pinnedLinkNodeId === entityId) return;
            hideDiscoveryLinksForEntity(entityId);
        });
    });

    if (topologyState.editMode) {
        wireTopologyLayoutEditor(stage, layer, entityMap);
        wireTopologyStateLogEditor();
        syncTopologyEntitySelectionStyles(layer);
    } else {
        syncTopologySelectionBox(null);
    }

    stage.onclick = (event) => {
        if (topologyState._lastDragEndTime && Date.now() - topologyState._lastDragEndTime < 300) {
            return;
        }
        const target = event.target;
        if (target instanceof Element && target.closest("[data-topology-id], [data-topology-link-id], #topology-state-log-preview, #topology-state-log-flyout")) {
            return;
        }
        if (topologyState.editMode) {
            if (topologyState.dragging?.mode === "select") {
                return;
            }
            topologyState.selectedEntityIds = new Set();
            setTopologyActiveLinkHandleTarget(null);
        }
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        topologyState.pinnedTooltipId = null;
        if (topologyState.pinnedLinkNodeId) {
            hideDiscoveryLinksForEntity(topologyState.pinnedLinkNodeId);
            topologyState.pinnedLinkNodeId = null;
        }
        if (topologyState.pinnedLinkTooltipId) {
            topologyState.pinnedLinkTooltipId = null;
            hideTopologyLinkTooltip();
        }
        topologyState.stateLogSelected = false;
        renderTopologyStage();
    };

    drawTopologyLinks(entityMap);
    // Re-reveal discovery links after SVG rebuild
    if (topologyState.editMode) {
        revealAllDiscoveryLinks();
    } else if (topologyState.pinnedLinkNodeId) {
        revealDiscoveryLinksForEntity(topologyState.pinnedLinkNodeId);
    }
    refreshPinnedLinkTooltip();
    renderTopologyDrawer();
    renderTopologyStateLog();
}

function drawTopologyLinks(entityMap) {
    const svg = document.getElementById("topology-links");
    const handleLayer = document.getElementById("topology-link-handle-layer");
    const stage = document.getElementById("topology-stage");
    if (!svg || !handleLayer || !stage || !topologyPayload) {
        return;
    }

    const stageRect = stage.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${Math.max(stage.clientWidth, 1)} ${Math.max(stage.clientHeight, 1)}`);
    svg.innerHTML = "";
    handleLayer.innerHTML = "";
    setTopologyActiveLinkHandleTarget(null);

    (topologyPayload.links ?? []).forEach((link, index) => {
        const fromEntity = entityMap.get(link.from);
        const toEntity = entityMap.get(link.to);
        if (!fromEntity || !toEntity) {
            return;
        }

        if (!isTopologyEntityVisible(fromEntity) || !isTopologyEntityVisible(toEntity)) {
            return;
        }

        if (topologyState.view === "backbone" && link.kind === "cluster") {
            return;
        }

        const fromNode = stage.querySelector(`[data-topology-id="${CSS.escape(link.from)}"]`);
        const toNode = stage.querySelector(`[data-topology-id="${CSS.escape(link.to)}"]`);
        if (!(fromNode instanceof HTMLElement) || !(toNode instanceof HTMLElement)) {
            return;
        }

        const linkId = getTopologyLinkId(link, index);
        const fromRect = fromNode.getBoundingClientRect();
        const toRect = toNode.getBoundingClientRect();
        const savedAssignment = getTopologyLinkAnchorAssignment(link, index);
        const anchorAssignment = {
            source: savedAssignment.source || link.source_anchor || null,
            target: savedAssignment.target || link.target_anchor || null,
        };
        const sourcePoint = getTopologyAnchorCoordinates(fromRect, stageRect, anchorAssignment.source);
        const targetPoint = getTopologyAnchorCoordinates(toRect, stageRect, anchorAssignment.target);
        // Pre-reveal discovery links for pinned node or edit mode at creation
        // so they don't flash/fade-in on every SVG rebuild.
        const shouldPreReveal = link.kind === "discovery" && (
            topologyState.editMode
            || topologyState.pinnedLinkNodeId === link.from
            || topologyState.pinnedLinkNodeId === link.to
        );

        const hitShape = document.createElementNS("http://www.w3.org/2000/svg", "line");
        hitShape.setAttribute("x1", String(sourcePoint.x));
        hitShape.setAttribute("y1", String(sourcePoint.y));
        hitShape.setAttribute("x2", String(targetPoint.x));
        hitShape.setAttribute("y2", String(targetPoint.y));
        hitShape.setAttribute("class", `topology-link-hitarea${shouldPreReveal ? " is-link-revealed" : ""}`);
        hitShape.setAttribute("data-topology-link-id", linkId);
        if (link.kind) {
            hitShape.setAttribute("data-link-kind", link.kind);
        }
        hitShape.setAttribute("data-link-from", link.from);
        hitShape.setAttribute("data-link-to", link.to);
        svg.appendChild(hitShape);

        const shape = document.createElementNS("http://www.w3.org/2000/svg", "line");
        shape.setAttribute("x1", String(sourcePoint.x));
        shape.setAttribute("y1", String(sourcePoint.y));
        shape.setAttribute("x2", String(targetPoint.x));
        shape.setAttribute("y2", String(targetPoint.y));
        shape.setAttribute(
            "class",
            `topology-link topology-link-${link.kind} topology-link-${getEffectiveTopologyLinkStatus(link, index) || "neutral"}${shouldPreReveal ? " is-link-revealed" : ""} ${topologyState.selectedKind === "link" && topologyState.selectedId === linkId ? "is-selected" : ""}`,
        );
        shape.setAttribute("data-topology-link-id", linkId);
        shape.setAttribute("data-link-from", link.from);
        shape.setAttribute("data-link-to", link.to);
        if (link.link_type === "dotted") {
            shape.setAttribute("stroke-dasharray", "8 6");
        }
        svg.appendChild(shape);

        if (topologyState.editMode && topologyState.selectedKind === "link" && topologyState.selectedId === linkId) {
            [
                { side: "source", x: sourcePoint.x, y: sourcePoint.y },
                { side: "target", x: targetPoint.x, y: targetPoint.y },
            ].forEach((handlePoint) => {
                const handle = document.createElement("button");
                handle.type = "button";
                handle.className = `topology-link-handle-button topology-link-handle-${handlePoint.side}`;
                handle.setAttribute("data-topology-link-handle", linkId);
                handle.setAttribute("data-topology-link-side", handlePoint.side);
                handle.style.left = `${handlePoint.x}px`;
                handle.style.top = `${handlePoint.y}px`;
                handleLayer.appendChild(handle);
            });
        }
    });

    svg.querySelectorAll("[data-topology-link-id]").forEach((line) => {
            const linkId = line.getAttribute("data-topology-link-id");
            const link = (topologyPayload?.links ?? []).find((item, index) => getTopologyLinkId(item, index) === linkId);
            line.addEventListener("click", (event) => {
                event.stopPropagation();
                if (!linkId) {
                    return;
                }
                // Clear any pinned entity tooltip when clicking a link
                if (topologyState.pinnedTooltipId) {
                    topologyState.pinnedTooltipId = null;
                }
                if (!topologyState.editMode && (link?.kind === "authored" || link?.kind === "discovery") && link?.status_node_id) {
                    const visualLine = svg.querySelector(`.topology-link[data-topology-link-id="${CSS.escape(linkId)}"]`);
                    if (visualLine instanceof SVGLineElement) {
                        const mx = (parseFloat(visualLine.getAttribute("x1")) + parseFloat(visualLine.getAttribute("x2"))) / 2;
                        const my = (parseFloat(visualLine.getAttribute("y1")) + parseFloat(visualLine.getAttribute("y2"))) / 2;
                        pinTopologyLinkTooltip(link, mx, my);
                    }
                    return;
                }
                setTopologyActiveLinkHandleTarget(null);
                if (topologyState.selectedKind === "link" && topologyState.selectedId === linkId) {
                    topologyState.selectedKind = null;
                    topologyState.selectedId = null;
                } else {
                    topologyState.selectedKind = "link";
                    topologyState.selectedId = linkId;
                }
                renderTopologyStage();
        });
        line.addEventListener("contextmenu", (event) => {
            if (!topologyState.editMode) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            if (!link || !link.db_id) {
                return;
            }
            topologyState.selectedKind = "link";
            topologyState.selectedId = linkId;
            openTopologyLinkContextMenu(link, event.clientX, event.clientY);
        });
        line.addEventListener("mouseenter", () => {
            if (topologyState.editMode || !link?.status_node_id || (link?.kind !== "authored" && link?.kind !== "discovery")) {
                return;
            }
            if (topologyState.pinnedLinkTooltipId) {
                return;
            }
            const visualLine = svg.querySelector(`.topology-link[data-topology-link-id="${CSS.escape(linkId)}"]`);
            if (visualLine instanceof SVGLineElement) {
                const mx = (parseFloat(visualLine.getAttribute("x1")) + parseFloat(visualLine.getAttribute("x2"))) / 2;
                const my = (parseFloat(visualLine.getAttribute("y1")) + parseFloat(visualLine.getAttribute("y2"))) / 2;
                showTopologyLinkTooltip(link, mx, my);
            }
        });
        line.addEventListener("mouseleave", () => {
            if (!topologyState.pinnedLinkTooltipId) {
                hideTopologyLinkTooltip();
            }
        });
    });

    handleLayer.querySelectorAll("[data-topology-link-handle]").forEach((handle) => {
        handle.addEventListener("pointerdown", (event) => {
            if (!topologyState.editMode) {
                return;
            }
            const linkId = handle.getAttribute("data-topology-link-handle");
            const side = handle.getAttribute("data-topology-link-side");
            if (!linkId || (side !== "source" && side !== "target")) {
                return;
            }
            const selectedLink = (topologyPayload?.links ?? []).find((item, index) => getTopologyLinkId(item, index) === linkId);
            if (!selectedLink) {
                return;
            }
            topologyState.dragging = {
                kind: "link-handle",
                pointerId: event.pointerId,
                linkId,
                side,
                entityId: side === "source" ? selectedLink.from : selectedLink.to,
            };
            try {
                handle.setPointerCapture(event.pointerId);
            } catch (error) {
                // Ignore capture failures.
            }

                const move = (moveEvent) => {
                    const drag = topologyState.dragging;
                    if (!drag || drag.kind !== "link-handle" || drag.pointerId !== moveEvent.pointerId) {
                        return;
                    }
                const line = svg.querySelector(`.topology-link[data-topology-link-id="${CSS.escape(linkId)}"]`);
                const activeHandle = handleLayer.querySelector(`.topology-link-handle-button[data-topology-link-handle="${CSS.escape(linkId)}"][data-topology-link-side="${side}"]`);
                    if (!(line instanceof SVGLineElement) || !(activeHandle instanceof HTMLElement)) {
                        return;
                    }
                    const anchorTarget = getTopologyAnchorTargetFromPoint(
                        moveEvent.clientX,
                        moveEvent.clientY,
                        drag.entityId,
                    );
                    let pointX = moveEvent.clientX - stageRect.left;
                    let pointY = moveEvent.clientY - stageRect.top;
                    if (anchorTarget?.anchorKey) {
                        const anchorBubble = stage.querySelector(`[data-topology-id="${CSS.escape(drag.entityId)}"]`);
                        if (anchorBubble instanceof HTMLElement) {
                            const snapPoint = getTopologyAnchorCoordinates(
                                anchorBubble.getBoundingClientRect(),
                                stageRect,
                                anchorTarget.anchorKey,
                            );
                            pointX = snapPoint.x;
                            pointY = snapPoint.y;
                            setTopologyActiveLinkHandleTarget(anchorTarget);
                            activeHandle.classList.add("is-snapped");
                        }
                    } else {
                        setTopologyActiveLinkHandleTarget(null);
                        activeHandle.classList.remove("is-snapped");
                    }
                    activeHandle.style.left = `${pointX}px`;
                    activeHandle.style.top = `${pointY}px`;
                    if (side === "source") {
                        line.setAttribute("x1", String(pointX));
                        line.setAttribute("y1", String(pointY));
                } else {
                    line.setAttribute("x2", String(pointX));
                    line.setAttribute("y2", String(pointY));
                }
                moveEvent.preventDefault();
            };

                const end = (endEvent) => {
                    const drag = topologyState.dragging;
                    if (!drag || drag.kind !== "link-handle" || drag.pointerId !== endEvent.pointerId) {
                        return;
                    }
                    const anchorTarget = topologyState.activeLinkHandleTarget?.entityId === drag.entityId
                        ? topologyState.activeLinkHandleTarget
                        : getTopologyAnchorTargetFromPoint(endEvent.clientX, endEvent.clientY, drag.entityId);
                    if (anchorTarget?.anchorKey) {
                        setTopologyLinkAnchorAssignment(linkId, side, anchorTarget.anchorKey);
                    }
                    setTopologyActiveLinkHandleTarget(null);
                    topologyState.dragging = null;
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", end);
                    window.removeEventListener("pointercancel", end);
                    renderTopologyStage();
                endEvent.preventDefault();
            };

            window.addEventListener("pointermove", move);
            window.addEventListener("pointerup", end);
            window.addEventListener("pointercancel", end);
            event.preventDefault();
            event.stopPropagation();
        });
    });
}

function getTopologySubmapIconMarkup(entity) {
    // Dense glowing mesh network — 12 nodes, ~30 connection lines
    // Nodes spread across a 96×64 viewBox for a wide, dense network feel
    const nodes = [
        // top row
        {x:16,y:8}, {x:38,y:6}, {x:60,y:10}, {x:82,y:7},
        // middle row
        {x:8,y:28}, {x:28,y:32}, {x:50,y:26}, {x:72,y:30}, {x:90,y:24},
        // bottom row
        {x:18,y:50}, {x:48,y:52}, {x:78,y:48},
    ];
    // Build dense connections — each node connects to its 3-4 nearest neighbors
    const lines = [
        // top row interconnections
        [0,1],[1,2],[2,3],
        // top to middle
        [0,4],[0,5],[1,5],[1,6],[2,6],[2,7],[3,7],[3,8],
        // middle row interconnections
        [4,5],[5,6],[6,7],[7,8],
        // middle to bottom
        [4,9],[5,9],[5,10],[6,10],[7,10],[7,11],[8,11],
        // bottom row interconnections
        [9,10],[10,11],
        // cross-links for density
        [0,6],[1,7],[2,8],[4,10],[6,11],[9,5],[3,6],
    ];
    const linesSvg = lines.map(([a,b]) =>
        `<line x1="${nodes[a].x}" y1="${nodes[a].y}" x2="${nodes[b].x}" y2="${nodes[b].y}" class="topology-submap-mesh-line"></line>`
    ).join("");
    const glowsSvg = nodes.map((n,i) =>
        `<circle cx="${n.x}" cy="${n.y}" r="${i===5||i===6? 6 : 5}" fill="url(#submap-node-glow)"></circle>`
    ).join("");
    const dotsSvg = nodes.map((n,i) =>
        `<circle cx="${n.x}" cy="${n.y}" r="${i===5||i===6? 2.2 : 1.8}" class="topology-submap-mesh-node"></circle>`
    ).join("");
    return `
        <span class="topology-node-icon topology-node-icon-submap" aria-hidden="true">
            <svg viewBox="0 0 96 58" focusable="false" preserveAspectRatio="xMidYMid meet">
                <defs>
                    <radialGradient id="submap-node-glow">
                        <stop offset="0%" stop-color="currentColor" stop-opacity="0.4"></stop>
                        <stop offset="50%" stop-color="currentColor" stop-opacity="0.15"></stop>
                        <stop offset="100%" stop-color="currentColor" stop-opacity="0"></stop>
                    </radialGradient>
                </defs>
                ${linesSvg}
                ${glowsSvg}
                ${dotsSvg}
            </svg>
        </span>
    `;
}

function getTopologyAnchorIconMarkup(entity) {
    if (!entity || entity.level >= 2) {
        return "";
    }

    if (entity.kind === "submap") {
        return getTopologySubmapIconMarkup(entity);
    }

    if (entity.kind === "services-cloud") {
        const statusClass = `topology-node-icon-status-${getTopologyIconStatus(entity.status)}`;
        return `
            <span class="topology-node-icon topology-node-icon-cloud ${statusClass}" aria-hidden="true">
                <svg viewBox="0 0 64 64" focusable="false">
                    <path d="M21 48h22.5c7 0 12.5-5 12.5-11.5 0-6.1-5-11.1-11.4-11.5C42.6 17.9 36.8 14 30.2 14c-8.1 0-14.8 5.7-16.4 13.2C8.8 28.1 5 32.4 5 37.7 5 43.4 9.9 48 16 48h5" class="topology-node-icon-stroke"></path>
                    <circle cx="24" cy="48" r="4.2" class="topology-node-icon-node"></circle>
                    <circle cx="34" cy="48" r="4.2" class="topology-node-icon-node"></circle>
                    <circle cx="44" cy="48" r="4.2" class="topology-node-icon-node"></circle>
                </svg>
            </span>
        `;
    }

    const accentClass = entity.level === 0 ? "topology-node-icon-agg" : "topology-node-icon-anchor";
    const iconStatus = entity.rtt_state
        ? getTopologyIconStatus(
            String(entity.rtt_state).toLowerCase() === "good"
                ? "healthy"
                : String(entity.rtt_state).toLowerCase() === "warn"
                    ? "degraded"
                    : "down",
        )
        : getTopologyIconStatus(entity.status);
    const statusClass = `topology-node-icon-status-${iconStatus}`;
    return `
        <span class="topology-node-icon ${accentClass} ${statusClass}" aria-hidden="true">
            <svg viewBox="0 0 64 64" focusable="false">
                <circle cx="32" cy="32" r="27" class="topology-node-icon-ring"></circle>
                <path d="M20 24v15l12 7 12-7" class="topology-node-icon-stroke"></path>
                <path d="M32 17v29" class="topology-node-icon-stroke"></path>
                <path d="M32 31l11-8" class="topology-node-icon-stroke"></path>
                <circle cx="20" cy="24" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="20" cy="39" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="32" cy="17" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="32" cy="31" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="32" cy="46" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="44" cy="23" r="3.2" class="topology-node-icon-node"></circle>
                <circle cx="44" cy="38" r="3.2" class="topology-node-icon-node"></circle>
            </svg>
        </span>
    `;
}

function getTopologyAnchorCoordinates(nodeRect, stageRect, anchorKey) {
    const points = Object.fromEntries(getTopologyAnchorPointDefinitions().map((point) => [point.key, point]));
    const point = points[anchorKey];
    if (!point) {
        return {
            x: nodeRect.left + nodeRect.width / 2 - stageRect.left,
            y: nodeRect.top + nodeRect.height / 2 - stageRect.top,
        };
    }

    return {
        x: nodeRect.left + nodeRect.width * point.x - stageRect.left,
        y: nodeRect.top + nodeRect.height * point.y - stageRect.top,
    };
}

function setTopologyActiveLinkHandleTarget(target) {
    topologyState.activeLinkHandleTarget = target || null;
    document.querySelectorAll(".topology-anchor-point.is-targeted").forEach((point) => {
        point.classList.remove("is-targeted");
    });
    if (!target?.entityId || !target?.anchorKey) {
        return;
    }
    const bubble = document.querySelector(`[data-topology-id="${CSS.escape(target.entityId)}"]`);
    const anchorPoint = bubble?.querySelector(
        `[data-topology-anchor-point="${CSS.escape(target.anchorKey)}"]`,
    );
    if (anchorPoint instanceof HTMLElement) {
        anchorPoint.classList.add("is-targeted");
    }
}

function getTopologyAnchorTargetFromPoint(clientX, clientY, entityId) {
    const elements = document.elementsFromPoint(clientX, clientY);
    for (const element of elements) {
        if (!(element instanceof Element)) {
            continue;
        }
        const anchorPoint = element.closest("[data-topology-anchor-point]");
        const bubble = anchorPoint?.closest("[data-topology-id]");
        if (!(anchorPoint instanceof HTMLElement) || !(bubble instanceof HTMLElement)) {
            continue;
        }
        if ((bubble.getAttribute("data-topology-id") || "") !== entityId) {
            continue;
        }
        const anchorKey = anchorPoint.getAttribute("data-topology-anchor-point") || null;
        if (!anchorKey) {
            continue;
        }
        return {
            entityId,
            anchorKey,
        };
    }
    return null;
}

function getAnyTopologyAnchorTargetFromPoint(clientX, clientY, excludeEntityId) {
    const stage = document.getElementById("topology-stage");
    if (!stage) {
        return null;
    }
    const snapRadius = 18;
    let best = null;
    let bestDist = snapRadius;
    stage.querySelectorAll("[data-topology-id]").forEach((bubble) => {
        const entityId = bubble.getAttribute("data-topology-id") || "";
        if (!entityId || entityId === excludeEntityId) {
            return;
        }
        bubble.querySelectorAll("[data-topology-anchor-point]").forEach((ap) => {
            const rect = ap.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const dist = Math.hypot(clientX - cx, clientY - cy);
            if (dist < bestDist) {
                bestDist = dist;
                best = { entityId, anchorKey: ap.getAttribute("data-topology-anchor-point") };
            }
        });
    });
    return best;
}

function setAnyTopologyAnchorTargetHighlight(target) {
    document.querySelectorAll(".topology-anchor-point.is-targeted").forEach((point) => {
        point.classList.remove("is-targeted");
    });
    if (!target?.entityId || !target?.anchorKey) {
        return;
    }
    const bubble = document.querySelector(`[data-topology-id="${CSS.escape(target.entityId)}"]`);
    const anchorPoint = bubble?.querySelector(
        `[data-topology-anchor-point="${CSS.escape(target.anchorKey)}"]`,
    );
    if (anchorPoint instanceof HTMLElement) {
        anchorPoint.classList.add("is-targeted");
    }
}

async function refreshTopologyData() {
    const root = document.getElementById("topology-root");
    if (root?.getAttribute("data-map-view-id")) {
        return;
    }
    try {
        const result = await apiRequest(buildNodeDashboardRequestUrl("/api/topology"));
        if (result) {
            topologyPayload = result;
        }
    } catch (error) {
        console.error("Failed to refresh topology data:", error);
    }
}

function buildSubmapEntityFromMapObject(obj) {
    const bindingKey = obj.binding_key || "";
    const inventoryNodeId = bindingKey.startsWith("anchor:") ? Number(bindingKey.split(":")[1]) : null;
    const inventoryNode = inventoryNodeId
        ? (Array.isArray(currentNodes) ? currentNodes : []).find((n) => n.id === inventoryNodeId)
        : null;
    return {
        id: `map-obj-${obj.id}`,
        map_object_id: obj.id,
        name: obj.label || inventoryNode?.name || obj.node_site_id || "Node",
        node_id: inventoryNode?.node_id || obj.node_site_id,
        site_id: inventoryNode?.node_id || obj.node_site_id,
        kind: "anchor",
        level: 0,
        x: obj.x,
        y: obj.y,
        width: obj.width || 160,
        height: obj.height || 96,
        status: "unknown",
        binding_key: obj.binding_key,
        inventory_node_id: inventoryNodeId,
        host: inventoryNode?.host,
        web_port: inventoryNode?.web_port,
        web_scheme: inventoryNode?.web_scheme,
        location: inventoryNode?.location,
    };
}

function renderSubmapAddNodeList() {
    const listEl = document.getElementById("submap-add-node-list");
    if (!listEl) {
        return;
    }
    const availableNodes = topologySubmapDetail?.available_nodes ?? [];
    const placedSiteIds = new Set(
        (topologyPayload?.lvl0_nodes ?? []).map((e) => e.site_id).filter(Boolean)
    );
    const unplaced = availableNodes.filter((n) => !placedSiteIds.has(n.site_id));
    if (unplaced.length === 0) {
        listEl.innerHTML = '<p class="table-message">All inventory nodes have been placed.</p>';
        return;
    }
    listEl.innerHTML = unplaced.map((node) => `
        <button type="button" class="submap-add-node-item" data-site-id="${escapeHtml(node.site_id)}" data-display-name="${escapeHtml(node.display_name)}" data-binding-key="${escapeHtml(node.binding_key || "")}">
            <strong>${escapeHtml(node.display_name)}</strong>
            <span class="submap-add-node-meta">${escapeHtml(node.site_id)}${node.location ? " · " + escapeHtml(node.location) : ""}</span>
        </button>
    `).join("");
    listEl.querySelectorAll(".submap-add-node-item").forEach((btn) => {
        btn.addEventListener("click", () => {
            placeSubmapNode(
                btn.getAttribute("data-site-id"),
                btn.getAttribute("data-display-name"),
                btn.getAttribute("data-binding-key"),
            );
        });
    });
}

async function placeSubmapNode(siteId, displayName, bindingKey) {
    const root = document.getElementById("topology-root");
    const submapViewId = root?.getAttribute("data-map-view-id");
    if (!submapViewId) {
        return;
    }
    const stage = document.getElementById("topology-stage");
    const x = 100 + Math.round(Math.random() * Math.max((stage?.clientWidth || 800) - 300, 200));
    const y = 100 + Math.round(Math.random() * Math.max((stage?.clientHeight || 600) - 300, 200));
    try {
        const response = await fetch(`/api/topology/maps/${encodeURIComponent(submapViewId)}/objects`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                map_view_id: Number(submapViewId),
                object_type: "node",
                label: displayName,
                node_site_id: siteId,
                binding_key: bindingKey || null,
                x: x,
                y: y,
            }),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            window.alert("Failed to place node: " + (errorData?.detail || response.statusText));
            return;
        }
        const created = await response.json();
        const entity = buildSubmapEntityFromMapObject(created);
        if (!topologyPayload.lvl0_nodes) {
            topologyPayload.lvl0_nodes = [];
        }
        topologyPayload.lvl0_nodes.push(entity);
        setTopologyEntityLayout(entity.id, { x: entity.x, y: entity.y, size: 96 });
        renderSubmapAddNodeList();
        renderTopologyStage();
    } catch (error) {
        console.error("Failed to place node on submap:", error);
        window.alert("Failed to place node.");
    }
}

async function refreshSubmapDiscovery(submapViewId) {
    if (!submapViewId || !topologyPayload) {
        return;
    }
    try {
        const result = await apiRequest(`/api/topology/maps/${encodeURIComponent(submapViewId)}/discovery`);
        const peers = result?.discovered_peers ?? [];
        const placedSiteIds = new Set(
            (topologyPayload.lvl0_nodes ?? []).map((e) => e.site_id).filter(Boolean)
        );
        const discoveredEntities = peers
            .filter((p) => !placedSiteIds.has(p.site_id))
            .map((peer) => {
                const entityId = `dn-${peer.site_id}`;
                return {
                    id: entityId,
                    name: peer.name || peer.site_id,
                    node_id: peer.site_id,
                    site_id: peer.site_id,
                    kind: "discovered",
                    level: 1,
                    status: peer.ping?.toLowerCase() === "up" ? "healthy" : "down",
                    rtt_state: peer.ping_state || null,
                    latency_ms: peer.latency_ms ?? null,
                    avg_latency_ms: peer.avg_latency_ms ?? null,
                    host: peer.host,
                    source_anchor_id: peer.source_anchor_id,
                    source_name: peer.source_name,
                    tx_display: peer.tx_rate,
                    rx_display: peer.rx_rate,
                };
            });
        topologyPayload.lvl1_nodes = discoveredEntities;

        // Apply saved positions from DB first, then grid-place truly new DNs
        const savedPositions = result?.saved_positions ?? {};
        const stage = document.getElementById("topology-stage");
        const stageW = stage ? stage.clientWidth : 1200;
        const stageH = stage ? stage.clientHeight : 800;
        const margin = 40;
        const dnSize = 60;
        const topBound = Math.round(stageH * 0.25);

        // Restore DB-saved positions for DNs that don't have a layout override yet
        discoveredEntities.forEach((dn) => {
            if (!topologyState.layoutOverrides?.[dn.id]) {
                const saved = savedPositions[dn.site_id];
                if (saved && saved.x != null && saved.y != null) {
                    setTopologyEntityLayout(dn.id, { x: saved.x, y: saved.y, size: dnSize });
                }
            }
        });

        // Collect occupied rectangles from all existing layout overrides
        const occupied = [];
        const allEntities = [...(topologyPayload.lvl0_nodes ?? []), ...discoveredEntities];
        allEntities.forEach((e) => {
            const lo = topologyState.layoutOverrides?.[e.id];
            if (lo) {
                occupied.push({ x: lo.x, y: lo.y, size: lo.size || 96 });
            }
        });

        // Grid-place only DNs with no layout override (no DB position and no local override)
        const needsLayout = discoveredEntities.filter((dn) => !topologyState.layoutOverrides?.[dn.id]);
        if (needsLayout.length) {
            const availW = stageW - margin * 2;
            const availH = stageH - topBound - margin;
            const cols = Math.max(1, Math.floor(availW / (dnSize + 30)));
            const rows = Math.ceil(needsLayout.length / cols);
            const cellW = availW / cols;
            const cellH = Math.max(dnSize + 20, availH / Math.max(rows, 1));

            needsLayout.forEach((dn, i) => {
                const col = i % cols;
                const row = Math.floor(i / cols);
                let x = Math.round(margin + col * cellW + cellW / 2 - dnSize / 2);
                let y = Math.round(topBound + row * cellH + cellH / 2 - dnSize / 2);

                // Nudge if overlapping an occupied node
                for (const occ of occupied) {
                    const dx = Math.abs(x - occ.x);
                    const dy = Math.abs(y - occ.y);
                    if (dx < (dnSize + occ.size) / 2 + 20 && dy < (dnSize + occ.size) / 2 + 20) {
                        y = occ.y + occ.size + 30;
                    }
                }

                occupied.push({ x, y, size: dnSize });
                setTopologyEntityLayout(dn.id, { x: Math.max(margin, x), y: Math.max(topBound, y), size: dnSize });
            });
        }
        // Build discovery links (AN↔DN and DN↔DN tunnel connections)
        const placedEntities = topologyPayload.lvl0_nodes ?? [];
        const rawLinks = result?.discovery_links ?? [];
        const anchorEntityMap = new Map();
        placedEntities.forEach((e) => {
            if (e.inventory_node_id) {
                anchorEntityMap.set(String(e.inventory_node_id), e.id);
            }
        });
        const dnEntityIds = new Set(discoveredEntities.map((dn) => dn.id));
        const discoveryLinks = rawLinks
            .map((link, i) => {
                const toEntityId = `dn-${link.target_site_id}`;
                if (!dnEntityIds.has(toEntityId)) return null;

                // DN↔DN link: from is a DN entity
                if (link.kind === "dn-dn" && link.source_dn_site_id) {
                    const fromEntityId = `dn-${link.source_dn_site_id}`;
                    if (!dnEntityIds.has(fromEntityId)) return null;
                    if (fromEntityId === toEntityId) return null; // self-link guard
                    return {
                        id: `discovery-link-${i}`,
                        from: fromEntityId,
                        to: toEntityId,
                        source_anchor: "s",
                        target_anchor: "n",
                        link_type: "dotted",
                        kind: "discovery",
                        status: link.status || "neutral",
                        status_node_id: link.source_anchor_id || null,
                        target_site_id: link.target_site_id,
                    };
                }

                // AN↔DN link: from is an anchor entity
                const fromEntityId = anchorEntityMap.get(String(link.source_anchor_id));
                if (!fromEntityId) return null;
                return {
                    id: `discovery-link-${i}`,
                    from: fromEntityId,
                    to: toEntityId,
                    source_anchor: "s",
                    target_anchor: "n",
                    link_type: "dotted",
                    kind: "discovery",
                    status: link.status || "neutral",
                    status_node_id: link.source_anchor_id,
                    target_site_id: link.target_site_id,
                };
            })
            .filter(Boolean);
        // Deduplicate: if A→B and B→A both exist, keep only one
        const seenLinkPairs = new Set();
        const dedupedLinks = discoveryLinks.filter((link) => {
            const pairKey = [link.from, link.to].sort().join("::");
            if (seenLinkPairs.has(pairKey)) return false;
            seenLinkPairs.add(pairKey);
            return true;
        });
        topologyPayload.links = dedupedLinks;

        // Detect DN status changes and flash links
        const currentDnStates = {};
        discoveredEntities.forEach((dn) => {
            const state = dn.rtt_state || (dn.status === "healthy" ? "good" : "down");
            currentDnStates[dn.id] = state;
        });
        // After render, flash links for DNs whose state changed
        const prevStates = topologyState._prevDnStates || {};
        const changedEntityIds = [];
        for (const [entityId, state] of Object.entries(currentDnStates)) {
            const prev = prevStates[entityId];
            if (prev && prev !== state) {
                changedEntityIds.push(entityId);
            }
        }
        topologyState._prevDnStates = currentDnStates;
        // Schedule flashes after next render (links need to be drawn first)
        if (changedEntityIds.length) {
            requestAnimationFrame(() => {
                changedEntityIds.forEach((entityId) => {
                    if (topologyState.pinnedLinkNodeId !== entityId) {
                        flashDiscoveryLinksForEntity(entityId);
                    }
                });
            });
        }
    } catch (error) {
        console.error("Failed to refresh submap discovery:", error);
    }
}

async function deleteSubmapObject(mapObjectId, entityId) {
    try {
        const response = await fetch(`/api/topology/maps/objects/${encodeURIComponent(mapObjectId)}`, {
            method: "DELETE",
        });
        if (!response.ok && response.status !== 204) {
            console.error("Failed to delete submap object:", response.status);
            return;
        }
        if (topologyPayload?.lvl0_nodes) {
            topologyPayload.lvl0_nodes = topologyPayload.lvl0_nodes.filter((e) => e.id !== entityId);
        }
        removeTopologyEntityLayout(entityId);
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        topologyState.selectedEntityIds.delete(entityId);
        renderSubmapAddNodeList();
        renderTopologyStage();
    } catch (error) {
        console.error("Failed to delete submap object:", error);
    }
}

async function renameTopologySubmap(entity) {
    const currentName = entity.name || "";
    const newName = window.prompt("Rename submap:", currentName);
    if (!newName || !newName.trim() || newName.trim() === currentName) {
        return;
    }
    const trimmedName = newName.trim();
    try {
        const response = await fetch(`/api/topology/maps/${encodeURIComponent(entity.map_view_id)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: trimmedName }),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            window.alert("Failed to rename submap: " + (errorData?.detail || response.statusText));
            return;
        }
        const submapList = topologyPayload?.submaps ?? [];
        const submapEntry = submapList.find((s) => s.id === entity.id);
        if (submapEntry) {
            submapEntry.name = trimmedName;
        }
        renderTopologyStage();
    } catch (error) {
        console.error("Failed to rename submap:", error);
        window.alert("Failed to rename submap.");
    }
}

async function createTopologyLink(sourceEntityId, sourceAnchor, targetEntityId, targetAnchor) {
    try {
        const response = await fetch("/api/topology/links", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_entity_id: sourceEntityId,
                target_entity_id: targetEntityId,
                source_anchor: sourceAnchor,
                target_anchor: targetAnchor,
                link_type: "solid",
            }),
        });
        if (!response.ok) {
            console.error("Failed to create topology link:", response.status);
            return null;
        }
        return await response.json();
    } catch (error) {
        console.error("Failed to create topology link:", error);
        return null;
    }
}

// --- Discovery link visibility helpers ---

function getDiscoveryLinksForEntity(entityId) {
    const svg = document.getElementById("topology-links");
    if (!svg) return [];
    return Array.from(svg.querySelectorAll(`.topology-link-discovery[data-link-from="${CSS.escape(entityId)}"], .topology-link-discovery[data-link-to="${CSS.escape(entityId)}"]`));
}

function getDiscoveryHitareasForEntity(entityId) {
    const svg = document.getElementById("topology-links");
    if (!svg) return [];
    return Array.from(svg.querySelectorAll(`.topology-link-hitarea[data-link-kind="discovery"][data-link-from="${CSS.escape(entityId)}"], .topology-link-hitarea[data-link-kind="discovery"][data-link-to="${CSS.escape(entityId)}"]`));
}

function revealDiscoveryLinksForEntity(entityId) {
    getDiscoveryLinksForEntity(entityId).forEach((el) => {
        el.classList.remove("is-link-flashing", "is-link-fading");
        el.classList.add("is-link-revealed");
    });
    getDiscoveryHitareasForEntity(entityId).forEach((el) => {
        el.classList.add("is-link-revealed");
    });
}

function hideDiscoveryLinksForEntity(entityId) {
    getDiscoveryLinksForEntity(entityId).forEach((el) => {
        el.classList.remove("is-link-revealed", "is-link-flashing", "is-link-fading");
    });
    getDiscoveryHitareasForEntity(entityId).forEach((el) => {
        el.classList.remove("is-link-revealed");
    });
}

function hideAllDiscoveryLinks() {
    const svg = document.getElementById("topology-links");
    if (!svg) return;
    svg.querySelectorAll(".topology-link-discovery.is-link-revealed").forEach((el) => {
        el.classList.remove("is-link-revealed", "is-link-flashing", "is-link-fading");
    });
    svg.querySelectorAll('.topology-link-hitarea[data-link-kind="discovery"].is-link-revealed').forEach((el) => {
        el.classList.remove("is-link-revealed");
    });
}

function revealAllDiscoveryLinks() {
    const svg = document.getElementById("topology-links");
    if (!svg) return;
    svg.querySelectorAll(".topology-link-discovery").forEach((el) => {
        el.classList.remove("is-link-flashing", "is-link-fading");
        el.classList.add("is-link-revealed");
    });
    svg.querySelectorAll('.topology-link-hitarea[data-link-kind="discovery"]').forEach((el) => {
        el.classList.add("is-link-revealed");
    });
}


function flashDiscoveryLinksForEntity(entityId) {
    const lines = getDiscoveryLinksForEntity(entityId);
    if (!lines.length) return;
    lines.forEach((el) => {
        el.classList.remove("is-link-revealed", "is-link-fading");
        el.classList.add("is-link-flashing");
    });
    const timer = setTimeout(() => {
        lines.forEach((el) => {
            el.classList.remove("is-link-flashing");
            el.classList.add("is-link-fading");
        });
        const fadeTimer = setTimeout(() => {
            lines.forEach((el) => {
                el.classList.remove("is-link-fading");
            });
        }, 1600);
        topologyState._flashTimers.push(fadeTimer);
    }, 3000);
    topologyState._flashTimers.push(timer);
}

// --- End discovery link visibility helpers ---

async function fetchTopologyLinkStats(inventoryNodeId) {
    const cached = topologyLinkStatsCache.get(inventoryNodeId);
    if (cached && Date.now() - cached.fetchedAt < 10000) {
        return cached.data;
    }
    try {
        const data = await apiRequest(`/api/nodes/${inventoryNodeId}/stats`);
        if (data?.status === "ok") {
            topologyLinkStatsCache.set(inventoryNodeId, { data, fetchedAt: Date.now() });
            return data;
        }
    } catch (error) {
        console.error("Failed to fetch link stats:", error);
    }
    return null;
}

function findTunnelRowForPeer(statsData, peerSiteId) {
    if (!statsData?.tunnels || !peerSiteId) {
        return null;
    }
    const peerId = String(peerSiteId).trim();
    return statsData.tunnels.find((t) => String(t.mate_site_id || "").trim() === peerId) || null;
}

function getTopologyLinkPeerSiteId(link) {
    const entities = getTopologyEntities();
    const statusNodeId = link.status_node_id;
    const sourceEntity = entities.find((e) => e.id === link.from);
    const targetEntity = entities.find((e) => e.id === link.to);
    const statusEntity = (sourceEntity?.inventory_node_id == statusNodeId) ? sourceEntity : targetEntity;
    const peerEntity = (statusEntity === sourceEntity) ? targetEntity : sourceEntity;
    return peerEntity?.site_id || peerEntity?.node_id || null;
}

function buildTopologyLinkTooltipMarkup(link, tunnelRow) {
    if (!link || (link.kind !== "authored" && link.kind !== "discovery")) {
        return "";
    }
    let name;
    let entity;
    if (link.kind === "discovery") {
        const entities = getTopologyEntities();
        const dnEntity = entities.find((e) => e.id === link.to);
        const anEntity = entities.find((e) => e.id === link.from);
        name = `${anEntity?.name || "AN"} ↔ ${dnEntity?.site_id || "DN"}`;
        entity = anEntity || dnEntity;
    } else {
        entity = getTopologyStatusNodeEntity(link.status_node_id);
        name = entity?.inventory_name || entity?.name || "Node";
    }
    if (!entity) {
        return "";
    }
    const linkStatus = link.kind === "discovery"
        ? (link.status === "down" ? "down" : link.status === "degraded" ? "degraded" : "healthy")
        : computeTopologyLinkStatusFromNode(entity);
    const statusLabel = linkStatus === "down" ? "Down" : linkStatus === "degraded" ? "Degraded" : "Healthy";
    const statusDot = linkStatus === "down" ? "down" : linkStatus === "degraded" ? "degraded" : "up";

    const hasTunnel = tunnelRow != null;
    const pingText = hasTunnel ? (tunnelRow.ping || "--") : (entity.ping_ok ? "Up" : "Down");
    const rttText = hasTunnel ? (tunnelRow.rtt_ms || "--") : "--";
    const txText = hasTunnel ? (tunnelRow.tx_rate || "--") : "--";
    const rxText = hasTunnel ? (tunnelRow.rx_rate || "--") : "--";
    const tunnelHealth = hasTunnel && Array.isArray(tunnelRow.tunnel_health) ? tunnelRow.tunnel_health : [];
    const reason = linkStatus === "down"
        ? "Ping is down"
        : linkStatus === "degraded"
            ? "RTT elevated >50% above average"
            : "Link healthy";

    const tunnelDots = tunnelHealth.length
        ? `<span class="topology-link-tooltip-label">Tunnels</span>
           <span class="topology-link-tooltip-value">${tunnelHealth.map((s, i) =>
               `<span class="topology-link-tooltip-status-dot ${s === "up" ? "up" : s === "down" ? "down" : s === "off" ? "off" : "degraded"}" title="Tun${i}: ${escapeHtml(s)}"></span>`
           ).join("")}</span>`
        : "";

    return `
        <strong class="topology-link-tooltip-title">
            <span class="topology-link-tooltip-status-dot ${statusDot}"></span>
            ${escapeHtml(name)} — ${escapeHtml(statusLabel)}
        </strong>
        <span class="topology-link-tooltip-reason">${escapeHtml(reason)}</span>
        <span class="topology-link-tooltip-grid">
            ${tunnelDots}
            <span class="topology-link-tooltip-label">Ping</span>
            <span class="topology-link-tooltip-value">${escapeHtml(pingText)}</span>
            <span class="topology-link-tooltip-label">RTT</span>
            <span class="topology-link-tooltip-value">${escapeHtml(rttText)}</span>
            <span class="topology-link-tooltip-label">TX Rate</span>
            <span class="topology-link-tooltip-value">${escapeHtml(txText)}</span>
            <span class="topology-link-tooltip-label">RX Rate</span>
            <span class="topology-link-tooltip-value">${escapeHtml(rxText)}</span>
        </span>
    `;
}

async function showTopologyLinkTooltip(link, midX, midY) {
    const tooltip = document.getElementById("topology-link-tooltip");
    if (!tooltip) {
        return;
    }
    if (!link || (!link.status_node_id && link.kind !== "discovery")) {
        hideTopologyLinkTooltip();
        return;
    }
    if (link.kind !== "authored" && link.kind !== "discovery") {
        hideTopologyLinkTooltip();
        return;
    }
    tooltip.innerHTML = '<span class="topology-link-tooltip-reason">Loading...</span>';
    tooltip.hidden = false;
    tooltip.style.left = `${midX}px`;
    tooltip.style.top = `${midY}px`;

    const peerSiteId = link.kind === "discovery" ? String(link.target_site_id || "") : getTopologyLinkPeerSiteId(link);
    const stats = link.status_node_id ? await fetchTopologyLinkStats(link.status_node_id) : null;
    const tunnelRow = findTunnelRowForPeer(stats, peerSiteId);
    const markup = buildTopologyLinkTooltipMarkup(link, tunnelRow);
    if (!markup) {
        hideTopologyLinkTooltip();
        return;
    }
    tooltip.innerHTML = markup;
}

function hideTopologyLinkTooltip(force) {
    const tooltip = document.getElementById("topology-link-tooltip");
    if (!tooltip) {
        return;
    }
    if (!force && tooltip.classList.contains("is-pinned")) {
        return;
    }
    tooltip.hidden = true;
    tooltip.classList.remove("is-pinned");
    tooltip.innerHTML = "";
}

function pinTopologyLinkTooltip(link, midX, midY) {
    const tooltip = document.getElementById("topology-link-tooltip");
    if (!tooltip) {
        return;
    }
    if (topologyState.pinnedLinkTooltipId === link.id && !tooltip.hidden) {
        hideTopologyLinkTooltip(true);
        topologyState.pinnedLinkTooltipId = null;
        return;
    }
    showTopologyLinkTooltip(link, midX, midY);
    tooltip.classList.add("is-pinned");
    topologyState.pinnedLinkTooltipId = link.id;
}

async function refreshPinnedLinkTooltip() {
    if (!topologyState.pinnedLinkTooltipId) {
        return;
    }
    const links = topologyPayload?.links ?? [];
    const link = links.find((l, i) => getTopologyLinkId(l, i) === topologyState.pinnedLinkTooltipId);
    if (!link) {
        hideTopologyLinkTooltip(true);
        topologyState.pinnedLinkTooltipId = null;
        return;
    }
    const svg = document.getElementById("topology-links");
    const visualLine = svg?.querySelector(`.topology-link[data-topology-link-id="${CSS.escape(link.id)}"]`);
    if (visualLine instanceof SVGLineElement) {
        const mx = (parseFloat(visualLine.getAttribute("x1")) + parseFloat(visualLine.getAttribute("x2"))) / 2;
        const my = (parseFloat(visualLine.getAttribute("y1")) + parseFloat(visualLine.getAttribute("y2"))) / 2;
        await showTopologyLinkTooltip(link, mx, my);
        const tooltip = document.getElementById("topology-link-tooltip");
        if (tooltip) {
            tooltip.classList.add("is-pinned");
        }
    }
}

async function updateTopologyLink(dbId, updates) {
    try {
        const response = await fetch(`/api/topology/links/${dbId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(updates),
        });
        if (!response.ok) {
            console.error("Failed to update topology link:", response.status);
            return null;
        }
        return await response.json();
    } catch (error) {
        console.error("Failed to update topology link:", error);
        return null;
    }
}

async function deleteTopologyLink(dbId) {
    try {
        const response = await fetch(`/api/topology/links/${dbId}`, { method: "DELETE" });
        return response.ok || response.status === 204;
    } catch (error) {
        console.error("Failed to delete topology link:", error);
        return false;
    }
}

function openTopologyLinkContextMenu(link, clientX, clientY) {
    const menu = document.getElementById("topology-link-context-menu");
    if (!menu) {
        return;
    }

    const typeSelect = document.getElementById("topology-link-ctx-type");
    const statusNodeSelect = document.getElementById("topology-link-ctx-status-node");
    if (!typeSelect || !statusNodeSelect) {
        return;
    }

    typeSelect.value = link.link_type || "solid";

    const entities = getTopologyEntities();
    const fromEntity = entities.find((e) => e.id === link.from);
    const toEntity = entities.find((e) => e.id === link.to);
    statusNodeSelect.innerHTML = '<option value="">None</option>';
    [fromEntity, toEntity].filter(Boolean).forEach((entity) => {
        const invId = entity.inventory_node_id;
        if (!invId) {
            return;
        }
        const opt = document.createElement("option");
        opt.value = String(invId);
        opt.textContent = entity.name || entity.id;
        statusNodeSelect.appendChild(opt);
    });
    statusNodeSelect.value = link.status_node_id ? String(link.status_node_id) : "";

    menu.hidden = false;
    menu.style.left = `${Math.min(clientX, window.innerWidth - 260)}px`;
    menu.style.top = `${Math.min(clientY, window.innerHeight - 240)}px`;
    menu.dataset.linkId = link.id;
    menu.dataset.dbId = String(link.db_id);
}

function closeTopologyLinkContextMenu() {
    const menu = document.getElementById("topology-link-context-menu");
    if (menu) {
        menu.hidden = true;
        delete menu.dataset.linkId;
        delete menu.dataset.dbId;
    }
}

function initTopologyLinkContextMenu() {
    const menu = document.getElementById("topology-link-context-menu");
    if (!menu) {
        return;
    }

    document.getElementById("topology-link-context-menu-close")?.addEventListener("click", () => {
        closeTopologyLinkContextMenu();
    });

    document.getElementById("topology-link-ctx-save")?.addEventListener("click", async () => {
        const dbId = menu.dataset.dbId;
        if (!dbId) {
            return;
        }
        const linkType = document.getElementById("topology-link-ctx-type")?.value || "solid";
        const statusNodeVal = document.getElementById("topology-link-ctx-status-node")?.value;
        const statusNodeId = statusNodeVal ? Number(statusNodeVal) : null;
        await updateTopologyLink(dbId, { link_type: linkType, status_node_id: statusNodeId });
        closeTopologyLinkContextMenu();
        await refreshTopologyData();
        renderTopologyStage();
    });

    document.getElementById("topology-link-ctx-delete")?.addEventListener("click", async () => {
        const dbId = menu.dataset.dbId;
        if (!dbId) {
            return;
        }
        await deleteTopologyLink(dbId);
        closeTopologyLinkContextMenu();
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        await refreshTopologyData();
        renderTopologyStage();
    });

    document.addEventListener("pointerdown", (event) => {
        if (menu.hidden) {
            return;
        }
        if (!menu.contains(event.target)) {
            closeTopologyLinkContextMenu();
        }
    });
}

function getTopologyInventoryNodeRecord(entity) {
    const inventoryNodeId = Number(entity?.inventory_node_id || 0);
    if (!inventoryNodeId) {
        return null;
    }
    return currentNodes.find((node) => Number(node.id) === inventoryNodeId) || null;
}

function renderTopologyNodeEditorMarkup(entity, node) {
    const authoredLabel = getTopologyEntityLabel(entity);
    const defaultDisplayName = entity.name || entity.unit || entity.id;
    const hasAuthoredLabel = authoredLabel !== defaultDisplayName;
    return `
        <div class="topology-drawer-block">
            <span class="dashboard-meta-label">Selected Node</span>
            <h3>${escapeHtml(authoredLabel)}</h3>
        </div>
        <div class="topology-drawer-block">
            <span class="dashboard-meta-label">Authored Label</span>
            <label class="topology-label-editor" for="topology-label-input">
                <span class="topology-label-editor-copy">Override the visible map label for this object.</span>
                <input
                    id="topology-label-input"
                    class="topology-label-editor-input"
                    type="text"
                    maxlength="80"
                    value="${escapeHtml(hasAuthoredLabel ? authoredLabel : "")}"
                    placeholder="${escapeHtml(defaultDisplayName)}"
                >
            </label>
            <div class="topology-label-editor-actions">
                <button type="button" class="button-primary" id="topology-label-save">Save Label</button>
                <button type="button" class="button-secondary" id="topology-label-reset"${hasAuthoredLabel ? "" : " disabled"}>Use Default</button>
            </div>
        </div>
        <form class="node-form topology-node-editor-form" id="topology-node-editor-form">
            <label>
                <span>Name</span>
                <input type="text" id="topology-node-name" value="${escapeHtml(node?.name || entity.name || "")}" required>
            </label>
            <label>
                <span>Host / IP</span>
                <input type="text" id="topology-node-host" value="${escapeHtml(node?.host || entity.metrics_text || "")}" required>
            </label>
            <label>
                <span>Web Port</span>
                <input type="number" id="topology-node-web-port" min="1" max="65535" value="${escapeHtml(String(node?.web_port || entity.web_port || 443))}" required>
            </label>
            <label>
                <span>SSH Port</span>
                <input type="number" id="topology-node-ssh-port" min="1" max="65535" value="${escapeHtml(String(node?.ssh_port || 22))}" required>
            </label>
            <label>
                <span>Location</span>
                <input type="text" id="topology-node-location" value="${escapeHtml(node?.location || entity.location || "Cloud")}" required>
            </label>
            <label class="checkbox-field">
                <input type="checkbox" id="topology-node-include" ${(node?.include_in_topology ?? entity.include_in_topology) ? "checked" : ""}>
                <span>Include in Topology</span>
            </label>
            <label class="full-width">
                <span>Notes</span>
                <textarea id="topology-node-notes" rows="3" placeholder="Optional notes">${escapeHtml(node?.notes || "")}</textarea>
            </label>
            <label>
                <span>API Username</span>
                <input type="text" id="topology-node-api-username" value="${escapeHtml(node?.api_username || "")}">
            </label>
            <label>
                <span>API Password</span>
                <input type="password" id="topology-node-api-password" value="${escapeHtml(node?.api_password || "")}">
            </label>
            <label class="checkbox-field">
                <input type="checkbox" id="topology-node-api-use-https" ${node?.api_use_https ? "checked" : ""}>
                <span>API Uses HTTPS</span>
            </label>
            <div class="form-actions full-width node-modal-actions">
                <button type="submit" class="button-primary">Save Node</button>
                <button type="button" class="button-secondary" id="topology-node-delete">Delete Node</button>
            </div>
            <p id="topology-node-editor-error" class="status-error" hidden>Unable to save node.</p>
        </form>
    `;
}

function wireTopologyNodeEditor(entity, node) {
    const labelInput = document.getElementById("topology-label-input");
    const labelSaveButton = document.getElementById("topology-label-save");
    const labelResetButton = document.getElementById("topology-label-reset");
    const form = document.getElementById("topology-node-editor-form");
    const deleteButton = document.getElementById("topology-node-delete");
    const formError = document.getElementById("topology-node-editor-error");
    const inventoryNodeId = Number(entity.inventory_node_id || node?.id || 0);

    const commitLabel = () => {
        if (!(labelInput instanceof HTMLInputElement)) {
            return;
        }
        setTopologyEntityLabel(entity.id, labelInput.value);
        renderTopologyStage();
    };
    labelSaveButton?.addEventListener("click", commitLabel);
    labelInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            commitLabel();
        }
    });
    labelResetButton?.addEventListener("click", () => {
        clearTopologyEntityLabel(entity.id);
        renderTopologyStage();
    });

    form?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!inventoryNodeId) {
            return;
        }
        try {
            const payload = {
                name: document.getElementById("topology-node-name").value.trim(),
                node_id: node?.node_id ?? null,
                host: document.getElementById("topology-node-host").value.trim(),
                web_port: Number(document.getElementById("topology-node-web-port").value),
                ssh_port: Number(document.getElementById("topology-node-ssh-port").value),
                location: document.getElementById("topology-node-location").value.trim(),
                include_in_topology: document.getElementById("topology-node-include").checked,
                topology_level: Number(entity.level || 0),
                topology_unit: String(entity.unit || (entity.level === 0 ? "AGG" : "DIV HQ")),
                enabled: node?.enabled ?? true,
                notes: document.getElementById("topology-node-notes").value.trim() || null,
                api_username: document.getElementById("topology-node-api-username").value.trim() || null,
                api_password: document.getElementById("topology-node-api-password").value.trim() || null,
                api_use_https: document.getElementById("topology-node-api-use-https").checked,
            };
            await apiRequest(`/api/nodes/${inventoryNodeId}`, {
                method: "PUT",
                body: JSON.stringify(payload),
            });
            await loadNodes();
            await loadNodeDashboard();
            await loadMainDashboard();
            await loadTopologyPage();
            topologyState.selectedKind = "entity";
            topologyState.selectedId = entity.id;
            showFeedback("Node updated.");
        } catch (error) {
            if (formError instanceof HTMLElement) {
                formError.textContent = error.message || "Unable to save node.";
                formError.hidden = false;
            }
        }
    });

    deleteButton?.addEventListener("click", async () => {
        if (!inventoryNodeId) {
            return;
        }
        if (!window.confirm(`Delete ${node?.name || entity.name || "this node"} from inventory and the map?`)) {
            return;
        }
        try {
            await apiRequest(`/api/nodes/${inventoryNodeId}`, { method: "DELETE" });
            removeTopologyEntityLayout(entity.id);
            clearTopologyEntityLabel(entity.id);
            topologyState.selectedKind = null;
            topologyState.selectedId = null;
            clearTopologyEntitySelection();
            await loadNodes();
            await loadNodeDashboard();
            await loadMainDashboard();
            await loadTopologyPage();
            showFeedback("Node deleted.");
        } catch (error) {
            if (formError instanceof HTMLElement) {
                formError.textContent = error.message || "Unable to delete node.";
                formError.hidden = false;
            }
        }
    });
}

function renderTopologyDrawer() {
    return;
}

function getNewTopologyNodeLayout() {
    const stage = document.getElementById("topology-stage");
    const width = Math.max(stage?.clientWidth || 1200, 600);
    const height = Math.max(stage?.clientHeight || 940, 500);
    return {
        x: Math.round(width * 0.5),
        y: Math.round(height * 0.42),
        size: 92,
    };
}

function toggleTopologySetValue(setRef, value) {
    if (setRef.has(value)) {
        setRef.delete(value);
    } else {
        setRef.add(value);
    }
}

function wireTopologyControls() {
    const root = document.getElementById("topology-root");
    if (!root) {
        return;
    }

    root.querySelectorAll("[data-topology-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            const filterType = button.getAttribute("data-topology-filter");
            const value = button.getAttribute("data-topology-value");
            if (!filterType || !value) {
                return;
            }

            const targetSet = filterType === "location" ? topologyState.activeLocations : topologyState.activeUnits;
            if (targetSet.size === 1 && targetSet.has(value)) {
                return;
            }

            if (filterType === "unit") {
                topologyState.focusUnit = null;
                updateTopologyUnitRoute(null);
            }
            toggleTopologySetValue(targetSet, value);
            renderTopologyControls();
            renderTopologyStage();
        });
    });

    root.querySelectorAll("[data-topology-view]").forEach((button) => {
        button.addEventListener("click", () => {
            topologyState.view = button.getAttribute("data-topology-view") || "backbone+l2";
            renderTopologyControls();
            renderTopologyStage();
        });
    });
}

function renderTopologyControls() {
    renderTopologyFilterButtons("topology-location-filters", TOPOLOGY_LOCATIONS, topologyState.activeLocations, "location");
    renderTopologyFilterButtons("topology-unit-filters", TOPOLOGY_UNITS, topologyState.activeUnits, "unit");
    renderTopologyViewButtons();
    wireTopologyControls();
    updateTopologyEditStatus();
}

function wireTopologyLayoutControls() {
    const editButton = document.getElementById("topology-layout-edit-toggle");
    const resetButton = document.getElementById("topology-layout-reset");
    const clearButton = document.getElementById("topology-selection-clear");
    const demoToggle = document.getElementById("topology-demo-toggle");
    const demoMenu = document.getElementById("topology-demo-menu");
    const fullscreenButton = document.getElementById("topology-fullscreen-toggle");

    if (editButton && editButton.dataset.bound !== "true") {
        editButton.dataset.bound = "true";
        editButton.addEventListener("click", () => {
            setTopologyEditMode(!topologyState.editMode);
            renderTopologyStage();
        });
    }

    if (resetButton && resetButton.dataset.bound !== "true") {
        resetButton.dataset.bound = "true";
        resetButton.addEventListener("click", () => {
            const root = document.getElementById("topology-root");
            const submapViewId = root?.getAttribute("data-map-view-id");
            if (submapViewId) {
                const confirmed = window.confirm("Reset submap layout? This only affects positions on this submap.");
                if (!confirmed) {
                    return;
                }
                const nextOverrides = { ...(topologyState.layoutOverrides || {}) };
                const dnSiteIdsToReset = [];
                for (const key of Object.keys(nextOverrides)) {
                    if (key.startsWith("dn-")) {
                        dnSiteIdsToReset.push(key.slice(3));
                        delete nextOverrides[key];
                    }
                }
                topologyState.layoutOverrides = nextOverrides;
                saveTopologyLayoutOverrides();
                // Clear DB positions for reset DNs
                dnSiteIdsToReset.forEach((siteId) => {
                    fetch(`/api/topology/maps/discovered-nodes/${encodeURIComponent(siteId)}/position`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ x: null, y: null }),
                    }).catch(() => {});
                });
            } else {
                const confirmed = window.confirm("Revert the current topology layout changes? This will reset saved bubble and widget positions.");
                if (!confirmed) {
                    return;
                }
                clearTopologyLayoutOverrides();
                topologyState.stateLogLayout = null;
                saveTopologyStateLogLayout();
            }
            renderTopologyStage();
        });
    }

    if (clearButton && clearButton.dataset.bound !== "true") {
        clearButton.dataset.bound = "true";
        clearButton.addEventListener("click", () => {
            clearTopologyEntitySelection();
            const layer = document.getElementById("topology-node-layer");
            if (layer) {
                syncTopologyEntitySelectionStyles(layer);
            } else {
                updateTopologyEditStatus();
            }
        });
    }

    if (demoToggle && demoToggle.dataset.bound !== "true") {
        demoToggle.dataset.bound = "true";
        demoToggle.addEventListener("click", (event) => {
            event.stopPropagation();
            if (!topologyState.editMode) {
                return;
            }
            topologyState.demoMenuOpen = !topologyState.demoMenuOpen;
            updateTopologyEditStatus();
        });
    }

    if (demoMenu && demoMenu.dataset.bound !== "true") {
        demoMenu.dataset.bound = "true";
        demoMenu.addEventListener("click", (event) => {
            const button = event.target instanceof Element ? event.target.closest("[data-topology-demo-mode]") : null;
            if (!(button instanceof HTMLElement)) {
                return;
            }
            event.stopPropagation();
            setTopologyDemoMode(button.getAttribute("data-topology-demo-mode") || "off");
            renderTopologyControls();
            renderTopologyStage();
        });
    }

    if (document.body && document.body.dataset.topologyDemoDismissBound !== "true") {
        document.body.dataset.topologyDemoDismissBound = "true";
        document.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }
            if (target.closest("#topology-demo-control")) {
                return;
            }
            if (topologyState.demoMenuOpen) {
                topologyState.demoMenuOpen = false;
                updateTopologyEditStatus();
            }
        });
    }

    const createSubmapButton = document.getElementById("topology-create-submap-button");
    if (createSubmapButton && createSubmapButton.dataset.bound !== "true") {
        createSubmapButton.dataset.bound = "true";
        createSubmapButton.addEventListener("click", async () => {
            const name = window.prompt("Enter a name for the new submap:");
            if (!name || !name.trim()) {
                return;
            }
            const trimmedName = name.trim();
            const slug = trimmedName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "submap";
            try {
                const response = await fetch("/api/topology/maps", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: trimmedName,
                        slug: slug,
                        map_type: "custom",
                    }),
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => null);
                    window.alert("Failed to create submap: " + (errorData?.detail || response.statusText));
                    return;
                }
                const created = await response.json();
                const viewId = created.id;
                const submapEntityId = `submap-${viewId}`;
                const stage = document.getElementById("topology-stage");
                const centerX = Math.round((stage?.clientWidth || 1200) / 2);
                const centerY = Math.round((stage?.clientHeight || 800) / 2);
                const submapEntity = {
                    id: submapEntityId,
                    map_view_id: viewId,
                    name: trimmedName,
                    slug: slug,
                    kind: "submap",
                    level: 0,
                    x: centerX,
                    y: centerY,
                    width: 160,
                    height: 96,
                };
                if (!topologyPayload.submaps) {
                    topologyPayload.submaps = [];
                }
                topologyPayload.submaps.push(submapEntity);
                setTopologyEntityLayout(submapEntityId, { x: centerX, y: centerY, size: 72 });
                renderTopologyStage();
            } catch (error) {
                console.error("Failed to create submap:", error);
                window.alert("Failed to create submap.");
            }
        });
    }

    const addNodeButton = document.getElementById("submap-add-node-button");
    const addNodePanel = document.getElementById("submap-add-node-panel");
    const addNodeClose = document.getElementById("submap-add-node-close");
    if (addNodeButton && addNodeButton.dataset.bound !== "true") {
        addNodeButton.dataset.bound = "true";
        addNodeButton.addEventListener("click", () => {
            if (addNodePanel) {
                addNodePanel.hidden = !addNodePanel.hidden;
                if (!addNodePanel.hidden) {
                    renderSubmapAddNodeList();
                }
            }
        });
    }
    if (addNodeClose && addNodeClose.dataset.bound !== "true") {
        addNodeClose.dataset.bound = "true";
        addNodeClose.addEventListener("click", () => {
            if (addNodePanel) {
                addNodePanel.hidden = true;
            }
        });
    }

    if (fullscreenButton && fullscreenButton.dataset.bound !== "true") {
        fullscreenButton.dataset.bound = "true";
        fullscreenButton.addEventListener("click", async () => {
            await toggleTopologyFullscreen();
        });
    }

    if (document.body && document.body.dataset.topologyFullscreenBound !== "true") {
        document.body.dataset.topologyFullscreenBound = "true";
        document.addEventListener("fullscreenchange", () => {
            syncTopologyFullscreenState();
            if (topologyPayload && document.getElementById("topology-root")) {
                renderTopologyStage();
            }
        });
    }

    if (document.body && document.body.dataset.topologyNudgeBound !== "true") {
        document.body.dataset.topologyNudgeBound = "true";
        document.addEventListener("keydown", (event) => {
            if (!topologyState.editMode || !topologyState.selectedEntityIds.size) {
                return;
            }
            const target = event.target;
            if (
                target instanceof HTMLElement &&
                (
                    target.isContentEditable ||
                    /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)
                )
            ) {
                return;
            }
            const step = event.shiftKey ? 10 : 2;
            switch (event.key) {
                case "ArrowLeft":
                    nudgeTopologySelection(-step, 0);
                    break;
                case "ArrowRight":
                    nudgeTopologySelection(step, 0);
                    break;
                case "ArrowUp":
                    nudgeTopologySelection(0, -step);
                    break;
                case "ArrowDown":
                    nudgeTopologySelection(0, step);
                    break;
                case "Escape":
                    clearTopologyEntitySelection();
                    const layer = document.getElementById("topology-node-layer");
                    if (layer) {
                        syncTopologyEntitySelectionStyles(layer);
                    } else {
                        updateTopologyEditStatus();
                    }
                    break;
                default:
                    return;
            }
            event.preventDefault();
        });
    }
}

async function loadTopologyPage() {
    const root = document.getElementById("topology-root");
    if (!root) {
        return;
    }

    const submapViewId = root.getAttribute("data-map-view-id") || null;

    startTopologyTimers();
    initTopologyLinkContextMenu();

    try {
        const requestedUnit = normalizeTopologyUnit(new URL(window.location.href).searchParams.get("unit"));
        const [topologyResult, discoveryResult, nodeDashboardResult, editorStateResult, dashboardServicesResult] = await Promise.allSettled([
            apiRequest(buildNodeDashboardRequestUrl("/api/topology")),
            apiRequest(buildNodeDashboardRequestUrl("/api/topology/discovery")),
            apiRequest(buildNodeDashboardRequestUrl("/api/node-dashboard")),
            apiRequest("/api/topology/editor-state"),
            apiRequest("/api/dashboard/services"),
        ]);
        if (topologyResult.status !== "fulfilled") {
            throw topologyResult.reason;
        }
        if (submapViewId) {
            const submapResult = await apiRequest(`/api/topology/maps/${encodeURIComponent(submapViewId)}`);
            topologySubmapDetail = submapResult;
            const submapEntities = (submapResult?.objects ?? [])
                .filter((obj) => obj.object_type === "node")
                .map(buildSubmapEntityFromMapObject);
            submapEntities.forEach((entity) => {
                if (!topologyState.layoutOverrides?.[entity.id]) {
                    setTopologyEntityLayout(entity.id, { x: entity.x, y: entity.y, size: 96 });
                }
            });
            topologyPayload = { lvl0_nodes: submapEntities, lvl1_nodes: [], lvl2_clusters: [], submaps: [], links: [] };
            await refreshSubmapDiscovery(submapViewId);
            renderSubmapAddNodeList();
        } else {
            topologyPayload = topologyResult.value;
        }
        topologyDiscoveryPayload = discoveryResult.status === "fulfilled"
            ? discoveryResult.value
            : { anchors: [], discovered: [], relationships: [], summary: {} };
        topologyNodeDashboardPayload = nodeDashboardResult.status === "fulfilled"
            ? nodeDashboardResult.value
            : { anchors: [], discovered: [] };
        topologyDashboardServicesPayload = dashboardServicesResult.status === "fulfilled"
            ? dashboardServicesResult.value
            : { summary: {}, services: [] };
        topologyState.activeLocations = new Set(TOPOLOGY_LOCATIONS);
        setTopologyUnitFocus(requestedUnit);
        if (!topologyState.focusUnit) {
            topologyState.activeUnits = new Set(TOPOLOGY_UNITS);
        }
        const hasLocalEditorState = hasLocalTopologyEditorState();
        topologyState.layoutOverrides = getSavedTopologyLayoutOverrides();
        topologyState.stateLogLayout = getSavedTopologyStateLogLayout();
        topologyState.linkAnchorAssignments = getSavedTopologyLinkAnchorAssignments();
        if (editorStateResult.status === "fulfilled" && editorStateResult.value?.exists) {
            topologyState.layoutOverrides = editorStateResult.value.layout_overrides || {};
            topologyState.stateLogLayout = editorStateResult.value.state_log_layout || null;
            topologyState.linkAnchorAssignments = editorStateResult.value.link_anchor_assignments || {};
            topologyState.demoMode = editorStateResult.value.demo_mode || "off";
            saveTopologyLayoutOverrides();
            saveTopologyStateLogLayout();
            saveTopologyLinkAnchorAssignments();
            topologyState.demoSnapshot = buildTopologyDemoSnapshot(topologyState.demoMode);
        }
        topologyState.view = "backbone+l2";
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        topologyEditorStateLoaded = true;
        if (editorStateResult.status === "fulfilled" && !editorStateResult.value?.exists && hasLocalEditorState) {
            queueTopologyEditorStateSave();
        }
        renderTopologyControls();
        wireTopologyLayoutControls();
        setTopologyEditMode(getSavedTopologyEditMode());
        syncTopologyFullscreenState();
        renderTopologyStage();
    } catch (error) {
        const drawer = document.getElementById("topology-details-drawer");
        const layer = document.getElementById("topology-node-layer");
        if (layer) {
            layer.innerHTML = "";
        }
        if (drawer) {
            drawer.innerHTML = `<p class="status-error">${escapeHtml(error.message || "Unable to load topology")}</p>`;
        }
    }

    if (!topologyResizeListenerBound) {
        window.addEventListener("resize", () => {
            if (topologyPayload && document.getElementById("topology-root")) {
                renderTopologyStage();
            }
        });
        topologyResizeListenerBound = true;
    }

    if (!topologyRouteListenerBound) {
        window.addEventListener("popstate", () => {
            const requestedUnit = normalizeTopologyUnit(new URL(window.location.href).searchParams.get("unit"));
            setTopologyUnitFocus(requestedUnit);
            if (!topologyState.focusUnit) {
                topologyState.activeUnits = new Set(TOPOLOGY_UNITS);
            }
            renderTopologyControls();
            renderTopologyStage();
        });
        topologyRouteListenerBound = true;
    }
}

async function loadNodeDetailPage() {
    const root = document.getElementById("node-detail-root");
    if (!root) {
        return;
    }

    const detailEndpoint = root.getAttribute("data-detail-endpoint");
    const nodeId = root.getAttribute("data-node-id");
    if (!detailEndpoint) {
        return;
    }

    try {
        const detail = await apiRequest(buildNodeDashboardRequestUrl(detailEndpoint));
        const node = detail.node ?? {};
        const summary = detail.node_summary ?? {};
        const config = detail.config_summary ?? {};
        const errors = detail.errors ?? {};

        document.getElementById("detail-node-name").textContent = node.name ?? `Node ${nodeId ?? "--"}`;
        const detailLocation = document.getElementById("detail-location");
        const detailLastRefresh = document.getElementById("detail-last-refresh");
        const detailLastTelemetry = document.getElementById("detail-last-telemetry");

        if (detailLocation) {
            detailLocation.textContent = node.location ?? "--";
        }
        if (detailLastRefresh) {
            detailLastRefresh.textContent = formatDashboardTimestamp(node.last_refresh);
        }
        if (detailLastTelemetry) {
            detailLastTelemetry.textContent = formatDashboardTimestamp(node.last_telemetry_pull);
        }

        renderDetailHeaderActions("detail-header-actions", summary, node);
        renderNodeSummaryPanel("detail-summary-grid", summary, node);
        wireNodeSummaryActions("detail-header-actions");

        renderDetailSummaryGrid("detail-config-grid", [
            ["Site ID", config.site_id ?? "--"],
            ["Site Name", config.site_name ?? "--"],
            ["Mgmt IP", config.mgmt_ip ?? "--"],
            ["Version", config.version ?? "--"],
            ["License Expiration", config.license_expires ?? "--"],
            ["Mate Count", config.n_mates ?? 0],
            ["Enclave", config.enclave_id ?? "--"],
            ["Platform", config.platform ?? "--"],
            ["Topology Unit", node.topology_unit ?? "--"],
        ]);

        renderDetailTableBody(
            "detail-tunnels-body",
            [...(detail.tunnels ?? [])].sort((left, right) => {
                const leftPingUp = String(left?.ping ?? "").trim().toLowerCase() === "up";
                const rightPingUp = String(right?.ping ?? "").trim().toLowerCase() === "up";
                const leftIndex = Number(left?.mate_index);
                const rightIndex = Number(right?.mate_index);
                const leftPinned = leftPingUp && leftIndex === 0;
                const rightPinned = rightPingUp && rightIndex === 0;

                if (leftPinned !== rightPinned) {
                    return leftPinned ? -1 : 1;
                }
                if (leftPingUp !== rightPingUp) {
                    return leftPingUp ? -1 : 1;
                }
                return (Number.isFinite(leftIndex) ? leftIndex : 999999) - (Number.isFinite(rightIndex) ? rightIndex : 999999);
            }),
            ["mate_index", "site_name", "mate_site_id", "mate_ip", "tunnel_health", "tx_rate", "rx_rate", "rtt_ms", "ping"],
            "No tunnel data available.",
        );
        wireSitesFilter();
        wireDetailLinks();
        renderDetailTableBody(
            "detail-channels-body",
            detail.channels ?? [],
            ["channel", "wan_up", "wan_delay_ms", "public_ip", "tx_rate", "rx_rate", "link_state"],
            "No channel data available.",
        );
        renderDetailTableBody(
            "detail-static-routes-body",
            detail.static_routes ?? [],
            ["prefix", "name", "next_hop", "site_id", "type", "metric"],
            "No static routes available.",
        );
        renderDetailTableBody(
            "detail-learnt-routes-body",
            detail.learnt_routes ?? [],
            ["prefix", "name", "next_hop", "site_id", "type", "metric"],
            "No dynamic routes available.",
        );
        wireRouteFilter(
            "detail-static-routes-filter",
            "detail-static-routes-body",
            ["prefix", "name", "next_hop", "site_id", "type", "metric"],
            "No static routes available.",
        );
        wireRouteFilter(
            "detail-dynamic-routes-filter",
            "detail-learnt-routes-body",
            ["prefix", "name", "next_hop", "site_id", "type", "metric"],
            "No dynamic routes available.",
        );

        setSectionError("detail-config-error", errors.config);
        setSectionError("detail-stats-error", errors.stats);
        setSectionError("detail-routes-error", errors.routes);
        document.getElementById("detail-raw-data").textContent = JSON.stringify(detail.raw ?? {}, null, 2);
        document.getElementById("detail-page-error").hidden = true;
    } catch (error) {
        const pageError = document.getElementById("detail-page-error");
        if (pageError) {
            pageError.textContent = error.message || "Unable to load anchor node detail.";
            pageError.hidden = false;
        }
    }
}

async function apiRequest(url, options = {}) {
    const response = await fetch(url, {
        headers: {
            "Content-Type": "application/json",
            ...(options.headers ?? {}),
        },
        ...options,
    });

    if (!response.ok) {
        let detail = "Request failed";

        try {
            const errorData = await response.json();
            detail = errorData.detail ?? detail;
        } catch (error) {
            // Ignore JSON parsing errors and keep the fallback message.
        }

        throw new Error(detail);
    }

    if (response.status === 204) {
        return null;
    }

    return response.json();
}

async function loadNodes() {
    const tableBody = document.getElementById("nodes-table-body");
    const nodesError = document.getElementById("nodes-error");

    try {
        currentNodes = sortNodesForDisplay(await apiRequest("/api/nodes"));
        if (tableBody) {
            renderNodesTable(currentNodes);
        }
        if (nodesError) {
            nodesError.hidden = true;
        }
        clearFeedback();

        const selectedNodeId = Number(new URLSearchParams(window.location.search).get("node"));
        if (selectedNodeId && tableBody) {
            populateNodeForm(selectedNodeId);
        }
    } catch (error) {
        if (tableBody) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="8" class="table-message">Unable to load node inventory</td>
                </tr>
            `;
        }
        if (nodesError) {
            nodesError.hidden = false;
        }
    }
}

async function loadNodeDashboard() {
    const anchorList = document.getElementById("anchor-node-list");
    const discoveredList = document.getElementById("discovered-node-list");
    const dashboardError = document.getElementById("dashboard-error");

    if (anchorList && discoveredList && dashboardError) {
        try {
            const payload = await apiRequest(buildNodeDashboardRequestUrl("/api/node-dashboard"));
            currentNodeDashboardPayload = payload;
            renderNodeDashboardLists(payload);
            dashboardError.hidden = true;
            disconnectNodeDashboardStream();
            return;
        } catch (error) {
            anchorList.innerHTML = `<div class="table-message">Unable to load anchor nodes.</div>`;
            discoveredList.innerHTML = `<div class="table-message">Unable to load discovered nodes.</div>`;
            dashboardError.textContent = error.message || "Unable to load node dashboard data";
            dashboardError.hidden = false;
            return;
        }
    }

    const grid = document.getElementById("nodeGrid");
    if (!grid || !dashboardError) {
        return;
    }

    try {
        const nodes = await apiRequest("/api/dashboard/nodes");
        renderDashboard(nodes);
        dashboardError.hidden = true;
        return;
    } catch (error) {
        grid.innerHTML = `
            <article class="node-card">
                <div class="node-header">
                    <div class="node-name">Dashboard unavailable</div>
                </div>
                <div class="node-sub">Unable to load dashboard nodes</div>
            </article>
        `;
        dashboardError.textContent = error.message || "Unable to load dashboard nodes";
        dashboardError.hidden = false;
    }
}

function normalizeNodeDashboardSearch(value) {
    return String(value ?? "").trim().toLowerCase();
}

function nodeListMatches(row, query) {
    if (!query) {
        return true;
    }
    const haystacks = [
        row.site_name,
        row.site_id,
        row.unit,
        row.version,
        row.host,
        row.discovered_parent_name,
        Array.isArray(row.surfaced_by_names) ? row.surfaced_by_names.join(" ") : "",
    ]
        .map((item) => String(item ?? "").toLowerCase());
    return haystacks.some((value) => value.includes(query));
}

function getNodeListRttState(latencyValue, row) {
    if (row && row.rtt_state) {
        return String(row.rtt_state);
    }
    if (typeof latencyValue === "number") {
        if (latencyValue >= 180) {
            return "down";
        }
        if (latencyValue >= 100) {
            return "warn";
        }
        return "good";
    }
    return row.ping_state || (String(row.ping || "").toLowerCase() === "up" ? "good" : "down");
}

async function refreshTopologyPage() {
    const root = document.getElementById("topology-root");
    if (!root) {
        return;
    }
    // Skip DOM rebuild while user is dragging to avoid jitter
    if (topologyState.dragging) {
        return;
    }
    const submapId = root.getAttribute("data-map-view-id");
    if (submapId) {
        const [, nodeDashResult] = await Promise.allSettled([
            refreshSubmapDiscovery(submapId),
            apiRequest(buildNodeDashboardRequestUrl("/api/node-dashboard")),
        ]);
        if (nodeDashResult.status === "fulfilled") {
            topologyNodeDashboardPayload = nodeDashResult.value;
        }
        if (topologyPayload) {
            renderTopologyStage();
        }
        return;
    }

    try {
        const [topologyResult, discoveryResult, nodeDashboardResult, dashboardServicesResult] = await Promise.allSettled([
            apiRequest(buildNodeDashboardRequestUrl("/api/topology")),
            apiRequest(buildNodeDashboardRequestUrl("/api/topology/discovery")),
            apiRequest(buildNodeDashboardRequestUrl("/api/node-dashboard")),
            apiRequest("/api/dashboard/services"),
        ]);

        if (topologyResult.status === "fulfilled") {
            topologyPayload = topologyResult.value;
        }
        if (discoveryResult.status === "fulfilled") {
            topologyDiscoveryPayload = discoveryResult.value;
        }
        if (nodeDashboardResult.status === "fulfilled") {
            topologyNodeDashboardPayload = nodeDashboardResult.value;
        }
        if (dashboardServicesResult.status === "fulfilled") {
            topologyDashboardServicesPayload = dashboardServicesResult.value;
        }

        if (topologyState.demoMode !== "off") {
            topologyState.demoSnapshot = buildTopologyDemoSnapshot(topologyState.demoMode);
        }

        if (topologyPayload) {
            renderTopologyControls();
            renderTopologyStage();

            // Fetch accurate DN counts from discovery endpoints for each submap
            const submaps = topologyPayload.submaps ?? [];
            if (submaps.length > 0) {
                const countPromises = submaps.map(async (sm) => {
                    try {
                        const disc = await apiRequest(`/api/topology/maps/${encodeURIComponent(sm.map_view_id)}/discovery`);
                        const peers = disc?.discovered_peers ?? [];
                        const placedSiteIds = new Set(
                            (topologyPayload.lvl0_nodes ?? []).map((e) => e.site_id).filter(Boolean)
                        );
                        const upNames = [];
                        const downNames = [];
                        for (const p of peers) {
                            if (placedSiteIds.has(p.site_id)) continue;
                            if ((p.ping || "").toLowerCase() === "up") {
                                upNames.push(p.site_id);
                            } else {
                                downNames.push(p.site_id);
                            }
                        }
                        sm.dn_up = upNames.length;
                        sm.dn_down = downNames.length;
                        sm.dn_up_names = upNames;
                        sm.dn_down_names = downNames;
                        _submapDnCountCache.set(sm.map_view_id, {
                            dn_up: sm.dn_up, dn_down: sm.dn_down,
                            dn_up_names: upNames, dn_down_names: downNames,
                        });
                    } catch (_e) { /* keep backend counts as fallback */ }
                });
                await Promise.allSettled(countPromises);
                renderTopologyStage();
            }
        }
        markTopologyLastUpdated();
    } catch (error) {
        console.error("Unable to refresh topology", error);
    }
}

async function refreshTopologyPingStatus() {
    if (!document.getElementById("topology-root")) return;
    try {
        const statuses = await apiRequest("/api/nodes/ping-status");
        if (!Array.isArray(statuses)) return;
        const byId = {};
        for (const s of statuses) byId[s.id] = s;

        // Update ping data in ALL cached payloads so renderTopologyStage picks it up
        let changed = false;
        const allLists = [
            topologyPayload?.lvl0_nodes,
            topologyPayload?.lvl1_nodes,
        ];
        // Also update the node-dashboard anchors (these take priority in mergeDashboardAnchorState)
        if (Array.isArray(topologyNodeDashboardPayload?.anchors)) {
            allLists.push(topologyNodeDashboardPayload.anchors);
        }
        for (const list of allLists) {
            if (!Array.isArray(list)) continue;
            for (const node of list) {
                const nodeId = node.inventory_node_id ?? node.id;
                const s = byId[nodeId];
                if (!s) continue;
                if (node.latency_ms !== s.latency_ms || node.ping_state !== s.ping_state) {
                    changed = true;
                }
                node.ping_state = s.ping_state;
                node.latency_ms = s.latency_ms;
                node.avg_latency_ms = s.avg_latency_ms;
            }
        }

        // Detect state changes and update the log
        detectNodeStateChanges();
        detectLinkStateChanges();

        // Re-render the stage so tooltip markup reflects fresh RTT
        if (changed && topologyPayload) {
            renderTopologyStage();
        }
    } catch (err) {
        // non-fatal — next poll will catch up
    }
}

function updateTopologyPingChips(byId) {
    const stage = document.getElementById("topology-stage");
    if (!stage) return;
    stage.querySelectorAll("[data-topology-id]").forEach(el => {
        const entityId = el.dataset.topologyId;
        if (!entityId?.startsWith("node-")) return;
        const nodeId = parseInt(entityId.replace("node-", ""), 10);
        const s = byId[nodeId];
        if (!s) return;
        const chip = el.querySelector(".topology-rtt-chip");
        if (chip) {
            const state = s.ping_state || "unknown";
            chip.className = `topology-rtt-chip rtt-${state}`;
            chip.textContent = s.latency_ms != null ? `${s.latency_ms} ms` : "--";
        }
        // Update tooltip RTT value in-place
        const tooltipRtt = el.querySelector("[data-tooltip-rtt]");
        if (tooltipRtt) {
            tooltipRtt.textContent = s.latency_ms != null ? `${s.latency_ms} ms` : "--";
        }
    });
}

function markTopologyLastUpdated() {
    topologyLastUpdatedAt = Date.now();
    const el = document.getElementById("topology-last-updated");
    if (el) el.hidden = false;
    updateTopologyLastUpdatedAge();
}

function updateTopologyLastUpdatedAge() {
    const ageEl = document.getElementById("topology-last-updated-age");
    if (!ageEl || topologyLastUpdatedAt === null) return;
    const sec = Math.round((Date.now() - topologyLastUpdatedAt) / 1000);
    if (sec < 60) {
        ageEl.textContent = `${sec}s ago`;
    } else {
        const min = Math.floor(sec / 60);
        ageEl.textContent = `${min}m ago`;
    }
}

function startTopologyTimers() {
    // Seeker data — user-selected interval (handled by applyDashboardRefreshInterval)
    applyDashboardRefreshInterval();

    // Ping RTT — poll every 2s (lightweight in-memory endpoint)
    if (topologyPingTimer) clearInterval(topologyPingTimer);
    topologyPingTimer = setInterval(refreshTopologyPingStatus, 2000);

    // "Updated X ago" counter — ticks every second
    if (topologyLastUpdatedTimer) clearInterval(topologyLastUpdatedTimer);
    topologyLastUpdatedTimer = setInterval(updateTopologyLastUpdatedAge, 1000);
}


function renderNodeDashboardRow(row, pinnedKeys) {
    const pinKey = row.pin_key || `anchor:${row.id}`;
    const isPinned = pinnedKeys.has(pinKey);
    const isAnchor = row.row_type === "anchor";
    const discoveredLevel = Number(row.discovered_level || 0);
    const indentClass = discoveredLevel >= 3 ? " node-list-row-child" : "";
    const parentLine = isAnchor ? "" : row.discovered_parent_name && !Array.isArray(row.surfaced_by_names)
        ? `<div class="node-list-parent">via ${escapeHtml(row.discovered_parent_name)}</div>`
        : "";
    const webScheme = row.web_scheme || "https";
    const webPort = Number(row.web_port || 443);
    const sshUser = row.ssh_username || "";
    const detailSummary = row.detail && typeof row.detail === "object" && row.detail.summary && typeof row.detail.summary === "object"
        ? row.detail.summary
        : null;
    const rawLatency = row.latency_ms
        ?? row.rtt_ms
        ?? row.rtt
        ?? row.mate_ping_rtt
        ?? row.ping_rtt
        ?? detailSummary?.latency_ms
        ?? detailSummary?.rtt_ms
        ?? detailSummary?.rtt;
    const latencyMatch = typeof rawLatency === "string"
        ? rawLatency.match(/-?\d+(?:\.\d+)?/)
        : null;
    const numericLatency = typeof rawLatency === "number"
        ? rawLatency
        : latencyMatch
            ? Number(latencyMatch[0])
            : Number(rawLatency);
    const latencyValue = Number.isFinite(numericLatency)
        ? (isAnchor ? Math.round(numericLatency) : Math.round(numericLatency))
        : null;
    const latencyText = latencyValue == null || rawLatency === "--" ? "--" : `${latencyValue} ms`;
    const rttState = getNodeListRttState(latencyValue, row);
    const latestLatencyText = typeof row.latest_latency_ms === "number" ? `${row.latest_latency_ms} ms` : "--";
    const baselineLatencyText = typeof row.rtt_baseline_ms === "number" ? `${row.rtt_baseline_ms} ms` : "--";
    const deviationText = typeof row.rtt_deviation_pct === "number"
        ? `${row.rtt_deviation_pct > 0 ? "+" : ""}${row.rtt_deviation_pct.toFixed(1)}%`
        : "--";
    const refreshWindowLabel = formatRefreshLabel(Number(row.refresh_window_seconds || getDashboardRefreshSeconds()));
    const txDisplay = row.tx_display || formatRate(row.tx_bps || row.tx_rate || 0);
    const rxDisplay = row.rx_display || formatRate(row.rx_bps || row.rx_rate || 0);
    const wanTxDisplay = isAnchor ? formatRate(row.wan_tx_bps || 0) : null;
    const wanRxDisplay = isAnchor ? formatRate(row.wan_rx_bps || 0) : null;
    const lanTxDisplay = isAnchor ? formatRate(row.lan_tx_bps || 0) : null;
    const lanRxDisplay = isAnchor ? formatRate(row.lan_rx_bps || 0) : null;
    const wanTxTotal = isAnchor ? (row.wan_tx_total || "--") : null;
    const wanRxTotal = isAnchor ? (row.wan_rx_total || "--") : null;
    const lanTxTotal = isAnchor ? (row.lan_tx_total || "--") : null;
    const lanRxTotal = isAnchor ? (row.lan_rx_total || "--") : null;
    const unitMarkup = !isAnchor
        ? `<div class="node-list-meta"><span class="node-list-meta-label">Unit</span><strong>${escapeHtml(row.unit || "--")}</strong></div>`
        : "";
    return `
        <article class="node-list-row${indentClass}" ${isAnchor ? `data-node-pin-key="${escapeHtml(pinKey)}" draggable="true"` : ""}>
            <div class="node-list-main node-list-main-inline">
                <div class="node-list-primary-cell">
                    <button type="button" class="node-list-name-button" data-node-detail-url="${escapeHtml(row.detail_url || "")}">
                        ${escapeHtml(row.site_name || row.name || row.site_id || "Unknown")}
                    </button>
                    ${parentLine}
                </div>
                <div class="node-list-meta-grid node-list-meta-grid-inline ${!isAnchor ? "node-list-meta-grid-inline-discovered" : ""}">
                    <div class="node-list-meta"><span class="node-list-meta-label">Site ID</span><strong>${escapeHtml(row.site_id || "--")}</strong></div>
                    <div class="node-list-meta"><span class="node-list-meta-label">Site IP</span><strong>${escapeHtml(row.host || "--")}</strong></div>
                    ${unitMarkup}
                    <div class="node-list-meta"><span class="node-list-meta-label">Version</span><strong>${escapeHtml(row.version || "--")}</strong></div>
                    <div class="node-list-meta">
                        <span class="node-list-meta-label">Avg RTT</span>
                        <span class="metric-chip metric-chip-rtt node-list-rtt-chip ping-${escapeHtml(String(rttState))}" title="${escapeHtml(`Window ${refreshWindowLabel} · latest ${latestLatencyText} · baseline ${baselineLatencyText} · deviation ${deviationText}`)}">
                            <span class="metric-chip-label">AVG</span>
                            <span class="metric-chip-value">${escapeHtml(latencyText)}</span>
                        </span>
                    </div>
                    ${isAnchor ? `
                    <div class="node-list-meta node-list-meta-traffic">
                        <span class="node-list-meta-label">WAN</span>
                        <strong>↑${escapeHtml(wanTxDisplay)} / ↓${escapeHtml(wanRxDisplay)}</strong>
                    </div>
                    <div class="node-list-meta node-list-meta-traffic">
                        <span class="node-list-meta-label">LAN</span>
                        <strong>↑${escapeHtml(lanTxDisplay)} / ↓${escapeHtml(lanRxDisplay)}</strong>
                    </div>
                    <div class="node-list-meta node-list-meta-totals">
                        <span class="node-list-meta-label">WAN Total</span>
                        <strong>↑${escapeHtml(wanTxTotal)} / ↓${escapeHtml(wanRxTotal)}</strong>
                    </div>
                    <div class="node-list-meta node-list-meta-totals">
                        <span class="node-list-meta-label">LAN Total</span>
                        <strong>↑${escapeHtml(lanTxTotal)} / ↓${escapeHtml(lanRxTotal)}</strong>
                    </div>
                    ` : `<div class="node-list-meta node-list-meta-traffic"><span class="node-list-meta-label">Tx / Rx</span><strong>${escapeHtml(txDisplay)} / ${escapeHtml(rxDisplay)}</strong></div>`}
                </div>
            </div>
            <div class="node-list-actions">
                <button
                    type="button"
                    class="dashboard-pin-button ${isPinned ? "pinned" : ""}"
                    data-node-list-action="toggle-pin"
                    data-node-pin-key="${escapeHtml(pinKey)}"
                    title="${isPinned ? "Remove from main dashboard" : "Add to main dashboard"}"
                >★</button>
                <button
                    type="button"
                    class="service-link ${row.web_ok ? "ok" : "down"}"
                    data-node-list-action="open-web"
                    data-host="${escapeHtml(row.host || "")}"
                    data-web-port="${Number.isFinite(webPort) ? webPort : 443}"
                    data-web-scheme="${escapeHtml(webScheme)}"
                ><span class="service-icon">🌐</span><span>Web</span></button>
                <button
                    type="button"
                    class="service-link ${row.ssh_ok ? "ok" : "down"}"
                    data-node-list-action="ssh"
                    data-host="${escapeHtml(row.host || "")}"
                    data-ssh-username="${escapeHtml(sshUser)}"
                ><span class="service-icon">🔐</span><span>SSH</span></button>
                ${isAnchor ? `
                    <button
                        type="button"
                        class="service-link"
                        data-node-list-action="edit-anchor"
                        data-node-id="${escapeHtml(String(row.id || ""))}"
                        aria-label="Edit node"
                        title="Edit node"
                    ><span class="service-icon">✎</span></button>
                    <button
                        type="button"
                        class="service-link down"
                        data-node-list-action="delete-anchor"
                        data-node-id="${escapeHtml(String(row.id || ""))}"
                        data-node-name="${escapeHtml(row.site_name || row.name || row.site_id || "node")}"
                        aria-label="Delete node"
                        title="Delete node"
                    ><span class="service-icon">🗑</span></button>
                ` : `
                    <button
                        type="button"
                        class="service-link down"
                        data-node-list-action="delete-discovered"
                        data-site-id="${escapeHtml(String(row.site_id || ""))}"
                        data-node-name="${escapeHtml(row.site_name || row.name || row.site_id || "node")}"
                        aria-label="Delete discovered node"
                        title="Delete discovered node"
                    ><span class="service-icon">🗑</span></button>
                `}
            </div>
        </article>
    `;
}

function syncNodeDashboardListMarkup(container, markup) {
    if (container.innerHTML !== markup) {
        container.innerHTML = markup;
        return true;
    }

    return false;
}

async function handleNodeDashboardListAction(button) {
    const action = button.getAttribute("data-node-list-action");
    if (action === "toggle-pin") {
        togglePinnedNodeKey(button.getAttribute("data-node-pin-key") || "");
        renderNodeDashboardLists(currentNodeDashboardPayload);
        return;
    }
    if (action === "open-web") {
        openWebForNode(
            button.getAttribute("data-host") || "",
            Number(button.getAttribute("data-web-port")),
            button.getAttribute("data-web-scheme") || "https",
        );
        return;
    }
    if (action === "ssh") {
        await copySshCommand(
            button.getAttribute("data-host") || "",
            button.getAttribute("data-ssh-username") || "",
        ).catch(() => {});
        return;
    }
    if (action === "edit-anchor") {
        const nodeId = Number(button.getAttribute("data-node-id"));
        if (Number.isFinite(nodeId)) {
            // Close the inventory modal first so the node form isn't hidden behind it
            const inventoryShell = document.getElementById("topology-inventory-shell");
            if (inventoryShell) {
                inventoryShell.hidden = true;
            }
            if (!currentNodes.length) {
                await loadNodes();
            }
            populateNodeForm(nodeId);
        }
        return;
    }
    if (action === "delete-anchor") {
        const nodeId = Number(button.getAttribute("data-node-id"));
        const nodeName = button.getAttribute("data-node-name") || "this node";
        if (!Number.isFinite(nodeId)) {
            return;
        }
        const confirmed = window.confirm(`Delete ${nodeName}?`);
        if (!confirmed) {
            return;
        }
        try {
            await apiRequest(`/api/nodes/${nodeId}`, { method: "DELETE" });
            await loadNodes();
            await loadNodeDashboard();
            await loadMainDashboard();
            showDashboardFeedback("Node deleted");
        } catch (error) {
            const dashboardError = document.getElementById("dashboard-error");
            if (dashboardError) {
                dashboardError.textContent = error.message || "Unable to delete node";
                dashboardError.hidden = false;
            }
        }
        return;
    }
    if (action === "delete-discovered") {
        const siteId = String(button.getAttribute("data-site-id") || "").trim();
        const nodeName = button.getAttribute("data-node-name") || "this discovered node";
        if (!siteId) {
            return;
        }
        const confirmed = window.confirm(`Delete ${nodeName}?`);
        if (!confirmed) {
            return;
        }
        try {
            await apiRequest(`/api/discovered-nodes/${encodeURIComponent(siteId)}`, { method: "DELETE" });
            currentNodeDashboardPayload = {
                anchors: Array.isArray(currentNodeDashboardPayload?.anchors) ? currentNodeDashboardPayload.anchors : [],
                discovered: Array.isArray(currentNodeDashboardPayload?.discovered)
                    ? currentNodeDashboardPayload.discovered.filter((row) => String(row?.site_id || "").trim() !== siteId)
                    : [],
            };
            renderNodeDashboardLists(currentNodeDashboardPayload);
            showDashboardFeedback("Discovered node deleted");
            loadMainDashboard().catch((refreshError) => {
                console.warn("Unable to refresh main dashboard after discovered-node delete", refreshError);
            });
        } catch (error) {
            const dashboardError = document.getElementById("dashboard-error");
            if (dashboardError) {
                dashboardError.textContent = error.message || "Unable to delete discovered node";
                dashboardError.hidden = false;
            }
        }
    }
}

function bindNodeDashboardListInteractions(listElement) {
    if (!listElement || listElement._nodeDashboardBound) {
        return;
    }

    listElement.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }

        const actionButton = target.closest("[data-node-list-action]");
        if (actionButton instanceof HTMLElement) {
            event.preventDefault();
            event.stopPropagation();
            void handleNodeDashboardListAction(actionButton);
            return;
        }

        const detailButton = target.closest("[data-node-detail-url]");
        if (detailButton instanceof HTMLElement) {
            event.preventDefault();
            event.stopPropagation();
            openNodeDetail(detailButton.getAttribute("data-node-detail-url"));
        }
    });

    listElement._nodeDashboardBound = true;
}

function renderNodeDashboardLists(payload) {
    const anchorList = document.getElementById("anchor-node-list");
    const discoveredList = document.getElementById("discovered-node-list");
    const anchorCount = document.getElementById("anchor-node-count");
    const discoveredCount = document.getElementById("discovered-node-count");
    const lastUpdated = document.getElementById("dashboard-last-updated");
    const anchorSearch = document.getElementById("anchor-node-search");
    const discoveredSearch = document.getElementById("discovered-node-search");
    if (!anchorList || !discoveredList) {
        return;
    }

    const anchors = Array.isArray(payload?.anchors) ? payload.anchors : [];
    const discovered = Array.isArray(payload?.discovered) ? payload.discovered : [];
    const pinnedKeys = new Set(getPinnedNodeKeys());
    const anchorQuery = normalizeNodeDashboardSearch(anchorSearch?.value);
    const discoveredQuery = normalizeNodeDashboardSearch(discoveredSearch?.value);
    const filteredAnchors = anchors.filter((row) => nodeListMatches(row, anchorQuery));
    const filteredDiscovered = discovered.filter((row) => nodeListMatches(row, discoveredQuery));

    const anchorMarkup = filteredAnchors.length
        ? sortAnchorListRows(filteredAnchors).map((row) => renderNodeDashboardRow(row, pinnedKeys)).join("")
        : `<div class="table-message">No anchor nodes matched.</div>`;
    const discoveredMarkup = filteredDiscovered.length
        ? filteredDiscovered.map((row) => renderNodeDashboardRow(row, pinnedKeys)).join("")
        : `<div class="table-message">No discovered nodes matched.</div>`;

    const anchorMarkupChanged = syncNodeDashboardListMarkup(anchorList, anchorMarkup);
    syncNodeDashboardListMarkup(discoveredList, discoveredMarkup);

    if (anchorCount) {
        anchorCount.textContent = String(anchors.length);
    }
    if (discoveredCount) {
        discoveredCount.textContent = String(discovered.length);
    }
    if (lastUpdated) {
        lastUpdated.textContent = new Date().toLocaleTimeString();
    }

    bindNodeDashboardListInteractions(anchorList);
    bindNodeDashboardListInteractions(discoveredList);

    if (anchorMarkupChanged) {
        anchorList.querySelectorAll(".node-list-row[data-node-pin-key]").forEach((row) => {
            if (!(row instanceof HTMLElement)) {
                return;
            }
            attachAnchorRowDragAndDrop(row);
        });
    }

    if (anchorSearch && !anchorSearch._nodeDashboardSearchBound) {
        anchorSearch.addEventListener("input", () => renderNodeDashboardLists(currentNodeDashboardPayload));
        anchorSearch._nodeDashboardSearchBound = true;
    }
    if (discoveredSearch && !discoveredSearch._nodeDashboardSearchBound) {
        discoveredSearch.addEventListener("input", () => renderNodeDashboardLists(currentNodeDashboardPayload));
        discoveredSearch._nodeDashboardSearchBound = true;
    }

    const flushButton = document.getElementById("flush-discovery-button");
    if (flushButton instanceof HTMLButtonElement && !flushButton._nodeDashboardFlushBound) {
        flushButton.addEventListener("click", async () => {
            const currentRows = Array.isArray(currentNodeDashboardPayload?.discovered) ? currentNodeDashboardPayload.discovered : [];
            const currentCount = currentRows.length;
            const confirmed = window.confirm(
                `Flush discovery and rebuild from current AN telemetry? This will clear ${currentCount} discovered node${currentCount === 1 ? "" : "s"} and force rediscovery.`,
            );
            if (!confirmed) {
                return;
            }
            try {
                const result = await apiRequest(buildNodeDashboardRequestUrl("/api/discovered-nodes/flush-discovery"), {
                    method: "POST",
                });
                const rediscoveredRows = await apiRequest(buildNodeDashboardRequestUrl("/api/node-dashboard"));
                currentNodeDashboardPayload = {
                    anchors: Array.isArray(rediscoveredRows?.anchors) ? rediscoveredRows.anchors : [],
                    discovered: Array.isArray(rediscoveredRows?.discovered) ? rediscoveredRows.discovered : [],
                };
                renderNodeDashboardLists(currentNodeDashboardPayload);
                const rediscoveredCount = Number(result?.rediscovered_count || currentNodeDashboardPayload.discovered.length || 0);
                showDashboardFeedback(`Discovery flushed. ${rediscoveredCount} discovered node${rediscoveredCount === 1 ? "" : "s"} rebuilt from AN data.`);
                loadMainDashboard().catch((refreshError) => {
                    console.warn("Unable to refresh main dashboard after discovery flush", refreshError);
                });
            } catch (error) {
                const dashboardError = document.getElementById("dashboard-error");
                if (dashboardError) {
                    dashboardError.textContent = error.message || "Unable to flush discovery";
                    dashboardError.hidden = false;
                }
            }
        });
        flushButton._nodeDashboardFlushBound = true;
    }
}

async function loadServicesDashboard() {
    const body = document.getElementById("dashboardServicesBody");
    const error = document.getElementById("dashboard-services-error");

    if (!body || !error || document.getElementById("service-form")) {
        return;
    }

    try {
        const payload = await apiRequest("/api/dashboard/services");
        renderDashboardServices(payload, { showPin: true });
        error.hidden = true;
    } catch (loadError) {
        body.innerHTML = `
            <tr>
                <td colspan="8" class="table-message">Unable to load dashboard services.</td>
            </tr>
        `;
        error.textContent = loadError.message || "Unable to load dashboard services";
        error.hidden = false;
    }
}

async function loadMainDashboard() {
    const mainNodeGrid = document.getElementById("mainNodeGrid");
    const mainServicesBody = document.getElementById("mainDashboardServicesBody");

    if (!mainNodeGrid || !mainServicesBody) {
        return;
    }

    const pinnedNodeKeys = getPinnedNodeKeys();
    const watchlistParams = new URLSearchParams();
    pinnedNodeKeys.forEach((pinKey) => {
        watchlistParams.append("pin_key", pinKey);
    });

    const [nodesResult, servicesResult] = await Promise.allSettled([
        apiRequest(`/api/dashboard/nodes/watchlist${watchlistParams.toString() ? `?${watchlistParams.toString()}` : ""}`),
        apiRequest("/api/dashboard/services"),
    ]);

    if (nodesResult.status === "fulfilled") {
        renderDashboard(Array.isArray(nodesResult.value?.nodes) ? nodesResult.value.nodes : [], {
            gridId: "mainNodeGrid",
            lastUpdatedId: "main-dashboard-last-updated",
            countId: "main-dashboard-node-count",
            showPin: true,
            pinnedNodeKeys,
            emptyTitle: "No pinned nodes",
            emptySubtitle: "Pin nodes from the full Node Dashboard to build this watchlist.",
        });
        const nodesError = document.getElementById("main-dashboard-nodes-error");
        if (nodesError) {
            nodesError.hidden = true;
        }
    } else {
        const nodesError = document.getElementById("main-dashboard-nodes-error");
        mainNodeGrid.innerHTML = `
            <article class="node-card">
                <div class="node-header">
                    <div class="node-name">Pinned nodes unavailable</div>
                </div>
                <div class="node-sub">Unable to load the main dashboard node watchlist.</div>
            </article>
        `;
        if (nodesError) {
            nodesError.textContent = nodesResult.reason?.message || "Unable to load pinned nodes";
            nodesError.hidden = false;
        }
    }

    if (servicesResult.status === "fulfilled") {
        const pinnedServiceIds = getPinnedServiceIds();
        renderDashboardServices(servicesResult.value, {
            bodyId: "mainDashboardServicesBody",
            errorId: "main-dashboard-services-error",
            totalId: "main-dashboard-services-total",
            healthyId: "main-dashboard-services-healthy",
            degradedId: "main-dashboard-services-degraded",
            failedId: "main-dashboard-services-failed",
            pinnedServiceIds,
            filterPinned: true,
            emptyMessage: "No pinned services. Pin checks from the full Services Dashboard to build this watchlist.",
        });
        const serviceCount = document.getElementById("main-dashboard-service-count");
        if (serviceCount) {
            serviceCount.textContent = String(pinnedServiceIds.length);
        }
        const servicesError = document.getElementById("main-dashboard-services-error");
        if (servicesError) {
            servicesError.hidden = true;
        }
    } else {
        const servicesError = document.getElementById("main-dashboard-services-error");
        mainServicesBody.innerHTML = `
            <tr>
                <td colspan="7" class="table-message">Unable to load pinned services.</td>
            </tr>
        `;
        if (servicesError) {
            servicesError.textContent = servicesResult.reason?.message || "Unable to load pinned services";
            servicesError.hidden = false;
        }
    }
}

function resetServiceForm() {
    const form = document.getElementById("service-form");
    const error = document.getElementById("service-form-error");

    if (!form) {
        return;
    }

    form.reset();
    document.getElementById("service-type").value = "url";
    document.getElementById("service-enabled").checked = true;
    if (error) {
        error.hidden = true;
        error.textContent = "";
    }
}

function renderServicesTable(services) {
    const tableBody = document.getElementById("services-table-body");

    if (!tableBody) {
        return;
    }

    if (!services.length) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="table-message">No service checks configured yet.</td>
            </tr>
        `;
        return;
    }

    tableBody.innerHTML = services
        .map(
            (service) => `
                <tr>
                    <td>${service.name}</td>
                    <td>${String(service.service_type || "--").toUpperCase()}</td>
                    <td>${service.target}</td>
                    <td>${service.enabled ? "Yes" : "No"}</td>
                    <td>${service.notes ?? "--"}</td>
                    <td>
                        <button type="button" class="button-danger action-button" data-service-action="delete" data-id="${service.id}">Delete</button>
                    </td>
                </tr>
            `,
        )
        .join("");
}

async function loadServices() {
    const tableBody = document.getElementById("services-table-body");
    const servicesError = document.getElementById("services-error");

    if (!tableBody || !servicesError) {
        return;
    }

    try {
        currentServices = await apiRequest("/api/services");
        renderServicesTable(currentServices);
        servicesError.hidden = true;
        clearServicesFeedback();
    } catch (error) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="table-message">Unable to load services</td>
            </tr>
        `;
        servicesError.textContent = error.message || "Unable to load services";
        servicesError.hidden = false;
    }
}

function collectServiceFormPayload() {
    return {
        name: document.getElementById("service-name").value.trim(),
        service_type: document.getElementById("service-type").value,
        target: document.getElementById("service-target").value.trim(),
        enabled: document.getElementById("service-enabled").checked,
        notes: document.getElementById("service-notes").value.trim() || null,
    };
}

async function handleServiceFormSubmit(event) {
    event.preventDefault();

    const error = document.getElementById("service-form-error");

    try {
        await apiRequest("/api/services", {
            method: "POST",
            body: JSON.stringify(collectServiceFormPayload()),
        });
        resetServiceForm();
        await loadServices();
        showServicesFeedback("Service check added.");
    } catch (requestError) {
        if (error) {
            error.textContent = requestError.message || "Unable to save service check";
            error.hidden = false;
        }
    }
}

async function handleServiceTableClick(event) {
    const rawTarget = event.target;

    if (!(rawTarget instanceof HTMLElement)) {
        return;
    }

    const target = rawTarget.closest("[data-service-action]");

    if (!(target instanceof HTMLElement)) {
        return;
    }

    const action = target.dataset.serviceAction;
    const serviceId = Number(target.dataset.id);

    if (action !== "delete" || !Number.isFinite(serviceId)) {
        return;
    }

    try {
        await apiRequest(`/api/services/${serviceId}`, { method: "DELETE" });
        await loadServices();
        showServicesFeedback("Service check deleted.");
    } catch (error) {
        const servicesError = document.getElementById("services-error");
        if (servicesError) {
            servicesError.textContent = error.message || "Unable to delete service check";
            servicesError.hidden = false;
        }
    }
}

async function handleDashboardServiceTableClick(event) {
    const rawTarget = event.target;
    if (!(rawTarget instanceof HTMLElement)) {
        return;
    }

    const target = rawTarget.closest("[data-dashboard-service-action]");
    if (!(target instanceof HTMLElement)) {
        return;
    }

    const action = target.dataset.dashboardServiceAction;
    const serviceId = Number(target.dataset.serviceId);
    if (action !== "toggle-service-pin" || !Number.isFinite(serviceId)) {
        return;
    }

    togglePinnedServiceId(serviceId);

    if (document.getElementById("mainDashboardServicesBody")) {
        await loadMainDashboard();
    } else {
        await loadServicesDashboard();
    }
}

function collectNodeFormPayload() {
    const topologyRoot = document.getElementById("topology-root");
    const nodeIdField = document.getElementById("node-node-id");
    const includeInTopologyField = document.getElementById("node-include-in-topology");
    const topologyLevelField = document.getElementById("node-topology-level");
    const topologyUnitField = document.getElementById("node-topology-unit");
    const enabledField = document.getElementById("node-enabled");
    const inferredTopologyLevel = topologyRoot && topologyState.focusUnit ? 1 : 0;
    const inferredTopologyUnit = topologyRoot
        ? (topologyState.focusUnit || (inferredTopologyLevel === 0 ? "AGG" : "DIV HQ"))
        : "AGG";

    return {
        name: document.getElementById("node-name").value.trim(),
        node_id: nodeIdField instanceof HTMLInputElement ? (nodeIdField.value.trim() || null) : null,
        host: document.getElementById("node-host").value.trim(),
        web_port: Number(document.getElementById("node-web-port").value),
        ssh_port: Number(document.getElementById("node-ssh-port").value),
        location: document.getElementById("node-location").value.trim(),
        include_in_topology: includeInTopologyField instanceof HTMLInputElement ? includeInTopologyField.checked : Boolean(topologyRoot),
        topology_level:
            topologyLevelField instanceof HTMLSelectElement || topologyLevelField instanceof HTMLInputElement
                ? Number(topologyLevelField.value)
                : inferredTopologyLevel,
        topology_unit:
            topologyUnitField instanceof HTMLSelectElement || topologyUnitField instanceof HTMLInputElement
                ? topologyUnitField.value
                : inferredTopologyUnit,
        enabled: enabledField instanceof HTMLInputElement ? enabledField.checked : true,
        notes: document.getElementById("node-notes").value.trim() || null,
        api_username: document.getElementById("node-api-username").value.trim() || null,
        api_password: document.getElementById("node-api-password").value.trim() || null,
        api_use_https: document.getElementById("node-api-use-https").checked,
        ping_enabled: document.getElementById("node-ping-enabled").checked,
        ping_interval_seconds: Number(document.getElementById("node-ping-interval").value) || 15,
    };
}

async function handleNodeFormSubmit(event) {
    event.preventDefault();

    const formError = document.getElementById("node-form-error");
    const nodeId = document.getElementById("node-id").value;
    const payload = collectNodeFormPayload();
    const method = nodeId ? "PUT" : "POST";
    const url = nodeId ? `/api/nodes/${nodeId}` : "/api/nodes";

    try {
        const savedNode = await apiRequest(url, {
            method,
            body: JSON.stringify(payload),
        });
        await loadNodes();
        await loadNodeDashboard();
        await loadMainDashboard();
        if (document.getElementById("topology-root")) {
            await loadTopologyPage();
            if (savedNode?.id != null) {
                const topologyEntityId = `node-${savedNode.id}`;
                const existingLayout = topologyState.layoutOverrides?.[topologyEntityId];
                if (!existingLayout) {
                    setTopologyEntityLayout(topologyEntityId, getNewTopologyNodeLayout());
                    renderTopologyStage();
                }
            }
        }
        if (keepNodeModalOpenAfterSave) {
            resetNodeForm();
            openNodeModal();
            showFeedback("Node saved. Ready for another.");
            return;
        }
        resetNodeForm();
        closeNodeModal();
        showFeedback(nodeId ? "Node updated." : "Node added.");
    } catch (error) {
        formError.textContent = error.message || "Unable to save node";
        formError.hidden = false;
    }
}

async function refreshAllNodes() {
    const refreshButton = document.getElementById("refresh-nodes-button");

    if (!refreshButton) {
        return;
    }

    refreshButton.disabled = true;

    try {
        await apiRequest("/api/nodes/refresh", { method: "POST" });
        await loadNodes();
        await loadTopologyPage();
        const frame = document.getElementById("topology-detail-frame");
        const detailShell = document.getElementById("topology-detail-shell");
        if (detailShell && !detailShell.hidden && frame instanceof HTMLIFrameElement && frame.src) {
            frame.src = frame.src;
        }
        showFeedback("Seeker refresh completed.");
        document.getElementById("nodes-error").hidden = true;
    } catch (error) {
        const nodesError = document.getElementById("nodes-error");
        nodesError.textContent = error.message || "Unable to refresh nodes";
        nodesError.hidden = false;
    } finally {
        refreshButton.disabled = false;
    }
}

async function handleNodeActionClick(event) {
    const rawTarget = event.target;

    if (!(rawTarget instanceof HTMLElement)) {
        return;
    }

    const target = rawTarget.closest("[data-action]");

    if (!(target instanceof HTMLElement)) {
        return;
    }

    const action = target.dataset.action;
    const nodeId = Number(target.dataset.id);
    const node = currentNodes.find((entry) => entry.id === nodeId);

    if (action === "edit") {
        populateNodeForm(nodeId);
        return;
    }

    if (action === "telemetry" && node) {
        try {
            const telemetryResult = await apiRequest(`/api/nodes/${nodeId}/telemetry`, { method: "POST" });

            if (telemetryResult.status === "ok") {
                renderNormalizedTelemetrySummary(
                    node.name,
                    telemetryResult.normalized ?? {},
                    telemetryResult.telemetry ?? null,
                );
                showFeedback("Telemetry loaded.");
            } else {
                showTelemetryError(telemetryResult.message || "Unable to retrieve telemetry");
            }
        } catch (error) {
            showTelemetryError(error.message || "Unable to retrieve telemetry");
        }

        return;
    }

    if (action === "open-web" && node) {
        openWebForNode(node.host, node.web_port, node.api_use_https ? "https" : "http");
        return;
    }

    if (action === "ssh" && node) {
        try {
            await copySshCommand(node.host, node.api_username || "");
            showFeedback("SSH command copied");
        } catch (error) {
            const nodesError = document.getElementById("nodes-error");
            nodesError.textContent = "Unable to copy SSH command";
            nodesError.hidden = false;
        }

        return;
    }

    if (action === "delete") {
        if (!window.confirm("Delete this node?")) {
            return;
        }

        try {
            await apiRequest(`/api/nodes/${nodeId}`, { method: "DELETE" });
            resetNodeForm();
            await loadNodes();
            showFeedback("Node deleted.");
        } catch (error) {
            const nodesError = document.getElementById("nodes-error");
            nodesError.textContent = error.message || "Unable to delete node";
            nodesError.hidden = false;
        }
    }
}

window.addEventListener("DOMContentLoaded", () => {
    applyThemeMode();
    mountThemeControl();
    safeStart(loadPlatformStatus, "platform-status");
    safeStart(loadNodes, "nodes");
    safeStart(loadNodeDashboard, "node-dashboard");
    safeStart(loadServicesDashboard, "services-dashboard");
    safeStart(loadMainDashboard, "main-dashboard");
    safeStart(loadServices, "services");
    safeStart(loadTopologyPage, "topology");
    safeStart(loadNodeDetailPage, "node-detail");

    const nodeForm = document.getElementById("node-form");
    const serviceForm = document.getElementById("service-form");
    const nodesTableBody = document.getElementById("nodes-table-body");
    const servicesTableBody = document.getElementById("services-table-body");
    const dashboardServicesBody = document.getElementById("dashboardServicesBody");
    const mainDashboardServicesBody = document.getElementById("mainDashboardServicesBody");
    const cancelButton = document.getElementById("node-cancel-button");
    const saveAddAnotherButton = document.getElementById("node-save-add-another-button");
    const deleteNodeButton = document.getElementById("node-delete-button");
    const openNodeModalButton = document.getElementById("open-node-modal-button");
    const nodeModalShell = document.getElementById("node-modal-shell");
    const topologyInventoryShell = document.getElementById("topology-inventory-shell");
    const topologyDetailShell = document.getElementById("topology-detail-shell");
    const openTopologyInventoryButton = document.getElementById("open-topology-inventory-button");
    const closeTopologyInventoryButton = document.getElementById("close-topology-inventory-button");
    const closeTopologyDetailButton = document.getElementById("close-topology-detail-button");
    const refreshButton = document.getElementById("refresh-nodes-button");
    const dashboardRefreshButton = document.getElementById("dashboard-refresh-button");
    const dashboardRefreshMenu = document.getElementById("dashboard-refresh-menu");

    if (nodeForm) {
        nodeForm.addEventListener("submit", handleNodeFormSubmit);
        resetNodeForm();
    }

    const topologyInclude = document.getElementById("node-include-in-topology");
    const topologyLevel = document.getElementById("node-topology-level");
    const topologyUnit = document.getElementById("node-topology-unit");
    if (topologyInclude) {
        topologyInclude.addEventListener("change", syncTopologyFormFields);
    }
    if (topologyLevel) {
        topologyLevel.addEventListener("change", syncTopologyFormFields);
    }
    if (topologyUnit) {
        topologyUnit.addEventListener("change", syncTopologyFormFields);
    }

    if (serviceForm) {
        serviceForm.addEventListener("submit", handleServiceFormSubmit);
        resetServiceForm();
    }

    if (nodesTableBody) {
        nodesTableBody.addEventListener("click", handleNodeActionClick);
    }

    if (servicesTableBody) {
        servicesTableBody.addEventListener("click", handleServiceTableClick);
    }

    if (dashboardServicesBody) {
        dashboardServicesBody.addEventListener("click", handleDashboardServiceTableClick);
    }

    if (mainDashboardServicesBody) {
        mainDashboardServicesBody.addEventListener("click", handleDashboardServiceTableClick);
    }

    if (cancelButton) {
        cancelButton.addEventListener("click", () => {
            resetNodeForm();
            closeNodeModal();
        });
    }

    if (saveAddAnotherButton) {
        saveAddAnotherButton.addEventListener("click", () => {
            keepNodeModalOpenAfterSave = true;
            nodeForm?.requestSubmit();
        });
    }

    if (deleteNodeButton) {
        deleteNodeButton.addEventListener("click", () => {
            deleteCurrentNodeFromModal();
        });
    }

    if (openNodeModalButton) {
        openNodeModalButton.addEventListener("click", () => {
            closeTopologyInventory();
            resetNodeForm();
            openNodeModal();
        });
    }

    if (nodeModalShell) {
        nodeModalShell.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) {
                return;
            }
            if (target.dataset.modalClose === "node-modal") {
                resetNodeForm();
                closeNodeModal();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !nodeModalShell.hidden) {
                resetNodeForm();
                closeNodeModal();
            }
        });
    }

    if (openTopologyInventoryButton) {
        openTopologyInventoryButton.addEventListener("click", () => {
            openTopologyInventory();
        });
    }

    if (closeTopologyInventoryButton) {
        closeTopologyInventoryButton.addEventListener("click", () => {
            closeTopologyInventory();
        });
    }

    if (topologyInventoryShell) {
        topologyInventoryShell.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) {
                return;
            }
            if (target.dataset.modalClose === "topology-inventory") {
                closeTopologyInventory();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !topologyInventoryShell.hidden) {
                closeTopologyInventory();
            }
        });
    }

    if (closeTopologyDetailButton) {
        closeTopologyDetailButton.addEventListener("click", () => {
            closeTopologyDetail();
        });
    }

    if (topologyDetailShell) {
        topologyDetailShell.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) {
                return;
            }
            if (target.dataset.modalClose === "topology-detail") {
                closeTopologyDetail();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !topologyDetailShell.hidden) {
                closeTopologyDetail();
            }
        });
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", refreshAllNodes);
    }

    if (dashboardRefreshButton && dashboardRefreshMenu) {
        updateDashboardRefreshButton();
        dashboardRefreshButton.addEventListener("click", (event) => {
            event.stopPropagation();
            setDashboardRefreshMenuOpen(dashboardRefreshMenu.hidden);
        });
        dashboardRefreshMenu.addEventListener("click", (event) => {
            event.stopPropagation();
            const target = event.target;

            if (!(target instanceof HTMLElement)) {
                return;
            }

            const option = target.closest("[data-seconds]");
            if (!(option instanceof HTMLElement)) {
                return;
            }

            const seconds = Number(option.dataset.seconds);

            if (!Number.isFinite(seconds) || seconds <= 0) {
                return;
            }

            setDashboardRefreshSeconds(seconds);
            setDashboardRefreshMenuOpen(false);
            applyDashboardRefreshInterval();
            showDashboardFeedback(`Updated ${formatRefreshLabel(seconds)}`);

            if (document.getElementById("anchor-node-list") && document.getElementById("discovered-node-list")) {
                loadNodeDashboard();
                return;
            }
            if (document.getElementById("node-detail-root")) {
                loadNodeDetailPage();
                return;
            }
            if (document.getElementById("mainNodeGrid")) {
                loadMainDashboard();
                return;
            }
            if (document.getElementById("topology-root")) {
                startTopologyTimers();
                refreshTopologyPage();
                return;
            }
        });
        document.addEventListener("pointerdown", (event) => {
            const target = event.target;

            if (!(target instanceof Node)) {
                return;
            }

            if (!dashboardRefreshMenu.contains(target) && !dashboardRefreshButton.contains(target)) {
                setDashboardRefreshMenuOpen(false);
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                setDashboardRefreshMenuOpen(false);
            }
        });
    }

    if (
        document.getElementById("nodeGrid")
        || document.getElementById("mainNodeGrid")
    ) {
        applyDashboardRefreshInterval();
    }

    if (document.getElementById("topology-root")) {
        startTopologyTimers();
    }

    window.addEventListener("beforeunload", () => {
        disconnectNodeDashboardStream();
    });
});

