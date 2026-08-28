#!/bin/bash
cd /home/marc/rpinode
export PYTHONPATH=src
pkill -9 -f "src/main.py" || true
sleep 1
nohup python3 -u src/main.py > log/stdout.log 2>&1 &
echo "Server started in background."
