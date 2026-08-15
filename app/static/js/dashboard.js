// Roothinks source maintenance contract
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 檔案路徑: roothinks/app/static/js/dashboard.js
// 產生時間: 2026-07-19 11:40 +08:00
// 版本: v2.0
// 模組定位:
//   Dashboard 專案管理中心前端:專案列表、建立/編輯 modal、刪除流程。
// 主要責任: 初始化 Dashboard 專案清單、workflow status、成員選單與導頁狀態，依 server 授權結果顯示操作。
//   1. fetchProjects/switchDashboardView:列表載入與 tab 切換。
//   2. submitCreate:建立/更新專案(含成員組織表)。
//   3. [v2.0] openMembersModal:成員管理 modal(GET/POST/PATCH/DELETE members API)。
// 維護提醒:
//   - v1.9 UX 修復(UI 走查實測):
//     (1) 建立驗證改為與後端/UI 標示一致——只有「專案名稱」與「一位主持人
//         (姓名至少一種)」必填;舊版強制 Table 1 全欄位+PI 單位,
//         但 UI 只在名稱標 *,使用者必被 alert 擋下。
//     (2) submitCreate 流程的 alert() 改為 modal 內 inline 錯誤(#create-form-error)
//         與非阻塞 toast;原生 alert 會凍住頁面且自動化不可測。
//   - 其餘既有 alert()(約 120 處)尚未替換,屬後續 UX 債。
//   - v2.0 本次新增成員管理 modal(#membersModal):owner 可新增/改角色/移除成員;
//     非 owner 唯讀+退出;AUTH_MODE=none(me 401)顯示單機提示。
// 驗證方式:
//   - preview UI 走查:註冊→登入→建專案→列表出現;pytest 全套不涉及本檔。
// ------------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const initialStatus = params.get('status') === 'formal' ? 'formal' : 'temp';
    switchDashboardView(initialStatus);
});

// [feature-flag] 「成員管理 (Members)」選單暫時隱藏（2026-07-28，產品決定）。
//
// 為什麼用開關而不是把三處樣板註解掉：這個選單項在三個地方各渲染一次
// （待擬研究的唯讀卡、可編輯卡、正式研究卡），逐處註解容易漏掉其中一處，
// 之後要恢復也得三處都改回來。集中在這裡一行控制。
//
// 只藏前端入口，modal（#membersModal）、openMembersModal() 與後端
// /api/projects/<pid>/members 全部保留且仍受權限保護——這不是安全措施，
// 只是先不讓使用者從選單走進來。要恢復把這個值改回 true 即可。
const SHOW_MEMBERS_MENU = false;

const MEMBERS_MENU_ITEM = (safePidJs) => SHOW_MEMBERS_MENU
    ? `<li><a class="dropdown-item" href="#" onclick="openMembersModal('${safePidJs}')"><i class="bi bi-people me-2"></i>成員管理 (Members)</a></li>`
    : '';

/**
 * 這個成員是不是「主持人 (PI)」。
 *
 * 必須用 startsWith 而不是 includes——「共同主持人 (Co-PI)」這個字串
 * 本身就包含「主持人」三個字。用 includes 的話 Co-PI 會被算成第二位 PI，
 * 一個正常的「一位 PI + 一位 Co-PI」專案就會被「必須且只能有一位主持人」
 * 擋下來，人員加不了也存不了。
 *
 * 語意刻意與後端 _validate_members 的 role.startswith('主持人') 一致：
 * 兩邊判斷不同的話，前端會擋掉後端其實接受的資料（這正是原本的狀況）。
 */
function isPrincipalInvestigator(role) {
    return String(role || '').startsWith('主持人');
}

function isAcademicLead(role) {
    const value = String(role || '');
    return isPrincipalInvestigator(value)
        || value.startsWith('共同主持人')
        || value.toLowerCase().includes('co-pi');
}

// [workflow] 五模組進度改在 dashboard 一覽。
//
// 原本這條狀態列出現在 PAQ/Literature/Study/Manuscript/Submit 五個模組頁，
// 但那五頁的導覽列本來就有同樣五個模組的按鈕——同一組資訊重複兩次，
// 還佔掉每頁頂部的垂直空間。改成只在 dashboard 呈現，且是「每張專案卡各自的
// 進度」，這樣一眼就能比較所有專案，而不是切進某一個專案才看得到它的進度。
const WORKFLOW_LABELS = {
    paq: 'PAQ', literature: '文獻', study: '研究',
    manuscript: '手稿', submit: '投稿',
};
const WORKFLOW_STATUS_LABELS = {
    complete: '完成', ready: '可進行', working: '進行中',
    blocked: '受阻', empty: '未開始',
};
const WORKFLOW_TONE = {
    complete: 'bg-success', ready: 'bg-primary', working: 'bg-warning',
    blocked: 'bg-secondary', empty: 'bg-light text-muted border',
};

async function renderCardWorkflows(projects) {
    const hosts = document.querySelectorAll('[data-workflow-card]');
    if (!hosts.length) return;
    const pids = Array.from(hosts).map(h => h.dataset.workflowCard).filter(Boolean);
    if (!pids.length) return;
    try {
        // 批次端點：逐張卡各打一次的話，9 個專案就是 9 次請求。
        const res = await fetch('/api/project/workflow?pids='
            + encodeURIComponent(pids.join(',')));
        const data = await res.json();
        if (!data || !data.success) return;
        hosts.forEach(host => {
            const wf = (data.projects || {})[host.dataset.workflowCard];
            // 契約：{ order: [key...], modules: { key: {status, detail, done, total, href} } }
            // 不是陣列、欄位叫 status 不叫 state——第一版寫錯過，改動時請對照
            // app/core_pro/workflow_status.py 的 _module()。
            if (!wf || !wf.modules) { host.innerHTML = ''; return; }
            const order = Array.isArray(wf.order) && wf.order.length
                ? wf.order : ['paq', 'literature', 'study', 'manuscript', 'submit'];
            host.innerHTML = order.map(k => {
                const m = wf.modules[k] || {};
                const tone = WORKFLOW_TONE[m.status] || WORKFLOW_TONE.empty;
                const status = WORKFLOW_STATUS_LABELS[m.status] || WORKFLOW_STATUS_LABELS.empty;
                const tip = `${WORKFLOW_LABELS[k] || k}：${status}`
                          + (m.detail ? ` — ${m.detail}` : '');
                return `<span class="badge ${tone} me-1 mb-1" title="${escapeHtml(tip)}"
                              aria-label="${escapeHtml(tip)}" style="font-weight:600;">`
                     + `${escapeHtml(WORKFLOW_LABELS[k] || k)} · ${escapeHtml(status)}</span>`;
            }).join('');
        });
    } catch (err) {
        console.warn('[workflow] 進度載入失敗', err);
    }
}

let currentStatus = 'temp';
let allProjectsCache = [];

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ==========================================
// 1. 視圖切換 (View Switching)
// ==========================================
async function switchDashboardView(status) {
    currentStatus = status;

    document.querySelectorAll('#dashboardTabs .nav-link').forEach(el => el.classList.remove('active'));
    document.getElementById(`tab-${status}`).classList.add('active');

    document.getElementById('view-temp').classList.add('d-none');
    document.getElementById('view-formal').classList.add('d-none');
    document.getElementById(`view-${status}`).classList.remove('d-none');

    // 完全獨立控制兩個按鈕區塊的顯示
    if (status === 'temp') {
        document.getElementById('btn-group-temp').classList.remove('d-none');
        document.getElementById('btn-group-formal').classList.add('d-none');
    } else {
        document.getElementById('btn-group-temp').classList.add('d-none');
        document.getElementById('btn-group-formal').classList.remove('d-none');
    }

    await fetchProjects(status);
}

// ==========================================
// 1.1 View State Helper
// ==========================================
function showViewState(status, state, errorMsg = '') {
    const viewPrefix = status === 'temp' ? 'temp' : 'formal';
    const loadingEl = document.getElementById(`loading-${viewPrefix}`);
    const containerEl = document.getElementById(`container-${viewPrefix}`);
    const emptyEl = document.getElementById(`empty-${viewPrefix}`);

    if (loadingEl) loadingEl.classList.add('d-none');
    if (containerEl) containerEl.classList.add('d-none');
    if (emptyEl) emptyEl.classList.add('d-none');

    if (state === 'loading' && loadingEl) {
        loadingEl.classList.remove('d-none');
    } else if (state === 'content' && containerEl) {
        containerEl.classList.remove('d-none');
    } else if (state === 'empty' && emptyEl) {
        emptyEl.classList.remove('d-none');
    } else if (state === 'error' && containerEl) {
        containerEl.classList.remove('d-none');
        containerEl.innerHTML = `
            <div class="col-12 text-center py-5 text-danger">
                <i class="bi bi-exclamation-triangle-fill display-4 d-block mb-2"></i>
                <p class="fw-bold">載入失敗 (Load Failed)</p>
                <p class="text-muted small">${escapeHtml(errorMsg)}</p>
            </div>
        `;
    }
}

// ==========================================
// 2. 資料獲取與渲染 (Fetching & Rendering)
// ==========================================
async function fetchProjects(status) {
    showViewState(status, 'loading');

    try {
        let projects = [];
        
        if (status === 'temp') {
            const [resTemp, resRO] = await Promise.all([
                fetch(`/api/project/list?status=temp`),
                fetch(`/api/project/list?status=readonly`)
            ]);
            const dataTemp = await resTemp.json();
            const dataRO = await resRO.json();

            if (dataTemp.success && dataTemp.projects) projects.push(...dataTemp.projects);
            if (dataRO.success && dataRO.projects) projects.push(...dataRO.projects);

            projects.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        } else {
            const res = await fetch(`/api/project/list?status=${status}`);
            const data = await res.json();
            if (data.success && data.projects) projects = data.projects;
        }

        allProjectsCache = projects; 
        
        if (projects.length === 0) {
            showViewState(status, 'empty');
        } else {
            showViewState(status, 'content');
            if (status === 'temp') {
                renderTempCards(projects);
            } else {
                renderFormalCards(projects);
            }
            renderCardWorkflows(projects);
            renderCardProgress(projects);
        }
    } catch (e) {
        console.error(e);
        showViewState(status, 'error', 'Network Error: ' + (e.message || 'Connection Failed'));
    }
}

function renderTempCards(projects) {
    const container = document.getElementById('container-temp');
    if (!container) return; 
    container.innerHTML = '';

    projects.forEach(p => {
        const projectId = String(p.project_id || '');
        const safePidJs = projectId.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const projectIdUrl = encodeURIComponent(projectId);

        let piName = 'Unknown';
        if (p.members && Array.isArray(p.members)) {
            const pi = p.members.find(m => isPrincipalInvestigator(m.role));
            if (pi) {
                if (pi.name && typeof pi.name === 'object' && pi.name.en) {
                    piName = `${pi.name.en.given || ''} ${pi.name.en.surname || ''}`.trim();
                } else if (typeof pi.name === 'string') {
                    piName = pi.name;
                }
            }
        }
        
        const motivationRaw = (p.context_background || p.background || '').substring(0, 100) + (p.context_background?.length > 100 ? '...' : '');
        const motivation = escapeHtml(motivationRaw || '無背景描述...');
        const safeName = escapeHtml(p.name || '');
        const safeAbbr = escapeHtml(p.abbreviation || '');
        const safePiName = escapeHtml(piName);
        let timeStr = '--';
        if (p.created_at) timeStr = p.created_at.substring(0, 10);
        const safeTime = escapeHtml(timeStr);

        const isReadonly = p.status === 'readonly';
        const cardStyle = isReadonly ? 'background-color: #f8f9fa; opacity: 0.95;' : 'background-color: #f0f8ff;';
        
        const badgeHtml = isReadonly 
            ? `<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-pill px-2" title="此專案已轉正，僅供唯讀"><i class="bi bi-lock-fill"></i> ${escapeHtml(projectId)}</span>`
            : `<span class="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25 rounded-pill px-2">${escapeHtml(projectId)}</span>`;
            
        const actionMenu = isReadonly
            ? `<li><a class="dropdown-item" href="#" onclick="openCreateModal('${safePidJs}', true)"><i class="bi bi-eye me-2"></i>檢視 (View)</a></li>
               ${MEMBERS_MENU_ITEM(safePidJs)}`
            : `<li><a class="dropdown-item" href="#" onclick="openCreateModal('${safePidJs}')"><i class="bi bi-pencil me-2"></i>編輯 (Edit)</a></li>
               ${MEMBERS_MENU_ITEM(safePidJs)}`;
            
        const enterBtn = isReadonly
            ? `<a href="/paq?pid=${projectIdUrl}" class="btn btn-outline-secondary w-100 fw-bold"><i class="bi bi-lock-fill me-1"></i> 唯讀工作檯 (Read-Only)</a>`
            : `<a href="/paq?pid=${projectIdUrl}" class="btn btn-outline-primary w-100 fw-bold"><i class="bi bi-box-arrow-in-right me-1"></i> 進入 PAQ 工作檯</a>`;

        const col = document.createElement('div');
        col.className = 'col-md-6 col-lg-4';
        
        col.innerHTML = `
            <div class="card h-100 shadow-sm project-card" style="${cardStyle}">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        ${badgeHtml}
                        <div class="dropdown">
                            <button class="btn btn-link text-muted p-0" data-bs-toggle="dropdown">
                                <i class="bi bi-three-dots-vertical"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end shadow">
                                ${actionMenu}
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="#" onclick="deleteProject(event, '${safePidJs}')"><i class="bi bi-trash me-2"></i>刪除 (Delete)</a></li>
                            </ul>
                        </div>
                    </div>
                    
                    <h5 class="card-title fw-bold ${isReadonly ? 'text-secondary' : 'text-primary'} mb-1 text-wrap text-break cursor-pointer" style="text-decoration: underline;" title="點擊檢視專案詳情" onclick="viewProjectReadOnly('${safePidJs}', 'temp')">
                        ${safeName}
                    </h5>
                    <p class="card-subtitle text-muted small mb-3">${safeAbbr}</p>
                    
                    <div class="mb-3">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-person-circle text-secondary me-2"></i>
                            <span class="text-dark small fw-bold">${safePiName}</span>
                        </div>
                        <div class="d-flex align-items-center text-muted small">
                            <i class="bi bi-clock me-2"></i>
                            <span>${safeTime}</span>
                        </div>
                    </div>

                    <p class="card-text text-secondary small text-truncate-2" style="min-height: 40px;">
                        ${motivation}
                    </p>
                </div>
                <div class="card-footer bg-white border-top-0 pt-0 pb-3">
                    <div class="rt-card-workflow small mb-2" data-workflow-card="${escapeHtml(projectId)}"></div>
                    ${enterBtn}
                </div>
            </div>
        `;
        container.appendChild(col);
    });
}

function renderFormalCards(projects) {
    const container = document.getElementById('container-formal');
    if (!container) return;
    container.innerHTML = '';

    projects.forEach(p => {
        const projectId = String(p.project_id || '');
        const safePidJs = projectId.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const projectIdUrl = encodeURIComponent(projectId);
        let piName = 'Unknown';
        if (p.members && Array.isArray(p.members)) {
            const pi = p.members.find(m => isPrincipalInvestigator(m.role));
            if (pi) {
                if (pi.name && typeof pi.name === 'object' && pi.name.en) {
                    piName = `${pi.name.en.given || ''} ${pi.name.en.surname || ''}`.trim();
                } else if (typeof pi.name === 'string') {
                    piName = pi.name;
                }
            }
        }
        
        const summaryRaw = (p.ai_summary || '').substring(0, 100) + (p.ai_summary?.length > 100 ? '...' : '尚未生成 AI 摘要');
        const summary = escapeHtml(summaryRaw);
        const safeResearchTitle = escapeHtml(p.research_title || p.name || '');
        const safeAbbr = escapeHtml(p.abbreviation || '');
        const safePiName = escapeHtml(piName);
        let timeStr = '--';
        if (p.created_at) timeStr = p.created_at.substring(0, 10);
        const safeTime = escapeHtml(timeStr);

        const col = document.createElement('div');
        col.className = 'col-md-6 col-lg-4';
        
        col.innerHTML = `
            <div class="card h-100 shadow-sm project-card" style="background-color: #f5fff5; border-color: #c3e6cb;">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25 rounded-pill px-2">
                            ${escapeHtml(projectId)}
                        </span>
                        <div class="dropdown">
                            <button class="btn btn-link text-muted p-0" data-bs-toggle="dropdown">
                                <i class="bi bi-three-dots-vertical"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end shadow">
                                <li><a class="dropdown-item" href="#" onclick="openCreateFormalModal('${safePidJs}')"><i class="bi bi-pencil me-2"></i>編輯 (Edit)</a></li>
                                ${MEMBERS_MENU_ITEM(safePidJs)}
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="#" onclick="deleteProject(event, '${safePidJs}')"><i class="bi bi-trash me-2"></i>刪除 (Delete)</a></li>
                            </ul>
                        </div>
                    </div>
                    
                    <h5 class="card-title fw-bold text-success mb-1 text-wrap text-break cursor-pointer" style="text-decoration: underline;" title="點擊檢視專案詳情" onclick="viewProjectReadOnly('${safePidJs}', 'formal')">
                        ${safeResearchTitle}
                    </h5>
                    <p class="card-subtitle text-muted small mb-3">${safeAbbr}</p>
                    
                    <div class="mb-3">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-person-circle text-secondary me-2"></i>
                            <span class="text-dark small fw-bold">${safePiName}</span>
                        </div>
                        <div class="d-flex align-items-center text-muted small">
                            <i class="bi bi-clock me-2"></i>
                            <span>${safeTime}</span>
                        </div>
                    </div>

                    <div class="card-text small text-truncate-2 p-2 bg-white rounded border border-success border-opacity-25" style="min-height: 50px;">
                        <i class="bi bi-robot text-success me-1"></i> ${summary}
                    </div>
                </div>
                <div class="card-footer bg-white border-top-0 pt-0 pb-3">
                    <div class="rt-card-workflow small mb-2" data-workflow-card="${escapeHtml(projectId)}"></div>
                    <div class="rt-card-progress small mb-2" data-progress-card="${escapeHtml(projectId)}"></div>
                    <div class="row g-2">
                        <div class="col-6">
                            <a href="/literature?pid=${projectIdUrl}" class="btn btn-success w-100 fw-bold">
                                <i class="bi bi-journal-check me-1"></i> 正式工作檯
                            </a>
                        </div>
                        <div class="col-6">
                            <a href="/manuscript?pid=${projectIdUrl}" class="btn btn-outline-warning w-100 fw-bold">
                                <i class="bi bi-pen me-1"></i> Manuscript
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        `;
        container.appendChild(col);
    });
}

// ==========================================
// 3. 專案建立與編輯 (兩條獨立路徑)
// ==========================================

let isEdit = false;
let editPid = null;

function viewProjectReadOnly(pid, type) {
    if (type === 'formal') {
        openCreateFormalModal(pid, true);
    } else {
        openCreateModal(pid, true);
    }
}

/**
 * 路徑一：獨立的「待擬研究」Modal 控制器
 */
function openCreateModal(pid = null, readOnly = false) {
    const form = document.getElementById('projectForm');
    if(form) form.reset();
    document.getElementById('c-personnel-container').innerHTML = '';
    
    document.getElementById('c_target_status').value = 'temp'; // 指定建立 temp

    const titleEl = document.getElementById('createModalLabel');
    const btnEl = document.getElementById('btn-submit-project');
    const headerBg = document.getElementById('modal-header-bg');
    const aiSummaryBlock = document.getElementById('ai-summary-block');
    
    // 待擬研究一律隱藏 AI 摘要
    aiSummaryBlock.style.display = 'none';
    headerBg.className = 'modal-header bg-primary text-white';

    if (pid) {
        const project = allProjectsCache.find(p => p.project_id === pid);
        isEdit = !readOnly;
        editPid = isEdit ? pid : null;
        
        titleEl.innerHTML = readOnly ? `<i class="bi bi-eye"></i> 檢視待擬研究 (View Project: #${pid})` : `<i class="bi bi-pencil-square"></i> 編輯待擬研究 (Edit Project: #${pid})`;
        
        if (btnEl) {
            btnEl.className = 'btn btn-primary px-4';
            btnEl.innerHTML = '<i class="bi bi-save"></i> 更新專案 (Update)';
            if (readOnly) btnEl.classList.add('d-none'); else btnEl.classList.remove('d-none');
        }

        document.getElementById('c_name').value = project.name || '';
        document.getElementById('c_abbr').value = project.abbreviation || '';
        document.getElementById('c_class').value = project.classification || '';
        document.getElementById('c_keywords').value = project.keywords || '';
        document.getElementById('c_context').value = project.context_background || project.background || '';

        if (project.members && project.members.length > 0) project.members.forEach(m => addMemberRow(m)); else addMemberRow();

    } else {
        isEdit = false;
        editPid = null;
        titleEl.innerHTML = '<i class="bi bi-plus-circle"></i> 新增待擬研究 (New Provisional)';
        if(btnEl) {
            btnEl.classList.remove('d-none');
            btnEl.className = 'btn btn-primary px-4';
            btnEl.innerHTML = '<i class="bi bi-rocket-takeoff"></i> 建立專案 (Create)';
        }
        addMemberRow();
    }
    
    applyReadOnlyState(form, readOnly);
    new bootstrap.Modal(document.getElementById('createModal')).show();
}

/**
 * 路徑二：完全獨立的「正式研究」Modal 控制器
 */
function openCreateFormalModal(pid = null, readOnly = false) {
    const form = document.getElementById('projectForm');
    if(form) form.reset();
    document.getElementById('c-personnel-container').innerHTML = '';
    
    document.getElementById('c_target_status').value = 'formal'; // 指定建立 formal

    const titleEl = document.getElementById('createModalLabel');
    const btnEl = document.getElementById('btn-submit-project');
    const headerBg = document.getElementById('modal-header-bg');
    const aiSummaryBlock = document.getElementById('ai-summary-block');
    
    // 1000000% 必須展示 AI 摘要
    aiSummaryBlock.style.display = 'block';
    headerBg.className = 'modal-header bg-success text-white';

    if (pid) {
        const project = allProjectsCache.find(p => p.project_id === pid);
        isEdit = !readOnly;
        editPid = isEdit ? pid : null;
        
        titleEl.innerHTML = readOnly ? `<i class="bi bi-eye"></i> 檢視正式研究 (View Formal: #${pid})` : `<i class="bi bi-pencil-square"></i> 編輯正式研究 (Edit Formal: #${pid})`;
        
        if (btnEl) {
            btnEl.className = 'btn btn-success px-4';
            btnEl.innerHTML = '<i class="bi bi-save"></i> 更新正式專案 (Update)';
            if (readOnly) btnEl.classList.add('d-none'); else btnEl.classList.remove('d-none');
        }

        document.getElementById('c_name').value = project.name || '';
        document.getElementById('c_abbr').value = project.abbreviation || '';
        document.getElementById('c_class').value = project.classification || '';
        document.getElementById('c_keywords').value = project.keywords || '';
        document.getElementById('c_context').value = project.context_background || project.background || '';
        document.getElementById('c_ai_summary').value = project.ai_summary || '等待系統執行摘要...';

        if (project.members && project.members.length > 0) project.members.forEach(m => addMemberRow(m)); else addMemberRow();

    } else {
        isEdit = false;
        editPid = null;
        titleEl.innerHTML = '<i class="bi bi-plus-circle"></i> 新增正式研究 (New Formal Project)';
        document.getElementById('c_ai_summary').value = '等待系統執行摘要...'; // 預設字串

        if(btnEl) {
            btnEl.classList.remove('d-none');
            btnEl.className = 'btn btn-success px-4';
            btnEl.innerHTML = '<i class="bi bi-rocket-takeoff"></i> 建立正式專案 (Create Formal)';
        }
        addMemberRow();
    }
    
    applyReadOnlyState(form, readOnly);
    new bootstrap.Modal(document.getElementById('createModal')).show();
}

function applyReadOnlyState(form, readOnly) {
    if (!form) return;
    const inputs = form.querySelectorAll('input, select, textarea:not(#c_ai_summary)');
    const actionBtns = form.querySelectorAll('.btn-outline-danger, .btn-add-email, .btn-remove-email');
    const addPersonBtn = document.querySelector('#createModal .card-header .btn-success');
    
    if (readOnly) {
        inputs.forEach(el => el.setAttribute('disabled', 'true'));
        actionBtns.forEach(el => el.style.display = 'none');
        if (addPersonBtn) addPersonBtn.classList.add('d-none');
    } else {
        inputs.forEach(el => el.removeAttribute('disabled'));
        actionBtns.forEach(el => el.style.display = '');
        if (addPersonBtn) addPersonBtn.classList.remove('d-none');
    }
}

function addMemberRow(data = null) {
    const container = document.getElementById('c-personnel-container');
    const div = document.createElement('div');
    div.className = 'member-card member-row card mb-3 border-start border-4 border-info shadow-sm';
    
    const safeGet = (obj, path, def = '') => {
        try {
            return path.split('.').reduce((o, k) => (o || {})[k], obj) || def;
        } catch (e) { return def; }
    };

    const role = data ? data.role : '主持人 (Principal Investigator)';
    // 系統權限與論文署名角色是兩回事，分開儲存於 access_role。
    const storedAccessRole = (data && data.access_role) ? data.access_role : '';
    const accessRole = storedAccessRole || (isAcademicLead(role) ? 'editor' : '');
    const title = data ? data.title : '';
    const enSurname = data ? safeGet(data, 'name.en.surname') : '';
    const enGiven = data ? safeGet(data, 'name.en.given') : '';
    const enMiddle = data ? safeGet(data, 'name.en.middle') : '';
    const orgSurname = data ? safeGet(data, 'name.original.surname') : '';
    const orgGiven = data ? safeGet(data, 'name.original.given') : '';
    const orgMiddle = data ? safeGet(data, 'name.original.middle') : '';
    // [org-v2] 所屬單位改為五層，順序由小到大：
    //   L1 Lab/Unit → L2 Dept./Div. → L3 Institution/Branch → L4 Univ./Co. → L5 Nationality/Region
    //
    // 舊版只有三層而且方向相反（L1=Institution 最大、L3=Lab 最小）。
    // 既有資料由 scripts/migrate_organization_v2.py 一次搬完（舊 l1→新 l4、
    // 舊 l3→新 l1），這裡只針對「漏網的舊格式」再擋一次：沒有 l4/l5 鍵
    // 就代表這筆還是舊三層，讀取時就地翻轉，免得把大學顯示成實驗室。
    const orgRaw = (data && data.organization) ? data.organization : {};
    const orgIsLegacy = !('l4' in orgRaw) && !('l5' in orgRaw)
        && ('l1' in orgRaw || 'l2' in orgRaw || 'l3' in orgRaw);
    const org = orgIsLegacy
        ? { l1: orgRaw.l3 || '', l2: orgRaw.l2 || '', l3: '', l4: orgRaw.l1 || '', l5: '' }
        : orgRaw;
    const orgL1 = org.l1 || '';
    const orgL2 = org.l2 || '';
    const orgL3 = org.l3 || '';
    const orgL4 = org.l4 || '';
    const orgL5 = org.l5 || '';
    const address = data ? data.address : '';
    
    let emailHtml = '';
    const emails = (data && data.emails && Array.isArray(data.emails)) ? data.emails : ['']; 
    if (emails.length === 0) emails.push('');
    
    emails.forEach((email, idx) => {
        emailHtml += `
            <div class="email-row d-flex align-items-center mt-1">
                <input type="email" class="form-control form-control-sm email-input" placeholder="email@example.com" value="${email}">
                ${idx === 0 
                    ? `<i class="bi bi-plus-circle-fill ms-2 btn-add-email text-success cursor-pointer" onclick="addEmailField(this)" title="Add Email" style="${emails.length >=3 ? 'display:none':''}"></i>`
                    : `<i class="bi bi-dash-circle-fill ms-2 btn-remove-email text-danger cursor-pointer" onclick="removeEmailField(this)" title="Remove"></i>`
                }
            </div>
        `;
    });

    div.innerHTML = `
        <div class="card-body p-3">
            <div class="d-flex justify-content-between mb-2">
                <h6 class="fw-bold text-info"><i class="bi bi-person-vcard"></i> 成員資料 (Member Profile)</h6>
                <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="this.closest('.member-card').remove()">
                    <i class="bi bi-trash"></i> 移除
                </button>
            </div>
            
            <div class="row g-2 mb-2">
                <div class="col-md-6">
                    <label class="small text-muted">角色 (Role)</label>
                    <select class="form-select form-select-sm role-select">
                        <option value="主持人 (Principal Investigator)" ${isPrincipalInvestigator(role) ? 'selected' : ''}>1. 主持人 (Principal Investigator)</option>
                        <option value="共同主持人 (Co-PI)" ${role.includes('共同主持人') ? 'selected' : ''}>2. 共同主持人 (Co-PI)</option>
                        <option value="主貢獻人員 (Principal Contributor)" ${role.includes('主貢獻') ? 'selected' : ''}>3. 主貢獻人員 (Principal Contributor)</option>
                        <option value="通信窗口 (Correspondent)" ${role.includes('通信') ? 'selected' : ''}>4. 通信窗口 (Correspondent)</option>
                        <option value="合作人員 (Collaborator)" ${role.includes('合作') ? 'selected' : ''}>5. 合作人員 (Collaborator)</option>
                        <option value="支援人員 (Supporter)" ${role.includes('支援') ? 'selected' : ''}>6. 支援人員 (Supporter)</option>
                    </select>
                </div>
                <div class="col-md-6">
                    <label class="small text-muted">職銜 (Title)</label>
                    <input type="text" class="form-control form-control-sm title-input" placeholder="e.g. Professor" value="${title}">
                </div>
            </div>

            <!-- [collab] 一般署名角色與系統權限分開；PI／Co-PI 例外，
                 依產品規則最低為 editor（全章節讀寫）。 -->
            <div class="row g-2 mb-2 border rounded p-2" style="background:#f1f8ff;">
                <div class="col-md-6">
                    <label class="small fw-bold text-primary">
                        <i class="bi bi-shield-lock me-1"></i>系統權限 (System Access)
                    </label>
                    <select class="form-select form-select-sm access-select">
                        <option value="" ${!accessRole ? 'selected' : ''}>不授予（僅列名）</option>
                        <option value="viewer" ${accessRole === 'viewer' ? 'selected' : ''}>檢視者 — 唯讀，看得到所有章節</option>
                        <option value="coauthor" ${accessRole === 'coauthor' ? 'selected' : ''}>限定編輯 — 只讀寫被指派的章節</option>
                        <option value="editor" ${accessRole === 'editor' ? 'selected' : ''}>總編輯 — 讀寫所有章節</option>
                        <option value="owner" ${accessRole === 'owner' ? 'selected' : ''}>擁有者 — 全部權限，可管理成員</option>
                    </select>
                </div>
                <div class="col-md-6 d-flex align-items-end">
                    <div class="text-muted" style="font-size:.72rem;">
                        依下方第一個 Email 對應已註冊帳號授權。<br>
                        PI／Co-PI 最低為總編輯；未註冊 Email 會略過。
                    </div>
                </div>
            </div>

            <div class="row g-2 mb-2 bg-light p-1 rounded">
                <div class="col-12"><small class="fw-bold text-dark">英文姓名 (English Name - Primary)</small></div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-surname" placeholder="Surname" value="${enSurname}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-given" placeholder="Given Name" value="${enGiven}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-middle" placeholder="Middle Name" value="${enMiddle}">
                </div>
            </div>

            <div class="row g-2 mb-2">
                <div class="col-12"><small class="fw-bold text-secondary">原文姓名 (Native/Original Name)</small></div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-surname" placeholder="姓" value="${orgSurname}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-given" placeholder="名" value="${orgGiven}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-middle" placeholder="中間名" value="${orgMiddle}">
                </div>
            </div>

            <div class="row g-2 mb-2 border-top pt-2">
                <div class="col-12">
                    <small class="fw-bold text-secondary">所屬單位 (Organization Hierarchy)</small>
                    <small class="text-muted ms-2">由小到大，可只填知道的層級</small>
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l1" placeholder="L1: Lab/Unit" value="${orgL1}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l2" placeholder="L2: Dept./Div." value="${orgL2}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l3" placeholder="L3: Institution/Branch" value="${orgL3}">
                </div>
                <div class="col-md-6">
                    <input type="text" class="form-control form-control-sm org-l4" placeholder="L4: Univ./Co." value="${orgL4}">
                </div>
                <div class="col-md-6">
                    <input type="text" class="form-control form-control-sm org-l5" placeholder="L5: Nationality/Region" value="${orgL5}">
                </div>
            </div>

            <div class="row g-2 mb-2">
                <div class="col-12">
                    <label class="small text-muted">電子郵件 (Emails)</label>
                    <div class="email-container">
                        ${emailHtml}
                    </div>
                </div>
            </div>

            <div class="row g-2">
                <div class="col-12">
                    <label class="small text-muted">通訊地址 (Address)</label>
                    <input type="text" class="form-control form-control-sm address-input" placeholder="完整通訊地址" value="${address}">
                </div>
            </div>
        </div>
    `;
    container.appendChild(div);

    const roleSelect = div.querySelector('.role-select');
    const accessSelect = div.querySelector('.access-select');
    const enforceLeadAccess = () => {
        const lead = isAcademicLead(roleSelect.value);
        Array.from(accessSelect.options).forEach((option) => {
            option.disabled = lead && ['', 'viewer', 'coauthor'].includes(option.value);
        });
        if (lead && !['editor', 'owner'].includes(accessSelect.value)) {
            accessSelect.value = 'editor';
        }
    };
    roleSelect.addEventListener('change', enforceLeadAccess);
    enforceLeadAccess();
}

function addEmailField(btn) {
    const container = btn.closest('.email-container');
    const rows = container.querySelectorAll('.email-row');
    if (rows.length >= 3) {
        alert('最多只能新增 3 組 Email (Max 3 emails allowed)');
        return;
    }
    const newRow = document.createElement('div');
    newRow.className = 'email-row d-flex align-items-center mt-1';
    newRow.innerHTML = `
        <input type="email" class="form-control form-control-sm email-input" placeholder="Next email...">
        <i class="bi bi-dash-circle-fill ms-2 btn-remove-email text-danger cursor-pointer" onclick="removeEmailField(this)" title="Remove"></i>
    `;
    container.appendChild(newRow);
    
    if (container.querySelectorAll('.email-row').length >= 3) {
        const firstRowBtn = container.querySelector('.btn-add-email');
        if(firstRowBtn) firstRowBtn.style.display = 'none';
    }
}

function removeEmailField(btn) {
    const container = btn.closest('.email-container');
    btn.closest('.email-row').remove();
    
    if (container.querySelectorAll('.email-row').length < 3) {
        const firstRowBtn = container.querySelector('.btn-add-email');
        if(firstRowBtn) firstRowBtn.style.display = 'inline-block';
    }
}

// v1.9: modal 內 inline 錯誤顯示(取代阻塞式 alert)
function showCreateFormError(msg) {
    const box = document.getElementById('create-form-error');
    if (box) {
        box.textContent = msg;
        box.classList.remove('d-none');
        box.scrollIntoView({ block: 'nearest' });
    } else {
        alert(msg); // 後備:error div 不存在時退回 alert
    }
}

function clearCreateFormError() {
    const box = document.getElementById('create-form-error');
    if (box) box.classList.add('d-none');
}

// v1.9: 非阻塞成功提示(右下角自動消失)
function showToast(msg) {
    const t = document.createElement('div');
    t.className = 'position-fixed bottom-0 end-0 m-3 alert alert-success shadow';
    t.style.zIndex = 2000;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
}

async function submitCreate() {
    clearCreateFormError();
    const name = document.getElementById('c_name').value.trim();
    const abbreviation = document.getElementById('c_abbr').value.trim();
    const classification = document.getElementById('c_class').value.trim();
    const keywords = document.getElementById('c_keywords').value.trim();
    const context = document.getElementById('c_context').value.trim();

    // 讀取隱藏欄位，確認是要建立 temp 還是 formal
    const statusVal = document.getElementById('c_target_status') ? document.getElementById('c_target_status').value : 'temp';

    // v1.9: 必填與後端/UI 星號一致——只有專案名稱必填,其餘欄位選填。
    if (!name) {
        showCreateFormError("請填寫「專案名稱 (Project Name)」。");
        return;
    }

    const members = [];
    const memberCards = document.querySelectorAll('.member-card');
    
    for (const card of memberCards) {
        const emails = [];
        card.querySelectorAll('.email-input').forEach(input => {
            if(input.value.trim()) emails.push(input.value.trim());
        });

        const accessSel = card.querySelector('.access-select');
        members.push({
            role: card.querySelector('.role-select').value,
            access_role: accessSel ? accessSel.value : '',
            title: card.querySelector('.title-input').value.trim(),
            name: {
                en: {
                    surname: card.querySelector('.en-surname').value.trim(),
                    given: card.querySelector('.en-given').value.trim(),
                    middle: card.querySelector('.en-middle').value.trim()
                },
                original: {
                    surname: card.querySelector('.org-surname').value.trim(),
                    given: card.querySelector('.org-given').value.trim(),
                    middle: card.querySelector('.org-middle').value.trim()
                },
            },
            // [org-v2] 五層一律寫入（含空字串）：l4/l5 鍵的存在本身就是
            // 「這筆已是新格式」的判準，讀取端據此決定要不要翻轉舊資料。
            organization: {
                l1: card.querySelector('.org-l1').value.trim(),
                l2: card.querySelector('.org-l2').value.trim(),
                l3: card.querySelector('.org-l3').value.trim(),
                l4: card.querySelector('.org-l4').value.trim(),
                l5: card.querySelector('.org-l5').value.trim()
            },
            emails: emails,
            address: card.querySelector('.address-input').value.trim()
        });
    }

    // v1.9: 主持人驗證與後端一致——恰一位主持人,且姓名(英文或原文)至少填一種。
    // 舊版額外硬要求單位 L1,但 UI 未標必填,移除以免使用者被莫名擋下。
    const pis = members.filter(m => isPrincipalInvestigator(m.role));
    if (pis.length === 0) {
        showCreateFormError("請在成員中指定一位「主持人 (Principal Investigator)」並填寫姓名。");
        return;
    } else if (pis.length !== 1) {
        showCreateFormError("必須且只能有一位「主持人 (Principal Investigator)」。");
        return;
    } else {
        const pi = pis[0];
        const hasEnName = pi.name.en.surname || pi.name.en.given;
        const hasOrgName = pi.name.original.surname || pi.name.original.given;
        if (!(hasEnName || hasOrgName)) {
            showCreateFormError("請填寫主持人的姓名(英文或原文至少一種)。");
            return;
        }
    }

    const payload = { 
        name, abbreviation, context, classification, keywords, members,
        status: statusVal // 送交給後端判定
    };

    try {
        let url = '/api/project/create';
        if (isEdit) url = `/api/project/update/${editPid}`;

        const res = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        
        if (result.success) {
            // v1.9 fix: modal 可能非經 bootstrap JS API 開啟,getInstance 會回 null
            // 導致 TypeError 炸掉後續刷新(既有 bug)。改用 getOrCreateInstance。
            const modalEl = document.getElementById('createModal');
            if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
            fetchProjects(currentStatus);
            showToast(isEdit ? '專案更新成功 (Updated)' : '專案建立成功 (Created)');
        } else {
            showCreateFormError((isEdit ? '更新失敗: ' : '建立失敗: ') + (result.message || '未知錯誤'));
        }
    } catch(e) {
        console.error(e);
        showCreateFormError('無法連接伺服器,請稍後再試。');
    }
}

async function deleteProject(event, pid) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    if (!confirm(`確定要刪除專案 ${pid} 嗎？\n此動作僅刪除目前專案，不會連帶刪除配對專案。`)) return;

    try {
        const encodedPid = encodeURIComponent(pid);
        const res = await fetch(`/api/project/delete/${encodedPid}?scope=single`, {
            method: 'DELETE'
        });
        const result = await res.json();

        if (result.success) {
            fetchProjects(currentStatus);
        } else {
            alert('刪除失敗: ' + result.message);
        }
    } catch (e) {
        alert('系統錯誤: ' + e.message);
    }
}

// ==========================================
// 5. 成員管理 Modal (v2.0)
// ==========================================

/** 目前 members modal 開啟的專案 id */
let _membersPid = null;
/** 目前登入者 username（由 /api/auth/me 取得；null=未知/單機） */
let _meUsername = null;
/** 目前登入者是否為 owner */
let _meIsOwner = false;
/** confirm-remove pending state: {username, timer} */
let _pendingRemove = {};

// [collab] coauthor（限定編輯）只讀得到也只寫得到被指派的章節，
// 讀取範圍比 viewer 還窄——所以它不是「viewer 加上一點權限」，選單文案要講清楚。
const ROLE_LABELS = {
    owner: '擁有者',
    editor: '總編輯',
    coauthor: '限定編輯',
    viewer: '檢視者',
};
const ROLE_HINTS = {
    owner: '全部權限，可管理成員',
    editor: '讀寫所有章節',
    coauthor: '只讀寫被指派的章節',
    viewer: '唯讀，看得到所有章節',
};
const ALL_ROLES = ['owner', 'editor', 'coauthor', 'viewer'];

function _membersShowError(msg) {
    const box = document.getElementById('members-form-error');
    if (!box) return;
    box.textContent = msg;
    box.classList.remove('d-none');
}

function _membersClearError() {
    const box = document.getElementById('members-form-error');
    if (box) box.classList.add('d-none');
}

/**
 * 開啟成員管理 modal。
 * 1. 先 GET /api/auth/me 確認身分
 * 2. GET /api/project/<pid>/members 取成員清單
 * 3. 依角色決定 UI 狀態
 */
async function openMembersModal(pid) {
    _membersPid = pid;
    _meUsername = null;
    _meIsOwner = false;
    _pendingRemove = {};

    // 重置 UI
    _membersClearError();
    const offlineNotice = document.getElementById('members-offline-notice');
    const addRow = document.getElementById('members-add-row');
    const selfExitRow = document.getElementById('members-self-exit-row');
    const listEl = document.getElementById('members-list');
    const loadingEl = document.getElementById('members-loading');

    if (offlineNotice) offlineNotice.classList.add('d-none');
    if (addRow) addRow.classList.add('d-none');
    if (selfExitRow) selfExitRow.classList.add('d-none');
    if (listEl) { listEl.innerHTML = ''; listEl.classList.add('d-none'); }
    if (loadingEl) loadingEl.classList.remove('d-none');

    const labelEl = document.getElementById('membersModalLabel');
    if (labelEl) labelEl.innerHTML = `<i class="bi bi-people-fill"></i> 成員管理 (Members) — #${escapeHtml(pid)}`;

    const modalEl = document.getElementById('membersModal');
    if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).show();

    // --- 1. 取得目前使用者 ---
    try {
        const meRes = await fetch('/api/auth/me');
        if (meRes.status === 401) {
            // AUTH_MODE=none 或未登入
            if (loadingEl) loadingEl.classList.add('d-none');
            if (offlineNotice) offlineNotice.classList.remove('d-none');
            return;
        }
        const meData = await meRes.json();
        if (meData.success && meData.user) {
            _meUsername = meData.user.username;
        }
    } catch (e) {
        if (loadingEl) loadingEl.classList.add('d-none');
        if (offlineNotice) offlineNotice.classList.remove('d-none');
        return;
    }

    // --- 2. 取得成員清單 ---
    await _membersRefresh();
}

async function _membersRefresh() {
    const loadingEl = document.getElementById('members-loading');
    const listEl = document.getElementById('members-list');
    if (loadingEl) loadingEl.classList.remove('d-none');
    if (listEl) { listEl.classList.add('d-none'); listEl.innerHTML = ''; }
    _membersClearError();
    _pendingRemove = {};

    try {
        const res = await fetch(`/api/project/${encodeURIComponent(_membersPid)}/members`);
        const data = await res.json();

        if (loadingEl) loadingEl.classList.add('d-none');

        if (res.status === 403) {
            _membersShowError('只有 Owner 可以管理成員');
            return;
        }
        if (!data.success) {
            _membersShowError(data.message || '載入成員失敗');
            return;
        }

        const members = data.members || [];

        // 判斷目前使用者是否為 owner
        _meIsOwner = members.some(m => m.username === _meUsername && m.role === 'owner');

        // 渲染
        _membersRenderList(members);

        // 顯示/隱藏功能列
        const addRow = document.getElementById('members-add-row');
        const selfExitRow = document.getElementById('members-self-exit-row');
        const isMember = members.some(m => m.username === _meUsername);

        if (_meIsOwner) {
            if (addRow) addRow.classList.remove('d-none');
            if (selfExitRow) selfExitRow.classList.add('d-none');
        } else {
            if (addRow) addRow.classList.add('d-none');
            if (selfExitRow && isMember) selfExitRow.classList.remove('d-none');
        }

    } catch (e) {
        if (loadingEl) loadingEl.classList.add('d-none');
        _membersShowError('網路錯誤，請稍後再試');
    }
}

function _membersRenderList(members) {
    const listEl = document.getElementById('members-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    if (members.length === 0) {
        listEl.innerHTML = '<li class="list-group-item text-muted small text-center">尚無成員</li>';
        listEl.classList.remove('d-none');
        return;
    }

    members.forEach(m => {
        const li = document.createElement('li');
        li.className = 'list-group-item d-flex align-items-center gap-3 py-2';
        li.id = `member-row-${CSS.escape(m.username)}`;

        const isSelf = m.username === _meUsername;
        const nameHtml = `<span class="fw-bold flex-grow-1">${escapeHtml(m.username)}${isSelf ? ' <span class="badge bg-secondary ms-1">我</span>' : ''}</span>`;

        if (_meIsOwner) {
            // Owner 視角：下拉改角色 + 移除按鈕
            const opts = ALL_ROLES.map(r =>
                `<option value="${r}" ${m.role === r ? 'selected' : ''}>${escapeHtml(ROLE_LABELS[r] || r)}</option>`
            ).join('');

            li.innerHTML = `
                ${nameHtml}
                <select class="form-select form-select-sm" style="width:auto;" onchange="membersChangeRole('${escapeHtml(m.username)}', this.value)">
                    ${opts}
                </select>
                <button class="btn btn-sm btn-outline-danger border-0" id="remove-btn-${CSS.escape(m.username)}"
                    onclick="membersRemove('${escapeHtml(m.username)}')">
                    <i class="bi bi-person-dash"></i>
                </button>
            `;
        } else {
            // 非 owner：唯讀
            li.innerHTML = `
                ${nameHtml}
                <span class="badge bg-light text-dark border">${escapeHtml(ROLE_LABELS[m.role] || m.role)}</span>
            `;
        }

        listEl.appendChild(li);
    });

    listEl.classList.remove('d-none');
}

/** owner 新增成員 */
async function membersAddNew() {
    _membersClearError();
    const usernameEl = document.getElementById('members-new-username');
    const roleEl = document.getElementById('members-new-role');
    const username = (usernameEl ? usernameEl.value.trim() : '');
    const role = (roleEl ? roleEl.value : 'viewer');

    if (!username) {
        _membersShowError('請輸入帳號');
        return;
    }

    try {
        const res = await fetch(`/api/project/${encodeURIComponent(_membersPid)}/members`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, role })
        });
        const data = await res.json();

        if (res.status === 404) {
            _membersShowError('找不到此帳號，請確認對方已註冊');
            return;
        }
        if (res.status === 400 && data.error === 'last_owner') {
            _membersShowError('專案至少需要一位 Owner');
            return;
        }
        if (res.status === 403) {
            _membersShowError('只有 Owner 可以管理成員');
            return;
        }
        if (!data.success) {
            _membersShowError(data.message || '新增失敗');
            return;
        }

        if (usernameEl) usernameEl.value = '';
        showToast(`已新增 ${username} (${ROLE_LABELS[role] || role})`);
        await _membersRefresh();
    } catch (e) {
        _membersShowError('網路錯誤，請稍後再試');
    }
}

/** owner 改角色（下拉 change 即觸發） */
async function membersChangeRole(username, newRole) {
    _membersClearError();
    try {
        const res = await fetch(`/api/project/${encodeURIComponent(_membersPid)}/members/${encodeURIComponent(username)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: newRole })
        });
        const data = await res.json();

        if (res.status === 400 && data.error === 'last_owner') {
            _membersShowError('專案至少需要一位 Owner');
            await _membersRefresh(); // 還原下拉
            return;
        }
        if (res.status === 403) {
            _membersShowError('只有 Owner 可以管理成員');
            await _membersRefresh();
            return;
        }
        if (!data.success) {
            _membersShowError(data.message || '更新失敗');
            await _membersRefresh();
            return;
        }

        showToast(`${username} 角色已更新為 ${ROLE_LABELS[newRole] || newRole}`);
        // 若我把自己從 owner 降級，需刷新整體
        if (username === _meUsername) await _membersRefresh();
    } catch (e) {
        _membersShowError('網路錯誤，請稍後再試');
    }
}

/**
 * owner 移除成員（雙擊確認，3 秒還原）。
 * 按一次 → 按鈕變「確認移除?」；再按才執行；3 秒後不按則還原。
 */
function membersRemove(username) {
    _membersClearError();
    const btnId = `remove-btn-${CSS.escape(username)}`;
    const btn = document.getElementById(btnId);
    if (!btn) return;

    if (_pendingRemove[username]) {
        // 第二次按：執行 DELETE
        clearTimeout(_pendingRemove[username].timer);
        delete _pendingRemove[username];
        _membersDoRemove(username);
        return;
    }

    // 第一次按：進入確認狀態
    btn.innerHTML = '<i class="bi bi-exclamation-triangle-fill me-1"></i>確認移除?';
    btn.classList.replace('btn-outline-danger', 'btn-danger');

    const timer = setTimeout(() => {
        // 3 秒後還原
        if (btn) {
            btn.innerHTML = '<i class="bi bi-person-dash"></i>';
            btn.classList.replace('btn-danger', 'btn-outline-danger');
        }
        delete _pendingRemove[username];
    }, 3000);

    _pendingRemove[username] = { timer };
}

async function _membersDoRemove(username) {
    try {
        const res = await fetch(`/api/project/${encodeURIComponent(_membersPid)}/members/${encodeURIComponent(username)}`, {
            method: 'DELETE'
        });
        const data = await res.json();

        if (res.status === 400 && data.error === 'last_owner') {
            _membersShowError('專案至少需要一位 Owner');
            await _membersRefresh();
            return;
        }
        if (res.status === 403) {
            _membersShowError('只有 Owner 可以管理成員');
            return;
        }
        if (!data.success) {
            _membersShowError(data.message || '移除失敗');
            return;
        }

        showToast(`已移除 ${username}`);
        await _membersRefresh();
    } catch (e) {
        _membersShowError('網路錯誤，請稍後再試');
    }
}

/** 非 owner 退出專案（DELETE 自己） */
async function membersSelfExit() {
    if (!_meUsername) return;
    _membersClearError();

    const btn = document.getElementById('members-self-exit-btn');
    if (!btn) return;

    if (!btn.dataset.confirm) {
        btn.dataset.confirm = '1';
        btn.textContent = '確認退出?';
        btn.classList.replace('btn-outline-danger', 'btn-danger');
        setTimeout(() => {
            btn.dataset.confirm = '';
            btn.textContent = '退出專案 (Leave Project)';
            btn.classList.replace('btn-danger', 'btn-outline-danger');
        }, 3000);
        return;
    }

    btn.dataset.confirm = '';
    try {
        const res = await fetch(`/api/project/${encodeURIComponent(_membersPid)}/members/${encodeURIComponent(_meUsername)}`, {
            method: 'DELETE'
        });
        const data = await res.json();

        if (res.status === 400 && data.error === 'last_owner') {
            _membersShowError('專案至少需要一位 Owner');
            return;
        }
        if (!data.success) {
            _membersShowError(data.message || '退出失敗');
            return;
        }

        showToast('已退出專案');
        const modalEl = document.getElementById('membersModal');
        if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        fetchProjects(currentStatus);
    } catch (e) {
        _membersShowError('網路錯誤，請稍後再試');
    }
}


// ==========================================
// 章節完成比例（NOTE-036）
// ==========================================

/**
 * 把每個正式專案的章節完成比例畫到卡片上。
 *
 * 走**一支批次端點**而不是逐張卡片打一次：卡片數量等於專案數量，
 * N+1 會在專案一多時把首頁拖垮。
 *
 * NOTE(NOTE-036) 「整體比例」現階段一律顯示「尚未計算」，不做各章平均。
 * 各章不等重（Abstract 與 Results 差很多），有些章節在特定研究裡根本不會寫；
 * 在權重規則定案前給一個看起來合理但其實錯的數字，比明白說「還沒算」更糟
 * —— 使用者會拿它去回報進度。欄位先留著，規則定了只改這一處與伺服器那一處。
 */
async function renderCardProgress(projects) {
    const slots = document.querySelectorAll('[data-progress-card]');
    if (!slots.length) return;

    let byPid = {};
    try {
        const res = await fetch('/manuscript/api/progress_summary');
        if (res.ok) {
            const data = await res.json();
            byPid = (data && data.projects) || {};
        }
    } catch (err) {
        // 讀不到進度不得讓整張卡片壞掉；留白比顯示錯的數字好。
        byPid = {};
    }

    slots.forEach((slot) => {
        const pid = slot.getAttribute('data-progress-card') || '';
        const info = byPid[pid];
        slot.innerHTML = '';

        const box = document.createElement('div');
        box.className = 'border rounded bg-white p-2';

        const head = document.createElement('div');
        head.className = 'd-flex justify-content-between align-items-center mb-1';
        const headLabel = document.createElement('span');
        headLabel.className = 'fw-bold text-secondary';
        headLabel.textContent = '整體完成度';
        const headValue = document.createElement('span');
        headValue.className = 'text-muted fst-italic';
        // 保留欄位、明說沒算。不要放 0% 或 -- ，那兩個都會被讀成「數字」。
        headValue.textContent = '尚未計算';
        head.appendChild(headLabel);
        head.appendChild(headValue);
        box.appendChild(head);

        if (!info || !Array.isArray(info.sections) || !info.sections.length) {
            const empty = document.createElement('div');
            empty.className = 'text-muted';
            empty.textContent = info ? '尚無章節' : '完成度未載入';
            box.appendChild(empty);
            slot.appendChild(box);
            return;
        }

        const list = document.createElement('div');
        list.className = 'rt-progress-list';
        list.style.maxHeight = '132px';
        list.style.overflowY = 'auto';

        info.sections.forEach((sec) => {
            const row = document.createElement('div');
            row.className = 'd-flex align-items-center gap-2 mb-1';

            const name = document.createElement('span');
            name.className = 'text-truncate text-dark';
            name.style.flex = '0 0 40%';
            name.title = String(sec.label || sec.id || '');
            name.textContent = String(sec.label || sec.id || '');

            const bar = document.createElement('div');
            bar.className = 'progress flex-grow-1';
            bar.style.height = '8px';
            const filled = document.createElement('div');
            filled.className = 'progress-bar bg-success';
            // null 代表「還沒填」，不是 0%：條是空的，右邊寫「--」而不是「0%」。
            const value = (sec.progress === null || sec.progress === undefined)
                ? null : Number(sec.progress);
            filled.style.width = (value === null ? 0 : value) + '%';
            bar.appendChild(filled);

            const pct = document.createElement('span');
            pct.className = value === null ? 'text-muted' : 'text-dark fw-bold';
            pct.style.flex = '0 0 40px';
            pct.style.textAlign = 'right';
            pct.textContent = value === null ? '--' : value + '%';

            row.appendChild(name);
            row.appendChild(bar);
            row.appendChild(pct);
            list.appendChild(row);
        });

        box.appendChild(list);
        slot.appendChild(box);
    });
}
