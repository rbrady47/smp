const operationalMapState = {
    views: [],
    currentMapId: null,
    currentDetail: null,
    selectedObjectId: null,
    nodeOptions: [],
    dragging: null,
};

function opEscapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

async function opApiRequest(url, options = {}) {
    const response = await fetch(url, {
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });

    if (!response.ok) {
        let message = "Request failed";
        try {
            const payload = await response.json();
            if (payload?.detail) {
                message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
            }
        } catch (error) {
            // Ignore parse errors and use default message.
        }
        throw new Error(message);
    }

    if (response.status === 204) {
        return null;
    }

    return response.json();
}

function setOperationalFeedback(message, isError = false) {
    const feedback = document.getElementById("operational-map-feedback");
    if (!feedback) {
        return;
    }
    if (!message) {
        feedback.hidden = true;
        feedback.textContent = "";
        feedback.classList.remove("is-error");
        return;
    }
    feedback.hidden = false;
    feedback.textContent = message;
    feedback.classList.toggle("is-error", isError);
}

function getCurrentMapView() {
    return operationalMapState.views.find((view) => view.id === operationalMapState.currentMapId) || null;
}

function getCurrentObjects() {
    return Array.isArray(operationalMapState.currentDetail?.objects) ? operationalMapState.currentDetail.objects : [];
}

function getSelectedObject() {
    return getCurrentObjects().find((mapObject) => mapObject.id === operationalMapState.selectedObjectId) || null;
}

function syncMapSelectList() {
    const container = document.getElementById("operational-map-list");
    if (!container) {
        return;
    }
    if (!operationalMapState.views.length) {
        container.innerHTML = `<p class="table-message">No maps yet. Create the first operational map.</p>`;
        return;
    }
    container.innerHTML = operationalMapState.views
        .map((view) => {
            const isActive = view.id === operationalMapState.currentMapId;
            return `
                <button type="button" class="operational-map-list-item ${isActive ? "is-active" : ""}" data-map-view-id="${view.id}">
                    <span class="operational-map-list-name">${opEscapeHtml(view.name)}</span>
                    <span class="operational-map-list-meta">${opEscapeHtml(view.map_type)} · ${opEscapeHtml(view.slug)}</span>
                </button>
            `;
        })
        .join("");
    container.querySelectorAll("[data-map-view-id]").forEach((button) => {
        button.addEventListener("click", () => {
            const mapViewId = Number(button.getAttribute("data-map-view-id"));
            if (Number.isFinite(mapViewId)) {
                void loadOperationalMapDetail(mapViewId);
            }
        });
    });
}

function syncNodeOptions() {
    const select = document.getElementById("operational-object-node-site-id");
    if (!select) {
        return;
    }
    const currentValue = select.value;
    select.innerHTML = `<option value="">Not assigned</option>${operationalMapState.nodeOptions
        .map(
            (option) =>
                `<option value="${opEscapeHtml(option.site_id)}">${opEscapeHtml(option.label)} (${opEscapeHtml(option.kind)})</option>`,
        )
        .join("")}`;
    if (currentValue) {
        select.value = currentValue;
    }
}

function syncSubmapOptions() {
    const select = document.getElementById("operational-object-child-map-id");
    if (!select) {
        return;
    }
    const currentValue = select.value;
    const currentMapId = operationalMapState.currentMapId;
    select.innerHTML = `<option value="">None</option>${operationalMapState.views
        .filter((view) => view.id !== currentMapId)
        .map((view) => `<option value="${view.id}">${opEscapeHtml(view.name)} (${opEscapeHtml(view.slug)})</option>`)
        .join("")}`;
    if (currentValue) {
        select.value = currentValue;
    }
}

async function loadNodeCatalog() {
    const payload = await opApiRequest("/api/node-dashboard");
    const anchors = Array.isArray(payload?.anchors) ? payload.anchors : [];
    const discovered = Array.isArray(payload?.discovered) ? payload.discovered : [];
    operationalMapState.nodeOptions = [
        ...anchors.map((row) => ({
            site_id: String(row.site_id || ""),
            label: `${row.site_name || row.name || row.site_id} · ${row.host || "--"}`,
            kind: "AN",
        })),
        ...discovered.map((row) => ({
            site_id: String(row.site_id || ""),
            label: `${row.site_name || row.site_id} · ${row.host || "--"}`,
            kind: "DN",
        })),
    ].filter((option) => option.site_id);
    syncNodeOptions();
}

function renderOperationalMapStage() {
    const stage = document.getElementById("operational-map-stage");
    const emptyState = document.getElementById("operational-map-empty");
    const title = document.getElementById("operational-map-title");
    const subtitle = document.getElementById("operational-map-subtitle");
    const slug = document.getElementById("operational-map-slug");
    if (!stage || !title || !subtitle || !slug) {
        return;
    }

    const view = getCurrentMapView();
    const objects = getCurrentObjects();
    if (!view || !operationalMapState.currentDetail) {
        title.textContent = "No map selected";
        subtitle.textContent = "Create or choose a map to start authoring.";
        slug.textContent = "--";
        if (emptyState) {
            emptyState.hidden = false;
        }
        return;
    }

    title.textContent = view.name;
    subtitle.textContent = view.parent_map_id ? "Submap canvas with authored drill-in workflow." : "Blank authored canvas for the operational picture.";
    slug.textContent = view.slug;
    stage.style.width = `${view.canvas_width}px`;
    stage.style.minHeight = `${view.canvas_height}px`;

    stage.querySelectorAll(".operational-map-object").forEach((element) => element.remove());
    if (emptyState) {
        emptyState.hidden = objects.length > 0;
    }

    objects.forEach((mapObject) => {
        const element = document.createElement("button");
        element.type = "button";
        element.className = `operational-map-object operational-map-object-kind-${mapObject.object_type}${mapObject.id === operationalMapState.selectedObjectId ? " is-selected" : ""}`;
        element.setAttribute("data-object-id", String(mapObject.id));
        element.style.left = `${mapObject.x}px`;
        element.style.top = `${mapObject.y}px`;
        element.style.width = `${mapObject.width}px`;
        element.style.height = `${mapObject.height}px`;
        element.style.zIndex = String(mapObject.z_index);
        const subtitleText = mapObject.object_type === "node"
            ? mapObject.node_site_id || "Assign node ID"
            : mapObject.object_type === "submap"
                ? "Drill-in"
                : "Free label";
        element.innerHTML = `
            <span class="operational-map-object-type">${opEscapeHtml(mapObject.object_type.toUpperCase())}</span>
            <span class="operational-map-object-label">${opEscapeHtml(mapObject.label || "Untitled object")}</span>
            <span class="operational-map-object-sub">${opEscapeHtml(subtitleText)}</span>
        `;
        element.addEventListener("click", (event) => {
            event.stopPropagation();
            operationalMapState.selectedObjectId = mapObject.id;
            syncInspector();
            renderOperationalMapStage();
        });
        if (mapObject.object_type === "submap" && mapObject.child_map_view_id) {
            element.addEventListener("dblclick", (event) => {
                event.stopPropagation();
                void loadOperationalMapDetail(mapObject.child_map_view_id).catch((error) =>
                    setOperationalFeedback(error.message || "Unable to load child map.", true),
                );
            });
        }
        stage.appendChild(element);
    });
}

function syncInspector() {
    const form = document.getElementById("operational-object-form");
    const empty = document.getElementById("operational-inspector-empty");
    const label = document.getElementById("operational-object-label");
    const type = document.getElementById("operational-object-type");
    const nodeSiteId = document.getElementById("operational-object-node-site-id");
    const childMapId = document.getElementById("operational-object-child-map-id");
    const width = document.getElementById("operational-object-width");
    const height = document.getElementById("operational-object-height");
    const deleteButton = document.getElementById("operational-object-delete-button");
    const selected = getSelectedObject();

    if (!form || !empty || !label || !type || !nodeSiteId || !childMapId || !width || !height || !deleteButton) {
        return;
    }

    syncNodeOptions();
    syncSubmapOptions();

    if (!selected) {
        form.hidden = true;
        empty.hidden = false;
        label.value = "";
        type.value = "";
        nodeSiteId.value = "";
        childMapId.value = "";
        width.value = "";
        height.value = "";
        deleteButton.disabled = true;
        return;
    }

    form.hidden = false;
    empty.hidden = true;
    label.value = selected.label || "";
    type.value = selected.object_type;
    nodeSiteId.value = selected.node_site_id || "";
    childMapId.value = selected.child_map_view_id ? String(selected.child_map_view_id) : "";
    width.value = String(selected.width);
    height.value = String(selected.height);
    nodeSiteId.disabled = selected.object_type !== "node";
    childMapId.disabled = selected.object_type !== "submap";
    deleteButton.disabled = false;
}

async function loadOperationalMapViews() {
    operationalMapState.views = await opApiRequest("/api/operational-maps/views");
    syncMapSelectList();
    syncSubmapOptions();
}

async function loadOperationalMapDetail(mapViewId) {
    operationalMapState.currentDetail = await opApiRequest(`/api/operational-maps/views/${mapViewId}`);
    operationalMapState.currentMapId = mapViewId;
    operationalMapState.selectedObjectId = null;
    const url = new URL(window.location.href);
    url.searchParams.set("map", String(mapViewId));
    window.history.replaceState({ map: mapViewId }, "", url);
    syncMapSelectList();
    syncInspector();
    renderOperationalMapStage();
}

async function ensureOperationalMapLoaded() {
    await loadOperationalMapViews();
    if (!operationalMapState.views.length) {
        const created = await opApiRequest("/api/operational-maps/views", {
            method: "POST",
            body: JSON.stringify({
                name: "Global Operational Map",
                slug: "global-operational-map",
                map_type: "global",
            }),
        });
        await loadOperationalMapViews();
        await loadOperationalMapDetail(created.id);
        setOperationalFeedback("Created the first operational map.");
        return;
    }

    const url = new URL(window.location.href);
    const requestedMapId = Number(url.searchParams.get("map"));
    const firstView = operationalMapState.views[0];
    const nextMapId = Number.isFinite(requestedMapId) && operationalMapState.views.some((view) => view.id === requestedMapId)
        ? requestedMapId
        : firstView.id;
    await loadOperationalMapDetail(nextMapId);
}

async function createOperationalMap() {
    const count = operationalMapState.views.length + 1;
    const name = `Operational Map ${count}`;
    const slug = `operational-map-${count}`;
    const created = await opApiRequest("/api/operational-maps/views", {
        method: "POST",
        body: JSON.stringify({
            name,
            slug,
            map_type: "custom",
        }),
    });
    await loadOperationalMapViews();
    await loadOperationalMapDetail(created.id);
    setOperationalFeedback(`Created ${name}.`);
}

async function createOperationalSubmap() {
    const parent = getCurrentMapView();
    if (!parent) {
        setOperationalFeedback("Select a parent map first.", true);
        return;
    }
    const count = operationalMapState.views.length + 1;
    const name = `${parent.name} Submap ${count}`;
    const slug = `${parent.slug}-submap-${count}`;
    const created = await opApiRequest("/api/operational-maps/views", {
        method: "POST",
        body: JSON.stringify({
            name,
            slug,
            map_type: "unit",
            parent_map_id: parent.id,
        }),
    });
    await loadOperationalMapViews();
    await loadOperationalMapDetail(created.id);
    setOperationalFeedback(`Created ${name}.`);
}

async function createOperationalObject(objectType) {
    const view = getCurrentMapView();
    if (!view) {
        setOperationalFeedback("Create or load a map first.", true);
        return;
    }
    const objects = getCurrentObjects();
    const payload = {
        map_view_id: view.id,
        object_type: objectType,
        label: objectType === "label" ? "Label" : objectType === "submap" ? "Submap" : "Node",
        x: 72 + objects.length * 24,
        y: 96 + objects.length * 24,
        width: objectType === "label" ? 200 : 168,
        height: objectType === "label" ? 72 : 104,
        z_index: objects.length + 1,
        connection_points: objectType === "label" ? [] : ["north", "east", "south", "west"],
        style: {},
    };

    if (objectType === "node") {
        const firstNode = operationalMapState.nodeOptions[0];
        if (firstNode) {
            payload.node_site_id = firstNode.site_id;
        }
    }

    if (objectType === "submap") {
        const candidate = operationalMapState.views.find((mapView) => mapView.id !== view.id);
        if (!candidate) {
            setOperationalFeedback("Create another map first so the submap can drill into it.", true);
            return;
        }
        payload.child_map_view_id = candidate.id;
    }

    const created = await opApiRequest("/api/operational-maps/objects", {
        method: "POST",
        body: JSON.stringify(payload),
    });
    operationalMapState.selectedObjectId = created.id;
    await loadOperationalMapDetail(view.id);
    operationalMapState.selectedObjectId = created.id;
    syncInspector();
    renderOperationalMapStage();
    setOperationalFeedback(`Added ${objectType} object.`);
}

async function saveSelectedObject(event) {
    event.preventDefault();
    const selected = getSelectedObject();
    if (!selected) {
        return;
    }
    const label = document.getElementById("operational-object-label");
    const nodeSiteId = document.getElementById("operational-object-node-site-id");
    const childMapId = document.getElementById("operational-object-child-map-id");
    const width = document.getElementById("operational-object-width");
    const height = document.getElementById("operational-object-height");
    const payload = {
        label: label?.value.trim() || null,
        width: Number(width?.value || selected.width),
        height: Number(height?.value || selected.height),
    };

    if (selected.object_type === "node") {
        payload.node_site_id = nodeSiteId?.value || null;
    }
    if (selected.object_type === "submap") {
        payload.child_map_view_id = childMapId?.value ? Number(childMapId.value) : null;
    }

    await opApiRequest(`/api/operational-maps/objects/${selected.id}`, {
        method: "PUT",
        body: JSON.stringify(payload),
    });
    await loadOperationalMapDetail(operationalMapState.currentMapId);
    operationalMapState.selectedObjectId = selected.id;
    syncInspector();
    renderOperationalMapStage();
    setOperationalFeedback("Object updated.");
}

async function deleteSelectedObject() {
    const selected = getSelectedObject();
    const currentMapId = operationalMapState.currentMapId;
    if (!selected || !currentMapId) {
        return;
    }
    await opApiRequest(`/api/operational-maps/objects/${selected.id}`, { method: "DELETE" });
    operationalMapState.selectedObjectId = null;
    await loadOperationalMapDetail(currentMapId);
    syncInspector();
    renderOperationalMapStage();
    setOperationalFeedback("Object deleted.");
}

function bindStageDragging() {
    const stage = document.getElementById("operational-map-stage");
    if (!stage || stage.dataset.dragBound === "true") {
        return;
    }
    stage.dataset.dragBound = "true";

    const onPointerMove = (event) => {
        const drag = operationalMapState.dragging;
        if (!drag) {
            return;
        }
        const nextX = Math.max(0, Math.round(drag.startX + (event.clientX - drag.pointerStartX)));
        const nextY = Math.max(0, Math.round(drag.startY + (event.clientY - drag.pointerStartY)));
        drag.element.style.left = `${nextX}px`;
        drag.element.style.top = `${nextY}px`;
    };

    const onPointerUp = async (event) => {
        const drag = operationalMapState.dragging;
        if (!drag) {
            return;
        }
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        operationalMapState.dragging = null;

        const nextX = Math.max(0, Math.round(drag.startX + (event.clientX - drag.pointerStartX)));
        const nextY = Math.max(0, Math.round(drag.startY + (event.clientY - drag.pointerStartY)));

        try {
            await opApiRequest(`/api/operational-maps/objects/${drag.objectId}`, {
                method: "PUT",
                body: JSON.stringify({ x: nextX, y: nextY }),
            });
            await loadOperationalMapDetail(operationalMapState.currentMapId);
            operationalMapState.selectedObjectId = drag.objectId;
            syncInspector();
            renderOperationalMapStage();
        } catch (error) {
            setOperationalFeedback(error.message || "Unable to save object position.", true);
        }
    };

    stage.addEventListener("pointerdown", (event) => {
        const target = event.target instanceof Element ? event.target.closest(".operational-map-object") : null;
        if (!(target instanceof HTMLElement)) {
            operationalMapState.selectedObjectId = null;
            syncInspector();
            renderOperationalMapStage();
            return;
        }

        const objectId = Number(target.getAttribute("data-object-id"));
        const selected = getCurrentObjects().find((mapObject) => mapObject.id === objectId);
        if (!selected) {
            return;
        }

        operationalMapState.selectedObjectId = objectId;
        syncInspector();
        renderOperationalMapStage();
        operationalMapState.dragging = {
            objectId,
            element: target,
            pointerStartX: event.clientX,
            pointerStartY: event.clientY,
            startX: selected.x,
            startY: selected.y,
        };
        window.addEventListener("pointermove", onPointerMove);
        window.addEventListener("pointerup", onPointerUp, { once: true });
        event.preventDefault();
    });
}

function bindOperationalMapControls() {
    document.getElementById("operational-map-new-map-button")?.addEventListener("click", () => {
        void createOperationalMap().catch((error) => setOperationalFeedback(error.message || "Unable to create map.", true));
    });
    document.getElementById("operational-map-new-submap-button")?.addEventListener("click", () => {
        void createOperationalSubmap().catch((error) => setOperationalFeedback(error.message || "Unable to create submap.", true));
    });
    document.getElementById("operational-map-back-button")?.addEventListener("click", () => {
        const currentView = getCurrentMapView();
        if (currentView?.parent_map_id) {
            void loadOperationalMapDetail(currentView.parent_map_id).catch((error) =>
                setOperationalFeedback(error.message || "Unable to load parent map.", true),
            );
            return;
        }
        window.history.back();
    });
    document.querySelectorAll("[data-create-object]").forEach((button) => {
        button.addEventListener("click", () => {
            void createOperationalObject(button.getAttribute("data-create-object")).catch((error) =>
                setOperationalFeedback(error.message || "Unable to add map object.", true),
            );
        });
    });
    document.getElementById("operational-object-form")?.addEventListener("submit", (event) => {
        void saveSelectedObject(event).catch((error) => setOperationalFeedback(error.message || "Unable to save object.", true));
    });
    document.getElementById("operational-object-delete-button")?.addEventListener("click", () => {
        void deleteSelectedObject().catch((error) => setOperationalFeedback(error.message || "Unable to delete object.", true));
    });
}

async function loadOperationalMapsPage() {
    if (!document.getElementById("operational-maps-root")) {
        return;
    }
    bindOperationalMapControls();
    bindStageDragging();
    await loadNodeCatalog();
    await ensureOperationalMapLoaded();
    syncInspector();
    renderOperationalMapStage();
}

window.addEventListener("DOMContentLoaded", () => {
    void loadOperationalMapsPage().catch((error) => {
        setOperationalFeedback(error.message || "Unable to load operational maps.", true);
    });
});
