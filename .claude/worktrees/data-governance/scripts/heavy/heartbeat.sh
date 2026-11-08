#!/usr/bin/env bash
# (laptop) light check: a timestamp every 30 s for N minutes → data/logs/heartbeat.log (gaps = sleep / pauses)
n=$(( ${1:-10} * 2 )); : > ~/groundtruth-heavy/data/logs/heartbeat.log
for i in $(seq "$n"); do date +%s >> ~/groundtruth-heavy/data/logs/heartbeat.log; sleep 30; done
echo done >> ~/groundtruth-heavy/data/logs/heartbeat.log
