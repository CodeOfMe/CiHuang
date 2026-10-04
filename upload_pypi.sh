#!/bin/bash
# Upload CiHuang to PyPI (Unix/Linux/macOS)

set -e

echo "=== CiHuang PyPI Upload Script ==="
echo ""

command -v python3 >/dev/null 2>&1 || { echo "Python3 required but not installed."; exit 1; }
command -v twine >/dev/null 2>&1 || { echo "Twine not installed. Run: pip install twine"; exit 1; }
command -v build >/dev/null 2>&1 || { echo "Build not installed. Run: pip install build"; exit 1; }

VERSION=$(python3 -c "import re,pathlib;print(re.search(r'\"([^\"]+)\"', pathlib.Path('src/cihuang/__version__.py').read_text()).group(1))")
echo "Building version: $VERSION"
echo ""

echo "Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info/
echo ""

echo "Building package..."
python3 -m build
echo ""

echo "Checking package..."
twine check dist/*
echo ""

echo "Uploading to PyPI..."
twine upload dist/*

echo ""
echo "=== Upload Complete ==="
echo "Package should be available at: https://pypi.org/project/cihuang/$VERSION/"
