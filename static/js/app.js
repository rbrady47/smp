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
let nodeDashboardEventSource = null;
const dashboardOrderStorageKey = "smp-dashboard-order";
const anchorListOrderStorageKey = "smp-anchor-list-order";
const dashboardRefreshStorageKey = "smp-dashboard-refresh-seconds";
const themeModeStorageKey = "smp-theme-mode";
const pinnedNodesStorageKey = "smp-main-dashboard-node-ids";
const pinnedServicesStorageKey = "smp-main-dashboard-service-ids";
const topologyControlsCollapsedStorageKey = "smp-topology-controls-collapsed";
const TOPOLOGY_LOCATIONS = ["HSMC", "Cloud", "Episodic"];
const TOPOLOGY_UNITS = ["DIV HQ", "1BCT", "2BCT", "3BCT", "CAB/DIVARTY", "Sustainment"];
let topologyPayload = null;
let topologyResizeListenerBound = false;
const topologyState = {
    activeLocations: new Set(TOPOLOGY_LOCATIONS),
    activeUnits: new Set(TOPOLOGY_UNITS),
    view: "backbone+l2",
    selectedKind: null,
    selectedId: null,
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

function saveThemeMode(mode) {
    try {
        window.localStorage.setItem(themeModeStorageKey, mode);
    } catch (error) {
        // Ignore storage failures and keep the app usable.
    }
}

function getResolvedTheme(mode) {
    if (mode === "light" || mode === "dark") {
        return mode;
    }

    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyThemeMode(mode = getSavedThemeMode()) {
    const resolvedTheme = getResolvedTheme(mode);
    document.documentElement.setAttribute("data-theme", resolvedTheme);
    document.documentElement.setAttribute("data-theme-mode", mode);
    updateThemeControlUi(mode);
}

function updateThemeControlUi(mode = getSavedThemeMode()) {
    const button = document.getElementById("theme-mode-button");
    const menu = document.getElementById("theme-mode-menu");
    if (button) {
        const label = mode.charAt(0).toUpperCase() + mode.slice(1);
        button.textContent = `Theme: ${label}`;
    }
    if (menu) {
        menu.querySelectorAll("[data-theme-mode]").forEach((item) => {
            item.setAttribute("aria-pressed", item.getAttribute("data-theme-mode") === mode ? "true" : "false");
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
        if (!mode) {
            return;
        }
        saveThemeMode(mode);
        applyThemeMode(mode);
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

    if ([0, 10, 30, 60].includes(seconds)) {
        return seconds;
    }

    return 10;
}

function setDashboardRefreshSeconds(seconds) {
    window.localStorage.setItem(dashboardRefreshStorageKey, String(seconds));
}

function formatRefreshLabel(seconds) {
    if (seconds === 0) {
        return "Refresh Off";
    }

    return `${seconds} seconds`;
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

function disconnectNodeDashboardStream() {
    if (nodeDashboardEventSource) {
        nodeDashboardEventSource.close();
        nodeDashboardEventSource = null;
    }
}

function connectNodeDashboardStream() {
    const anchorList = document.getElementById("anchor-node-list");
    const discoveredList = document.getElementById("discovered-node-list");
    const dashboardError = document.getElementById("dashboard-error");

    if (!anchorList || !discoveredList || !dashboardError || !window.EventSource || nodeDashboardEventSource) {
        return;
    }

    nodeDashboardEventSource = new EventSource("/api/node-dashboard/stream");

    nodeDashboardEventSource.addEventListener("snapshot", (event) => {
        try {
            const payload = JSON.parse(event.data || "{}");
            currentNodeDashboardPayload = {
                anchors: Array.isArray(payload.anchors) ? payload.anchors : [],
                discovered: Array.isArray(payload.discovered) ? payload.discovered : [],
            };
            renderNodeDashboardLists(currentNodeDashboardPayload);
            dashboardError.hidden = true;
        } catch (error) {
            console.error("Unable to parse node dashboard stream payload", error);
        }
    });

    nodeDashboardEventSource.onerror = () => {
        disconnectNodeDashboardStream();
        window.setTimeout(() => {
            connectNodeDashboardStream();
        }, 3000);
    };
}

function applyDashboardRefreshInterval() {
    if (dashboardRefreshTimer) {
        window.clearInterval(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
    }

    const seconds = getDashboardRefreshSeconds();
    updateDashboardRefreshButton();
    setDashboardRefreshMenuOpen(false);

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
    }
}

function statusCell(node) {
    const latencyText = typeof node.latency_ms === "number" ? `${node.latency_ms} ms` : "No latency";
    return `
        <div class="status-stack">
            <span class="status-badge ${node.status}">${node.status}</span>
            <span class="status-meta">${latencyText}</span>
            <span class="status-meta">${formatLastChecked(node.last_checked)}</span>
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

    if (!form) {
        return;
    }

    form.reset();
    currentEditNodeId = null;
    document.getElementById("node-id").value = "";
    document.getElementById("node-node-id").value = "";
    document.getElementById("node-web-port").value = "443";
    document.getElementById("node-ssh-port").value = "22";
    document.getElementById("node-include-in-topology").checked = false;
    document.getElementById("node-topology-level").value = "0";
    document.getElementById("node-topology-unit").value = "AGG";
    document.getElementById("node-enabled").checked = true;
    formTitle.textContent = "Add Node";
    submitButton.textContent = "Save";
    cancelButton.hidden = false;
    if (saveAddAnotherButton) {
        saveAddAnotherButton.hidden = false;
    }
    formError.hidden = true;
    formError.textContent = "Unable to save node";
    keepNodeModalOpenAfterSave = false;
    syncTopologyFormFields();
}

function getNodeModalShell() {
    return document.getElementById("node-modal-shell");
}

function openNodeModal() {
    const modalShell = getNodeModalShell();
    if (!modalShell) {
        return;
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

function syncTopologyFormFields() {
    const includeCheckbox = document.getElementById("node-include-in-topology");
    const levelField = document.getElementById("node-topology-level");
    const unitField = document.getElementById("node-topology-unit");

    if (!includeCheckbox || !levelField || !unitField) {
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

    if (!node) {
        return;
    }

    document.getElementById("node-id").value = String(node.id);
    document.getElementById("node-name").value = node.name;
    document.getElementById("node-node-id").value = node.node_id ?? "";
    document.getElementById("node-host").value = node.host;
    document.getElementById("node-web-port").value = String(node.web_port);
    document.getElementById("node-ssh-port").value = String(node.ssh_port);
    document.getElementById("node-location").value = node.location;
    document.getElementById("node-include-in-topology").checked = Boolean(node.include_in_topology);
    document.getElementById("node-topology-level").value = String(node.topology_level ?? 0);
    document.getElementById("node-topology-unit").value = node.topology_unit ?? "AGG";
    document.getElementById("node-enabled").checked = node.enabled;
    document.getElementById("node-notes").value = node.notes ?? "";
    document.getElementById("node-api-username").value = node.api_username ?? "";
    document.getElementById("node-api-password").value = node.api_password ?? "";
    document.getElementById("node-api-use-https").checked = node.api_use_https;
    document.getElementById("node-form-title").textContent = `Edit ${node.name}`;
    document.getElementById("node-submit-button").textContent = "Save";
    document.getElementById("node-cancel-button").hidden = false;
    if (saveAddAnotherButton) {
        saveAddAnotherButton.hidden = true;
    }
    document.getElementById("node-form-error").hidden = true;
    currentEditNodeId = node.id;
    syncTopologyFormFields();
    renderNodesTable(currentNodes);
    openNodeModal();
}

function renderNodesTable(nodes) {
    const tableBody = document.getElementById("nodes-table-body");

    if (!tableBody) {
        return;
    }

    if (nodes.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="8" class="table-message">No nodes have been added yet.</td>
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
                    <td>${node.host}</td>
                    <td>${node.location}</td>
                    <td>${formatTopologyCell(node)}</td>
                    <td>${servicesCell(node)}</td>
                    <td>${statusCell(node)}</td>
                    <td class="action-cell">
                        <button type="button" class="button-secondary action-button" data-action="telemetry" data-id="${node.id}">Telemetry</button>
                        <button type="button" class="button-secondary action-button" data-action="edit" data-id="${node.id}">Edit</button>
                        <button type="button" class="button-danger action-button" data-action="delete" data-id="${node.id}">Delete</button>
                    </td>
                </tr>
            `,
        )
        .join("");
}

function formatTopologyCell(node) {
    if (!node.include_in_topology) {
        return `<span class="status-pill status-neutral">Excluded</span>`;
    }

    const level = node.topology_level ?? "--";
    const unit = node.topology_unit ?? "--";
    return `
        <div class="table-stack">
            <strong>L${level}</strong>
            <span>${escapeHtml(unit)}</span>
        </div>
    `;
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
                <span class="metric-chip metric-chip-rtt ping-${node.ping_state || "down"}">
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
    const peakTraffic = Math.max(txBps, rxBps, 1_000_000);

    container.innerHTML = `
        <div class="node-summary-panel">
            ${renderMetricGauge("TX", formatRate(txBps), txBps, peakTraffic)}
            ${renderMetricGauge("RX", formatRate(rxBps), rxBps, peakTraffic)}
            ${renderMetricGauge("CPU", formatCpuPercent(summary.cpu_avg), cpuPercent, 100)}
            ${renderMetricGauge("RTT", summary.latency_ms != null ? `${summary.latency_ms} ms` : "--", rttMs, 200)}
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
    `;
}

function renderMetricGauge(label, valueText, rawValue, maxValue) {
    const safeMax = Math.max(Number(maxValue) || 1, 1);
    const safeValue = Math.max(Number(rawValue) || 0, 0);
    const ratio = Math.min(safeValue / safeMax, 1);
    const degrees = 360 * ratio;
    const { primary, secondary } = splitMetricDisplay(valueText);
    return `
        <div class="metric-gauge-card">
            <span class="metric-gauge-label">${label}</span>
            <div class="metric-gauge-shell" style="--gauge-deg:${degrees}deg;">
                <div class="metric-gauge">
                    <div class="metric-gauge-center">
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
                >
                    ${label}
                </button>
            `,
        )
        .join("");
}

function renderTopologyLocationHeaders() {
    const container = document.getElementById("topology-locations-header");
    if (!container) {
        return;
    }
    container.innerHTML = "";
}

function getTopologyNodePosition(entity) {
    const stage = document.getElementById("topology-stage");
    if (!stage) {
        return { x: 0, y: 0 };
    }

    const width = Math.max(stage.clientWidth, 2140);
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
        Cloud: 140,
        HSMC: 92,
        Episodic: 140,
    };
    const lvl1Y = 340;
    const lvl2Y = 610;

    if (entity.level === 0) {
        return {
            x: aggXs[entity.location] ?? width / 2,
            y: aggYs[entity.location] ?? 120,
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

    return [
        ...(topologyPayload.lvl0_nodes ?? []),
        ...(topologyPayload.lvl1_nodes ?? []),
        ...(topologyPayload.lvl2_clusters ?? []),
    ];
}

function getTopologyLinkId(link, index) {
    return link.id || `link-${index}`;
}

function isTopologyEntityVisible(entity) {
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
    const layer = document.getElementById("topology-node-layer");
    const stage = document.getElementById("topology-stage");
    if (!layer || !stage || !topologyPayload) {
        return;
    }

    renderTopologyLocationHeaders();

    const entities = getTopologyEntities();
    const visibleEntities = entities.filter(isTopologyEntityVisible);
    const entityMap = new Map(entities.map((entity) => [entity.id, entity]));

    layer.innerHTML = visibleEntities
        .map((entity) => {
            const position = getTopologyNodePosition(entity);
            const isCluster = entity.level === 2;
            const isLvl1 = entity.level === 1;
            const classes = [
                "topology-entity",
                isCluster ? "topology-cluster" : "topology-node",
                `topology-status-${entity.status || "neutral"}`,
                entity.level === 0 ? "topology-node-agg" : "",
                isLvl1 ? "topology-node-lvl1" : "",
                topologyState.selectedKind === "entity" && topologyState.selectedId === entity.id ? "is-selected" : "",
            ]
                .filter(Boolean)
                .join(" ");

            const badge = isCluster ? `<span class="topology-count-badge">${entity.count}</span>` : "";
            const subtitle = isCluster
                ? "Aggregated Lvl2"
                : entity.level === 1
                    ? escapeHtml(entity.unit)
                    : `${escapeHtml(entity.location)} · ${escapeHtml(entity.unit)}`;
            const displayName = entity.level === 1 ? escapeHtml(entity.location) : escapeHtml(entity.name);

            return `
                <button
                    type="button"
                    class="${classes}"
                    data-topology-id="${entity.id}"
                    style="left:${position.x}px; top:${position.y}px;"
                >
                    <span class="topology-node-name">${displayName}</span>
                    <span class="topology-node-meta">${subtitle}</span>
                    ${badge}
                </button>
            `;
        })
        .join("");

    const visibleIds = new Set(visibleEntities.map((entity) => entity.id));
    if (topologyState.selectedKind === "entity" && topologyState.selectedId && !visibleIds.has(topologyState.selectedId)) {
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
    }

    layer.querySelectorAll("[data-topology-id]").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            const nextId = button.getAttribute("data-topology-id");
            if (topologyState.selectedKind === "entity" && topologyState.selectedId === nextId) {
                topologyState.selectedKind = null;
                topologyState.selectedId = null;
            } else {
                topologyState.selectedKind = "entity";
                topologyState.selectedId = nextId;
            }
            renderTopologyStage();
        });
    });

    stage.onclick = (event) => {
        const target = event.target;
        if (target instanceof Element && target.closest("[data-topology-id], [data-topology-link-id], #topology-drawer")) {
            return;
        }
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        renderTopologyStage();
    };

    drawTopologyLinks(entityMap);
    renderTopologyDrawer();
}

function drawTopologyLinks(entityMap) {
    const svg = document.getElementById("topology-links");
    const stage = document.getElementById("topology-stage");
    if (!svg || !stage || !topologyPayload) {
        return;
    }

    const stageRect = stage.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${Math.max(stage.clientWidth, 1)} ${Math.max(stage.clientHeight, 1)}`);
    svg.innerHTML = "";

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

        const fromRect = fromNode.getBoundingClientRect();
        const toRect = toNode.getBoundingClientRect();
        const x1 = fromRect.left + fromRect.width / 2 - stageRect.left;
        const y1 = fromRect.top + fromRect.height / 2 - stageRect.top;
        const x2 = toRect.left + toRect.width / 2 - stageRect.left;
        const y2 = toRect.top + toRect.height / 2 - stageRect.top;

        const linkId = getTopologyLinkId(link, index);
        const shape = document.createElementNS("http://www.w3.org/2000/svg", "line");
        shape.setAttribute("x1", String(x1));
        shape.setAttribute("y1", String(y1));
        shape.setAttribute("x2", String(x2));
        shape.setAttribute("y2", String(y2));
        shape.setAttribute(
            "class",
            `topology-link topology-link-${link.kind} topology-link-${link.status || "neutral"} ${topologyState.selectedKind === "link" && topologyState.selectedId === linkId ? "is-selected" : ""}`,
        );
        shape.setAttribute("data-topology-link-id", linkId);
        svg.appendChild(shape);
    });

    svg.querySelectorAll("[data-topology-link-id]").forEach((line) => {
        line.addEventListener("click", (event) => {
            event.stopPropagation();
            const nextId = line.getAttribute("data-topology-link-id");
            if (!nextId) {
                return;
            }
            if (topologyState.selectedKind === "link" && topologyState.selectedId === nextId) {
                topologyState.selectedKind = null;
                topologyState.selectedId = null;
            } else {
                topologyState.selectedKind = "link";
                topologyState.selectedId = nextId;
            }
            renderTopologyStage();
        });
    });
}

function renderTopologyDrawer() {
    const drawer = document.getElementById("topology-details-drawer");
    const drawerShell = document.getElementById("topology-drawer");
    if (!drawer || !drawerShell || !topologyPayload) {
        return;
    }

    const entity = topologyState.selectedKind === "entity"
        ? getTopologyEntities().find((item) => item.id === topologyState.selectedId)
        : null;
    const link = topologyState.selectedKind === "link"
        ? (topologyPayload.links ?? []).find((item, index) => getTopologyLinkId(item, index) === topologyState.selectedId)
        : null;

    if (!entity && !link) {
        drawerShell.hidden = true;
        drawerShell.classList.remove("is-open");
        return;
    }

    drawerShell.hidden = false;
    requestAnimationFrame(() => {
        drawerShell.classList.add("is-open");
    });

    if (link) {
        drawer.innerHTML = `
            <div class="topology-drawer-block">
                <span class="dashboard-meta-label">Selected Link</span>
                <h3>${escapeHtml(link.label || `${link.from} -> ${link.to}`)}</h3>
            </div>
            <div class="topology-drawer-grid">
                <div class="detail-summary-item">
                    <span class="detail-summary-label">Kind</span>
                    <strong class="detail-summary-value">${escapeHtml(link.kind || "--")}</strong>
                </div>
                <div class="detail-summary-item">
                    <span class="detail-summary-label">Status</span>
                    <strong class="detail-summary-value">${escapeHtml(link.status || "neutral")}</strong>
                </div>
                <div class="detail-summary-item">
                    <span class="detail-summary-label">From</span>
                    <strong class="detail-summary-value">${escapeHtml(link.from || "--")}</strong>
                </div>
                <div class="detail-summary-item">
                    <span class="detail-summary-label">To</span>
                    <strong class="detail-summary-value">${escapeHtml(link.to || "--")}</strong>
                </div>
            </div>
            <div class="topology-drawer-block">
                <span class="dashboard-meta-label">Operational Notes</span>
                <p>Phase 1 placeholder link telemetry. Phase 2 can add observed versus expected overlays and richer link health.</p>
            </div>
        `;
        return;
    }

    const levelLabel = entity.level === 0 ? "Lvl0" : entity.level === 1 ? "Lvl1" : "Lvl2 Cluster";
    drawer.innerHTML = `
        <div class="topology-drawer-block">
            <span class="dashboard-meta-label">Selected</span>
            <h3>${escapeHtml(entity.name)}</h3>
        </div>
        <div class="topology-drawer-grid">
            <div class="detail-summary-item">
                <span class="detail-summary-label">Level</span>
                <strong class="detail-summary-value">${levelLabel}</strong>
            </div>
            <div class="detail-summary-item">
                <span class="detail-summary-label">Status</span>
                <strong class="detail-summary-value">${escapeHtml(entity.status || "neutral")}</strong>
            </div>
            <div class="detail-summary-item">
                <span class="detail-summary-label">Location</span>
                <strong class="detail-summary-value">${escapeHtml(entity.location || "Cross-Location")}</strong>
            </div>
            <div class="detail-summary-item">
                <span class="detail-summary-label">Unit</span>
                <strong class="detail-summary-value">${escapeHtml(entity.unit || "--")}</strong>
            </div>
        </div>
        <div class="topology-drawer-block">
            <span class="dashboard-meta-label">Operational Notes</span>
            <p>${escapeHtml(entity.metrics_text || "Placeholder metrics for Phase 1 topology inspection.")}</p>
        </div>
    `;
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
}

function getTopologyControlsCollapsed() {
    try {
        return window.localStorage.getItem(topologyControlsCollapsedStorageKey) === "true";
    } catch (error) {
        return false;
    }
}

function setTopologyControlsCollapsed(collapsed) {
    const bar = document.getElementById("topology-filter-bar");
    const body = document.getElementById("topology-filter-bar-body");
    const button = document.getElementById("topology-filter-toggle");

    if (!bar || !body || !button) {
        return;
    }

    bar.hidden = collapsed;
    body.hidden = collapsed;
    button.textContent = collapsed ? "Show Controls" : "Hide Controls";
    button.setAttribute("aria-expanded", collapsed ? "false" : "true");

    try {
        window.localStorage.setItem(topologyControlsCollapsedStorageKey, collapsed ? "true" : "false");
    } catch (error) {
        // Ignore storage failures.
    }
}

function wireTopologyBarToggle() {
    const button = document.getElementById("topology-filter-toggle");
    if (!button || button.dataset.bound === "true") {
        return;
    }

    button.dataset.bound = "true";
    button.addEventListener("click", () => {
        const collapsed = !getTopologyControlsCollapsed();
        setTopologyControlsCollapsed(collapsed);
        renderTopologyStage();
    });
}

async function loadTopologyPage() {
    const root = document.getElementById("topology-root");
    if (!root) {
        return;
    }

    try {
        topologyPayload = await apiRequest("/api/topology");
        topologyState.activeLocations = new Set(TOPOLOGY_LOCATIONS);
        topologyState.activeUnits = new Set(TOPOLOGY_UNITS);
        topologyState.view = "backbone+l2";
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
        renderTopologyControls();
        wireTopologyBarToggle();
        setTopologyControlsCollapsed(getTopologyControlsCollapsed());
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
        const detail = await apiRequest(detailEndpoint);
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
            ["In Topology", node.include_in_topology ? "Yes" : "No"],
            ["Topology Level", node.topology_level ?? "--"],
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
            const payload = await apiRequest("/api/node-dashboard");
            currentNodeDashboardPayload = payload;
            renderNodeDashboardLists(payload);
            dashboardError.hidden = true;
            connectNodeDashboardStream();
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
        row.version,
        row.host,
        row.discovered_parent_name,
        Array.isArray(row.surfaced_by_names) ? row.surfaced_by_names.join(" ") : "",
    ]
        .map((item) => String(item ?? "").toLowerCase());
    return haystacks.some((value) => value.includes(query));
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
    const rttState = row.ping_state || (String(row.ping || "").toLowerCase() === "up" ? "good" : "down");
    const txDisplay = row.tx_display || formatRate(row.tx_bps || row.tx_rate || 0);
    const rxDisplay = row.rx_display || formatRate(row.rx_bps || row.rx_rate || 0);
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
                    <div class="node-list-meta"><span class="node-list-meta-label">Version</span><strong>${escapeHtml(row.version || "--")}</strong></div>
                    <div class="node-list-meta">
                        <span class="node-list-meta-label">RTT</span>
                        <span class="metric-chip metric-chip-rtt node-list-rtt-chip ping-${escapeHtml(String(rttState))}">
                            <span class="metric-chip-label">RTT</span>
                            <span class="metric-chip-value">${escapeHtml(latencyText)}</span>
                        </span>
                    </div>
                    <div class="node-list-meta node-list-meta-traffic"><span class="node-list-meta-label">Tx / Rx</span><strong>${escapeHtml(txDisplay)} / ${escapeHtml(rxDisplay)}</strong></div>
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

    anchorList.innerHTML = filteredAnchors.length
        ? sortAnchorListRows(filteredAnchors).map((row) => renderNodeDashboardRow(row, pinnedKeys)).join("")
        : `<div class="table-message">No anchor nodes matched.</div>`;
    discoveredList.innerHTML = filteredDiscovered.length
        ? filteredDiscovered.map((row) => renderNodeDashboardRow(row, pinnedKeys)).join("")
        : `<div class="table-message">No discovered nodes matched.</div>`;

    if (anchorCount) {
        anchorCount.textContent = String(anchors.length);
    }
    if (discoveredCount) {
        discoveredCount.textContent = String(discovered.length);
    }
    if (lastUpdated) {
        lastUpdated.textContent = new Date().toLocaleTimeString();
    }

    document.querySelectorAll("[data-node-detail-url]").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openNodeDetail(button.getAttribute("data-node-detail-url"));
        });
    });

    document.querySelectorAll("[data-node-list-action]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
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
                    await loadNodeDashboard();
                    await loadMainDashboard();
                    showDashboardFeedback("Discovered node deleted");
                } catch (error) {
                    const dashboardError = document.getElementById("dashboard-error");
                    if (dashboardError) {
                        dashboardError.textContent = error.message || "Unable to delete discovered node";
                        dashboardError.hidden = false;
                    }
                }
            }
        });
    });

    anchorList.querySelectorAll(".node-list-row[data-node-pin-key]").forEach((row) => {
        if (!(row instanceof HTMLElement)) {
            return;
        }
        attachAnchorRowDragAndDrop(row);
    });

    if (anchorSearch && !anchorSearch._nodeDashboardSearchBound) {
        anchorSearch.addEventListener("input", () => renderNodeDashboardLists(currentNodeDashboardPayload));
        anchorSearch._nodeDashboardSearchBound = true;
    }
    if (discoveredSearch && !discoveredSearch._nodeDashboardSearchBound) {
        discoveredSearch.addEventListener("input", () => renderNodeDashboardLists(currentNodeDashboardPayload));
        discoveredSearch._nodeDashboardSearchBound = true;
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

    const [nodesResult, servicesResult] = await Promise.allSettled([
        apiRequest("/api/node-dashboard"),
        apiRequest("/api/dashboard/services"),
    ]);

    if (nodesResult.status === "fulfilled") {
        const pinnedNodeKeys = new Set(getPinnedNodeKeys());
        const allNodes = [
            ...(Array.isArray(nodesResult.value?.anchors) ? nodesResult.value.anchors : []),
            ...(Array.isArray(nodesResult.value?.discovered) ? nodesResult.value.discovered : []),
        ];
        const pinnedNodes = allNodes.filter((node) => pinnedNodeKeys.has(String(node.pin_key || `anchor:${node.id}`)));
        renderDashboard(pinnedNodes, {
            gridId: "mainNodeGrid",
            lastUpdatedId: "main-dashboard-last-updated",
            countId: "main-dashboard-node-count",
            showPin: true,
            pinnedNodeKeys: Array.from(pinnedNodeKeys),
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
    return {
        name: document.getElementById("node-name").value.trim(),
        node_id: document.getElementById("node-node-id").value.trim() || null,
        host: document.getElementById("node-host").value.trim(),
        web_port: Number(document.getElementById("node-web-port").value),
        ssh_port: Number(document.getElementById("node-ssh-port").value),
        location: document.getElementById("node-location").value.trim(),
        include_in_topology: document.getElementById("node-include-in-topology").checked,
        topology_level: Number(document.getElementById("node-topology-level").value),
        topology_unit: document.getElementById("node-topology-unit").value,
        enabled: document.getElementById("node-enabled").checked,
        notes: document.getElementById("node-notes").value.trim() || null,
        api_username: document.getElementById("node-api-username").value.trim() || null,
        api_password: document.getElementById("node-api-password").value.trim() || null,
        api_use_https: document.getElementById("node-api-use-https").checked,
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
        await apiRequest(url, {
            method,
            body: JSON.stringify(payload),
        });
        await loadNodes();
        await loadNodeDashboard();
        await loadMainDashboard();
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
        currentNodes = sortNodesForDisplay(await apiRequest("/api/nodes/refresh", { method: "POST" }));
        renderNodesTable(currentNodes);
        showFeedback("Health checks refreshed.");
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
    const openNodeModalButton = document.getElementById("open-node-modal-button");
    const nodeModalShell = document.getElementById("node-modal-shell");
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

    if (openNodeModalButton) {
        openNodeModalButton.addEventListener("click", () => {
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

            const seconds = Number(target.dataset.seconds);

            if (!Number.isFinite(seconds)) {
                return;
            }

            setDashboardRefreshSeconds(seconds);
            setDashboardRefreshMenuOpen(false);
            applyDashboardRefreshInterval();
            showDashboardFeedback(`Updated ${formatRefreshLabel(seconds)}`);
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

    window.addEventListener("beforeunload", () => {
        disconnectNodeDashboardStream();
    });
});
