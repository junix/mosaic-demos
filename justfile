set shell := ["bash", "-euo", "pipefail", "-c"]

# Homebrew node@26 first when it exists (macOS); a no-op elsewhere.
export PATH := "/opt/homebrew/opt/node@26/bin:" + env_var("PATH")

default: build

# Install deps on demand, type-check and build the bundle.
build:
    #!/usr/bin/env bash
    [[ -d node_modules ]] || npm ci --no-fund --no-audit
    npm run build

# Build, then re-render and validate every capture in out/.
test: build
    npm test

# Browser demo repo — no binary, no launcher (ADR-749: nothing to install).
install:
    @echo "mosaic-demos: browser demos, nothing to install"

# Re-render the captures without the full validate pass.
render: build
    npm run render

# Vite dev server for live editing.
dev:
    npm run dev

# Remove generated captures.
clean:
    rm -rf out
    mkdir -p out
    touch out/.gitkeep

# Rebuild gallery.html from catalog.json, README.md and the artifacts in out/.
gallery:
    python3 tools/gallery.py

# Fail unless gallery.html matches the sources and artifacts.
gallery-check:
    python3 tools/gallery.py --check
