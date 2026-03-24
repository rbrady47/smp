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

function statusBadge(status, latencyMs) {
    const latencyText = typeof latencyMs === "number" ? ` (${latencyMs} ms)` : "";
    return `<span class="status-badge ${status}">${status}${latencyText}</span>`;
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
                <tr>
                    <td>${node.name}</td>
                    <td>${node.host}</td>
                    <td>${node.web_port}</td>
                    <td>${node.ssh_port}</td>
                    <td>${node.location}</td>
                    <td>${statusBadge(node.status, node.latency_ms)}</td>
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
        currentNodes = await apiRequest("/api/nodes");
        renderNodesTable(currentNodes);
        nodesError.hidden = true;
    } catch (error) {
        // If the fetch fails, keep the table simple and show a clear error message.
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="table-message">Unable to load node inventory</td>
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
    } catch (error) {
        formError.textContent = error.message || "Unable to save node";
        formError.hidden = false;
    }
}

async function handleNodeActionClick(event) {
    const target = event.target;

    if (!(target instanceof HTMLElement)) {
        return;
    }

    const action = target.dataset.action;
    const nodeId = Number(target.dataset.id);

    if (action === "edit") {
        populateNodeForm(nodeId);
        return;
    }

    if (action === "delete") {
        try {
            await apiRequest(`/api/nodes/${nodeId}`, { method: "DELETE" });
            resetNodeForm();
            await loadNodes();
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
});
