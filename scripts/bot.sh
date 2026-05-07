#!/usr/bin/env bash
# OpenTrading Bot 관리 스크립트 (launchd 기반)
#
# 사용법:
#   ./scripts/bot.sh start    - 봇 시작 + launchd 등록 (자동 재시작/부팅 시작)
#   ./scripts/bot.sh stop     - 봇 정지 + launchd 해제
#   ./scripts/bot.sh restart  - 봇 재시작
#   ./scripts/bot.sh status   - 실행 상태 확인
#   ./scripts/bot.sh logs     - 실시간 로그 (trading.log)
#   ./scripts/bot.sh errors   - 에러 로그 (launchd.err)

set -euo pipefail

LABEL="com.opentrading.bot"
PROJECT_DIR="/Users/n-whjeong/Developer/private/OpenTrading"
PLIST_SRC="$PROJECT_DIR/scripts/com.opentrading.bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.opentrading.bot.plist"
LOG_DIR="$PROJECT_DIR/logs"

cmd="${1:-status}"

case "$cmd" in
    start)
        mkdir -p "$LOG_DIR"
        if [ ! -f "$PLIST_DST" ]; then
            echo "[bot] plist 설치: $PLIST_SRC -> $PLIST_DST"
            cp "$PLIST_SRC" "$PLIST_DST"
        fi
        if launchctl list | grep -q "$LABEL"; then
            echo "[bot] 이미 등록됨. 재시작..."
            launchctl unload "$PLIST_DST" 2>/dev/null || true
        fi
        launchctl load "$PLIST_DST"
        sleep 2
        if launchctl list | grep -q "$LABEL"; then
            pid=$(launchctl list | grep "$LABEL" | awk '{print $1}')
            echo "[bot] ✓ 시작됨 (PID: $pid)"
        else
            echo "[bot] ✗ 시작 실패. 에러 로그 확인: ./scripts/bot.sh errors"
            exit 1
        fi
        ;;

    stop)
        if [ -f "$PLIST_DST" ]; then
            launchctl unload "$PLIST_DST" 2>/dev/null || true
            echo "[bot] ✓ 정지됨"
        else
            echo "[bot] 등록되지 않음"
        fi
        ;;

    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;

    status)
        if launchctl list | grep -q "$LABEL"; then
            line=$(launchctl list | grep "$LABEL")
            pid=$(echo "$line" | awk '{print $1}')
            exit_code=$(echo "$line" | awk '{print $2}')
            if [ "$pid" = "-" ]; then
                echo "[bot] ✗ 등록됨 but 정지 상태 (last exit: $exit_code)"
            else
                echo "[bot] ✓ 실행 중 (PID: $pid)"
                ps -p "$pid" -o pid,rss,vsz,%cpu,etime,comm 2>/dev/null || true
            fi
        else
            echo "[bot] 등록되지 않음 (./scripts/bot.sh start 로 시작)"
        fi
        ;;

    logs)
        echo "[bot] 트레이딩 로그 실시간 조회 (Ctrl+C 종료)"
        tail -f "$LOG_DIR/trading.log"
        ;;

    errors)
        echo "[bot] launchd 에러 로그"
        tail -n 50 "$LOG_DIR/launchd.err" 2>/dev/null || echo "에러 로그 없음"
        ;;

    *)
        echo "사용법: $0 {start|stop|restart|status|logs|errors}"
        exit 1
        ;;
esac
