#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -d outputs/demo ]]; then
  python3 examples/build_demo.py
fi
python3 -m partshelf --catalog outputs/demo/catalog serve --workspace . --project outputs/demo/workshop-board --port 0 --open
