/* 路徑(./app/static/js/lava_setup.js) #版本 v0.8 #更版時間 20260419-1730 */
/* [MVP+Prototype Handoff Header]
 * 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
 * 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
 * 與 LAVA 綁定流程與 CPU/runtime 設定調整相關，後續請由人類團隊接手做完整治理。
 */

document.addEventListener('DOMContentLoaded', () => {
    loadConnections();
    loadRuntimeCpuSettings();
    loadBindings();

    const saveCpuBtn = document.getElementById('btnSaveCpuCores');
    if (saveCpuBtn) {
        saveCpuBtn.addEventListener('click', () => saveRuntimeCpuSettings());
    }
    const saveWorkersBtn = document.getElementById('btnSaveFlowaWorkers');
    if (saveWorkersBtn) {
        saveWorkersBtn.addEventListener('click', () => saveFlowaWorkersSettings());
    }
});

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeJsSingle(value) {
    return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

// =========================================
// 1. Connection Logic (三階段狀態機與 CRUD)
// =========================================

async function loadConnections() {
    const container = document.getElementById('connection-list-container');
    container.innerHTML = ''; 

    try {
        const res = await fetch('/api/llm/connection/list');
        const data = await res.json();

        if (!data.success) {
            console.warn(data.message);
        }

        if (!data.connections || data.connections.length === 0) {
            container.innerHTML = '<div class="text-center text-muted py-3" id="empty-conn-msg">暫無線路，請點擊「新增線路」。</div>';
        } else {
            // 動態賦予連續的視覺序號 (Visual ID)
            data.connections.forEach((conn, index) => {
                conn.visual_id = index + 1;
                renderConnection(container, conn);
            });
        }
        
        refreshBindingDropdowns(data.connections || []);

    } catch (e) {
        console.error(e);
        container.innerHTML = '<div class="text-danger p-3">載入失敗: ' + escapeHtml(e.message) + '</div>';
    }
}

async function createConnection() {
    try {
        const res = await fetch('/api/llm/connection/create', { method: 'POST' });
        const result = await res.json();
        
        if (result.success && result.connection) {
            const container = document.getElementById('connection-list-container');
            const emptyMsg = document.getElementById('empty-conn-msg');
            if (emptyMsg) emptyMsg.remove();
            
            // 新增時延續當前畫面的數量作為視覺序號
            result.connection.visual_id = container.children.length + 1;
            renderConnection(container, result.connection);
        } else {
            alert('新增失敗: ' + (result.message || 'Unknown API format'));
            loadConnections();
        }
    } catch (e) {
        console.error('Create Error:', e);
        alert('系統錯誤: 無法建立線路');
    }
}

// 將真實 ID 與視覺顯示名稱分開處理
async function deleteConnection(connId, visualName, rowElement) {
    if (!confirm(`確定要刪除 ${visualName} 線路嗎？\n注意：此動作將同步解除依賴此線路的任務綁定！`)) {
        return;
    }

    try {
        const res = await fetch(`/api/llm/connection/delete/${connId}`, { method: 'DELETE' });
        const result = await res.json();

        if (result.success) {
            if (rowElement) {
                rowElement.remove();
            }
            
            const container = document.getElementById('connection-list-container');
            if (container.children.length === 0) {
                container.innerHTML = '<div class="text-center text-muted py-3" id="empty-conn-msg">暫無線路，請點擊「新增線路」。</div>';
            }

            // 刪除後必須重新載入，以重新計算並整齊排列視覺序號
            loadConnections(); 
            loadBindings();
        } else {
            alert('刪除失敗: ' + (result.message || 'Unknown error'));
        }
    } catch (e) {
        console.error('Delete Error:', e);
        alert('系統錯誤: 無法刪除線路');
    }
}

function renderConnection(container, conn) {
    const template = document.getElementById('conn-row-template');
    const clone = template.content.cloneNode(true);
    const row = clone.querySelector('.conn-row');
    
    const badge = row.querySelector('.conn-id-badge');
    const selectVendor = row.querySelector('.conn-vendor');
    const inputKey = row.querySelector('.conn-key');
    const btnFetch = row.querySelector('.conn-btn-fetch');
    const selectModel = row.querySelector('.conn-model');
    const statusBadge = row.querySelector('.conn-status');
    const btnTest = row.querySelector('.conn-btn-test');
    const btnLock = row.querySelector('.conn-btn-lock');
    const btnDel = row.querySelector('.conn-btn-del');

    // 顯示連續的視覺序號，而非跳號的真實資料庫 ID
    const visualName = `LLM-${conn.visual_id || conn.id}`;
    badge.textContent = visualName;
    
    selectVendor.value = conn.vendor || 'openrouter';
    inputKey.value = conn.api_key || '';
    
    if (conn.model_name) {
        const opt = document.createElement('option');
        opt.value = conn.model_name;
        opt.textContent = conn.model_name;
        selectModel.appendChild(opt);
        selectModel.value = conn.model_name;
        selectModel.disabled = false;
    }

    updateRowUIByStatus(conn.status, { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel });

    // 傳遞真實 ID 供刪除使用，傳遞視覺名稱供 Alert 提示
    btnDel.addEventListener('click', () => deleteConnection(conn.id, visualName, row));

    const saveChanges = async (newStatus = 'draft') => {
        try {
            const res = await fetch('/api/llm/connection/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: conn.id,
                    vendor: selectVendor.value,
                    api_key: inputKey.value,
                    model_name: selectModel.value,
                    status: newStatus
                })
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.message);

            conn.status = newStatus;
            updateRowUIByStatus(newStatus, { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel });
            refreshBindingDropdowns();
        } catch(e) {
            alert('儲存狀態失敗: ' + e.message);
            updateRowUIByStatus(conn.status, { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel });
        }
    };

    selectVendor.addEventListener('change', () => saveChanges('draft'));
    inputKey.addEventListener('change', () => saveChanges('draft'));
    
    selectModel.addEventListener('change', () => {
        if (selectModel.value) {
            saveChanges('fetched'); 
        } else {
            saveChanges('draft');
        }
    });

    // [v0.8 核心升級] 改寫 fetch 行為，精準處理 Object Array 並高亮免費模型
    btnFetch.addEventListener('click', async () => {
        if (!inputKey.value) {
            alert('請先輸入 API Key');
            return;
        }
        btnFetch.disabled = true;
        btnFetch.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        
        try {
            const res = await fetch('/api/llm/connection/fetch_models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ vendor: selectVendor.value, api_key: inputKey.value, conn_id: conn.id })
            });
            const result = await res.json();
            
            if (result.success && result.models.length > 0) {
                selectModel.innerHTML = '<option value="">-- Select Model --</option>';
                result.models.forEach(m => {
                    const opt = document.createElement('option');
                    // 兼容 Object(OpenRouter) 與 String(Google/OpenAI) 的回傳結構
                    if (typeof m === 'object' && m !== null) {
                        opt.value = m.id; // 真實寫入 DB 的乾淨 ID
                        opt.textContent = m.is_free ? `[免費] ${m.name || m.id}` : (m.name || m.id);
                        if (m.is_free) {
                            // 使用原生 DOM 控制樣式，不污染 value
                            opt.style.color = 'red';
                            opt.style.fontWeight = 'bold';
                        }
                    } else {
                        opt.value = m;
                        opt.textContent = m;
                    }
                    selectModel.appendChild(opt);
                });
                
                const preferredModel = result.models.find(m => {
                    const val = typeof m === 'object' && m !== null ? m.id : m;
                    return typeof val === 'string' && val.trim() !== '';
                });
                if (preferredModel) {
                    selectModel.value = typeof preferredModel === 'object' ? preferredModel.id : preferredModel;
                }
                
                selectModel.disabled = false;
                saveChanges('fetched');
            } else {
                alert('無法取得模型: ' + (result.message || 'Key 無效或無可用模型'));
            }
        } catch (e) {
            alert('Fetch Error: ' + e.message);
        } finally {
            btnFetch.disabled = false;
            btnFetch.innerHTML = '<i class="bi bi-cloud-download"></i> Fetch';
        }
    });

    btnTest.addEventListener('click', async () => {
        if (!selectModel.value || !inputKey.value) {
            alert('請確認已輸入 API Key 並選擇模型');
            return;
        }
        
        const originalHtml = btnTest.innerHTML;
        btnTest.disabled = true;
        btnTest.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        
        try {
            const res = await fetch('/api/llm/connection/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ vendor: selectVendor.value, api_key: inputKey.value, model_name: selectModel.value })
            });
            const result = await res.json();
            
            if (result.success) {
                alert('連線測試成功！伺服器已回應。');
                saveChanges('connected');
            } else {
                alert('測試失敗: ' + result.message);
                updateRowUIByStatus(conn.status, { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel });
            }
        } catch(e) {
            alert('網路異常，測試中斷。');
            updateRowUIByStatus(conn.status, { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel });
        } finally {
            btnTest.disabled = false;
            btnTest.innerHTML = originalHtml;
        }
    });

    btnLock.addEventListener('click', () => {
        if (confirm('鎖定後將正式提供服務且無法修改，確定嗎？')) {
            const originalHtml = btnLock.innerHTML;
            btnLock.disabled = true;
            btnLock.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            
            saveChanges('locked').finally(() => {
                btnLock.innerHTML = originalHtml;
            });
        }
    });

    container.appendChild(row);
}

function updateRowUIByStatus(status, elements) {
    const { selectVendor, inputKey, btnFetch, selectModel, statusBadge, btnTest, btnLock, btnDel } = elements;
    
    selectVendor.disabled = false;
    inputKey.disabled = false;
    btnFetch.disabled = false;
    btnDel.disabled = false;
    
    switch (status) {
        case 'draft':
            statusBadge.className = 'badge bg-secondary conn-status';
            statusBadge.textContent = 'Draft';
            btnTest.disabled = true;
            btnLock.disabled = true;
            break;
        case 'fetched':
            statusBadge.className = 'badge bg-info text-dark conn-status';
            statusBadge.textContent = 'Fetched';
            btnTest.disabled = false;
            btnLock.disabled = true;
            break;
        case 'connected':
            statusBadge.className = 'badge bg-primary conn-status';
            statusBadge.textContent = 'Connected';
            btnTest.disabled = false;
            btnLock.disabled = false;
            break;
        case 'locked':
            statusBadge.className = 'badge bg-success conn-status';
            statusBadge.textContent = 'Locked';
            selectVendor.disabled = true;
            inputKey.disabled = true;
            btnFetch.disabled = true;
            selectModel.disabled = true;
            btnTest.disabled = true;
            btnLock.disabled = true;
            break;
        default:
            break;
    }
}

// =========================================
// 1.5 Runtime CPU & FlowA Workers Control
// =========================================

function loadRuntimeCpuSettings() {
    const cpuSel = document.getElementById('cpuCoresSelect');
    const workerSel = document.getElementById('flowaWorkersSelect');
    const info = document.getElementById('cpuRuntimeInfo');
    if (!cpuSel || !workerSel || !info) return;

    Promise.all([
        fetch('/api/llm/runtime/cpu').then(r => r.json()),
        fetch('/api/llm/runtime/flowa_workers').then(r => r.json()),
    ])
        .then(([cpuRes, workerRes]) => {
            if (!cpuRes.ok) throw new Error(cpuRes.msg || '讀取 CPU 設定失敗');
            if (!workerRes.ok) throw new Error(workerRes.msg || '讀取 Flow A workers 設定失敗');

            cpuSel.innerHTML = '';
            const cpuChoices = Array.isArray(cpuRes.choices) ? cpuRes.choices : [];
            cpuChoices.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.value;
                opt.innerText = c.label;
                cpuSel.appendChild(opt);
            });
            cpuSel.value = cpuRes.selected_value || 'auto';

            workerSel.innerHTML = '';
            const workerChoices = Array.isArray(workerRes.choices) ? workerRes.choices : [];
            workerChoices.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.value;
                opt.innerText = c.label;
                workerSel.appendChild(opt);
            });
            workerSel.value = workerRes.selected_value || '1';

            const cpu = cpuRes.cpu_limit || {};
            info.innerHTML = `
                <span class="badge text-bg-light me-2">CPU Requested: ${cpu.requested ?? '-'}</span>
                <span class="badge text-bg-light me-2">Logical: ${cpu.logical ?? '-'}</span>
                <span class="badge text-bg-light me-2">Physical: ${cpu.physical ?? '-'}</span>
                <span class="badge text-bg-success me-2">CPU Effective: ${cpu.effective ?? '-'}</span>
                <span class="badge text-bg-info">Workers Effective: ${workerRes.effective_workers ?? '-'}</span>
            `;
        })
        .catch(err => {
            console.error('讀取 Runtime 設定失敗:', err);
            info.innerText = `讀取失敗: ${err.message}`;
        });
}

function saveRuntimeCpuSettings() {
    const sel = document.getElementById('cpuCoresSelect');
    const btn = document.getElementById('btnSaveCpuCores');
    if (!sel || !btn) return;

    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 套用中...';

    fetch('/api/llm/runtime/cpu', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cpu_cores: sel.value || 'auto' }),
    })
        .then(r => r.json())
        .then(res => {
            btn.disabled = false;
            btn.innerHTML = original;
            if (!res.ok) {
                alert(`套用失敗: ${res.msg || '未知錯誤'}`);
                return;
            }
            loadRuntimeCpuSettings();
            alert(`CPU 核數已套用（Effective: ${res.cpu_limit?.effective ?? '-'}）`);
        })
        .catch(err => {
            btn.disabled = false;
            btn.innerHTML = original;
            alert(`套用失敗: ${err.message}`);
        });
}

function saveFlowaWorkersSettings() {
    const sel = document.getElementById('flowaWorkersSelect');
    const btn = document.getElementById('btnSaveFlowaWorkers');
    if (!sel || !btn) return;

    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 套用中...';

    fetch('/api/llm/runtime/flowa_workers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_workers: sel.value || '1' }),
    })
        .then(r => r.json())
        .then(res => {
            btn.disabled = false;
            btn.innerHTML = original;
            if (!res.ok) {
                alert(`套用失敗: ${res.msg || '未知錯誤'}`);
                return;
            }
            loadRuntimeCpuSettings();
            alert(`Flow A workers 已套用（Effective: ${res.effective_workers ?? '-'}）`);
        })
        .catch(err => {
            btn.disabled = false;
            btn.innerHTML = original;
            alert(`套用失敗: ${err.message}`);
        });
}

// =========================================
// 2. Service Binding Logic
// =========================================

let globalLockedConnections = [];
let bindingCacheHtml = '';
let bindingRateLimitRetryTimer = null;
let bindingRateLimitRetryCount = 0;
let bindingLoadInFlight = false;

function getBindingStatusBanner() {
    const tbody = document.getElementById('binding-table-body');
    if (!tbody) return null;
    const table = tbody.closest('table');
    if (!table || !table.parentElement) return null;

    let banner = document.getElementById('binding-status-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'binding-status-banner';
        banner.className = 'alert py-2 px-3 mb-2 d-none';
        table.parentElement.insertBefore(banner, table);
    }
    return banner;
}

function showBindingStatusBanner(message, level = 'warning') {
    const banner = getBindingStatusBanner();
    if (!banner) return;
    banner.className = `alert alert-${level} py-2 px-3 mb-2`;
    banner.innerHTML = `<i class="bi bi-exclamation-circle-fill me-1"></i>${escapeHtml(message)}`;
}

function hideBindingStatusBanner() {
    const banner = document.getElementById('binding-status-banner');
    if (!banner) return;
    banner.className = 'alert py-2 px-3 mb-2 d-none';
    banner.textContent = '';
}

function scheduleBindingRateLimitRetry(waitSeconds) {
    const retrySeconds = Number.isFinite(waitSeconds) && waitSeconds > 0 ? waitSeconds : 60;
    if (bindingRateLimitRetryCount >= 1) {
        showBindingStatusBanner(`讀取頻率仍受限流，請 ${retrySeconds} 秒後手動重試。`, 'warning');
        return;
    }

    bindingRateLimitRetryCount += 1;
    if (bindingRateLimitRetryTimer) {
        clearTimeout(bindingRateLimitRetryTimer);
    }
    showBindingStatusBanner(`讀取頻率過高，已保留現有清單，${retrySeconds} 秒後自動重試一次。`, 'warning');
    bindingRateLimitRetryTimer = window.setTimeout(() => {
        bindingRateLimitRetryTimer = null;
        loadBindings();
    }, retrySeconds * 1000);
}

async function refreshBindingDropdowns(connectionsData = null) {
    if (!connectionsData) {
        try {
            const res = await fetch('/api/llm/connection/list');
            const data = await res.json();
            if(data.success && data.connections) {
                connectionsData = data.connections;
            } else {
                connectionsData = [];
            }
        } catch (e) {
            connectionsData = [];
        }
    }
    
    // 確保背景更新或無參數呼叫時，也能賦予連續視覺序號
    connectionsData.forEach((c, idx) => c.visual_id = idx + 1);
    
    globalLockedConnections = connectionsData.filter(c => c.status === 'locked');
    loadBindings(); 
}

async function loadBindings() {
    const tbody = document.getElementById('binding-table-body');
    if (!tbody) return;

    if (bindingLoadInFlight) return;
    bindingLoadInFlight = true;

    if (!bindingCacheHtml) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted"><span class="spinner-border spinner-border-sm"></span> 讀取綁定中...</td></tr>';
    }

    try {
        const res = await fetch('/api/llm/binding/list');

        if (!res.ok) {
            let errData = {};
            try {
                errData = await res.json();
            } catch (_) {
                errData = {};
            }
            const err = new Error(errData.message || `HTTP Server Error: ${res.status}`);
            err.status = res.status;
            const retryAfterRaw = res.headers.get('Retry-After');
            const retryAfterParsed = parseInt(retryAfterRaw || '', 10);
            if (!Number.isNaN(retryAfterParsed) && retryAfterParsed > 0) {
                err.retryAfter = retryAfterParsed;
            }
            throw err;
        }

        const data = await res.json();

        if (!data.success) {
            throw new Error(data.message || 'API responded with Success=False');
        }

        tbody.innerHTML = '';

        if (data.bindings && data.bindings.length > 0) {
            data.bindings.forEach(task => renderBindingRow(tbody, task));
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center text-warning">目前無任何任務綁定資料</td></tr>';
        }
        bindingCacheHtml = tbody.innerHTML;
        bindingRateLimitRetryCount = 0;
        if (bindingRateLimitRetryTimer) {
            clearTimeout(bindingRateLimitRetryTimer);
            bindingRateLimitRetryTimer = null;
        }
        hideBindingStatusBanner();
    } catch (e) {
        console.error('Load Bindings Error:', e);
        const isRateLimited = Number(e.status) === 429 || /(^|\s)429(\s|$)|Too Many Requests|per\s+1\s+minute/i.test(String(e.message || ''));
        if (isRateLimited) {
            if (bindingCacheHtml) {
                tbody.innerHTML = bindingCacheHtml;
            } else {
                tbody.innerHTML = '<tr><td colspan="4" class="text-center text-warning fw-bold"><i class="bi bi-hourglass-split"></i> 讀取頻率過高，請稍後重試。</td></tr>';
            }
            scheduleBindingRateLimitRetry(Number(e.retryAfter || 60));
        } else {
            bindingRateLimitRetryCount = 0;
            if (bindingRateLimitRetryTimer) {
                clearTimeout(bindingRateLimitRetryTimer);
                bindingRateLimitRetryTimer = null;
            }
            showBindingStatusBanner(`系統讀取異常: ${e.message}`, 'danger');
            tbody.innerHTML = `<tr><td colspan="4" class="text-center text-danger fw-bold"><i class="bi bi-exclamation-triangle-fill"></i> 系統讀取異常: ${escapeHtml(e.message)}</td></tr>`;
        }
    } finally {
        bindingLoadInFlight = false;
    }
}

function renderBindingRow(tbody, task) {
    const tr = document.createElement('tr');
    const isBound = task.connection_id !== null;
    
    const isLocked = Boolean(task.is_locked === 1 || task.is_locked === true || task.is_locked === '1');
    const safeTaskId = escapeHtml(task.task_id);
    const safeTaskIdJs = escapeJsSingle(task.task_id);

    let optionsHtml = '<option value="">-- 尚未指派 --</option>';
    globalLockedConnections.forEach(conn => {
        const selected = (task.connection_id === conn.id) ? 'selected' : '';
        // 下拉選單同步顯示連續視覺序號
        optionsHtml += `<option value="${conn.id}" ${selected}>LLM-${escapeHtml(conn.visual_id || conn.id)} (${escapeHtml(conn.model_name)})</option>`;
    });

    tr.innerHTML = `
        <td><i class="bi bi-cpu-fill text-primary"></i> ${safeTaskId}</td>
        <td>
            <select class="form-select form-select-sm binding-select" onchange="updateBinding('${safeTaskIdJs}', this.value)" ${isLocked ? 'disabled' : ''}>
                ${optionsHtml}
            </select>
        </td>
        <td>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary" onclick="testBinding('${safeTaskIdJs}', this)" ${!isBound ? 'disabled' : ''}>
                    <i class="bi bi-play-fill"></i> Test
                </button>
                <button class="btn btn-outline-success" onclick="lockBinding('${safeTaskIdJs}', this)" ${(!isBound || isLocked) ? 'disabled' : ''}>
                    <i class="bi bi-lock-fill"></i> Lock
                </button>
                <button class="btn btn-outline-secondary" onclick="unlockBinding('${safeTaskIdJs}', this)" ${!isLocked ? 'disabled' : ''}>
                    <i class="bi bi-unlock-fill"></i> Unlock
                </button>
            </div>
        </td>
        <td>
            ${isLocked 
                ? '<span class="badge bg-dark">已鎖定</span>' 
                : (isBound ? '<span class="badge bg-success">已配對</span>' : '<span class="badge bg-secondary">未配對</span>')}
        </td>
    `;
    tbody.appendChild(tr);
}

async function updateBinding(taskId, connId) {
    const targetConnId = connId ? parseInt(connId) : null;
    try {
        await fetch('/api/llm/binding/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ task_id: taskId, connection_id: targetConnId })
        });
        loadBindings();
    } catch(e) {
        console.error(e);
        alert('更新配對失敗');
    }
}

async function testBinding(taskId, btnEl) {
    const originalHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    
    try {
        const res = await fetch('/api/llm/binding/test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ task_id: taskId })
        });
        const result = await res.json();
        if (result.success) {
            alert('任務綁定測試成功: ' + result.message);
        } else {
            alert('測試失敗: ' + result.message);
        }
    } catch(e) {
        alert('系統錯誤: 無法執行測試');
    } finally {
        btnEl.disabled = false;
        btnEl.innerHTML = originalHtml;
    }
}

async function lockBinding(taskId, btnEl) {
    if (!confirm(`確定要鎖定 ${taskId} 的綁定狀態嗎？鎖定後將正式啟用此線路任務。`)) return;
    
    const originalHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    
    try {
        const res = await fetch('/api/llm/binding/lock', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ task_id: taskId })
        });
        const result = await res.json();
        if (result.success) {
            loadBindings();
        } else {
            alert('鎖定失敗: ' + result.message);
            btnEl.disabled = false;
            btnEl.innerHTML = originalHtml;
        }
    } catch(e) {
        alert('系統錯誤: 無法鎖定');
        btnEl.disabled = false;
        btnEl.innerHTML = originalHtml;
    }
}

async function unlockBinding(taskId, btnEl) {
    if (!confirm(`確定要解除 ${taskId} 的鎖定嗎？解除後可重新調整綁定。`)) return;

    const originalHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
        const res = await fetch('/api/llm/binding/unlock', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ task_id: taskId })
        });
        const result = await res.json();
        if (result.success) {
            loadBindings();
        } else {
            alert('解除鎖定失敗: ' + result.message);
            btnEl.disabled = false;
            btnEl.innerHTML = originalHtml;
        }
    } catch(e) {
        alert('系統錯誤: 無法解除鎖定');
        btnEl.disabled = false;
        btnEl.innerHTML = originalHtml;
    }
}
