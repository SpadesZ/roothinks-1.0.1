# Roothinks source maintenance contract
# 目的：R7 雙語閱讀介面的合成「豐富」論文；格式與 Flow A／Flow B 產物相同（05_interprets/fusion/full_text.json、
#       05_interprets/full_text_trans.json、segmentation/{pid}_vns.json＋切塊 PNG），供真瀏覽器檢查閱讀器版面。
# 內容：4 頁、多欄式長段落、行內數學（$\kappa$、µm、10⁻³）、4 個獨立公式（含 1 個 KaTeX 不支援的巨集、1 個 OCR 失敗）、
#       圖與表（真 PNG）、行內引文 [12]／(Smith et al., 2020)、參考文獻、註腳、超長網址／DOI／檔名、
#       缺譯段落、譯文遠長於原文的段落、短碎片、臺灣繁中術語。全部自寫，不含任何真實論文內容。
# 呼叫：study_coresearch_fixture.py 在 app context 內呼叫 install(root, pid)；論文代碼排在第一篇之後，
#       既有旅程的「第一個閱讀全文按鈕」仍是 synthetic_calibration。
# 邊界：只寫 fixture 的新 tmp 目錄；不連外、不呼叫模型；資料只供瀏覽器測試。
# 驗證：python test/integration_e2e/reading_ui_fixture_paper.py（印出區塊統計）；pytest test/unit/test_reading_ui_r7_fixture.py
import json
from pathlib import Path

PAPER_ID = 'synthetic_r7_reading'
TITLE = 'Strain-Tunable In-Plane Thermal Conductivity of Suspended Few-Layer Graphene Microribbons'
LONG_URL = ('https://data.example.org/repository/synthetic-thermal-transport/graphene-microribbons/'
            'raman-thermometry/2026-05-01/raw_spectra_and_calibration_v2.3.1.zip')
LONG_DOI = 'doi:10.5555/zenodo.1234567890123'
LONG_FILE = 'ThermoFlux_v2.3.1_calibration_matrix_20260501_final_revised.csv'
LONG_EQ = (r'\kappa(T,\varepsilon) = \kappa_0 \left(1 + \alpha \varepsilon + \beta \varepsilon^{2}\right)'
           r' \exp\!\left(-\frac{E_a}{k_B T}\right) + \sum_{i=1}^{N} \frac{C_i\, v_i^{2}\, \tau_i}{3}'
           r' \left[1 - \frac{\tau_i}{\tau_i + \tau_{\mathrm{b}}(w)}\right] - \gamma\, \frac{\partial \omega_{2D}}{\partial \varepsilon}')
BAD_EQ = r'R_{\mathrm{th}} = \SI{4.2e6}{\kelvin\per\watt} \pm \SI{0.3e6}{\kelvin\per\watt}'

# (type, content, content_zh, extra)。content_zh 為 None = 沒有機器翻譯（Flow B 漏翻）。
PAGES = [
    (1, [
        ('Header', 'J. Synth. Therm. Mater. 12 (2026) 101–118', None, {'is_page_noise': True}),
        ('MainTitle', TITLE, '懸空少層石墨烯微帶的應變可調面內熱導率', {}),
        ('Body', 'Ana Lin¹, Bo-Wei Chen²*, Carla Ruiz-Hernández¹ and Daniel O’Connor³',
         'Ana Lin¹、Bo-Wei Chen²*、Carla Ruiz-Hernández¹ 與 Daniel O’Connor³', {}),
        ('Subtitle', 'Abstract', '摘要', {}),
        ('Body',
         'We report Raman-thermometry measurements of the in-plane thermal conductivity $\\kappa$ of suspended '
         'few-layer graphene (FLG) microribbons under uniaxial tensile strain $\\varepsilon$ up to 0.8%. For ribbons '
         '2–5 µm wide and 12 µm long, $\\kappa$ decreased from 1850 ± 120 to 1210 ± 95 W m⁻¹ K⁻¹ at 300 K, a '
         'reduction of 34.6%. The strain coefficient $d\\kappa/d\\varepsilon = -(8.0 \\pm 0.6) \\times 10^{2}$ '
         'W m⁻¹ K⁻¹ per percent strain is consistent with phonon softening predicted by first-principles '
         'calculations [12, 14] but is about twice the value inferred from bulk graphite (Smith et al., 2020). '
         'Absorbed laser power was kept below 1.5 mW, corresponding to a temperature rise ΔT ≤ 18 K and a power '
         'density below 10⁻³ W µm⁻².',
         '我們以拉曼測溫法量測懸空少層石墨烯（FLG）微帶在單軸拉伸應變 $\\varepsilon$（最高 0.8%）下的面內熱導率 '
         '$\\kappa$。寬 2–5 µm、長 12 µm 的微帶在 300 K 時，$\\kappa$ 由 1850 ± 120 降至 1210 ± 95 W m⁻¹ K⁻¹，'
         '降幅 34.6%。應變係數 $d\\kappa/d\\varepsilon = -(8.0 \\pm 0.6) \\times 10^{2}$ W m⁻¹ K⁻¹（每 1% 應變）'
         '與第一原理計算預測的聲子軟化一致 [12, 14]，但約為由塊材石墨推得之值的兩倍（Smith et al., 2020）。'
         '吸收雷射功率維持在 1.5 mW 以下，對應溫升 ΔT ≤ 18 K、功率密度低於 10⁻³ W µm⁻²。', {}),
        ('Body', '1. Introduction', '1. 緒論', {}),
        ('Body',
         'Graphene combines an exceptionally high intrinsic thermal con- ductivity with a mechanical stiffness that '
         'allows reversible elastic strain of several percent [1–4]. Because the long-wavelength flexural (ZA) '
         'phonons that dominate heat transport in suspended layers are sensitive to tension, strain has been '
         'proposed as a knob for tuning $\\kappa$ in situ [5, 7, 9]. Previous measurements, however, were performed '
         'on supported films where substrate scattering masks the intrinsic response (Balandin et al., 2008; Nika & '
         'Balandin, 2017), or on ribbons whose width $w$ was comparable to the phonon mean free path $\\Lambda \\approx '
         '0.8$ µm, so that boundary scattering and strain effects could not be separated. A quantitative, '
         'width-resolved strain coefficient is therefore still missing, and reported values differ by more than a '
         'factor of three across groups using nominally identical protocols [10, 11].',
         '石墨烯兼具極高的本徵熱導率與足以承受數個百分比可逆彈性應變的機械剛性 [1–4]。由於在懸空層中主導熱傳輸的長波長'
         '撓曲（ZA）聲子對張力很敏感，應變被提出作為原位調控 $\\kappa$ 的手段 [5, 7, 9]。然而，過去的量測若不是在有'
         '基板支撐的薄膜上進行（基板散射會掩蓋本徵響應；Balandin et al., 2008；Nika & Balandin, 2017），就是使用寬度 '
         '$w$ 與聲子平均自由徑 $\\Lambda \\approx 0.8$ µm 相當的微帶，因此無法分離邊界散射與應變效應。依寬度解析的'
         '定量應變係數至今仍付之闕如，而各團隊以名義上相同流程得到的數值相差超過三倍 [10, 11]。', {}),
        ('Body',
         'Here we isolate the strain dependence by suspending each ribbon over a 6 µm trench and straining it '
         'electrostatically, which avoids substrate scattering [8].',
         '在本研究中，我們把每一條微帶懸空架設在寬 6 µm 的溝槽上方，並以靜電方式對其施加應變，藉此把應變對熱導率的影響'
         '與其他因素分離開來。這種做法的主要好處是完全避免了基板散射：在有基板支撐的樣本中，聲子會與基板表面的粗糙度、'
         '電荷雜質以及界面振動模態發生交互作用，使得量測到的熱導率同時受到多種散射機制影響，難以判斷應變本身造成的變化'
         '究竟有多大。懸空結構讓聲子只在石墨烯層內傳播，因此量測結果更接近材料的本徵行為；同時，靜電施力不需要機械夾具，'
         '可以在同一條微帶上連續、可逆地調整應變大小，並在每一個應變值重複量測，降低樣本之間差異造成的系統誤差 [8]。', {}),
        ('Footnote',
         '¹ Department of Physics, National Synthetic University, Hsinchu 30013, Taiwan. ² Corresponding author: '
         'bowei.chen@example.invalid. ³ Institute of Imaginary Materials, Dublin, Ireland.',
         '¹ 國立合成大學物理學系，新竹 30013，臺灣。² 通訊作者：bowei.chen@example.invalid。³ 虛構材料研究所，'
         '愛爾蘭都柏林。', {}),
        ('Header', '— 101 —', None, {'is_page_noise': True}),
    ]),
    (2, [
        ('Header', 'Lin et al. · Strain-tunable thermal conductivity', None, {'is_page_noise': True}),
        ('Subtitle', '2. Methods', '2. 方法', {}),
        ('SubSubtitle', '2.1 Sample fabrication and strain calibration', '2.1 樣本製作與應變校正', {}),
        ('Body',
         'Ribbons were patterned by electron-beam lithography (PMMA 950K A4, 300 nm) and oxygen-plasma etching, then '
         'released in buffered HF and dried at the critical point. Strain was calibrated from the shift of the 2D '
         'Raman peak, ∂ω₂D/∂ε = −64 ± 3 cm⁻¹ %⁻¹ [15]. All raw spectra, the calibration script and the fitted '
         f'parameters are archived at {LONG_URL} ({LONG_DOI}) under the file name {LONG_FILE}.',
         '微帶以電子束微影（PMMA 950K A4，300 nm）與氧電漿蝕刻定義圖形，接著在緩衝氫氟酸中釋放並以臨界點乾燥。應變由 '
         '2D 拉曼峰的位移校正，∂ω₂D/∂ε = −64 ± 3 cm⁻¹ %⁻¹ [15]。所有原始光譜、校正程式與擬合參數都已封存於 '
         f'{LONG_URL}（{LONG_DOI}），檔名為 {LONG_FILE}。', {}),
        ('Equation', 'κ = L/(2A) · χT / (∂ω/∂P) (1)', None,
         {'latex': r'\kappa = \frac{L}{2 A}\,\frac{\chi_T}{\partial \omega / \partial P} \qquad (1)', 'latex_confidence': 0.94}),
        ('Body',
         'where $L$ is the suspended length, $A = w t$ the cross-sectional area with width $w$ and thickness '
         '$t = n \\times 0.335$ nm for $n$ layers, $\\chi_T = -0.0162$ cm⁻¹ K⁻¹ the temperature coefficient of the '
         'G peak, and $\\partial \\omega/\\partial P$ the measured power coefficient. The factor 2 accounts for heat '
         'flowing to both clamped ends.',
         '其中 $L$ 為懸空長度，$A = w t$ 為截面積（寬度 $w$、$n$ 層的厚度 $t = n \\times 0.335$ nm），'
         '$\\chi_T = -0.0162$ cm⁻¹ K⁻¹ 為 G 峰的溫度係數，$\\partial \\omega/\\partial P$ 為量得的功率係數。'
         '係數 2 表示熱量流向兩個夾持端。', {}),
        # OCR 短碎片（< 12 字）：閱讀器會黏到上一段；譯文也必須跟著顯示。
        ('Body', '(n = 12)', '（n = 12）', {}),
        ('Equation', '(2)', None, {'latex': LONG_EQ + r' \qquad (2)', 'latex_confidence': 0.91}),
        ('Body',
         'The absorbed fraction of laser power, $\\eta = 2.3 \\pm 0.2\\%$ per layer, was measured in situ with a '
         'calibrated photodiode placed below the trench; reflection losses from the gold contacts were neglected.',
         None, {}),
        ('Equation', 'Rth = 4.2×10⁶ K W⁻¹ ± 0.3×10⁶ K W⁻¹ (3)', None, {'latex': BAD_EQ, 'latex_confidence': 0.88}),
        ('Equation', '<Failed to OCR Equation>', None,
         {'latex': '', 'equation_failed': True, 'equation_marker': '<Failed to OCR Equation>',
          'equation_failure_reason': 'latex_low_confidence', 'latex_confidence': 0.31}),
        ('Footnote', '⁴ Unless stated otherwise, uncertainties are one standard deviation over n = 12 ribbons.',
         '⁴ 除非另有說明，不確定度皆為 n = 12 條微帶的一個標準差。', {}),
    ]),
    (3, [
        ('Header', 'Lin et al. · Strain-tunable thermal conductivity', None, {'is_page_noise': True}),
        ('Title', '3. Results', '3. 結果', {}),
        ('Body',
         'Figure 1 shows that $\\kappa$ decreases linearly with $\\varepsilon$ for all widths, with no hysteresis '
         'between loading and unloading within the 2.5% repeatability of the measurement. Extrapolating the fit to '
         '$\\varepsilon = 0$ gives $\\kappa_0 = 1850 \\pm 120$ W m⁻¹ K⁻¹, in agreement with suspended monolayer '
         'values of 1800–2500 W m⁻¹ K⁻¹ [3, 16] once the interlayer correction of Ref. [17] is applied. Narrower '
         'ribbons are systematically less conductive (Table 1), as expected when boundary scattering shortens the '
         'effective mean free path.',
         '圖 1 顯示所有寬度的 $\\kappa$ 都隨 $\\varepsilon$ 線性下降；在量測重現性 2.5% 以內，加載與卸載之間沒有遲滯。'
         '將擬合外插至 $\\varepsilon = 0$ 得到 $\\kappa_0 = 1850 \\pm 120$ W m⁻¹ K⁻¹，套用文獻 [17] 的層間修正後，'
         '與懸空單層的 1800–2500 W m⁻¹ K⁻¹ 相符 [3, 16]。較窄的微帶導熱性系統性地較低（表 1），這與邊界散射縮短'
         '有效平均自由徑的預期一致。', {}),
        ('Figure',
         'Figure 1. In-plane thermal conductivity κ of suspended FLG microribbons versus uniaxial strain ε at 300 K. '
         'Symbols: mean of n = 12 ribbons; error bars: ±1 s.d.; dashed line: linear fit with dκ/dε = −(8.0 ± 0.6) × 10² '
         'W m⁻¹ K⁻¹ %⁻¹. Inset: optical micrograph of a 3 µm-wide ribbon (scale bar 5 µm).',
         '圖 1．懸空 FLG 微帶在 300 K 下的面內熱導率 κ 對單軸應變 ε。符號：n = 12 條微帶的平均；誤差棒：±1 標準差；'
         '虛線：線性擬合，dκ/dε = −(8.0 ± 0.6) × 10² W m⁻¹ K⁻¹ %⁻¹。插圖：寬 3 µm 微帶的光學顯微影像（比例尺 5 µm）。',
         {'has_caption': True}),
        ('Caption',
         'Table 1. Thermal conductivity κ (W m⁻¹ K⁻¹) and strain coefficient dκ/dε (10² W m⁻¹ K⁻¹ %⁻¹) for each '
         'ribbon width w. Values are mean ± s.d. over n ribbons.',
         '表 1．各微帶寬度 w 的熱導率 κ（W m⁻¹ K⁻¹）與應變係數 dκ/dε（10² W m⁻¹ K⁻¹ %⁻¹）。數值為 n 條微帶的'
         '平均 ± 標準差。', {}),
        ('Table',
         'w (µm) n κ(ε=0) κ(ε=0.8%) dκ/dε Λ_eff (µm) 2.0 4 1720 ± 140 1150 ± 110 −7.1 ± 0.9 0.61 3.0 4 1850 ± 120 '
         '1210 ± 95 −8.0 ± 0.6 0.74 5.0 4 1960 ± 150 1290 ± 120 −8.4 ± 0.8 0.82', None, {'has_caption': True}),
        ('Body',
         'The width dependence is captured by a Fuchs–Sondheimer-type boundary term with a specularity parameter '
         '$p = 0.12 \\pm 0.05$, i.e. predominantly diffuse edges, consistent with the edge roughness of 4–6 nm '
         'measured by atomic-force microscopy. Encapsulation with poly(methyl-methacrylate)-block-poly(dimethylsiloxane)'
         '-co-hydroxyethylmethacrylate did not change $\\kappa$ within uncertainty (see Supplementary Note S3 and '
         f'{LONG_FILE}).',
         '寬度相依性可由 Fuchs–Sondheimer 型邊界項描述，鏡面反射參數 $p = 0.12 \\pm 0.05$，亦即邊緣以漫反射為主，'
         '與原子力顯微鏡量得的 4–6 nm 邊緣粗糙度一致。以 poly(methyl-methacrylate)-block-poly(dimethylsiloxane)'
         '-co-hydroxyethylmethacrylate 封裝後，$\\kappa$ 在不確定度內沒有改變（見補充說明 S3 與 '
         f'{LONG_FILE}）。', {}),
    ]),
    (4, [
        ('Header', 'Lin et al. · Strain-tunable thermal conductivity', None, {'is_page_noise': True}),
        ('Body', '4. Discussion and conclusions', '4. 討論與結論', {}),
        ('Body',
         'The measured coefficient is roughly twice the bulk-graphite estimate (Smith et al., 2020) and within 15% of '
         'the first-principles prediction [12, 14]. We stress that all data were taken at a single temperature '
         '(300 K) and for strains below 0.8%; we make no claim about the low-temperature regime where ZA phonons '
         'are expected to dominate even more strongly, nor about strains beyond the linear elastic limit.',
         '量得的係數約為塊材石墨估計值的兩倍（Smith et al., 2020），且與第一原理預測相差在 15% 以內 [12, 14]。'
         '我們強調所有資料都是在單一溫度（300 K）、應變低於 0.8% 下取得；對於 ZA 聲子預期更占主導的低溫區，'
         '以及超過線性彈性極限的應變，本文不做任何主張。', {}),
        ('Body', 'References', '參考文獻', {}),
        ('Reference',
         '[1] A. A. Balandin, S. Ghosh, W. Bao, I. Calizo, D. Teweldebrhan, F. Miao, C. N. Lau, Superior thermal '
         'conductivity of single-layer graphene, Nano Lett. 8 (2008) 902–907. https://doi.org/10.1021/nl0731872',
         '[1] A. A. Balandin, S. Ghosh, W. Bao, I. Calizo, D. Teweldebrhan, F. Miao, C. N. Lau，單層石墨烯的優異'
         '熱導率，Nano Lett. 8 (2008) 902–907. https://doi.org/10.1021/nl0731872', {}),
        ('Reference',
         '[12] J. Smith, K. Tanaka, L. Moreau, Strain-dependent phonon transport in two-dimensional carbon from first '
         'principles, Phys. Rev. B 101 (2020) 165415. https://doi.org/10.1103/PhysRevB.101.165415',
         None, {}),
        ('Reference',
         '[15] Synthetic Data Consortium, Raman strain calibration tables for few-layer graphene, version 2.3.1, '
         '2026. Available at: https://data.example.org/repository/synthetic-thermal-transport/calibration/'
         'raman-2D-peak-strain-coefficients-few-layer-graphene-v2.3.1-supplementary-tables.pdf (accessed 1 May 2026).',
         '[15] Synthetic Data Consortium，少層石墨烯拉曼應變校正表，版本 2.3.1，2026。取自：'
         'https://data.example.org/repository/synthetic-thermal-transport/calibration/'
         'raman-2D-peak-strain-coefficients-few-layer-graphene-v2.3.1-supplementary-tables.pdf（2026 年 5 月 1 日存取）。',
         {}),
        ('Header', '— 104 —', None, {'is_page_noise': True}),
    ]),
]

_FAMILY = {'figure': 'fig', 'table': 'table'}


def _seq_id(page, kind, idx):
    return f'p{page}_{_FAMILY.get(kind.lower(), "body")}_{idx}'


def build_content(pid):
    """回傳 (含譯文的 content, 去掉譯文的 content)；欄位與 Flow A 融合檔相同。"""
    with_zh, plain = [], []
    for page, blocks in PAGES:
        rows = []
        for idx, (kind, text, zh, extra) in enumerate(blocks, start=1):
            row = {'type': kind, 'content': text, 'seq_id': _seq_id(page, kind, idx), 'pid': pid, 'paper_id': PAPER_ID,
                   'bbox': [72, 80 + idx * 60, 540, 120 + idx * 60]}
            row.update(extra)
            if zh:
                row['content_zh'] = zh
            rows.append(row)
        with_zh.append({'page': page, 'blocks': rows})
        plain.append({'page': page, 'blocks': [{k: v for k, v in r.items() if k != 'content_zh'} for r in rows]})
    return with_zh, plain


def _png(path, kind):
    """畫一張合成圖／表 PNG。沒有 Pillow 時退回單色 PNG（仍是合法圖檔）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - fixture 環境有 Pillow
        import struct, zlib
        w, h = (1400, 760) if kind == 'fig' else (2000, 420)
        raw = b''.join(b'\x00' + b'\xf4\xf6\xfa' * w for _ in range(h))
        chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
        path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                         + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
        return
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 28)
    except OSError:
        font = ImageFont.load_default()
    if kind == 'eq':
        img = Image.new('RGB', (900, 120), 'white')
        ImageDraw.Draw(img).text((30, 40), 'q(x) = −κ A dT/dx + σ_SB ε_r A (T⁴ − T_0⁴)        (4)', fill='black', font=font)
    elif kind == 'fig':
        img = Image.new('RGB', (1400, 760), 'white')
        d = ImageDraw.Draw(img)
        d.line([(140, 660), (1340, 660)], fill='black', width=3)
        d.line([(140, 660), (140, 60)], fill='black', width=3)
        d.text((620, 700), 'strain ε (%)', fill='black', font=font)
        d.text((10, 330), 'κ', fill='black', font=font)
        for i, (x, y) in enumerate([(200, 140), (420, 230), (640, 320), (860, 400), (1080, 490), (1300, 580)]):
            d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=('#2b6cb0' if i % 2 else '#c05621'))
            d.line([(x, y - 40), (x, y + 40)], fill='#4a5568', width=2)
        d.line([(200, 140), (1300, 580)], fill='#718096', width=2)
        d.rectangle([980, 80, 1320, 300], outline='black', width=2)
        d.text((1000, 100), 'inset: 3 µm ribbon', fill='black', font=font)
    else:
        img = Image.new('RGB', (2000, 420), 'white')
        d = ImageDraw.Draw(img)
        cols = ['w (µm)', 'n', 'κ(ε=0)', 'κ(ε=0.8%)', 'dκ/dε', 'Λ_eff (µm)']
        rows = [['2.0', '4', '1720 ± 140', '1150 ± 110', '−7.1 ± 0.9', '0.61'],
                ['3.0', '4', '1850 ± 120', '1210 ± 95', '−8.0 ± 0.6', '0.74'],
                ['5.0', '4', '1960 ± 150', '1290 ± 120', '−8.4 ± 0.8', '0.82']]
        d.line([(40, 40), (1960, 40)], fill='black', width=3)
        d.line([(40, 110), (1960, 110)], fill='black', width=2)
        d.line([(40, 380), (1960, 380)], fill='black', width=3)
        for c, label in enumerate(cols):
            d.text((60 + c * 320, 60), label, fill='black', font=font)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                d.text((60 + c * 320, 140 + r * 80), cell, fill='black', font=font)
    img.save(path)


def _write_vns(root, pid, content):
    """把圖／表／公式切塊登錄到 {pid}_vns.json；已有檔案（其他 fixture 寫的）就合併，不覆蓋別篇。"""
    seg = Path(root) / pid / 'literature' / 'segmentation'
    vns_path = seg / f'{pid}_vns.json'
    try:
        vns = json.loads(vns_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        vns = {}
    records = []
    for page in content:
        for block in page['blocks']:
            kind = block['type'].lower()
            if kind not in ('figure', 'table', 'equation'):
                continue
            family = {'figure': 'fig', 'table': 'table', 'equation': 'body'}[kind]
            png = seg / family / f'{pid}_{PAPER_ID}_{block["seq_id"]}.png'
            _png(png, {'figure': 'fig', 'table': 'table', 'equation': 'eq'}[kind])
            records.append({'seq_id': block['seq_id'], 'pid': pid, 'paper_id': PAPER_ID, 'page': page['page'],
                            'type': block['type'], 'png_path': str(png), 'has_caption': bool(block.get('has_caption'))})
    vns.setdefault('papers', {})[PAPER_ID] = {'paper_id': PAPER_ID, 'records': records}
    seg.mkdir(parents=True, exist_ok=True)
    vns_path.write_text(json.dumps(vns, ensure_ascii=False), encoding='utf-8')


def write_paper(root, pid):
    """只寫檔（不碰資料庫）；回傳論文目錄。"""
    paper_dir = Path(root) / pid / 'literature' / 'papers' / PAPER_ID
    fusion = paper_dir / '05_interprets' / 'fusion'
    fusion.mkdir(parents=True, exist_ok=True)
    with_zh, plain = build_content(pid)
    meta = {'paper_id': PAPER_ID, 'metadata': {'title': TITLE}}
    (fusion / 'full_text.json').write_text(json.dumps({**meta, 'content': plain}, ensure_ascii=False), encoding='utf-8')
    (paper_dir / '05_interprets' / 'full_text_trans.json').write_text(
        json.dumps({**meta, 'content': with_zh}, ensure_ascii=False), encoding='utf-8')
    (paper_dir / '05_interprets' / 'summary.json').write_text(json.dumps({
        'abstract_zh': '合成論文：懸空少層石墨烯微帶的熱導率在 300 K 隨單軸應變（≤ 0.8%）線性下降，'
                       'dκ/dε = −(8.0 ± 0.6) × 10² W m⁻¹ K⁻¹ %⁻¹；未量測其他溫度。',
        'key_findings': ['只在 300 K 量測', '應變 ≤ 0.8%', '較窄微帶熱導率較低']}, ensure_ascii=False), encoding='utf-8')
    _write_vns(root, pid, with_zh)
    return paper_dir


def install(root, pid):
    """fixture 掛鉤：寫檔＋登錄 Paper 資料列（需在 app context 內呼叫）。"""
    from app import db
    from app.models import Paper
    write_paper(root, pid)
    db.session.add(Paper(paper_id=PAPER_ID, pid=pid, title=TITLE, interpretation_status=Paper.STATUS_GOLD_READY))
    db.session.commit()
    return PAPER_ID


if __name__ == '__main__':
    content, _ = build_content('STUDYCO-p')
    kinds = {}
    for page in content:
        for block in page['blocks']:
            kinds[block['type']] = kinds.get(block['type'], 0) + 1
    print(json.dumps({'paper_id': PAPER_ID, 'pages': len(content), 'types': kinds,
                      'missing_zh': sum(1 for p in content for b in p['blocks']
                                        if b['type'] in ('Body', 'Reference') and 'content_zh' not in b)},
                     ensure_ascii=False))
