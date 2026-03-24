// Load live platform status from the backend after the page finishes loading.
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
        // Fetch the existing status endpoint and parse the JSON response.
        const response = await fetch("/api/status");

        if (!response.ok) {
            throw new Error("Status request failed");
        }

        const data = await response.json();

        // Populate the status card fields with the live backend values.
        statusFields.app.textContent = data.app;
        statusFields.version.textContent = data.version;
        statusFields.hostname.textContent = data.hostname;
        statusFields.time.textContent = data.time;
        statusError.hidden = true;
    } catch (error) {
        // If the fetch fails, show a simple error message in the card.
        Object.values(statusFields).forEach((field) => {
            field.textContent = "--";
        });
        statusError.hidden = false;
    }
}

let currentNodes = [];
let currentEditNodeId = null;

const statusPriority = {
    online: 0,
    degraded: 1,
    offline: 2,
    disabled: 3,
};

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

function formatLastChecked(timestamp) {
    if (!timestamp) {
        return "Not checked";
    }

    return new Date(timestamp).toLocaleTimeString();
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
                        <button type="button" class="button-secondary action-button" data-action="edit" data-id="${node.id}">Edit</button>
                        <button type="button" class="button-danger action-button" data-action="delete" data-id="${node.id}">Delete</button>
                    </td>
                </tr>
            `,
        )
        .join("");
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

// Load the database-backed node inventory and render it into the table on the nodes page.
async function loadNodes() {
    const tableBody = document.getElementById("nodes-table-body");
    const nodesError = document.getElementById("nodes-error");

    if (!tableBody) {
        return;
    }

    try {
        // Fetch the node list from the backend API and store it for edit actions.
        currentNodes = sortNodesForDisplay(await apiRequest("/api/nodes"));
        renderNodesTable(currentNodes);
        nodesError.hidden = true;
        clearFeedback();
    } catch (error) {
        // If the fetch fails, keep the table simple and show a clear error message.
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="table-message">Unable to load node inventory</td>
            </tr>
        `;
        nodesError.hidden = false;
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
    };
}

// Submit the add/edit form to the matching CRUD endpoint, then refresh the table.
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

    if (action === "open-web" && node) {
        window.open(`https://${node.host}:${node.web_port}`, "_blank", "noopener");
        return;
    }

    if (action === "ssh" && node) {
        const sshCommand = `ssh ${node.host}`;

        try {
            // Copy the SSH command so the operator can paste it into a terminal quickly.
            await navigator.clipboard.writeText(sshCommand);
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
    loadPlatformStatus();
    loadNodes();

    const nodeForm = document.getElementById("node-form");
    const nodesTableBody = document.getElementById("nodes-table-body");
    const cancelButton = document.getElementById("node-cancel-button");
    const refreshButton = document.getElementById("refresh-nodes-button");

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
});
