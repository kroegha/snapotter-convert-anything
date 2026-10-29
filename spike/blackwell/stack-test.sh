#!/usr/bin/env bash
# Phase 0 spike, part A: CUDA 13.4 runtime + torch 2.15 cu134 + ORT 1.31.1 +
# every amd64 GPU bundle's Python deps, then GPU checks on the RTX 5090.
# Runs inside nvidia/cuda:13.4.3-cudnn-runtime-ubuntu24.04 with --gpus all.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CACHE_DIR=/cache/pip
mkdir -p /out
exec > >(tee /out/stack-test.log) 2>&1

echo "== nvidia-smi"; nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv
echo "== apt"; apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  python3 python3-venv python3-dev gcc g++ git libgl1 libglib2.0-0 ca-certificates >/dev/null
python3 --version

echo "== venv"; python3 -m venv /opt/venv; . /opt/venv/bin/activate
pip install -q --upgrade pip wheel "setuptools<75"

echo "== install bundle stack"; python3 /spike/install_stack.py
pip install -q --no-deps onnx==1.16.2   # test-only: builds the tiny ONNX model for the ORT check (newer onnx needs numpy 2 ml_dtypes)

echo "== GPU checks"; python3 /spike/gpu_tests.py
echo "== done"
