// Load live platform status from the backend after the page finishes loading.
async function loadPlatformStatus() {
    const statusFields = {
        app: document.getElementById("status-app"),
        version: document.getElementById("status-version"),
        hostname: document.getElementById("status-hostname"),
        time: document.getElementById("status-time"),
    };
    const statusError = document.getElementById("status-error");

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

// Load the prototype node inventory and render it into the table on the nodes page.
async function loadNodes() {
    const tableBody = document.getElementById("nodes-table-body");
    const nodesError = document.getElementById("nodes-error");

    if (!tableBody) {
        return;
    }

    try {
        // Fetch the node list from the backend API and parse the JSON payload.
        const response = await fetch("/api/nodes");

        if (!response.ok) {
            throw new Error("Node request failed");
        }

        const nodes = await response.json();

        // Replace the loading row with rows built from the returned node inventory.
        tableBody.innerHTML = nodes
            .map(
                (node) => `
                    <tr>
                        <td>${node.name}</td>
                        <td>${node.ip}</td>
                        <td>${node.location}</td>
                        <td><span class="status-badge ${node.status}">${node.status}</span></td>
                    </tr>
                `,
            )
            .join("");
        nodesError.hidden = true;
    } catch (error) {
        // If the fetch fails, keep the table simple and show a clear error message.
        tableBody.innerHTML = `
            <tr>
                <td colspan="4" class="table-message">Unable to load node inventory</td>
            </tr>
        `;
        nodesError.hidden = false;
    }
}

window.addEventListener("DOMContentLoaded", () => {
    loadPlatformStatus();
    loadNodes();
});
