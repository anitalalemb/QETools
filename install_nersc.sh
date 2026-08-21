#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
python -m pip install --user -e "$ROOT"
echo
echo "Add this once to ~/.bashrc:"
echo "export PATH=\"$ROOT/bin:\$PATH\""
