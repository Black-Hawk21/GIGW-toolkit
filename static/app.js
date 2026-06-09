// ── File Upload Handler ──────────────────────────────────────────
function setupFileUpload(zoneId, inputId, nameId) {
    const zone = document.getElementById(zoneId);
    const input = document.getElementById(inputId);
    if (!zone || !input) return;

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            input.dispatchEvent(new Event('change'));
        }
    });
    input.addEventListener('change', () => {
        if (input.files.length) {
            zone.classList.add('file-selected');
            const fname = input.files[0].name;
            if (nameId) {
                const el = document.getElementById(nameId);
                if (el) el.textContent = fname;
            }
            const p = zone.querySelector('p');
            if (p) p.innerHTML = `<strong>${fname}</strong> selected`;
        }
    });
}

// ── Mobile Menu ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('mobileMenuBtn');
    const sidebar = document.getElementById('sidebar');
    if (btn && sidebar) {
        btn.addEventListener('click', () => sidebar.classList.toggle('open'));
        document.getElementById('mainContent')?.addEventListener('click', () => sidebar.classList.remove('open'));
    }
});
