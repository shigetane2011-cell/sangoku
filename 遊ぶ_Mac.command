#!/bin/bash
# 三国布陣 — Mac用ランチャー。ダブルクリックで起動します。
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Python が見つかりません。https://www.python.org/downloads/ から入れてください。"
  read -p "Enterで閉じる"
  exit 1
fi
(sleep 1.5 && open http://localhost:8035) &
python3 -m sim.web
