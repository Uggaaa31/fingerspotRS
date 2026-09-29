/**
 * app.js — Client-Side Logic untuk Live Monitoring Absensi Fingerspot RSUP
 * Mendukung: Live Feed ADMS, Rekap Harian SatuTalenta RSUP, Saring Tap Dobel, dan Kelola Pegawai
 */

// Konfigurasi API
const API_BASE = window.location.origin.includes("5005") || window.location.origin.includes("8000")
    ? window.location.origin
    : "http://localhost:5005";

const POLL_INTERVAL_MS = 2500; // Polling setiap 2.5 detik

// State aplikasi
let allRecords = [];
let lastKnownMaxId = null;
let soundEnabled = true;
let activeFilter = "all";
let searchQuery = "";
let isFetching = false;
let deduplicateEnabled = true;
let currentView = "daily"; // "daily" (default) atau "live"

// DOM Elements: Live Log
const tableBody = document.getElementById("attendanceTableBody");
const emptyState = document.getElementById("emptyState");
const searchInput = document.getElementById("searchInput");
const filterButtons = document.querySelectorAll(".filter-btn");
const soundToggleBtn = document.getElementById("soundToggleBtn");
const soundIcon = document.getElementById("soundIcon");
const refreshBtn = document.getElementById("refreshBtn");
const digitalClock = document.getElementById("digitalClock");
const recordsCounter = document.getElementById("recordsCounter");
const apiBaseUrlText = document.getElementById("apiBaseUrlText");

// Stats Elements
const statTotalToday = document.getElementById("statTotalToday");
const statCheckinToday = document.getElementById("statCheckinToday");
const statCheckoutToday = document.getElementById("statCheckoutToday");
const statOnlineDevices = document.getElementById("statOnlineDevices");

// Toast
const toast = document.getElementById("tapToast");
const toastName = document.getElementById("toastName");
const toastDetail = document.getElementById("toastDetail");
let toastTimeout = null;

// Tab Elements
const tabLiveLog = document.getElementById("tabLiveLog");
const tabDailySummary = document.getElementById("tabDailySummary");
const liveSection = document.getElementById("liveSection");
const dailySection = document.getElementById("dailySection");
const dedupToggle = document.getElementById("dedupToggle");

// Daily Summary Elements
const dailyTableBody = document.getElementById("dailyTableBody");
const dailyEmptyState = document.getElementById("dailyEmptyState");
const dailyRecordsCounter = document.getElementById("dailyRecordsCounter");
const summaryDateInput = document.getElementById("summaryDateInput");

// Sync Section Elements
const tabSync = document.getElementById("tabSync");
const syncSection = document.getElementById("syncSection");
const syncDevicesList = document.getElementById("syncDevicesList");
const syncTableBody = document.getElementById("syncTableBody");
const syncEmptyState = document.getElementById("syncEmptyState");
const syncQueueCounter = document.getElementById("syncQueueCounter");
const btnPushAllSync = document.getElementById("btnPushAllSync");
const btnSyncTime = document.getElementById("btnSyncTime");
const btnPullDevice = document.getElementById("btnPullDevice");
const btnRetryFailed = document.getElementById("btnRetryFailed");
const btnRefreshSync = document.getElementById("btnRefreshSync");

// Modal Elements
const manageEmployeesBtn = document.getElementById("manageEmployeesBtn");
const employeeModal = document.getElementById("employeeModal");
const closeModalBtn = document.getElementById("closeModalBtn");
const mapPinInput = document.getElementById("mapPinInput");
const mapNameInput = document.getElementById("mapNameInput");
const mapNipInput = document.getElementById("mapNipInput");
const saveMappingBtn = document.getElementById("saveMappingBtn");
const pinMappingTableBody = document.getElementById("pinMappingTableBody");

// Set default date input hari ini
const todayStr = new Date().toISOString().split("T")[0];
if (summaryDateInput) {
    summaryDateInput.value = todayStr;
}

// Inisialisasi Audio Chime (Web Audio API murni tanpa file mp3)
function playChime() {
    if (!soundEnabled) return;
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        osc.type = "sine";
        osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
        osc.frequency.exponentialRampToValueAtTime(880.00, audioCtx.currentTime + 0.15); // A5

        gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.4);

        osc.connect(gain);
        gain.connect(audioCtx.destination);

        osc.start();
        osc.stop(audioCtx.currentTime + 0.4);
    } catch (e) {
        console.warn("Audio playback not allowed yet without user interaction:", e);
    }
}

// Update Jam Digital
function updateClock() {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, "0");
    const minutes = String(now.getMinutes()).padStart(2, "0");
    const seconds = String(now.getSeconds()).padStart(2, "0");
    digitalClock.textContent = `${hours}:${minutes}:${seconds} WITA`;
}
setInterval(updateClock, 1000);
updateClock();

// Ambil Data Live Feed dari API
async function fetchAttendanceData(isManual = false) {
    if (isFetching && !isManual) return;
    isFetching = true;

    if (isManual) {
        refreshBtn.querySelector(".refresh-icon").classList.add("spinning");
    }

    try {
        const dedupParam = deduplicateEnabled ? "&deduplicate=true" : "";
        const response = await fetch(`${API_BASE}/api/v1/live-feed?limit=100${dedupParam}`, {
            headers: { "Accept": "application/json" }
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        handleNewData(data);
    } catch (error) {
        console.error("Gagal mengambil data live feed:", error);
    } finally {
        isFetching = false;
        if (isManual) {
            setTimeout(() => {
                refreshBtn.querySelector(".refresh-icon").classList.remove("spinning");
            }, 500);
        }
    }
}

// Proses Data Baru
function handleNewData(payload) {
    // 1. Update Statistik
    if (payload.stats) {
        statTotalToday.textContent = payload.stats.total_today.toLocaleString("id-ID");
        statCheckinToday.textContent = payload.stats.checkin_today.toLocaleString("id-ID");
        statCheckoutToday.textContent = payload.stats.checkout_today.toLocaleString("id-ID");
        statOnlineDevices.textContent = payload.stats.online_devices;
    }

    const records = payload.data || [];

    // 2. Deteksi Tap Baru untuk Notifikasi & Audio Chime
    if (records.length > 0) {
        const newestRecord = records[0];
        if (lastKnownMaxId !== null && newestRecord.id > lastKnownMaxId) {
            playChime();
            showToast(newestRecord);
        }
        lastKnownMaxId = newestRecord.id;
    }

    allRecords = records;
    if (currentView === "live") {
        renderTable();
    }
}

// Render Tabel Log Live
function renderTable() {
    const query = searchQuery.trim().toLowerCase();

    const filtered = allRecords.filter(item => {
        // Filter Status (0=Masuk, 1=Pulang)
        if (activeFilter !== "all" && String(item.status_code) !== activeFilter) {
            return false;
        }

        // Search Query
        if (query) {
            const matchName = (item.employee_name || "").toLowerCase().includes(query);
            const matchNip = (item.employee_nip || "").toLowerCase().includes(query);
            const matchPin = (item.pin || "").toLowerCase().includes(query);
            const matchLoc = (item.location || "").toLowerCase().includes(query);
            if (!matchName && !matchNip && !matchPin && !matchLoc) return false;
        }

        return true;
    });

    recordsCounter.textContent = `Menampilkan ${filtered.length} tap ${deduplicateEnabled ? '(Tersaring)' : '(Semua Log)'}`;

    if (filtered.length === 0) {
        tableBody.innerHTML = "";
        emptyState.style.display = "block";
        return;
    }

    emptyState.style.display = "none";

    const rowsHtml = filtered.map((item, index) => {
        const isMasuk = item.status_code === 0;
        const statusBadgeClass = isMasuk ? "badge-masuk" : "badge-pulang";
        const statusIcon = isMasuk ? "🟢" : "🔴";
        const initials = getInitials(item.employee_name);

        return `
            <tr class="${index === 0 && isJustReceived(item.received_at) ? 'row-new' : ''}">
                <td style="color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">#${item.id}</td>
                <td>
                    <div class="employee-cell">
                        <div class="employee-avatar">${initials}</div>
                        <div class="employee-info">
                            <h4>${escapeHtml(item.employee_name)}</h4>
                            <p>NIP: ${escapeHtml(item.employee_nip)}</p>
                        </div>
                    </div>
                </td>
                <td>
                    <span class="pin-pill">PIN ${escapeHtml(item.pin)}</span>
                </td>
                <td>
                    <div style="font-weight: 600;">${formatTime(item.timestamp)}</div>
                    <div style="font-size: 11px; color: var(--text-muted);">${formatDate(item.timestamp)}</div>
                </td>
                <td>
                    <span class="badge-status ${statusBadgeClass}">
                        ${statusIcon} ${item.status_label}
                    </span>
                </td>
                <td>
                    <span class="badge-verify">
                        <span>${getVerifyIcon(item.verify_code)}</span>
                        <span>${item.verify_label}</span>
                    </span>
                </td>
                <td>
                    <div class="device-location">📍 ${escapeHtml(item.location)}</div>
                    <div class="device-sn">SN: ${escapeHtml(item.device_sn)}</div>
                </td>
            </tr>
        `;
    }).join("");

    tableBody.innerHTML = rowsHtml;
}

// ── Rekap Presensi Harian (SatuTalenta RSUP) ──
async function fetchDailySummary() {
    const selectedDate = summaryDateInput.value || todayStr;
    try {
        const response = await fetch(`${API_BASE}/api/v1/daily-attendance?target_date=${selectedDate}`, {
            headers: { "Accept": "application/json" }
        });
        if (!response.ok) throw new Error("Gagal mengambil data rekap");

        const payload = await response.json();
        renderDailyTable(payload.data || [], selectedDate);
    } catch (err) {
        console.error("Gagal load daily summary:", err);
    }
}

function renderDailyTable(records, dateStr) {
    dailyRecordsCounter.textContent = `${records.length} Pegawai Hadir (${dateStr})`;

    if (records.length === 0) {
        dailyTableBody.innerHTML = "";
        dailyEmptyState.style.display = "block";
        return;
    }

    dailyEmptyState.style.display = "none";

    const rowsHtml = records.map((item, index) => {
        const initials = getInitials(item.employee_name);
        return `
            <tr>
                <td style="color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">${index + 1}</td>
                <td>
                    <div class="employee-cell">
                        <div class="employee-avatar">${initials}</div>
                        <div class="employee-info">
                            <h4>${escapeHtml(item.employee_name)}</h4>
                            <p>NIP: ${escapeHtml(item.employee_nip)}</p>
                        </div>
                    </div>
                </td>
                <td>
                    <span class="pin-pill">PIN ${escapeHtml(item.pin)}</span>
                </td>
                <td>${escapeHtml(item.date)}</td>
                <td>
                    <span style="font-weight: 700; color: #10b981;">
                        ${item.checkin_time ? formatTime(item.checkin_time) : '-'}
                    </span>
                </td>
                <td>
                    <span style="font-weight: 700; color: ${item.checkout_time ? '#f43f5e' : 'var(--text-muted)'};">
                        ${item.checkout_time ? formatTime(item.checkout_time) : '(Belum Pulang)'}
                    </span>
                </td>
                <td>
                    <span style="font-weight: 600; color: #93c5fd;">${escapeHtml(item.duration)}</span>
                </td>
                <td>
                    <span class="badge-status badge-masuk">
                        ✅ ${escapeHtml(item.status)}
                    </span>
                </td>
            </tr>
        `;
    }).join("");

    dailyTableBody.innerHTML = rowsHtml;
}

// ── Modal Manajemen Pemetaan Pegawai ──
async function fetchPinMappings() {
    try {
        const response = await fetch(`${API_BASE}/api/v1/pin-mapping`);
        if (!response.ok) throw new Error("Gagal mengambil data mapping");

        const payload = await response.json();
        renderPinMappingTable(payload.data || []);
    } catch (err) {
        console.error("Gagal load pin mappings:", err);
    }
}

function renderPinMappingTable(items) {
    if (items.length === 0) {
        pinMappingTableBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 16px;">Belum ada PIN yang terdeteksi dari mesin.</td></tr>`;
        return;
    }

    pinMappingTableBody.innerHTML = items.map(item => {
        const isMapped = item.is_mapped;
        const statusBadge = isMapped
            ? `<span class="badge-status badge-masuk">✅ Terpetakan</span>`
            : `<span class="badge-status badge-pulang">⚠️ Belum Terpetakan</span>`;

        return `
            <tr>
                <td><strong>PIN ${escapeHtml(item.pin)}</strong></td>
                <td>${escapeHtml(item.employee_name)}</td>
                <td>${escapeHtml(item.employee_nip)}</td>
                <td>${item.total_taps} tap</td>
                <td>${statusBadge}</td>
                <td>
                    <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;" 
                            onclick="selectPinForMapping('${escapeHtml(item.pin)}', '${escapeHtml(isMapped ? item.employee_name : '')}', '${escapeHtml(isMapped ? item.employee_nip : '')}')">
                        ✏️ ${isMapped ? 'Edit' : 'Petakan'}
                    </button>
                </td>
            </tr>
        `;
    }).join("");
}

window.selectPinForMapping = function(pin, name, nip) {
    mapPinInput.value = pin;
    mapNameInput.value = name;
    mapNipInput.value = nip;
    mapNameInput.focus();
};

async function handleSaveMapping() {
    const pin = mapPinInput.value.trim();
    const name = mapNameInput.value.trim();
    const nip = mapNipInput.value.trim();

    if (!pin || !name || !nip) {
        alert("Harap lengkapi PIN, Nama, dan NIP Pegawai!");
        return;
    }

    saveMappingBtn.disabled = true;
    saveMappingBtn.textContent = "Menyimpan...";

    try {
        const response = await fetch(`${API_BASE}/api/v1/pin-mapping`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pin: pin, employee_name: name, employee_nip: nip })
        });

        if (!response.ok) throw new Error("Gagal menyimpan pemetaan");

        const res = await response.json();
        alert(res.message || "Pemetaan berhasil disimpan!");

        // Reset form input
        mapPinInput.value = "";
        mapNameInput.value = "";
        mapNipInput.value = "";

        // Refresh mapping table & live feed
        fetchPinMappings();
        fetchAttendanceData(true);
        if (currentView === "daily") fetchDailySummary();
    } catch (err) {
        alert("Gagal menyimpan pemetaan: " + err.message);
    } finally {
        saveMappingBtn.disabled = false;
        saveMappingBtn.textContent = "💾 Simpan Pemetaan";
    }
}

// Helpers
function getInitials(name) {
    if (!name || name === "Belum Terpetakan") return "👤";
    const clean = name.replace(/^(dr\.|drg\.|Ns\.|H\.|Hj\.)\s*/i, "").trim();
    const parts = clean.split(" ");
    if (parts.length >= 2) {
        return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return clean.substring(0, 2).toUpperCase();
}

function getVerifyIcon(code) {
    switch (code) {
        case 1: return "👆";
        case 2: return "👤";
        case 15: return "💳";
        default: return "🔢";
    }
}

function formatTime(dateTimeStr) {
    if (!dateTimeStr) return "-";
    const parts = dateTimeStr.split(" ");
    return parts.length > 1 ? parts[1] : dateTimeStr;
}

function formatDate(dateTimeStr) {
    if (!dateTimeStr) return "-";
    const parts = dateTimeStr.split(" ");
    return parts[0];
}

function isJustReceived(receivedAtStr) {
    if (!receivedAtStr) return false;
    const diff = (new Date() - new Date(receivedAtStr)) / 1000;
    return diff < 6; // Kurang dari 6 detik yang lalu
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showToast(tap) {
    clearTimeout(toastTimeout);
    toastName.textContent = tap.employee_name || `PIN ${tap.pin}`;
    const action = tap.status_code === 0 ? "Masuk" : "Pulang";
    toastDetail.textContent = `${action} pada ${formatTime(tap.timestamp)} • ${tap.location}`;
    toast.classList.add("show");

    toastTimeout = setTimeout(() => {
        toast.classList.remove("show");
    }, 4500);
}

// Event Listeners: Search & Filter
searchInput.addEventListener("input", (e) => {
    searchQuery = e.target.value;
    renderTable();
});

filterButtons.forEach(btn => {
    btn.addEventListener("click", () => {
        filterButtons.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        activeFilter = btn.dataset.filter;
        renderTable();
    });
});

// Event Listeners: Sound Toggle & Refresh
soundToggleBtn.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    soundIcon.textContent = soundEnabled ? "🔔" : "🔕";
    soundToggleBtn.title = soundEnabled ? "Suara Notifikasi: Aktif" : "Suara Notifikasi: Hening";
    if (soundEnabled) playChime();
});

refreshBtn.addEventListener("click", () => {
    if (currentView === "live") {
        fetchAttendanceData(true);
    } else {
        fetchDailySummary();
    }
});

// Event Listener: Saring Tap Dobel
dedupToggle.addEventListener("change", (e) => {
    deduplicateEnabled = e.target.checked;
    fetchAttendanceData(true);
});

// ── SINKRONISASI OTOMATIS ANTAR-MESIN (SYNC LOGIC) ──
async function fetchSyncStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/v1/sync/status`);
        if (!response.ok) return;
        const data = await response.json();

        // 1. Render Devices
        renderSyncDevices(data.devices || []);

        // 2. Render Queue
        renderSyncQueue(data.recent_commands || [], data.queue_summary || {});
    } catch (e) {
        console.error("Gagal mengambil status sinkronisasi:", e);
    }
}

function renderSyncDevices(devices) {
    if (!syncDevicesList) return;
    if (devices.length === 0) {
        syncDevicesList.innerHTML = `<div style="color: var(--text-muted); font-size: 13px;">Belum ada mesin yang terdaftar.</div>`;
        return;
    }

    syncDevicesList.innerHTML = devices.map(d => {
        const isMaster = d.is_master;
        return `
            <div class="sync-device-card">
                <div class="sync-device-info">
                    <h4>${escapeHtml(d.location)} ${isMaster ? '<span class="badge-cmd badge-cmd-success" style="margin-left: 6px;">MASTER</span>' : '<span class="badge-cmd badge-cmd-sent" style="margin-left: 6px;">PEER</span>'}</h4>
                    <p>SN: ${escapeHtml(d.device_sn)} • Terakhir Aktif: ${d.last_seen_at || '-'}</p>
                </div>
                <div>
                    <span class="badge-cmd badge-cmd-success">POLLING READY</span>
                </div>
            </div>
        `;
    }).join("");
}

function renderSyncQueue(commands, summary) {
    if (!syncTableBody) return;

    if (syncQueueCounter) {
        const pending = summary.pending || 0;
        const sent = summary.sent || 0;
        const success = summary.success || 0;
        syncQueueCounter.textContent = `${summary.total_commands || 0} Total (${pending} Pending, ${sent} Terkirim, ${success} Sukses)`;
    }

    if (commands.length === 0) {
        syncTableBody.innerHTML = "";
        if (syncEmptyState) syncEmptyState.style.display = "block";
        return;
    }

    if (syncEmptyState) syncEmptyState.style.display = "none";
    syncTableBody.innerHTML = commands.map(c => {
        let badgeClass = "badge-cmd-pending";
        if (c.status === "SENT") badgeClass = "badge-cmd-sent";
        else if (c.status === "SUCCESS") badgeClass = "badge-cmd-success";
        else if (c.status.startsWith("FAILED")) badgeClass = "badge-cmd-failed";

        return `
            <tr>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">${c.id}</td>
                <td><span style="font-family: 'JetBrains Mono', monospace; font-size: 12px;">${escapeHtml(c.device_sn)}</span></td>
                <td><strong>${escapeHtml(c.cmd_code || '-')}</strong></td>
                <td style="font-size: 12px; color: var(--text-secondary);">${escapeHtml(c.command_text || '-')}</td>
                <td><span class="badge-cmd ${badgeClass}">${c.status}</span></td>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 12px;">${escapeHtml(c.return_code || '-')}</td>
                <td style="font-size: 12px; color: var(--text-muted);">${c.created_at || '-'}</td>
            </tr>
        `;
    }).join("");
}

async function handlePushAllSync() {
    if (!confirm("Dorong seluruh data pegawai yang terpetakan dan template sidik jari ke SEMUA mesin Fingerspot fisik?")) {
        return;
    }
    try {
        btnPushAllSync.disabled = true;
        btnPushAllSync.textContent = "⏳ Mengantrekan...";
        const res = await fetch(`${API_BASE}/api/v1/sync/push-all`, { method: "POST" });
        const data = await res.json();
        alert(`Berhasil! ${data.commands_queued || 0} perintah sinkronisasi berhasil diantrekan ke seluruh mesin.`);
        fetchSyncStatus();
    } catch (e) {
        alert("Gagal mengantrekan sinkronisasi: " + e.message);
    } finally {
        btnPushAllSync.disabled = false;
        btnPushAllSync.innerHTML = "<span>🔄</span> Dorong Semua Data ke Seluruh Mesin";
    }
}

async function handleSyncTime() {
    try {
        btnSyncTime.disabled = true;
        const res = await fetch(`${API_BASE}/api/v1/sync/set-time`, { method: "POST" });
        const data = await res.json();
        alert(data.message || "Perintah penyesuaian jam telah dikirim ke seluruh mesin.");
        fetchSyncStatus();
    } catch (e) {
        alert("Gagal: " + e.message);
    } finally {
        btnSyncTime.disabled = false;
    }
}

async function handlePullDevice() {
    try {
        btnPullDevice.disabled = true;
        const res = await fetch(`${API_BASE}/api/v1/sync/pull-from-device`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device_sn: "C2657C94530C2D25" })
        });
        const data = await res.json();
        alert(data.message || "Perintah penarikan data pengguna berhasil dikirim.");
        fetchSyncStatus();
    } catch (e) {
        alert("Gagal: " + e.message);
    } finally {
        btnPullDevice.disabled = false;
    }
}

async function handleRetryFailed() {
    try {
        btnRetryFailed.disabled = true;
        const res = await fetch(`${API_BASE}/api/v1/sync/retry-failed`, { method: "POST" });
        const data = await res.json();
        alert(data.message || "Perintah gagal berhasil direset.");
        fetchSyncStatus();
    } catch (e) {
        alert("Gagal: " + e.message);
    } finally {
        btnRetryFailed.disabled = false;
    }
}

// Event Listeners: Sync Actions
if (btnPushAllSync) btnPushAllSync.addEventListener("click", handlePushAllSync);
if (btnSyncTime) btnSyncTime.addEventListener("click", handleSyncTime);
if (btnPullDevice) btnPullDevice.addEventListener("click", handlePullDevice);
if (btnRetryFailed) btnRetryFailed.addEventListener("click", handleRetryFailed);
if (btnRefreshSync) btnRefreshSync.addEventListener("click", fetchSyncStatus);

// --- [SYNC-05] Revoke Fingerprint ---
const btnRevokeFingerprint = document.getElementById("btnRevokeFingerprint");
const revokeModal = document.getElementById("revokeModal");
const closeRevokeModalBtn = document.getElementById("closeRevokeModalBtn");
const cancelRevokeBtn = document.getElementById("cancelRevokeBtn");
const confirmRevokeBtn = document.getElementById("confirmRevokeBtn");
const revokePinInput = document.getElementById("revokePinInput");
const revokeReasonInput = document.getElementById("revokeReasonInput");
const revokeResult = document.getElementById("revokeResult");

if (btnRevokeFingerprint) {
    btnRevokeFingerprint.addEventListener("click", () => {
        if (revokeModal) revokeModal.style.display = "flex";
        if (revokePinInput) revokePinInput.value = "";
        if (revokeResult) { revokeResult.style.display = "none"; revokeResult.innerHTML = ""; }
    });
}

if (closeRevokeModalBtn) closeRevokeModalBtn.addEventListener("click", () => { revokeModal.style.display = "none"; });
if (cancelRevokeBtn) cancelRevokeBtn.addEventListener("click", () => { revokeModal.style.display = "none"; });
if (revokeModal) {
    revokeModal.addEventListener("click", (e) => { if (e.target === revokeModal) revokeModal.style.display = "none"; });
}

if (confirmRevokeBtn) {
    confirmRevokeBtn.addEventListener("click", async () => {
        const pin = revokePinInput ? revokePinInput.value.trim() : "";
        const reason = revokeReasonInput ? revokeReasonInput.value.trim() : "Pencabutan biometrik oleh HR/IT";

        if (!pin) {
            alert("Harap masukkan PIN mesin karyawan yang akan dicabut biometriknya.");
            return;
        }

        const confirmed = confirm(
            `⚠️ KONFIRMASI PENCABUTAN BIOMETRIK\n\n` +
            `PIN: ${pin}\nAlasan: ${reason}\n\n` +
            `Perintah DELETE_USER akan dikirim ke SEMUA mesin Fingerspot.\n` +
            `Karyawan ini tidak akan bisa absen secara fisik.\n\n` +
            `Lanjutkan?`
        );
        if (!confirmed) return;

        try {
            confirmRevokeBtn.disabled = true;
            confirmRevokeBtn.textContent = "⏳ Memproses...";

            const res = await fetch(`${API_BASE}/api/v1/sync/revoke-fingerprint`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pin, reason }),
            });
            const data = await res.json();

            if (revokeResult) {
                revokeResult.style.display = "block";
                if (data.status === "success") {
                    revokeResult.innerHTML = `
                        <div style="background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); border-radius: 8px; padding: 16px;">
                            <strong style="color: #22c55e;">✅ Berhasil!</strong><br>
                            PIN <code>${escapeHtml(data.pin)}</code> telah dicabut:<br>
                            • Template dinonaktifkan: <strong>${data.templates_invalidated || 0}</strong><br>
                            • Mesin yang menerima perintah: <strong>${data.devices_queued || 0}</strong><br>
                            • Total perintah diantrekan: <strong>${data.commands_queued || 0}</strong>
                        </div>`;
                } else {
                    revokeResult.innerHTML = `
                        <div style="background: rgba(234,179,8,0.1); border: 1px solid rgba(234,179,8,0.3); border-radius: 8px; padding: 16px;">
                            <strong style="color: #eab308;">⚠️ ${escapeHtml(data.status)}</strong>: ${escapeHtml(data.message || JSON.stringify(data))}
                        </div>`;
                }
            }
            fetchSyncStatus();
        } catch (e) {
            alert("Gagal melakukan revoke: " + e.message);
        } finally {
            confirmRevokeBtn.disabled = false;
            confirmRevokeBtn.innerHTML = "🚫 Konfirmasi Cabut Sidik Jari";
        }
    });
}

// --- [SYNC-06] Manual Cleanup Commands ---
const btnCleanupCommands = document.getElementById("btnCleanupCommands");
if (btnCleanupCommands) {
    btnCleanupCommands.addEventListener("click", async () => {
        try {
            btnCleanupCommands.disabled = true;
            btnCleanupCommands.textContent = "⏳ Membersihkan...";
            const res = await fetch(`${API_BASE}/api/v1/sync/cleanup-commands`, { method: "POST" });
            const data = await res.json();
            const r = data.result || {};
            alert(`✅ Pembersihan Selesai!\n• SENT timeout (24j): ${r.timed_out || 0} perintah\n• Dihapus (>30 hari): ${r.deleted || 0} perintah`);
            fetchSyncStatus();
        } catch (e) {
            alert("Gagal cleanup: " + e.message);
        } finally {
            btnCleanupCommands.disabled = false;
            btnCleanupCommands.innerHTML = "🗑️ Bersihkan Perintah Lama";
        }
    });
}

// Event Listeners: Tabs
tabLiveLog.addEventListener("click", () => {
    currentView = "live";
    tabLiveLog.classList.add("active");
    tabDailySummary.classList.remove("active");
    if (tabSync) tabSync.classList.remove("active");
    liveSection.style.display = "block";
    dailySection.style.display = "none";
    if (syncSection) syncSection.style.display = "none";
    renderTable();
});

tabDailySummary.addEventListener("click", () => {
    currentView = "daily";
    tabDailySummary.classList.add("active");
    tabLiveLog.classList.remove("active");
    if (tabSync) tabSync.classList.remove("active");
    dailySection.style.display = "block";
    liveSection.style.display = "none";
    if (syncSection) syncSection.style.display = "none";
    fetchDailySummary();
});

if (tabSync) {
    tabSync.addEventListener("click", () => {
        currentView = "sync";
        tabSync.classList.add("active");
        tabDailySummary.classList.remove("active");
        tabLiveLog.classList.remove("active");
        if (syncSection) syncSection.style.display = "block";
        dailySection.style.display = "none";
        liveSection.style.display = "none";
        fetchSyncStatus();
    });
}

if (summaryDateInput) {
    summaryDateInput.addEventListener("change", fetchDailySummary);
}

// Event Listeners: Modal
manageEmployeesBtn.addEventListener("click", () => {
    employeeModal.style.display = "flex";
    fetchPinMappings();
});

closeModalBtn.addEventListener("click", () => {
    employeeModal.style.display = "none";
});

employeeModal.addEventListener("click", (e) => {
    if (e.target === employeeModal) {
        employeeModal.style.display = "none";
    }
});

saveMappingBtn.addEventListener("click", handleSaveMapping);

// Set base URL label
if (apiBaseUrlText) {
    apiBaseUrlText.textContent = API_BASE;
}

// Mulai Polling Interval
fetchDailySummary();
fetchAttendanceData(true);

setInterval(() => {
    if (currentView === "daily") {
        fetchDailySummary();
    } else if (currentView === "live") {
        fetchAttendanceData();
    } else if (currentView === "sync") {
        fetchSyncStatus();
    }
}, POLL_INTERVAL_MS);

