/* 路徑(./app/static/js/cube_renderer.js) #版本 v1.6 #更版時間 20260226-0630 */

/** CubeRenderer (v1.6) - 3D Academic Cube 視覺化
 * [v1.6 Update]:
 * 1. [Fix] 統一 Colorbar 與方塊顏色的映射基準，強制將 Colorbar 的 cmin 設為 0.0，cmax 設為 1.0，解決低分方塊顏色與表尺對不上的視覺脫鉤問題。
 * [v1.5 Update]:
 * 1. [Critical Fix] 解決 ReferenceError (Cannot access 'self' before initialization)。
 * 將 self = this 的宣告提升至函式最頂端，避免在迴圈內觸發 TDZ (暫時性死區) 導致前端死鎖與後端 500 錯誤。
 * [v1.4 Update]:
 * 1. 軸標題強制截斷：避免長段落當 axis title（災難性顯示）
 * 2. 軸標題格式：「中文名稱\nEnglish Name」精簡雙語
 * 3. Tag 刻度文字截斷：最多10中文字 + 4英文字
 * 4. Hover 文字格式優化：雙語對照 + 精簡
 */
const CubeRenderer = {
    _observer: null,

    /**
     * 格式化雙語標籤 → "中文<br>English"
     */
    _formatBilingualTag: function(tag) {
        if (!tag || typeof tag !== 'string') return tag;
        
        if (tag.includes('\\n')) {
            return tag.split('\\n').map(s => s.trim()).join('<br>');
        }
        const match = tag.match(/^(.+?)\s*\((.+)\)\s*$/);
        if (match) {
            return match[1].trim() + '<br>' + match[2].trim();
        }
        if (tag.includes('\n')) {
            return tag.split('\n').map(s => s.trim()).filter(Boolean).join('<br>');
        }
        return tag;
    },

    /**
     * [v1.4 New] 截斷軸標題 — 只保留短詞組
     * 輸入可能是長段落，需截斷為 ≤maxCN 中文 + ≤maxEN 英文字
     */
    _truncateLabel: function(text, maxCN, maxEN) {
        if (!text || typeof text !== 'string') return text;
        
        // 先分離中英文
        let cn = '', en = '';
        if (text.includes('\\n')) {
            const parts = text.split('\\n');
            cn = parts[0].trim();
            en = (parts[1] || '').trim();
        } else if (text.includes('\n')) {
            const parts = text.split('\n');
            cn = parts[0].trim();
            en = (parts[1] || '').trim();
        } else {
            // 單語：嘗試分離中英文
            const cjk = text.match(/[\u4e00-\u9fff\u3400-\u4dbf]+/g);
            const latin = text.match(/[A-Za-z][\w\s&-]*/g);
            cn = cjk ? cjk.join('') : '';
            en = latin ? latin.join(' ').trim() : '';
            if (!cn && !en) return text.substring(0, 20);
        }
        
        // 截斷中文
        if (cn.length > maxCN) cn = cn.substring(0, maxCN);
        // 截斷英文
        if (en) {
            const words = en.split(/\s+/);
            if (words.length > maxEN) en = words.slice(0, maxEN).join(' ');
        }
        
        if (cn && en) return cn + '<br>' + en;
        return cn || en || text.substring(0, 15);
    },

    /**
     * [v1.4 New] 截斷 Tag 刻度文字（比 label 可稍長）
     */
    _truncateTag: function(tag) {
        return this._truncateLabel(tag, 10, 5);
    },

    /**
     * 渲染 3D Voxel Cube
     * @param {string} containerId - DOM 容器 ID (例如 'cube-container')
     * @param {object} data - 後端回傳的資料物件
     * @param {Array} data.voxels - Voxel 陣列 [{x, y, z, val, hover}, ...]
     * @param {object} data.axis_labels - 軸向定義 {x, y, z}
     */
    renderVoxels: function(containerId, data) {
        // [v1.5 Fix] 提前綁定 this，徹底消除下方迴圈內的 ReferenceError 崩潰
        const self = this;

        const container = document.getElementById(containerId);
        if (!container) {
            console.error(`[CubeRenderer] Container #${containerId} not found.`);
            return;
        }

        // [v0.9 Fix] 強制修正容器樣式以適配 Plotly
        // Bootstrap 的 d-flex align-items-center 會導致 Canvas 高度塌陷或位置偏移
        // 這裡我們移除 Flex 相關 class，並強制設為 Block 佈局撐滿父元素
        // [v1.2 Fix] 強制移除 Flex class，確保 Plotly 正常撐滿
        container.classList.remove('d-flex', 'align-items-center', 'justify-content-center');
        container.style.display = 'block';
        container.style.width = '100%';
        container.style.height = '100%';

        // [v0.7] 步驟 1: 強制清理舊狀態 (Memory Leak Prevention)
        this._cleanup(containerId);
        
        // [v1.2 Fix] 清除殘留的 placeholder 文字 (Waiting for Data...)
        container.innerHTML = '';

        // [v0.7] 步驟 2: 資料防呆與格式檢查
        if (data && !data.voxels && !Array.isArray(data.voxels)) {
             // 容許 data.voxels 為 undefined (視為空), 但若有值必須是 Array
             console.warn("[CubeRenderer] Voxels data is missing or invalid format.");
        }
        
        const voxels = (data && Array.isArray(data.voxels)) ? data.voxels : [];
        const defs = data.axis_labels || { x: 'X-Axis', y: 'Y-Axis', z: 'Z-Axis' };

        // 若無資料，顯示空狀態
        // [v0.9] 若無資料，需暫時加回 Flex 屬性以置中顯示文字，保持 UX 一致性
        if (voxels.length === 0) {
            container.classList.add('d-flex', 'align-items-center', 'justify-content-center');
            container.innerHTML = `
                <div class="h-100 w-100 d-flex align-items-center justify-content-center text-white-50">
                    <div class="text-center">
                        <i class="bi bi-box-seam display-4 d-block mb-2"></i>
                        No Data Available to Render
                    </div>
                </div>`;
            return;
        }

        console.log(`[CubeRenderer] Rendering ${voxels.length} voxels as 3D cubes...`);

        // =====================================================
        // [v1.2] 步驟 3: 分類軸 → 數值索引映射
        // =====================================================
        const uniqueX = [...new Set(voxels.map(v => v.x))];
        const uniqueY = [...new Set(voxels.map(v => v.y))];
        const uniqueZ = [...new Set(voxels.map(v => v.z))];
        
        const xMap = {}; uniqueX.forEach((t, i) => xMap[t] = i);
        const yMap = {}; uniqueY.forEach((t, i) => yMap[t] = i);
        const zMap = {}; uniqueZ.forEach((t, i) => zMap[t] = i);

        const xLen = uniqueX.length;
        const yLen = uniqueY.length;
        const zLen = uniqueZ.length;

        // =====================================================
        // [v1.2] 步驟 4: 正確的立方體幾何定義
        //
        // 頂點排列 (center=0, half=s):
        //   0:(−s,−s,−s) 1:(+s,−s,−s) 2:(+s,+s,−s) 3:(−s,+s,−s)
        //   4:(−s,−s,+s) 5:(+s,−s,+s) 6:(+s,+s,+s) 7:(−s,+s,+s)
        //
        // 6 面 × 2 三角 = 12 個三角面
        // =====================================================
        const halfSize = 0.22;
        const traces = [];

        // Viridis 色階
        const viridis = (t) => {
            t = Math.max(0, Math.min(1, t));
            const stops = [
                [0.0,  68, 1, 84],
                [0.25, 59, 82, 139],
                [0.5,  33, 145, 140],
                [0.75, 94, 201, 98],
                [1.0,  253, 231, 37]
            ];
            let lo = stops[0], hi = stops[stops.length - 1];
            for (let i = 0; i < stops.length - 1; i++) {
                if (t >= stops[i][0] && t <= stops[i + 1][0]) {
                    lo = stops[i]; hi = stops[i + 1]; break;
                }
            }
            const f = (hi[0] === lo[0]) ? 0 : (t - lo[0]) / (hi[0] - lo[0]);
            const r = Math.round(lo[1] + f * (hi[1] - lo[1]));
            const g = Math.round(lo[2] + f * (hi[2] - lo[2]));
            const b = Math.round(lo[3] + f * (hi[3] - lo[3]));
            return `rgb(${r},${g},${b})`;
        };

        // [v1.2 Fix] 正確的立方體三角面索引
        //  Front(z−): 0-1-2, 0-2-3 | Back(z+): 4-6-5, 4-7-6
        //  Bottom(y−): 0-5-1, 0-4-5 | Top(y+): 3-2-6, 3-6-7
        //  Left(x−): 0-3-7, 0-7-4   | Right(x+): 1-5-6, 1-6-2
        const cubeI = [0, 0, 4, 4, 0, 0, 3, 3, 0, 0, 1, 1];
        const cubeJ = [1, 2, 6, 7, 5, 4, 2, 6, 3, 7, 5, 6];
        const cubeK = [2, 3, 5, 6, 1, 5, 6, 7, 7, 4, 6, 2];

        voxels.forEach((v, idx) => {
            const cx = xMap[v.x];
            const cy = yMap[v.y];
            const cz = zMap[v.z];
            const s = halfSize;
            
            // 8 個頂點 (標準立方體排列)
            const vx = [cx-s, cx+s, cx+s, cx-s, cx-s, cx+s, cx+s, cx-s];
            const vy = [cy-s, cy-s, cy+s, cy+s, cy-s, cy-s, cy+s, cy+s];
            const vz = [cz-s, cz-s, cz-s, cz-s, cz+s, cz+s, cz+s, cz+s];

            const color = viridis(v.val);
            
            // [v1.4] 格式化 hover：精簡雙語顯示
            // [v1.5 Fix] 這裡的 self._truncateTag 已能正確取得外層宣告的 self
            const hoverParts = [];
            hoverParts.push(`<b>RPI: ${v.val.toFixed(2)}</b>`);
            hoverParts.push(`X: ${self._truncateTag(v.x)}`);
            hoverParts.push(`Y: ${self._truncateTag(v.y)}`);
            hoverParts.push(`Z: ${self._truncateTag(v.z)}`);
            if (v.hover) {
                // hover 可能含 <br>，直接附加
                hoverParts.push('─────');
                hoverParts.push(v.hover.length > 120 ? v.hover.substring(0, 120) + '...' : v.hover);
            }
            const hoverText = hoverParts.join('<br>');

            traces.push({
                type: 'mesh3d',
                x: vx, y: vy, z: vz,
                i: cubeI, j: cubeJ, k: cubeK,
                color: color,
                opacity: 0.90,
                flatshading: true,
                lighting: { 
                    ambient: 0.65, 
                    diffuse: 0.45, 
                    specular: 0.2, 
                    roughness: 0.6,
                    fresnel: 0.1
                },
                lightposition: { x: 800, y: 800, z: 1200 },
                hoverinfo: 'text',
                text: hoverText,
                name: `V${idx}`,
                showlegend: false
            });
        });

        // =====================================================
        // [v1.2 New] 步驟 4A: 三條主軸線 (粗線 + 軸定義標籤)
        // =====================================================
        const axisLineStyle = { width: 4, color: 'rgba(255,255,255,0.7)' };
        const axisStart = -0.5;
        const xEnd = xLen - 0.5;
        const yEnd = yLen - 0.5;
        const zEnd = zLen - 0.5;

        // X 軸線
        traces.push({
            type: 'scatter3d', mode: 'lines',
            x: [axisStart, xEnd], y: [axisStart, axisStart], z: [axisStart, axisStart],
            line: { ...axisLineStyle, color: 'rgba(255,100,100,0.8)' },
            hoverinfo: 'skip', showlegend: false
        });
        // Y 軸線
        traces.push({
            type: 'scatter3d', mode: 'lines',
            x: [axisStart, axisStart], y: [axisStart, yEnd], z: [axisStart, axisStart],
            line: { ...axisLineStyle, color: 'rgba(100,255,100,0.8)' },
            hoverinfo: 'skip', showlegend: false
        });
        // Z 軸線
        traces.push({
            type: 'scatter3d', mode: 'lines',
            x: [axisStart, axisStart], y: [axisStart, axisStart], z: [axisStart, zEnd],
            line: { ...axisLineStyle, color: 'rgba(100,150,255,0.8)' },
            hoverinfo: 'skip', showlegend: false
        });

        // =====================================================
        // [v1.6 Fix] 步驟 4B: Colorbar (隱形 scatter) - 鎖定絕對範圍
        // =====================================================
        // [v1.6] 不再使用動態的 valMin / valMax，強制使用絕對 0.0 ~ 1.0 作為標尺基準
        // 這樣方塊內部的 viridis(0.75) 才會與右側 Colorbar 上的 0.75 刻度顏色完美對齊。
        traces.push({
            type: 'scatter3d', mode: 'markers',
            x: [null], y: [null], z: [null],
            marker: {
                size: 0.01,
                color: [0.0, 1.0], // [v1.6] 給定絕對範圍的假資料
                cmin: 0.0,         // [v1.6] 強制鎖定底限為 0
                cmax: 1.0,         // [v1.6] 強制鎖定上限為 1
                colorscale: 'Viridis',
                showscale: true,
                colorbar: {
                    title: 'RPI Score',
                    thickness: 15, len: 0.6,
                    tickfont: { color: '#ffffff' },
                    titlefont: { color: '#ffffff' }
                }
            },
            hoverinfo: 'skip', showlegend: false
        });

        // =====================================================
        // [v1.2] 步驟 5: Layout — 軸標題 + 雙語刻度
        // =====================================================
        const whiteFont = { color: '#ffffff', size: 12 };
        // [v1.5 Fix] const self = this; 已經提早至函式頂部宣告，此處移除以防止重複宣告

        const buildAxis = (title, uniqueTags, axisColor) => ({
            title: { 
                text: self._truncateLabel(title, 8, 4),  // [v1.4] 強制截短軸標題
                font: { color: axisColor, size: 12, family: 'sans-serif' } 
            },
            tickvals: uniqueTags.map((_, i) => i),
            ticktext: uniqueTags.map(t => self._truncateTag(t)),  // [v1.4] 截短 tick
            tickfont: { color: '#ffffff', size: 9 },
            tickangle: 0,
            showgrid: true,
            gridcolor: 'rgba(255,255,255,0.12)',
            zerolinecolor: 'rgba(255,255,255,0.3)',
            showbackground: true,
            backgroundcolor: 'rgba(30,30,40,0.3)',
            range: [-0.5, uniqueTags.length - 0.5]
        });

        const layout = {
            margin: { l: 0, r: 0, b: 0, t: 0 },
            paper_bgcolor: 'rgba(0,0,0,0)', 
            plot_bgcolor: 'rgba(0,0,0,0)',
            scene: {
                xaxis: buildAxis(defs.x, uniqueX, 'rgba(255,130,130,1)'),
                yaxis: buildAxis(defs.y, uniqueY, 'rgba(130,255,130,1)'),
                zaxis: buildAxis(defs.z, uniqueZ, 'rgba(130,170,255,1)'),
                camera: {
                    eye: { x: 1.9, y: 1.9, z: 1.5 },
                    center: { x: 0, y: 0, z: -0.05 }
                },
                aspectmode: 'cube'
            },
            autosize: true,
            showlegend: false
        };

        const config = {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ['resetCameraLastSave3d']
        };

        // 步驟 6: 執行繪圖
        Plotly.newPlot(containerId, traces, layout, config)
            .then(() => {
                // 步驟 7: 綁定 ResizeObserver
                // [v0.9] 再次確保容器大小正確
                Plotly.Plots.resize(container);
                
                this._observer = new ResizeObserver(entries => {
                    for (let entry of entries) {
                        Plotly.Plots.resize(container);
                    }
                });
                
                this._observer.observe(container);
                console.log("[CubeRenderer] Render complete with ResizeObserver (v0.9).");
            })
            .catch(err => {
                console.error("[CubeRenderer] Plotly render error:", err);
                container.innerHTML = `<div class="text-danger text-center p-3">Render Error: ${err.message}</div>`;
            });
    },

    /**
     * 清除畫布並重置為等待狀態
     * @param {string} containerId 
     */
    clear: function(containerId) {
        const container = document.getElementById(containerId);
        if (container) {
            this._cleanup(containerId);
            
            // [v0.9] 清除時恢復 Flexbox 置中，讓等待文字好看一點
            container.classList.add('d-flex', 'align-items-center', 'justify-content-center');

            container.innerHTML = `
                <div class="h-100 w-100 d-flex align-items-center justify-content-center text-white-50">
                    <div class="text-center">
                        <i class="bi bi-box-seam display-4 d-block mb-2"></i>
                        Waiting for Taxonomy Data...
                    </div>
                </div>`;
        }
    },

    /**
     * [v0.7 New] 內部清理函數
     * 負責斷開 Observer 與銷毀 Plotly 實例
     * @param {string} containerId 
     */
    _cleanup: function(containerId) {
        if (this._observer) {
            this._observer.disconnect();
            this._observer = null;
        }
        
        try {
            const container = document.getElementById(containerId);
            if (container && container.data) {
                Plotly.purge(containerId);
            }
        } catch(e) { 
            console.warn("[CubeRenderer] Purge warning:", e);
        }
    }
};