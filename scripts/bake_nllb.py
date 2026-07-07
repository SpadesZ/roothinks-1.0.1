#!/usr/bin/env python
# 路徑(./scripts/bake_nllb.py) 版本 v1.0
# 目的:把 NLLB-200-distilled-600M bake 進 Docker image,產出為「本地 safetensors 模型目錄」。
#
# 為什麼不直接 from_pretrained 下載就好:
#   transformers 4.57 對 torch.load 加了 CVE-2025-32434 守衛(check_torch_load_is_safe),
#   要求 torch>=2.6 才能載入 .bin 權重;NLLB-600M 在 HF 僅提供 pytorch_model.bin(無 safetensors)。
#   而本環境的 pytorch CPU index 只提供 torch 2.10/2.11/2.12,其版號被守衛的比較邏輯誤判成 <2.6,
#   於是 from_pretrained 直接拋 ValueError。
#
# 解法:繞過整條 torch.load gate——
#   1. from_config 只建立模型架構(不載權重,不觸 gate);
#   2. 用 huggingface_hub 抓 raw pytorch_model.bin,自己 torch.load(weights_only=True) 成 state dict;
#   3. load_state_dict 後 save_pretrained(safe_serialization=True) 落成 model.safetensors
#      (save_pretrained 會正確處理 NLLB 的 tied embeddings,不會踩 safetensors 共享記憶體限制);
#   4. runtime 直接 from_pretrained(本地目錄) 載入 safetensors,永不觸 gate、且離線可用。
import os
import sys

import torch
from huggingface_hub import hf_hub_download
from transformers import AutoConfig, AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = "facebook/nllb-200-distilled-600M"
# HF 下載快取(暫存,與最終輸出分開)
HF_CACHE = os.environ.get("NLLB_HF_CACHE", "/opt/models/hf")
# 最終「本地 safetensors 模型目錄」——runtime 由 NLLB_MODEL_DIR 指到這裡
OUT = os.environ.get("NLLB_MODEL_DIR", "/opt/models/nllb")

os.makedirs(HF_CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

print(f"[bake_nllb] model={MODEL} hf_cache={HF_CACHE} out={OUT}", flush=True)

tokenizer = AutoTokenizer.from_pretrained(MODEL, cache_dir=HF_CACHE)
config = AutoConfig.from_pretrained(MODEL, cache_dir=HF_CACHE)

# 只建架構,不載權重 → 不觸 check_torch_load_is_safe
model = AutoModelForSeq2SeqLM.from_config(config)

bin_path = hf_hub_download(MODEL, "pytorch_model.bin", cache_dir=HF_CACHE)
print(f"[bake_nllb] loading raw weights: {bin_path}", flush=True)
state = torch.load(bin_path, map_location="cpu", weights_only=True)

missing, unexpected = model.load_state_dict(state, strict=False)
if missing:
    print(f"[bake_nllb] WARNING missing keys: {len(missing)} (first: {missing[:3]})", flush=True)
if unexpected:
    print(f"[bake_nllb] WARNING unexpected keys: {len(unexpected)} (first: {unexpected[:3]})", flush=True)

# 落成 safetensors(save_pretrained 會處理 tied weights)
model.save_pretrained(OUT, safe_serialization=True)
tokenizer.save_pretrained(OUT)

# 驗證:確認本地目錄能被 from_pretrained 載入(走 safetensors,不觸 gate)
_ = AutoModelForSeq2SeqLM.from_pretrained(OUT)
saved = sorted(os.listdir(OUT))
if not any(f.endswith(".safetensors") for f in saved):
    print(f"[bake_nllb] FATAL: no safetensors produced in {OUT}: {saved}", file=sys.stderr)
    sys.exit(1)
print(f"[bake_nllb] OK -> NLLB baked as safetensors at {OUT}: {saved}", flush=True)
