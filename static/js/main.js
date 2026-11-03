document.addEventListener("DOMContentLoaded", () => {
    initHeader();
    initMobileNavigation();
    initRevealMotion();
    initSourceMode();
    initDatasetRoomFilter();
    initCapacityPreview();
    initFileInputs();
    initLayoutRecommendations();
    initTableSearch();
    initSeatLabelToggle();
    initSubmitState();
});

function initHeader() {
    const header = document.querySelector("[data-site-header]");
    if (!header) return;

    const update = () => header.classList.toggle("is-scrolled", window.scrollY > 12);
    window.addEventListener("scroll", update, { passive: true });
    update();
}

function initMobileNavigation() {
    const button = document.querySelector("[data-nav-toggle]");
    const navigation = document.querySelector("[data-primary-nav]");
    if (!button || !navigation) return;

    const close = () => {
        button.setAttribute("aria-expanded", "false");
        navigation.classList.remove("is-open");
    };

    button.addEventListener("click", () => {
        const open = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!open));
        navigation.classList.toggle("is-open", !open);
    });

    navigation.addEventListener("click", event => {
        if (event.target.closest("a")) close();
    });

    document.addEventListener("click", event => {
        if (!navigation.contains(event.target) && !button.contains(event.target)) close();
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") close();
    });
}

function initRevealMotion() {
    const elements = Array.from(document.querySelectorAll(".reveal"));
    if (!elements.length) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        elements.forEach(element => element.classList.add("is-visible"));
        return;
    }

    elements.forEach((element, index) => {
        element.style.animationDelay = `${Math.min(index % 4, 3) * 45}ms`;
    });
    requestAnimationFrame(() => elements.forEach(element => element.classList.add("is-visible")));
}

function setSectionEnabled(section, enabled) {
    if (!section) return;
    section.hidden = !enabled;
    section.querySelectorAll("input, select, textarea, button").forEach(control => {
        control.disabled = !enabled;
    });
}

function initSourceMode() {
    const options = Array.from(document.querySelectorAll("input[name='data_mode']"));
    const databaseFields = document.getElementById("database-source-fields");
    const manualFields = document.getElementById("manual-source-fields");
    if (!options.length || !databaseFields || !manualFields) return;

    const update = () => {
        const selected = options.find(option => option.checked)?.value || "manual";
        setSectionEnabled(databaseFields, selected === "database");
        setSectionEnabled(manualFields, selected === "manual");
    };

    options.forEach(option => option.addEventListener("change", update));
    update();
}

function initDatasetRoomFilter() {
    const examSelect = document.getElementById("exam_id");
    const roomSelect = document.getElementById("room_ids");
    if (!examSelect || !roomSelect) return;

    const update = () => {
        const examOption = examSelect.options[examSelect.selectedIndex];
        const datasetId = examOption?.dataset.datasetId || "";
        let firstMatch = null;

        Array.from(roomSelect.options).forEach(option => {
            const matches = Boolean(datasetId) && option.dataset.datasetId === datasetId;
            option.disabled = !matches;
            option.hidden = !matches;
            if (!matches) option.selected = false;
            if (matches && !firstMatch) firstMatch = option;
        });

        if (!Array.from(roomSelect.selectedOptions).length && firstMatch) firstMatch.selected = true;
    };

    examSelect.addEventListener("change", update);
    update();
}

function initCapacityPreview() {
    const fieldNames = ["num_rooms", "rows", "cols", "seats_per_bench"];
    const fields = fieldNames.map(name => document.querySelector(`[name='${name}']`));
    const preview = document.getElementById("capacity-preview");
    if (!preview || fields.some(field => !field)) return;

    const update = () => {
        const [roomsField, rowsField, columnsField, seatsField] = fields;
        const rooms = Math.max(Number.parseInt(roomsField.value, 10) || 1, 1);
        const rows = Math.max(Number.parseInt(rowsField.value, 10) || 1, 1);
        const columns = Math.max(Number.parseInt(columnsField.value, 10) || 1, 1);
        const seatsPerBench = Math.max(Number.parseInt(seatsField.value, 10) || 1, 1);
        const seatsPerRoom = rows * columns * seatsPerBench;
        const total = rooms * seatsPerRoom;

        preview.replaceChildren(
            createCapacityItem("Physical capacity", true),
            createCapacityItem(`${rooms} ${rooms === 1 ? "room" : "rooms"}`),
            createCapacityItem(`${seatsPerRoom} seats per room`),
            createCapacityItem(`${total} seats total`)
        );
    };

    fields.forEach(field => {
        field.addEventListener("input", update);
        field.addEventListener("change", update);
    });
    update();
}

function createCapacityItem(text, strong = false) {
    const element = document.createElement(strong ? "strong" : "span");
    element.textContent = text;
    return element;
}

function initLayoutRecommendations() {
    const workspace = document.querySelector("[data-layout-recommendations]");
    const csvInput = document.getElementById("csv_file");
    const textInput = document.getElementById("student_data_text");
    const accessibility = document.getElementById("accessible_rule");
    const adjacencyInputs = Array.from(document.querySelectorAll("input[name='adjacency_rule']"));
    if (!workspace || !csvInput || !textInput || !accessibility || !adjacencyInputs.length) return;

    const empty = workspace.querySelector("[data-recommendation-empty]");
    const loading = workspace.querySelector("[data-recommendation-loading]");
    const errorPanel = workspace.querySelector("[data-recommendation-error]");
    const results = workspace.querySelector("[data-recommendation-results]");
    const summary = workspace.querySelector("[data-recommendation-summary]");
    const list = workspace.querySelector("[data-recommendation-list]");
    const csrfToken = document.querySelector("input[name='csrf_token']")?.value || "";
    let debounceTimer;
    let activeRequest;

    const show = target => {
        [empty, loading, errorPanel, results].forEach(panel => {
            if (panel) panel.hidden = panel !== target;
        });
    };

    const hasRecords = () => Boolean(csvInput.files?.[0] || textInput.value.trim());

    const calculate = async () => {
        window.clearTimeout(debounceTimer);
        if (!hasRecords()) {
            activeRequest?.abort();
            list.replaceChildren();
            show(empty);
            return;
        }

        activeRequest?.abort();
        activeRequest = new AbortController();
        show(loading);

        const formData = new FormData();
        const file = csvInput.files?.[0];
        if (file) formData.append("csv_file", file);
        else formData.append("student_data_text", textInput.value);
        formData.append("csrf_token", csrfToken);
        formData.append("accessible_rule", accessibility.value);
        formData.append(
            "adjacency_rule",
            adjacencyInputs.find(input => input.checked)?.value || "orthogonal"
        );

        try {
            const response = await fetch(workspace.dataset.endpoint, {
                method: "POST",
                body: formData,
                credentials: "same-origin",
                headers: { "X-CSRF-Token": csrfToken },
                signal: activeRequest.signal
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error?.message || "Room options could not be calculated.");
            renderLayoutRecommendations(payload, summary, list);
            show(results);
        } catch (error) {
            if (error.name === "AbortError") return;
            errorPanel.textContent = error.message || "Room options could not be calculated.";
            show(errorPanel);
        }
    };

    const schedule = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(calculate, 350);
    };

    csvInput.addEventListener("change", calculate);
    textInput.addEventListener("input", schedule);
    accessibility.addEventListener("change", calculate);
    adjacencyInputs.forEach(input => input.addEventListener("change", calculate));
}

function renderLayoutRecommendations(payload, summary, list) {
    summary.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = `${payload.student_count} students analysed`;
    const detail = document.createElement("span");
    detail.textContent = `Largest class: ${payload.largest_class} · Accessibility requests: ${payload.accessibility_count}`;
    summary.append(heading, detail);

    const cards = payload.recommendations.map(item => {
        const card = document.createElement("article");
        card.className = "layout-choice-card";

        const label = document.createElement("span");
        label.className = "layout-choice-label";
        label.textContent = `${item.seats_per_bench}-seat shared bench option`;

        const title = document.createElement("h3");
        title.textContent = `${item.rooms} ${item.rooms === 1 ? "room" : "rooms"} · ${item.rows} rows · ${item.benches} benches per row`;

        const formula = document.createElement("p");
        formula.className = "layout-choice-formula";
        formula.textContent = `${item.rooms} × ${item.rows} × ${item.benches} × ${item.seats_per_bench} = ${item.capacity} seats`;

        const explanation = document.createElement("p");
        explanation.className = "layout-choice-explanation";
        explanation.textContent = `If you choose this setup, ${payload.student_count} students are arranged across ${item.rooms} room${item.rooms === 1 ? "" : "s"}, leaving ${item.spare_seats} spare seat${item.spare_seats === 1 ? "" : "s"}. It provides ${item.accessible_seats} permitted accessible positions and a same-class separation bound of ${item.independence_bound}.`;

        const button = document.createElement("button");
        button.className = "button button-secondary layout-choice-button";
        button.type = "button";
        button.textContent = "Use this room setup";
        button.addEventListener("click", () => applyLayoutRecommendation(item, card, list));

        card.append(label, title, formula, explanation, button);
        return card;
    });
    list.replaceChildren(...cards);
}

function applyLayoutRecommendation(item, selectedCard, list) {
    const values = {
        num_rooms: item.rooms,
        rows: item.rows,
        cols: item.benches,
        seats_per_bench: item.seats_per_bench
    };

    Object.entries(values).forEach(([name, value]) => {
        const field = document.querySelector(`[name='${name}']`);
        if (!field) return;
        field.value = String(value);
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
    });

    list.querySelectorAll(".layout-choice-card").forEach(card => {
        const selected = card === selectedCard;
        card.classList.toggle("is-selected", selected);
        const button = card.querySelector(".layout-choice-button");
        if (button) button.textContent = selected ? "Selected setup" : "Use this room setup";
    });
}

function initFileInputs() {
    document.querySelectorAll("[data-file-input]").forEach(input => {
        const control = input.closest("[data-file-control]") || input.closest("label") || input.parentElement;
        const fileName = control?.querySelector("[data-file-name]");
        const action = control?.querySelector("[data-file-action]");
        if (!control || !fileName) return;

        const preview = createFilePreview(control);
        const update = async () => {
            const file = input.files?.[0];
            input.setCustomValidity("");
            control.classList.toggle("is-selected", Boolean(file));

            if (!file) {
                fileName.textContent = "No file selected";
                fileName.removeAttribute("title");
                if (action) action.textContent = control.classList.contains("file-drop") ? "Choose a student CSV" : "Choose file";
                resetFilePreview(preview);
                return;
            }

            fileName.textContent = `${file.name} · ${formatFileSize(file.size)}`;
            fileName.title = file.name;
            if (action) action.textContent = "Change file";

            if (!/\.csv$/i.test(file.name)) {
                const message = "Choose a file with the .csv extension.";
                input.setCustomValidity(message);
                showFilePreviewError(preview, message);
                return;
            }
            if (file.size > 2 * 1024 * 1024) {
                const message = "This file is larger than the 2 MiB upload limit.";
                input.setCustomValidity(message);
                showFilePreviewError(preview, message);
                return;
            }

            await renderCsvPreview(input, file, preview);
        };

        input.addEventListener("change", update);
        input.addEventListener("input", update);
        update();
    });
}

function createFilePreview(control) {
    const preview = document.createElement("section");
    preview.className = "csv-file-preview";
    preview.hidden = true;
    preview.setAttribute("aria-live", "polite");

    const summary = document.createElement("div");
    summary.className = "csv-preview-summary";
    const title = document.createElement("strong");
    title.dataset.csvPreviewTitle = "";
    const meta = document.createElement("span");
    meta.dataset.csvPreviewMeta = "";
    summary.append(title, meta);

    const tableWrap = document.createElement("div");
    tableWrap.className = "csv-preview-table-wrap";
    const table = document.createElement("table");
    table.className = "csv-preview-table";
    const head = document.createElement("thead");
    head.dataset.csvPreviewHead = "";
    const body = document.createElement("tbody");
    body.dataset.csvPreviewBody = "";
    table.append(head, body);
    tableWrap.append(table);

    const message = document.createElement("p");
    message.className = "csv-preview-message";
    message.dataset.csvPreviewMessage = "";

    preview.append(summary, tableWrap, message);
    control.insertAdjacentElement("afterend", preview);
    return preview;
}

function resetFilePreview(preview) {
    preview.hidden = true;
    preview.classList.remove("has-error");
    preview.querySelector("[data-csv-preview-head]").replaceChildren();
    preview.querySelector("[data-csv-preview-body]").replaceChildren();
    preview.querySelector("[data-csv-preview-message]").textContent = "";
}

function showFilePreviewError(preview, message) {
    resetFilePreview(preview);
    preview.hidden = false;
    preview.classList.add("has-error");
    preview.querySelector("[data-csv-preview-title]").textContent = "CSV cannot be uploaded";
    preview.querySelector("[data-csv-preview-meta]").textContent = "Check the file and choose another.";
    preview.querySelector("[data-csv-preview-message]").textContent = message;
}

async function renderCsvPreview(input, file, preview) {
    try {
        const text = (await file.text()).replace(/^\uFEFF/, "");
        if (input.files?.[0] !== file) return;

        const rows = parseCsvPreviewRows(text).filter(row => row.some(cell => cell.trim()));
        if (!rows.length || !rows[0].some(cell => cell.trim())) {
            const message = "The selected CSV is empty or has no header row.";
            input.setCustomValidity(message);
            showFilePreviewError(preview, message);
            return;
        }

        const header = rows[0];
        const records = rows.slice(1);
        const visibleColumns = header.slice(0, 8);
        const visibleRecords = records.slice(0, 5);
        const head = preview.querySelector("[data-csv-preview-head]");
        const body = preview.querySelector("[data-csv-preview-body]");
        const headingRow = document.createElement("tr");

        visibleColumns.forEach(column => {
            const cell = document.createElement("th");
            cell.textContent = column || "Unnamed column";
            headingRow.append(cell);
        });
        head.replaceChildren(headingRow);

        const renderedRows = visibleRecords.map(record => {
            const row = document.createElement("tr");
            visibleColumns.forEach((_, index) => {
                const cell = document.createElement("td");
                cell.textContent = record[index] || "—";
                row.append(cell);
            });
            return row;
        });
        body.replaceChildren(...renderedRows);

        preview.hidden = false;
        preview.classList.remove("has-error");
        preview.querySelector("[data-csv-preview-title]").textContent = "CSV ready to upload";
        preview.querySelector("[data-csv-preview-meta]").textContent = `${records.length} data ${records.length === 1 ? "row" : "rows"} · ${header.length} ${header.length === 1 ? "column" : "columns"}`;
        preview.querySelector("[data-csv-preview-message]").textContent = records.length > 5
            ? `Showing the first 5 of ${records.length} rows. The server validates every row.`
            : "Preview complete. The server validates every row before saving.";
    } catch (error) {
        const message = "The browser could not read this file. Choose the CSV again.";
        input.setCustomValidity(message);
        showFilePreviewError(preview, message);
    }
}

function parseCsvPreviewRows(text) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;

    for (let index = 0; index < text.length; index += 1) {
        const character = text[index];
        if (character === '"') {
            if (quoted && text[index + 1] === '"') {
                field += '"';
                index += 1;
            } else {
                quoted = !quoted;
            }
        } else if (character === "," && !quoted) {
            row.push(field);
            field = "";
        } else if ((character === "\n" || character === "\r") && !quoted) {
            if (character === "\r" && text[index + 1] === "\n") index += 1;
            row.push(field);
            rows.push(row);
            row = [];
            field = "";
        } else {
            field += character;
        }
    }

    if (field.length || row.length) {
        row.push(field);
        rows.push(row);
    }
    return rows;
}

function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function initTableSearch() {
    document.querySelectorAll("[data-table-search]").forEach(input => {
        const table = document.getElementById(input.dataset.tableSearch);
        if (table) bindTableSearch(input, table);
    });

    const allocationSearch = document.getElementById("allocation-search");
    const allocationTable = document.getElementById("allocation-table");
    if (allocationSearch && allocationTable) bindTableSearch(allocationSearch, allocationTable);
}

function bindTableSearch(input, table) {
    input.addEventListener("input", () => {
        const query = input.value.trim().toLocaleLowerCase();
        table.querySelectorAll("tbody tr").forEach(row => {
            row.hidden = Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);
        });
    });
}

function initSeatLabelToggle() {
    const buttons = Array.from(document.querySelectorAll(".view-toggle-btn"));
    if (!buttons.length) return;

    buttons.forEach(button => {
        button.addEventListener("click", () => {
            const mode = button.dataset.mode;
            buttons.forEach(item => item.classList.toggle("is-active", item === button));

            document.querySelectorAll(".student-badge-display").forEach(badge => {
                const value = mode === "subject" ? badge.dataset.subject : badge.dataset.class;
                const color = mode === "subject" ? badge.dataset.subjectColorIdx : badge.dataset.classColorIdx;
                badge.textContent = value;
                badge.className = `student-badge student-badge-display tone-${color}`;
            });
        });
    });
}

function initSubmitState() {
    const form = document.getElementById("seating-form");
    const button = form?.querySelector("[data-submit-button]");
    if (!form || !button) return;

    form.addEventListener("submit", () => {
        if (!form.checkValidity()) return;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        const label = button.querySelector("span");
        if (label) label.textContent = "Generating and validating…";
    });
}
