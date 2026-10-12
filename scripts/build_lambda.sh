#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo " Building AWS Lambda Deployment Package (Python 3.11)     "
echo "=========================================================="

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${PROJECT_DIR}/build"
DIST_DIR="${PROJECT_DIR}/dist"
TERRAFORM_DIR="${PROJECT_DIR}/terraform"

rm -rf "${BUILD_DIR}" "${DIST_DIR}"
mkdir -p "${BUILD_DIR}/package" "${DIST_DIR}"

echo "Step 1: Installing production dependencies into package..."
pip3 install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  --upgrade \
  --target "${BUILD_DIR}/package" \
  -r "${PROJECT_DIR}/requirements.txt" || {
    echo "Warning: Binary platform install encountered errors, falling back to local pip target install..."
    pip3 install --upgrade --target "${BUILD_DIR}/package" -r "${PROJECT_DIR}/requirements.txt"
}

echo "Step 2: Copying application source code..."
cp -R "${PROJECT_DIR}/src" "${BUILD_DIR}/package/"
cp "${PROJECT_DIR}/src/lambda_handler.py" "${BUILD_DIR}/package/"

echo "Step 3: Creating deployment zip artifact..."
(
  cd "${BUILD_DIR}/package"
  # Remove unnecessary files to reduce artifact size
  find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find . -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
  find . -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
  find . -name "*.pyc" -delete 2>/dev/null || true
  zip -r9 "${DIST_DIR}/lambda_function.zip" . > /dev/null
)

cp "${DIST_DIR}/lambda_function.zip" "${TERRAFORM_DIR}/lambda_payload.zip"

echo "=========================================================="
echo " Build Succeeded!"
echo " Artifact: ${DIST_DIR}/lambda_function.zip"
echo " Copied to: ${TERRAFORM_DIR}/lambda_payload.zip"
echo " Ready for: terraform apply"
echo "=========================================================="

