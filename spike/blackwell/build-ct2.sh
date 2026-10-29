#!/usr/bin/env bash
# Phase 0 spike, part B: build CTranslate2 against CUDA 13.4 with native sm_120.
# Runs inside nvidia/cuda:13.4.3-cudnn-devel-ubuntu24.04 (no GPU needed).
#
# CUDA_ARCH_LIST keeps to single-digit majors because CTranslate2 still uses
# CMake's legacy FindCUDA arch parser, which is reported to mangle "12.0".
# Blackwell is added with explicit -gencode flags instead, and the result is
# checked afterwards for real sm_120 SASS.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CACHE_DIR=/cache/pip
CT2_VERSION="${CT2_VERSION:-4.8.4}"
JOBS="${CT2_JOBS:-16}"
mkdir -p /out
exec > >(tee /out/ct2-build.log) 2>&1

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  git cmake ninja-build g++ python3 python3-venv python3-dev ca-certificates patchelf >/dev/null
cmake --version | head -1; nvcc --version | tail -2

git clone -q --depth 1 --branch "v${CT2_VERSION}" --recurse-submodules --shallow-submodules \
  https://github.com/OpenNMT/CTranslate2 /src/ct2
cd /src/ct2

cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/opt/ct2 \
  -DWITH_CUDA=ON -DWITH_CUDNN=ON -DCUDA_DYNAMIC_LOADING=ON \
  -DWITH_MKL=OFF -DWITH_DNNL=OFF -DWITH_RUY=ON -DOPENMP_RUNTIME=COMP \
  -DBUILD_CLI=OFF \
  -DCUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0" \
  -DCUDA_NVCC_FLAGS="-gencode=arch=compute_120,code=sm_120;-gencode=arch=compute_120,code=compute_120"
cmake --build build -j"${JOBS}"
cmake --install build

python3 -m venv /opt/bvenv; . /opt/bvenv/bin/activate
pip install -q -r python/install_requirements.txt wheel auditwheel
cd python
CTRANSLATE2_ROOT=/opt/ct2 python3 setup.py -q bdist_wheel
LD_LIBRARY_PATH=/opt/ct2/lib auditwheel repair dist/*.whl -w /out/wheel \
  --exclude 'libcudnn*' --exclude 'libcuda.so*' --exclude 'libcublas*' --exclude 'libcudart*'
ls -la /out/wheel
echo "== embedded GPU architectures"
python3 /spike/fatbin_archs.py /out/wheel/*.whl libctranslate2
echo "== done"
