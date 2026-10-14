/**
 * Examination Seating Planner - interface behavior
 */

document.addEventListener("DOMContentLoaded", () => {
    initDataMode();
    initDatasetRoomFilter();
    initRoomPreview();
    initColorCodingToggle();
    initTableSearch();
});

function initDataMode() {
    const mode = document.getElementById("data_mode");
    const databaseFields = document.getElementById("database-source-fields");
    const manualFields = document.getElementById("manual-source-fields");
    if (!mode || !databaseFields || !manualFields) return;

    const update = () => {
        const useDatabase = mode.value === "database";
        databaseFields.hidden = !useDatabase;
        manualFields.hidden = useDatabase;
    };
    mode.addEventListener("change", update);
    update();
}

function initDatasetRoomFilter() {
    const examSelect = document.getElementById("exam_id");
    const roomSelect = document.getElementById("room_ids");
    if (!examSelect || !roomSelect) return;

    const updateRooms = () => {
        const selectedExam = examSelect.options[examSelect.selectedIndex];
        const datasetId = selectedExam?.dataset.datasetId || "";
        let firstMatchingRoom = null;

        Array.from(roomSelect.options).forEach(option => {
            const matches = Boolean(datasetId) && option.dataset.datasetId === datasetId;
            option.disabled = !matches;
            option.hidden = !matches;
            if (!matches) option.selected = false;
            if (matches && firstMatchingRoom === null) firstMatchingRoom = option;
        });

        const selectedMatchingRooms = Array.from(roomSelect.selectedOptions);
        if (!selectedMatchingRooms.length && firstMatchingRoom) {
            firstMatchingRoom.selected = true;
        }
    };

    examSelect.addEventListener("change", updateRooms);
    updateRooms();
}

function initRoomPreview() {
    const inputs = ["num_rooms", "rows", "cols", "seats_per_bench"];
    inputs.forEach(id => {
        const el = document.querySelector(`[name='${id}']`);
        if (el) {
            el.addEventListener("input", updateCapacityPreview);
            el.addEventListener("change", updateCapacityPreview);
        }
    });
    updateCapacityPreview();
}

function updateCapacityPreview() {
    const numRoomsEl = document.querySelector("[name='num_rooms']");
    const rowsEl = document.querySelector("[name='rows']");
    const colsEl = document.querySelector("[name='cols']");
    const seatsEl = document.querySelector("[name='seats_per_bench']");

    if (!numRoomsEl || !rowsEl || !colsEl || !seatsEl) return;

    const rooms = parseInt(numRoomsEl.value) || 1;
    const rows = parseInt(rowsEl.value) || 1;
    const cols = parseInt(colsEl.value) || 1;
    const seatsPerBench = parseInt(seatsEl.value) || 1;

    const seatsPerRoom = rows * cols * seatsPerBench;
    const totalCapacity = rooms * seatsPerRoom;
    const previewEl = document.getElementById("capacity-preview");
    if (previewEl) {
        previewEl.innerHTML = `
            <strong>Physical capacity</strong>
            <span>${rooms} ${rooms === 1 ? "room" : "rooms"}</span>
            <span>${seatsPerRoom} seats per room</span>
            <span>${totalCapacity} seats in total</span>
        `;
    }
}

function initColorCodingToggle() {
    const toggleBtns = document.querySelectorAll(".view-toggle-btn");
    if (!toggleBtns.length) return;

    toggleBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const mode = btn.dataset.mode; // 'class' or 'subject'
            toggleBtns.forEach(b => {
                b.classList.remove("btn-primary");
                b.classList.add("btn-outline");
            });
            btn.classList.add("btn-primary");
            btn.classList.remove("btn-outline");

            const seatBadges = document.querySelectorAll(".student-badge-display");
            seatBadges.forEach(badge => {
                if (mode === "subject") {
                    badge.textContent = badge.dataset.subject;
                    badge.className = `student-class-badge student-badge-display color-class-${badge.dataset.subjectColorIdx}`;
                } else {
                    badge.textContent = badge.dataset.class;
                    badge.className = `student-class-badge student-badge-display color-class-${badge.dataset.classColorIdx}`;
                }
            });
        });
    });
}

function initTableSearch() {
    const searchInput = document.getElementById("allocation-search");
    const table = document.getElementById("allocation-table");
    if (!searchInput || !table) return;

    searchInput.addEventListener("input", () => {
        const query = searchInput.value.toLowerCase().trim();
        const rows = table.querySelectorAll("tbody tr");

        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(query) ? "" : "none";
        });
    });
}
