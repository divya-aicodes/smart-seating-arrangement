document.addEventListener("DOMContentLoaded", () => {
    initHeader();
    initMobileNavigation();
    initRevealMotion();
    initSourceMode();
    initDatasetRoomFilter();
    initCapacityPreview();
    initFileInputs();
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

function initFileInputs() {
    document.querySelectorAll("[data-file-input]").forEach(input => {
        const container = input.closest("label") || input.parentElement;
        const label = container?.querySelector("[data-file-name]");
        if (!label) return;

        input.addEventListener("change", () => {
            const file = input.files?.[0];
            label.textContent = file ? `${file.name} · ${formatFileSize(file.size)}` : "No file selected";
        });
    });
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
