// Roothinks source maintenance contract
// 檔案路徑: app/static/js/manuscript_image.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 處理 Manuscript 圖片上傳、插入與預覽生命週期，將檔案操作綁定目前 PID/section。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/manuscript_image.js
//路徑(./app/static/js/manuscript_image.js)
//版本 v1.8 (ForeignObject Nuclear Removal & Pure SVG Canvas Engine)
//更版時間 20260318-2200
// inner comment: 嚴格遵守人機協作定律，全量保留 v1.7 邏輯。根本性修復 Canvas 安全攔截：
// 瀏覽器安全策略從根本上禁止 canvas 渲染含 <foreignObject> 的 SVG (無論 Blob 或 Base64 都無法繞過)。
// v1.8 策略：在序列化前，物理移除所有 foreignObject 並替換為原生 SVG <text> 元素，
// 產出「純 SVG」讓 canvas 毫無阻礙地渲染。

class ManuImage {
    constructor(app) {
        this.app = app;
        console.log("[ManuImage] Engine v1.8 initializing (ForeignObject Nuclear Removal Mode)...");
        
        // 初始化 Mermaid，盡可能要求它輸出純淨格式
        if (typeof mermaid !== 'undefined') {
            mermaid.initialize({ 
                startOnLoad: false, 
                theme: 'default', 
                securityLevel: 'strict',
                flowchart: { htmlLabels: false },
                sequence: { htmlLabels: false },
                gantt: { htmlLabels: false }
            });
            console.log("[ManuImage] Verified: Global Mermaid 10 Bridge is ACTIVE.");
        }

        this.setupSocketEvents();
    }

    _getApiToken() {
        try {
            return (window.localStorage.getItem('roothinks_api_token') || '').trim();
        } catch (e) {
            return '';
        }
    }

    _withAuthToken(url) {
        const u = String(url || '').trim();
        if (!u) return '';
        const token = this._getApiToken();
        if (!token) return u;
        const sep = u.includes('?') ? '&' : '?';
        return `${u}${sep}access_token=${encodeURIComponent(token)}`;
    }

    _escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    setupSocketEvents() {
        this.app.socket.on('image_saved', (data) => {
            if (data.ok && data.meta) {
                if (data.meta.source === 'gallery_upload') {
                    this.app.soed.addSystemMessage(`圖片已加入素材庫：${data.meta.fig_id}`);
                    return;
                }
                const safePath = this._escapeHtml(this._withAuthToken(data.meta.path));
                const safeCaption = this._escapeHtml(data.meta.caption);
                const safeFig = this._escapeHtml(data.meta.fig_id);
                const imgTag = `
                    <div class="text-center my-4 image-container" contenteditable="false">
                        <img src="${safePath}" alt="${safeCaption}" class="img-fluid border border-2 rounded shadow-sm" style="max-width: 90%;">
                        <p class="text-muted fw-bold small mt-2"><i>${safeFig}: ${safeCaption}</i></p>
                    </div><p><br></p>
                `;
                this.app.soed.importToEditor(imgTag);
                this.app.soed.addSystemMessage(`影像實體化成功：ID ${data.meta.id} 已存入 Docker Volume`);
            }
        });
    }

    async renderMermaidToChat(code) {
        const chatContainer = this.app.chatContainer;
        const uniqueId = 'mermaid_' + Math.random().toString(36).substr(2, 9);
        const wrapper = document.createElement('div');
        wrapper.className = 'message-bubble ai animate__animated animate__fadeInRight';
        const contentDiv = document.createElement('div');
        contentDiv.className = 'bubble-content shadow border-success border-start border-4 p-3 bg-white w-100';
        
        const header = document.createElement('div');
        header.className = 'd-flex justify-content-between align-items-center border-bottom pb-2 mb-3';
        header.innerHTML = `<div></div>`; // 保持右側按鈕對齊的佔位符
        
        const btnSave = document.createElement('button');
        btnSave.className = 'btn btn-sm btn-success fw-bold shadow-sm';
        btnSave.innerHTML = `<i class="bi bi-arrow-left-circle-fill me-1"></i> 轉存 PNG 並推入 2B`;
        btnSave.onclick = () => this.convertAndSave(uniqueId, btnSave);
        header.appendChild(btnSave);
        
        const svgContainer = document.createElement('div');
        svgContainer.id = uniqueId + '_container';
        svgContainer.className = 'text-center overflow-auto p-2 bg-light rounded';
        svgContainer.style.minHeight = '120px';
        svgContainer.innerHTML = `<div class="spinner-border text-success mt-3"></div>`;
        
        contentDiv.appendChild(header);
        contentDiv.appendChild(svgContainer);
        wrapper.appendChild(contentDiv);
        chatContainer.appendChild(wrapper);
        this.app.soed.scrollToBottom();

        try {
            // 語法降級與防護
            let cleanCode = code.replace(/```mermaid/gi, '').replace(/```/g, '').trim();
            cleanCode = cleanCode.replace(/subgraph\s+([a-zA-Z0-9_-]+)\s*\[(.*?)\]/gi, "subgraph $2");
            const codeLines = cleanCode.split('\n');
            for (let i = 0; i < codeLines.length; i++) {
                if (codeLines[i].trim().toLowerCase().startsWith('subgraph ')) {
                    codeLines[i] = codeLines[i].replace(/[()[\]{}]/g, ' '); 
                }
            }
            cleanCode = codeLines.join('\n');
            
            const { svg } = await mermaid.render(uniqueId, cleanCode);
            svgContainer.innerHTML = svg;
            this.app.soed.scrollToBottom();
        } catch (err) {
            console.error("[ManuImage] Render error:", err);
            svgContainer.innerHTML = `<div class="alert alert-danger small">繪圖解析失敗：${err.message}</div>`;
        }
    }

    // =========================================================================
    // [v1.8 核心] 將 foreignObject 替換為原生 SVG <text>，產出「純 SVG」
    // =========================================================================
    _nukeForeignObjects(svgElement) {
        const foreignObjects = svgElement.querySelectorAll('foreignObject');
        if (foreignObjects.length === 0) {
            console.log("[ManuImage] No foreignObject found — SVG is already pure.");
            return;
        }
        console.log(`[ManuImage] Detected ${foreignObjects.length} foreignObject(s). Initiating nuclear replacement...`);

        foreignObjects.forEach((fo, idx) => {
            // 1. 提取 foreignObject 的位置與尺寸屬性
            const x = parseFloat(fo.getAttribute('x')) || 0;
            const y = parseFloat(fo.getAttribute('y')) || 0;
            const width = parseFloat(fo.getAttribute('width')) || 200;
            const height = parseFloat(fo.getAttribute('height')) || 50;

            // 2. 提取內部所有純文字內容
            const rawText = (fo.textContent || '').trim();
            if (!rawText) {
                // 空的 foreignObject，直接移除
                fo.parentNode.removeChild(fo);
                return;
            }

            // 3. 取得 foreignObject 內部的實際樣式 (字級、顏色)
            //    注意：clonedSvg 不在 DOM 中，getComputedStyle 可能失效，使用安全預設值
            let fontSize = 14;
            let fontFamily = 'Arial, sans-serif';
            let fontWeight = 'normal';
            let color = '#333333';

            try {
                const innerEl = fo.querySelector('div, span, p') || fo;
                // 嘗試從 inline style 取得
                const inlineFont = innerEl.style?.fontSize;
                if (inlineFont) fontSize = parseFloat(inlineFont) || 14;
                const inlineColor = innerEl.style?.color;
                if (inlineColor) color = inlineColor;
                const inlineWeight = innerEl.style?.fontWeight;
                if (inlineWeight) fontWeight = inlineWeight;
            } catch (e) {
                // 安全靜默，使用預設值
            }

            // 4. 建立 SVG <text> 替代品
            const svgNS = "http://www.w3.org/2000/svg";
            const textEl = document.createElementNS(svgNS, "text");

            // 5. 計算文字定位 (居中對齊)
            const textX = x + width / 2;
            const textAnchor = 'middle';

            textEl.setAttribute('x', textX);
            textEl.setAttribute('text-anchor', textAnchor);
            textEl.setAttribute('font-size', fontSize);
            textEl.setAttribute('font-family', fontFamily);
            textEl.setAttribute('font-weight', fontWeight);
            textEl.setAttribute('fill', color);
            textEl.setAttribute('pointer-events', 'none');

            // 6. 處理多行文字：按換行或超出寬度自動折行
            const lines = this._wrapText(rawText, width, fontSize);
            const lineHeight = fontSize * 1.3;
            // 垂直居中：計算起始 y 值
            const totalTextHeight = lines.length * lineHeight;
            const startY = y + (height - totalTextHeight) / 2 + fontSize;

            lines.forEach((line, lineIdx) => {
                const tspan = document.createElementNS(svgNS, "tspan");
                tspan.setAttribute('x', textX);
                tspan.setAttribute('dy', lineIdx === 0 ? '0' : String(lineHeight));
                tspan.textContent = line;
                textEl.appendChild(tspan);
            });

            // 第一行的 y 定位
            textEl.setAttribute('y', startY);

            // 7. 在 foreignObject 的父節點中替換
            const parent = fo.parentNode;
            parent.insertBefore(textEl, fo);
            parent.removeChild(fo);

            console.log(`[ManuImage]   Replaced FO#${idx}: "${rawText.substring(0, 30)}..." -> SVG <text> (${lines.length} lines)`);
        });
    }

    _wrapText(text, maxWidth, fontSize) {
        // 用 canvas 精確測量文字寬度來折行
        const measureCanvas = document.createElement('canvas');
        const ctx = measureCanvas.getContext('2d');
        ctx.font = `${fontSize}px sans-serif`;

        // 先按明確的換行符分段
        const paragraphs = text.split(/\n/);
        const result = [];

        for (const para of paragraphs) {
            const words = para.split(/\s+/).filter(w => w.length > 0);
            if (words.length === 0) {
                result.push('');
                continue;
            }
            let currentLine = words[0];
            for (let i = 1; i < words.length; i++) {
                const testLine = currentLine + ' ' + words[i];
                const metrics = ctx.measureText(testLine);
                if (metrics.width > maxWidth - 16) { // 16px 內邊距
                    result.push(currentLine);
                    currentLine = words[i];
                } else {
                    currentLine = testLine;
                }
            }
            result.push(currentLine);
        }

        return result;
    }

    // =========================================================================
    // [v1.8 新增] 遞迴清除所有非 SVG 命名空間的子元素 (額外安全防護)
    // =========================================================================
    _sanitizeNonSvgElements(svgElement) {
        const xlinkNS = "http://www.w3.org/1999/xlink";
        
        // 移除 <style> 中的 @import 和 url() 引用 (canvas 安全策略也會擋這些)
        const styles = svgElement.querySelectorAll('style');
        styles.forEach(styleEl => {
            let css = styleEl.textContent || '';
            css = css.replace(/@import[^;]+;/g, '');
            css = css.replace(/url\([^)]*\)/g, 'none');
            styleEl.textContent = css;
        });

        // 移除 <image> 中的外部 href (也可能觸發 taint)
        const images = svgElement.querySelectorAll('image');
        images.forEach(img => {
            const href = img.getAttribute('href') || img.getAttributeNS(xlinkNS, 'href') || '';
            if (href && !href.startsWith('data:')) {
                console.log(`[ManuImage] Removing external image ref: ${href.substring(0, 50)}...`);
                img.parentNode.removeChild(img);
            }
        });
    }

    async convertAndSave(svgId, btn) {
        const originalHtml = btn.innerHTML;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> 物理轉檔中...`;
        btn.disabled = true;

        try {
            const container = document.getElementById(svgId + '_container');
            const originalSvg = container ? container.querySelector('svg') : null;
            if (!originalSvg) throw new Error("找不到生成的 SVG 元素");

            const clonedSvg = originalSvg.cloneNode(true);
            const bbox = originalSvg.getBoundingClientRect();
            
            // 1. 抓取並注入 Mermaid 關聯樣式
            let styles = "";
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of (sheet.cssRules || sheet.rules)) {
                        if (rule.selectorText && (rule.selectorText.includes('.mermaid') || rule.selectorText.includes(`#${svgId}`))) {
                            styles += rule.cssText + "\n";
                        }
                    }
                } catch (e) { /* Ignore CORS errors for external stylesheets */ }
            }

            const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
            styleEl.textContent = styles; // [v1.8] 使用 textContent 替代 innerHTML + CDATA，更安全
            clonedSvg.insertBefore(styleEl, clonedSvg.firstChild);
            
            clonedSvg.setAttribute("width", bbox.width);
            clonedSvg.setAttribute("height", bbox.height);
            clonedSvg.style.backgroundColor = "white";

            // =========================================================================
            // [v1.8 核心修復] 在序列化前淨化 SVG DOM，產出「純 SVG」
            // =========================================================================
            // A. 核心：物理移除所有 foreignObject -> 替換為原生 SVG <text>
            this._nukeForeignObjects(clonedSvg);
            
            // B. 額外安全防護：移除外部圖片引用、@import 等可能汙染 canvas 的元素
            this._sanitizeNonSvgElements(clonedSvg);
            // =========================================================================

            const serializer = new XMLSerializer();
            let svgStr = serializer.serializeToString(clonedSvg);

            // C. 保留 v1.7 的字串層級修復 (以防萬一)
            svgStr = svgStr.replace(/<br>/gi, '<br/>').replace(/<hr>/gi, '<hr/>');

            // D. 補齊最外層 svg 命名空間
            if (!svgStr.includes('xmlns="http://www.w3.org/2000/svg"')) {
                svgStr = svgStr.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"');
            }

            console.log(`[ManuImage] Serialized pure SVG string length: ${svgStr.length} chars`);

            const base64Data = await new Promise((resolve, reject) => {
                const encodedSvg = window.btoa(unescape(encodeURIComponent(svgStr)));
                const url = `data:image/svg+xml;base64,${encodedSvg}`;
                
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                const img = new Image();

                img.onload = () => {
                    const padding = 40; 
                    canvas.width = img.width + padding * 2;
                    canvas.height = img.height + padding * 2;
                    ctx.fillStyle = "#ffffff";
                    ctx.fillRect(0, 0, canvas.width, canvas.height);
                    ctx.drawImage(img, padding, padding);
                    const b64 = canvas.toDataURL("image/png");
                    console.log(`[ManuImage] Canvas export SUCCESS. PNG data length: ${b64.length}`);
                    resolve(b64);
                };
                
                img.onerror = (e) => {
                    console.error("[ManuImage] Canvas Export STILL Blocked:", e);
                    // [v1.8 Fallback] 如果純 SVG 方案仍失敗，嘗試暴力清除後重試
                    console.log("[ManuImage] Attempting brute-force fallback capture...");
                    this._fallbackDomCapture(container, resolve, reject);
                };
                
                img.src = url;
            });
            // =========================================================================

            const currentSec = Array.from(this.app.selectedSections)[0] || 'general';
            this.app.socket.emit('cmd_save_image', {
                pid: this.app.pid,
                image_data: base64Data,
                filename: `arch_${currentSec}.png`,
                fig_id: `Figure ${Math.floor(Math.random()*100)}`,
                caption: `System Architecture for ${currentSec}`,
                source: "ai_generated"
            });

            btn.innerHTML = `<i class="bi bi-check-all me-1"></i> 轉檔成功`;
            btn.classList.replace('btn-success', 'btn-secondary');

        } catch (err) {
            console.error("[ManuImage] Export failure:", err);
            btn.innerHTML = `<i class="bi bi-x-circle me-1"></i> 轉檔失敗`;
            btn.classList.replace('btn-success', 'btn-danger');
            btn.disabled = false;
            setTimeout(() => {
                btn.innerHTML = originalHtml;
                btn.classList.replace('btn-danger', 'btn-success');
            }, 3000);
        }
    }

    // =========================================================================
    // [v1.8 Fallback] 暴力清除後重試 — 犧牲 foreignObject 文字但保證骨架圖能輸出
    // =========================================================================
    _fallbackDomCapture(container, resolve, reject) {
        try {
            const svgEl = container.querySelector('svg');
            if (!svgEl) {
                reject(new Error("Fallback: 找不到 SVG 元素"));
                return;
            }

            const deepClone = svgEl.cloneNode(true);
            
            // 暴力移除：直接刪掉所有 foreignObject，不做替換
            const allFO = deepClone.querySelectorAll('foreignObject');
            allFO.forEach(fo => fo.parentNode.removeChild(fo));
            
            // 移除所有 style 標籤 (可能含有外部引用)
            const allStyles = deepClone.querySelectorAll('style');
            allStyles.forEach(s => s.parentNode.removeChild(s));

            // 移除所有 image 標籤
            const allImages = deepClone.querySelectorAll('image');
            allImages.forEach(img => img.parentNode.removeChild(img));

            const bbox = svgEl.getBoundingClientRect();
            deepClone.setAttribute('width', bbox.width);
            deepClone.setAttribute('height', bbox.height);
            if (!deepClone.getAttribute('xmlns')) {
                deepClone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            }

            const serializer = new XMLSerializer();
            const cleanSvgStr = serializer.serializeToString(deepClone);
            const encodedSvg = window.btoa(unescape(encodeURIComponent(cleanSvgStr)));
            const url = `data:image/svg+xml;base64,${encodedSvg}`;

            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            const img = new Image();

            img.onload = () => {
                const padding = 40;
                canvas.width = img.width + padding * 2;
                canvas.height = img.height + padding * 2;
                ctx.fillStyle = "#ffffff";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, padding, padding);
                const b64 = canvas.toDataURL("image/png");
                console.log(`[ManuImage] Fallback capture SUCCESS (foreignObject text stripped). PNG: ${b64.length} chars`);
                resolve(b64);
            };

            img.onerror = (e) => {
                console.error("[ManuImage] Fallback capture ALSO failed:", e);
                reject(new Error("所有轉檔管線均失敗。請嘗試使用瀏覽器截圖 (Ctrl+Shift+S) 手動保存。"));
            };

            img.src = url;
        } catch (e) {
            reject(e);
        }
    }
}
