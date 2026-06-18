#!/usr/bin/env bash
# Render build script — runs during deploy

set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright's Chromium + its OS-level dependencies
playwright install --with-deps chromium
