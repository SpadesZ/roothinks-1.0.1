// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_progress.js
// 模組定位: 瀏覽器互動層；2B 工具列的「章節完成比例」欄位。
// 主要責任: 讀取 /manuscript/api/progress/<pid>、隨切章顯示該章的比例、
//           使用者改動後寫回；依權限決定欄位是否唯讀。
// 上下游: manuscript_workspace.html 建立 #sectionProgress；本檔呼叫
//         /manuscript/api/progress/<pid>（GET/POST），並由 manuscript_ws.js
//         在切章時呼叫 onSectionChanged()。
// 維護邊界: 前端狀態不是授權來源 —— 這裡的唯讀鎖定純為體驗優化，
//           真正把關在伺服器的 _socket_can_write_section（NOTE-036）。
//           解除 disabled 不等於取得授權。
// 驗證: 這台機器沒有 Node，無法 node --check；行為證據見 docs/HANDOFF.md 的
//       真瀏覽器紀錄，伺服器契約見 test/unit/test_section_progress.py。
//路徑(./app/static/js/manuscript_progress.js)
//版本 v0.1
//更版時間 20260814-0100

class ManuProgress {
    constructor(app) {
        this.app = app;
        // { section_key: number|null }。null 代表「還沒填」，與 0 不同。
        this.bySection = {};
        this.loaded = false;
        this._pending = false;

        this.input = document.getElementById('sectionProgress');
        this.status = document.getElementById('sectionProgressStatus');

        if (this.input) {
            // change 而不是 input：邊打邊送會為 "1" / "10" / "100" 各寫一次，
            // 中間那兩個都是錯的值，而且會蓋掉別人的併發寫入。
            this.input.addEventListener('change', () => this.save());
            this.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); this.input.blur(); }
            });
        }
    }

    _setStatus(text, cls) {
        if (!this.status) return;
        this.status.textContent = text || '';
        this.status.className = 'small ms-1 ' + (cls || 'text-muted');
    }

    currentSection() {
        return Array.from(this.app.selectedSections || [])[0] || '';
    }

    async load() {
        if (!this.app.pid) return;
        try {
            const res = await fetch(
                `/manuscript/api/progress/${encodeURIComponent(this.app.pid)}`);
            if (!res.ok) {
                // 讀不到就不要假裝有值。欄位留空，狀態說明白。
                this.loaded = false;
                this._setStatus('進度未載入', 'text-muted');
                return;
            }
            const data = await res.json();
            this.bySection = {};
            (data.sections || []).forEach(s => { this.bySection[s.id] = s.progress; });
            this.loaded = true;
            this.render();
        } catch (err) {
            this.loaded = false;
            this._setStatus('進度未載入', 'text-muted');
        }
    }

    /** 切章之後由 manuscript_ws.js 呼叫；只換顯示，不打伺服器。 */
    onSectionChanged() {
        this.render();
    }

    render() {
        if (!this.input) return;
        const section = this.currentSection();
        const value = this.bySection[section];
        // undefined（章節不在清單裡）與 null（還沒填）都顯示空白。
        this.input.value = (value === null || value === undefined) ? '' : String(value);

        const canWrite = !this.app.collab || this.app.collab.canWrite(section);
        this.input.disabled = !section || !canWrite;
        if (!section) {
            this._setStatus('', 'text-muted');
        } else if (!canWrite) {
            this._setStatus('唯讀', 'text-muted');
        } else if (value === null || value === undefined) {
            this._setStatus('尚未填寫', 'text-muted');
        } else {
            this._setStatus('', 'text-muted');
        }
    }

    async save() {
        if (!this.input || this._pending) return;
        const section = this.currentSection();
        if (!section || !this.app.pid) return;

        const raw = String(this.input.value || '').trim();
        if (raw === '') {
            // 清空不是「設成 0」。目前沒有「取消填寫」的語意，所以還原顯示值。
            this.render();
            return;
        }

        const parsed = Number(raw);
        if (!Number.isFinite(parsed)) {
            this._setStatus('請輸入 0-100', 'text-danger');
            this.render();
            return;
        }
        const percent = Math.max(0, Math.min(100, Math.round(parsed)));

        this._pending = true;
        this._setStatus('儲存中…', 'text-muted');
        try {
            const res = await fetch(
                `/manuscript/api/progress/${encodeURIComponent(this.app.pid)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ section, progress: percent }),
                });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                // 存不進去就**不得**讓畫面顯示成功值：把顯示還原成伺服器已知的值，
                // 否則使用者會以為填好了（同 NOTE-022 的判斷）。
                this._setStatus(res.status === 403 ? '無權限' : '儲存失敗', 'text-danger');
                this.render();
                return;
            }
            this.bySection[section] = data.progress;
            this.input.value = String(data.progress);
            this._setStatus('已儲存', 'text-success');
            setTimeout(() => {
                if (this.status && this.status.textContent === '已儲存') {
                    this._setStatus('', 'text-muted');
                }
            }, 2000);
        } catch (err) {
            this._setStatus('儲存失敗', 'text-danger');
            this.render();
        } finally {
            this._pending = false;
        }
    }
}

window.ManuProgress = ManuProgress;
