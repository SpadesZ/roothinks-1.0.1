/* 路徑(./app/static/js/paq_interact.js) #版本 v3.8 #更版時間 20260221-2000 */
/**
 * ==================================================================================
 * Roothinks-PAQ Panel Interaction Controller 從paq_interact.js v3.4拆分(1045行→3檔案)
 * ==================================================================================
 * [v3.6 Critical Fix — 動態按鈕與純存檔通道]
 * 1. 新增: renderTaxonomyUI 內動態生成 "+ Add Tag" 按鈕，防止按鈕因 HTML 重繪而消失。
 * 2. 新增: saveManualTaxonomy() 專屬存檔通道，並攔截 Manual Modify 按鈕。
 * 3. 修正: runCubeTask 中傳送的 payload 參數精準校正為 labels 與 tags。
 * * [v0.1 Critical Fix — CubeRenderer 函式簽名修正]
 * cube_renderer.js v1.4 的 renderVoxels 期望 2 個參數: renderVoxels(containerId, { voxels: [...], axis_labels: {...} })
 * v3.4 原始碼 (L629) 誤傳 3 個參數:
 * renderVoxels(containerId, voxelArray, labelsObj)
 * 導致 Cube 渲染靜默失敗 (第二參數被當作整個 config，axis_labels 丟失)
 * 修正位置: runCubeTask() 內的 CubeRenderer.renderVoxels 呼叫
 *
 * 負責三個工作面板的互動邏輯：
 * 1. 任務一：分類生成 (Task 1: Taxonomy Generation) — Section 4
 * 2. 任務二：Cube 生成 (Task 2: Cube Generation) — Section 5
 * 3. 任務二 A：Co-Pilot 聊天 (Task 2A: Chat) — Section 6
 *
 * 依賴關係 (Dependencies):
 * - 全域變數: currentPid, localTaxonomy, taxonomyDirty, chatSessionHistory
 * (宣告於 paq_initial.js)
 * - 函式: setDirty(), toggleLoading() (定義於 paq_initial.js)
 * - 外部: CubeRenderer (cube_renderer.js v1.4)
 */

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ==================================================================================
// [v3.6 New] 事件攔截器與防呆 UI 鎖定機制
// ==================================================================================
document.addEventListener('DOMContentLoaded', () => {
    // 攔截 Manual Modify 按鈕，阻斷原本打向 LLM 的舊邏輯，導向純存檔
    const btnRefine = document.getElementById('btn-tax-refine');
    if (btnRefine) {
        btnRefine.onclick = (e) => {
            e.preventDefault(); 
            saveManualTaxonomy(); 
        };
    }

    // 監聽 Label 輸入框，只要使用者打字，立刻鎖定 AI Auto-Gen 按鈕防呆
    ['x', 'y', 'z'].forEach(axis => {
        const input = document.getElementById(`label-${axis}`);
        if (input) {
            input.addEventListener('input', () => {
                const btnFresh = document.getElementById('btn-tax-fresh');
                if (btnFresh) btnFresh.disabled = true;
                setDirty(true);
            });
        }
    });
});

// ==================================================================================
// 4. 任務一：分類生成 (Task 1: Taxonomy Generation)
// ==================================================================================
/**
 * 渲染 Taxonomy UI
 * 將 localTaxonomy.axis_tags 的資料轉為 HTML List
 */
function renderTaxonomyUI() {
    const axes = ['x', 'y', 'z'];
    
    axes.forEach(axis => {
        const list = document.getElementById(`list-${axis}`);
        if (!list) return;
        
        list.innerHTML = ''; // 清空列表
        
        const tags = localTaxonomy.axis_tags[axis] || [];
        
        tags.forEach((tag, idx) => {
            const li = document.createElement('li');
            li.className = 'list-group-item d-flex justify-content-between align-items-center px-2 py-1 border-0 bg-transparent';
            
            // 處理標籤內容 (換行轉 <br>)
            const displayTag = typeof tag === 'string'
                ? escapeHtml(tag).replace(/\n/g, '<br>')
                : escapeHtml(tag);
            
            li.innerHTML = `
                <span class="badge bg-light text-dark border text-wrap text-start shadow-sm" style="max-width: 90%; font-weight: normal;">
                    ${displayTag}
                </span>
                <i class="bi bi-x text-danger cursor-pointer ms-1 remove-tag-icon" onclick="removeTag('${axis}', ${idx})" title="Remove Tag"></i>
            `;
            list.appendChild(li);
        });

        // [v3.6 Critical Fix] 動態注入新增按鈕，確保它永遠存在於列表最下方
        const addBtnLi = document.createElement('li');
        addBtnLi.className = 'list-group-item px-2 py-1 border-0 bg-transparent text-center mt-2';
        addBtnLi.innerHTML = `
            <button class="btn btn-sm btn-outline-secondary w-100" onclick="createTag('${axis}')" style="border-style: dashed;">
                <i class="bi bi-plus-circle"></i> Add Tag
            </button>
        `;
        list.appendChild(addBtnLi);
    });
}

/**
 * 手動建立 Tag (透過 Prompt)
 * @param {string} axis - 'x', 'y', 'z'
 */
function createTag(axis) {
    const val = prompt(`Add Tag for ${axis.toUpperCase()}-Axis:`);
    
    if (val && val.trim() !== '') {
        if (!localTaxonomy.axis_tags[axis]) {
            localTaxonomy.axis_tags[axis] = [];
        }
        
        localTaxonomy.axis_tags[axis].push(val.trim());
        
        renderTaxonomyUI();
        setDirty(true); 
        
        const btnFresh = document.getElementById('btn-tax-fresh');
        if (btnFresh) btnFresh.disabled = true; // 防呆鎖定
    }
}

/**
 * 移除 Tag
 * @param {string} axis 
 * @param {number} idx 
 */
function removeTag(axis, idx) {
    if (confirm('Are you sure you want to remove this tag?')) {
        if (localTaxonomy.axis_tags[axis]) {
            localTaxonomy.axis_tags[axis].splice(idx, 1);
            renderTaxonomyUI();
            setDirty(true); 
            
            const btnFresh = document.getElementById('btn-tax-fresh');
            if (btnFresh) btnFresh.disabled = true; // 防呆鎖定
        }
    }
}

/**
 * [Core Logic] 收集當前 DOM 上的資料
 */
function collectCurrentData() {
    const dx = document.getElementById('label-x');
    const dy = document.getElementById('label-y');
    const dz = document.getElementById('label-z');
    
    if(dx) localTaxonomy.axis_labels.x = dx.value.trim();
    if(dy) localTaxonomy.axis_labels.y = dy.value.trim();
    if(dz) localTaxonomy.axis_labels.z = dz.value.trim();
    
    return localTaxonomy;
}

/**
 * [v3.6 New] 專屬手動儲存通道 (Manual Modify)
 * 繞過 AI，將使用者的修改直接覆寫至資料庫，並連鎖觸發 Cube 生成。
 */
async function saveManualTaxonomy() {
    const btnRefine = document.getElementById('btn-tax-refine');
    const loading = document.getElementById('loading-taxonomy');
    
    if(btnRefine) btnRefine.disabled = true;
    if(loading) loading.classList.remove('d-none');

    try {
        const currentData = collectCurrentData();

       // v3.7 增修起點 ======================================================
        const res = await fetch('/api/paq/save_taxonomy', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                pid: currentPid,
                labels: currentData.axis_labels,
                tags: currentData.axis_tags
            })
        });
        
        // ==========================================
        // [v3.7 Fix] 新增 HTTP 狀態碼防呆，避免解析 HTML 導致 SyntaxError
        // ==========================================
        if (!res.ok) {
            throw new Error(`伺服器連線異常 (HTTP ${res.status})，確認後端存檔API已啟動。`);
        }
        // ==========================================
        // 只有確定是 200 OK，才安全解析 JSON
        const result = await res.json();    
        // v3.7 增修終點 ==========================================       

        if (result.success) {
            console.log("[Save] Manual taxonomy saved successfully.");
            setDirty(false);       
            const btnFresh = document.getElementById('btn-tax-fresh');
            if (btnFresh) btnFresh.disabled = false; // 解鎖 AI 按鈕
            
            // 存檔成功後，自動驅動 Task 2 重繪 3D Cube
            runCubeTask();
        } else {
            throw new Error(result.message || 'Database save failed.');
        }

    } catch (e) {
        console.error(e);
        alert('儲存修改失敗: ' + e.message);
    } finally {
        if(btnRefine) btnRefine.disabled = false;
        if(loading) loading.classList.add('d-none');
    }
}

/**
 * 執行 Taxonomy 生成任務 (支援雙模式)
 */
async function runTaxonomyTask(mode = 'fresh') {
    const btnFresh = document.getElementById('btn-tax-fresh');
    const btnRefine = document.getElementById('btn-tax-refine');
    const loading = document.getElementById('loading-taxonomy');
    
    if (mode === 'fresh') {
        if (!confirm('警告：這將會清除您目前所有的修改並重新生成。\n確定要執行「全新生成 (Fresh)」嗎？')) {
            return;
        }
    } else if (mode === 'refine') {
        const data = collectCurrentData();
        if (!data.axis_labels.x && !data.axis_labels.y) {
             if(!confirm("目前軸向定義似乎為空，確定要進行「優化」嗎？建議先輸入一些想法。")) return;
        }
    }

    if(btnFresh) btnFresh.disabled = true;
    if(btnRefine) btnRefine.disabled = true;
    if(loading) loading.classList.remove('d-none');

    try {
        let payload = {
            pid: currentPid,
            task_id: 'task_1paqswot',
            input_data: { mode: mode }
        };

        if (mode === 'refine') {
            const currentData = collectCurrentData();
            payload.input_data.user_edits = {
                axis_labels: currentData.axis_labels,
                axis_tags: currentData.axis_tags
            };
        }

        const res = await fetch('/api/paq/run_task', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        const result = await res.json();
        
        if (result.success && result.data) {
            const newLabels = result.data.axis_labels || result.data.axis_definitions || {};
            const newTags = result.data.axis_tags || {};

            localTaxonomy.axis_labels = newLabels;
            localTaxonomy.axis_tags = newTags;
            
            ['x', 'y', 'z'].forEach(axis => {
                const el = document.getElementById(`label-${axis}`);
                if(el) el.value = localTaxonomy.axis_labels[axis] || '';
            });
            
            renderTaxonomyUI();
            setDirty(false);
            
            if (typeof CubeRenderer !== 'undefined') CubeRenderer.clear('cube-container');

        } else {
            throw new Error(result.message || 'Unknown error from LLM');
        }

    } catch (e) {
        console.error(e);
        alert('AI 生成失敗: ' + e.message);
    } finally {
        if(btnFresh) btnFresh.disabled = false;
        if(btnRefine) btnRefine.disabled = false;
        if(loading) loading.classList.add('d-none');
    }
}
// ==================================================================================
// 5. 任務二：Cube 生成 (Task 2: Cube Generation)
// ==================================================================================
async function runCubeTask() {
    const btn = document.getElementById('btn-run-cube');
    const loading = document.getElementById('loading-cube');
    const containerId = 'cube-container';

    if (typeof CubeRenderer !== 'undefined') {
        CubeRenderer.clear(containerId);
    }
    
    if(btn) btn.disabled = true;
    if(loading) loading.classList.remove('d-none');

    try {
        const currentData = collectCurrentData();

        const tx = currentData.axis_tags.x || [];
        const ty = currentData.axis_tags.y || [];
        const tz = currentData.axis_tags.z || [];

        if (tx.length === 0 || ty.length === 0 || tz.length === 0) {
            throw new Error("Taxonomy is incomplete. Please complete Task 1 first (Ensure all axes have tags).");
        }

    // v3.8 增修起點 ================================================================
        const res = await fetch('/api/paq/run_task', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                pid: currentPid,
                task_id: 'task_2cubegen',
                input_data: {
                    labels: currentData.axis_labels, 
                    tags: currentData.axis_tags      
                }
            })
        });
        
        // === [新增防呆機制] ===
        if (!res.ok) {
            throw new Error(`伺服器連線或運算異常 (HTTP ${res.status})。可能是 AI 運算逾時，請稍後再試。`);
        }
        // ======================

        const result = await res.json();
         
    //v3.8 增修終點============================================= ======================    

        if (result.success && result.data && result.data.view_data) {
            if (typeof CubeRenderer !== 'undefined') {
                CubeRenderer.renderVoxels(containerId, {
                    voxels: result.data.view_data,
                    axis_labels: currentData.axis_labels
                });
            } else {
                 throw new Error('CubeRenderer library not loaded.');
            }
        } else {
            const el = document.getElementById(containerId);
            if(el) el.innerHTML = `<div class="text-danger text-center mt-5">AI Error: ${escapeHtml(result.message || 'No Data')}</div>`;
        }

    } catch (e) {
        console.error(e);
        const el = document.getElementById(containerId);
        if(el) el.innerHTML = `<div class="text-danger text-center mt-5">System Error: ${escapeHtml(e.message)}</div>`;
    } finally {
        if(btn) btn.disabled = false;
        if(loading) loading.classList.add('d-none');
    }
}

// ==================================================================================
// 6. 任務二 A：Co-Pilot 聊天 (Task 2A: Chat)
// ==================================================================================
async function sendChat() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    
    const text = input.value.trim();
    if (!text) return;

    appendChatBubble('user', text);
    input.value = '';

    const loadingId = appendChatBubble('ai', '', true);

    try {
        const res = await fetch('/api/paq/run_task', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                pid: currentPid,
                task_id: 'task_2a_chat', 
                input_data: { 
                    user_input: text,
                    chat_history: chatSessionHistory 
                }
            })
        });
        const result = await res.json();
        
        removeChatBubble(loadingId);

        if (result.success && result.data && result.data.reply) {
            appendChatBubble('ai', result.data.reply);
            
            chatSessionHistory.push({role: 'user', content: text});
            chatSessionHistory.push({role: 'assistant', content: result.data.reply});
            
            if (chatSessionHistory.length > 20) {
                chatSessionHistory = chatSessionHistory.slice(-20);
            }
        } else {
            appendChatBubble('ai', 'Sorry, I encountered an error: ' + (result.message || 'Unknown'));
        }

    } catch (e) {
        removeChatBubble(loadingId);
        appendChatBubble('ai', 'Network Error: ' + e.message);
    }
}

function handleChatKey(e) {
    if (e.key === 'Enter') sendChat();
}

function appendChatBubble(role, text, isLoading=false) {
    const container = document.getElementById('chat-history');
    if (!container) return;

    const div = document.createElement('div');
    div.className = `chat-bubble ${role === 'user' ? 'user-bubble' : 'ai-bubble'}`;
    
    if (isLoading) {
        div.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Thinking...';
        div.id = 'chat-loading-' + Date.now();
    } else {
        div.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
    }
    
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    
    return div.id;
}

function removeChatBubble(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
}
