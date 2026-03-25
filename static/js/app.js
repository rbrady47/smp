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
let dashboardRefreshTimer = null;
const dashboardOrderStorageKey = "smp-dashboard-order";
const dashboardRefreshStorageKey = "smp-dashboard-refresh-seconds";
const themeModeStorageKey = "smp-theme-mode";

const statusPriority = {
    online: 0,
    degraded: 1,
    offline: 2,
    disabled: 3,
};

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

function openNode(nodeId) {
    window.location.href = `/nodes/${nodeId}`;
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

function applyDashboardRefreshInterval() {
    if (dashboardRefreshTimer) {
        window.clearInterval(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
    }

    const seconds = getDashboardRefreshSeconds();
    updateDashboardRefreshButton();
    setDashboardRefreshMenuOpen(false);

    if (seconds > 0 && document.getElementById("nodeGrid")) {
        dashboardRefreshTimer = window.setInterval(loadDashboard, seconds * 1000);
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

    if (!form) {
        return;
    }

    form.reset();
    currentEditNodeId = null;
    document.getElementById("node-id").value = "";
    document.getElementById("node-web-port").value = "443";
    document.getElementById("node-ssh-port").value = "22";
    document.getElementById("node-enabled").checked = true;
    formTitle.textContent = "Add Node";
    submitButton.textContent = "Add Node";
    cancelButton.hidden = true;
    formError.hidden = true;
}

function populateNodeForm(nodeId) {
    const node = currentNodes.find((entry) => entry.id === nodeId);

    if (!node) {
        return;
    }

    document.getElementById("node-id").value = String(node.id);
    document.getElementById("node-name").value = node.name;
    document.getElementById("node-host").value = node.host;
    document.getElementById("node-web-port").value = String(node.web_port);
    document.getElementById("node-ssh-port").value = String(node.ssh_port);
    document.getElementById("node-location").value = node.location;
    document.getElementById("node-enabled").checked = node.enabled;
    document.getElementById("node-notes").value = node.notes ?? "";
    document.getElementById("node-api-username").value = node.api_username ?? "";
    document.getElementById("node-api-password").value = node.api_password ?? "";
    document.getElementById("node-api-use-https").checked = node.api_use_https;
    document.getElementById("node-form-title").textContent = `Edit ${node.name}`;
    document.getElementById("node-submit-button").textContent = "Save Changes";
    document.getElementById("node-cancel-button").hidden = false;
    document.getElementById("node-form-error").hidden = true;
    currentEditNodeId = node.id;
    renderNodesTable(currentNodes);
}

function renderNodesTable(nodes) {
    const tableBody = document.getElementById("nodes-table-body");

    if (!tableBody) {
        return;
    }

    if (nodes.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="table-message">No nodes have been added yet.</td>
            </tr>
        `;
        return;
    }

    tableBody.innerHTML = nodes
        .map(
            (node) => `
                <tr class="${currentEditNodeId === node.id ? "row-editing" : ""}">
                    <td>${node.name}</td>
                    <td>${node.host}</td>
                    <td>${node.location}</td>
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

function renderDashboard(nodes) {
    const grid = document.getElementById("nodeGrid");
    const lastUpdated = document.getElementById("dashboard-last-updated");
    const nodeCount = document.getElementById("dashboard-node-count");

    if (!grid || !lastUpdated || !nodeCount) {
        return;
    }

    if (nodes.length === 0) {
        setDashboardRefreshMenuOpen(false);
        grid.innerHTML = `
            <article class="node-card">
                <div class="node-header">
                    <div class="node-name">No nodes configured</div>
                </div>
                <div class="node-sub">Add nodes from the inventory page to populate the dashboard.</div>
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
        const card = document.createElement("article");
        card.className = `node-card node-card-${node.status}`;
        card.dataset.nodeId = String(node.id);
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("draggable", "true");
        card.innerHTML = `
            <div class="node-card-glow"></div>
            <div class="node-header">
                <div>
                    <div class="node-name">${node.name}</div>
                    <div class="node-sub">${node.site} · ${node.host}</div>
                </div>
                <div class="service-list service-list-dashboard">
                    ${dashboardServiceItem("Web", "web", "open-web", node)}
                    ${dashboardServiceItem("SSH", "ssh", "ssh", node)}
                </div>
            </div>

            <div class="node-strip">
                <span class="metric-chip metric-chip-rtt ping-${node.ping_state || "down"}">
                    <span class="ping-dot ${node.ping_ok ? "up" : "down"}" title="${node.ping_ok ? "Ping reachable" : "Ping unreachable"}"></span>
                    <span class="metric-chip-label">RTT</span>
                    <span class="metric-chip-value">${node.latency_ms ?? "--"} ms</span>
                </span>
            </div>

              <div class="node-metrics">
                  <div class="metric-block">
                      <span class="metric-label">Sites</span>
                      <strong>${node.sites_up}/${node.sites_total}</strong>
                  </div>
                  <div class="metric-block metric-block-traffic">
                      <span class="metric-label">Tx / Rx</span>
                      <strong>${formatRate(node.tx_bps)} / ${formatRate(node.rx_bps)}</strong>
                  </div>
              </div>
          `;
        card.addEventListener("click", (event) => {
            const target = event.target;

            if (target instanceof Element && target.closest(".service-link, .dashboard-service-pill, .dashboard-view-button")) {
                return;
            }

            openNode(node.id);
        });
        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
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

function renderNodeSummaryPanel(containerId, summary) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    const txBps = Number(summary.tx_bps ?? 0);
    const rxBps = Number(summary.rx_bps ?? 0);
    const rttMs = Number(summary.latency_ms ?? 0);
    const peakTraffic = Math.max(txBps, rxBps, 1_000_000);

    container.innerHTML = `
        <div class="node-summary-panel">
            <div class="node-summary-card">
                <div class="node-summary-title-row">
                    <div>
                        <span class="detail-summary-label">Anchor Node</span>
                        <h3 class="node-summary-title">${summary.name ?? "--"}</h3>
                    </div>
                    <span class="node-summary-health">${summary.health_state ?? "--"}</span>
                </div>
                <div class="node-summary-keyline">
                    <div class="node-summary-primary">${summary.host ?? "--"}</div>
                    <div class="node-summary-subline">
                        <span>Status ${summary.status ?? "--"}</span>
                    </div>
                </div>
                <div class="node-summary-stats">
                    <div class="node-summary-stat">
                        <span class="detail-summary-label">Active Sites</span>
                        <strong>${summary.active_site_count ?? 0}</strong>
                    </div>
                    <div class="node-summary-stat">
                        <span class="detail-summary-label">WAN</span>
                        <strong>${summary.wan_count ?? 0}</strong>
                    </div>
                </div>
            </div>
            <div class="node-gauge-grid">
                ${renderMetricGauge("TX", formatRate(txBps), txBps, peakTraffic)}
                ${renderMetricGauge("RX", formatRate(rxBps), rxBps, peakTraffic)}
                ${renderMetricGauge("RTT", summary.latency_ms != null ? `${summary.latency_ms} ms` : "--", rttMs, 200)}
            </div>
        </div>
    `;
}

function renderMetricGauge(label, valueText, rawValue, maxValue) {
    const safeMax = Math.max(Number(maxValue) || 1, 1);
    const safeValue = Math.max(Number(rawValue) || 0, 0);
    const ratio = Math.min(safeValue / safeMax, 1);
    const degrees = 180 * ratio;
    return `
        <div class="metric-gauge-card">
            <span class="detail-summary-label">${label}</span>
            <div class="metric-gauge" style="--gauge-deg:${degrees}deg;">
                <div class="metric-gauge-inner">
                    <strong class="metric-gauge-value">${valueText}</strong>
                </div>
            </div>
        </div>
    `;
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
                    ${columns.map((column) => `<td>${formatDetailCell(column, row[column])}</td>`).join("")}
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

function formatDetailCell(column, value) {
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

async function loadNodeDetailPage() {
    const root = document.getElementById("node-detail-root");
    if (!root) {
        return;
    }

    const nodeId = Number(root.getAttribute("data-node-id"));
    if (!Number.isFinite(nodeId)) {
        return;
    }

    try {
        const detail = await apiRequest(`/api/nodes/${nodeId}/detail`);
        const node = detail.node ?? {};
        const summary = detail.node_summary ?? {};
        const config = detail.config_summary ?? {};
        const errors = detail.errors ?? {};

        document.getElementById("detail-node-name").textContent = node.name ?? `Node ${nodeId}`;
        document.getElementById("detail-host").textContent = node.host ?? "--";
        document.getElementById("detail-location").textContent = node.location ?? "--";
        document.getElementById("detail-status").textContent = node.status ?? "--";
        document.getElementById("detail-services").textContent =
            `Web ${node.web_ok ? "Up" : "Down"} · SSH ${node.ssh_ok ? "Up" : "Down"}`;
        document.getElementById("detail-last-refresh").textContent = formatDashboardTimestamp(node.last_refresh);
        document.getElementById("detail-last-telemetry").textContent = formatDashboardTimestamp(node.last_telemetry_pull);

        renderNodeSummaryPanel("detail-summary-grid", summary);

        renderDetailSummaryGrid("detail-config-grid", [
            ["Site ID", config.site_id ?? "--"],
            ["Site Name", config.site_name ?? "--"],
            ["Mgmt IP", config.mgmt_ip ?? "--"],
            ["Version", config.version ?? "--"],
            ["License Expiration", config.license_expires ?? "--"],
            ["Mate Count", config.n_mates ?? 0],
            ["Enclave", config.enclave_id ?? "--"],
            ["Platform", config.platform ?? "--"],
        ]);

        renderDetailTableBody(
            "detail-tunnels-body",
            detail.tunnels ?? [],
            ["mate_index", "site_name", "mate_site_id", "mate_ip", "tunnel_health", "tx_rate", "rx_rate", "rtt_ms", "ping"],
            "No tunnel data available.",
        );
        wireSitesFilter();
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

    if (!tableBody) {
        return;
    }

    try {
        currentNodes = sortNodesForDisplay(await apiRequest("/api/nodes"));
        renderNodesTable(currentNodes);
        nodesError.hidden = true;
        clearFeedback();

        const selectedNodeId = Number(new URLSearchParams(window.location.search).get("node"));
        if (selectedNodeId) {
            populateNodeForm(selectedNodeId);
        }
    } catch (error) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="table-message">Unable to load node inventory</td>
            </tr>
        `;
        nodesError.hidden = false;
    }
}

async function loadDashboard() {
    const grid = document.getElementById("nodeGrid");
    const dashboardError = document.getElementById("dashboard-error");

    if (!grid || !dashboardError) {
        return;
    }

    try {
        const nodes = await apiRequest("/api/dashboard/nodes");
        renderDashboard(nodes);
        dashboardError.hidden = true;
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

function collectNodeFormPayload() {
    return {
        name: document.getElementById("node-name").value.trim(),
        host: document.getElementById("node-host").value.trim(),
        web_port: Number(document.getElementById("node-web-port").value),
        ssh_port: Number(document.getElementById("node-ssh-port").value),
        location: document.getElementById("node-location").value.trim(),
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
        resetNodeForm();
        await loadNodes();
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
    loadPlatformStatus();
    loadNodes();
    loadDashboard();
    loadNodeDetailPage();

    const nodeForm = document.getElementById("node-form");
    const nodesTableBody = document.getElementById("nodes-table-body");
    const cancelButton = document.getElementById("node-cancel-button");
    const refreshButton = document.getElementById("refresh-nodes-button");
    const dashboardRefreshButton = document.getElementById("dashboard-refresh-button");
    const dashboardRefreshMenu = document.getElementById("dashboard-refresh-menu");

    if (nodeForm) {
        nodeForm.addEventListener("submit", handleNodeFormSubmit);
        resetNodeForm();
    }

    if (nodesTableBody) {
        nodesTableBody.addEventListener("click", handleNodeActionClick);
    }

    if (cancelButton) {
        cancelButton.addEventListener("click", resetNodeForm);
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

    if (document.getElementById("nodeGrid")) {
        applyDashboardRefreshInterval();
    }
});
