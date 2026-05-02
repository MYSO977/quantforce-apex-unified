#!/usr/bin/env python3
import time, requests, os, sys
sys.path.insert(0, '/home/heng/QuantForce_Labs/src/core')
from telegram_notify import send

NODES = {
    'center':   {'ip': '192.168.0.18', 'total': 500},
    'executor': {'ip': '192.168.0.11', 'total': 750},
    'compute':  {'ip': '192.168.0.143','total': 250},
}
API = 'http://192.168.0.18:5800/scanner/status'
TIMEOUT_MIN = 15
CHECK_INTERVAL = 300  # 5分钟检查一次

alerted = set()  # 避免重复告警

while True:
    try:
        r = requests.get(API, timeout=5)
        data = r.json()
        now = time.time()
        for node, info in NODES.items():
            s = data.get(node, {})
            updated = s.get('updated_at')
            if updated:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(updated)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_min = (now - dt.timestamp()) / 60
            else:
                age_min = 999

            if age_min > TIMEOUT_MIN:
                if node not in alerted:
                    send(f"⚠️ <b>Scanner 离线告警</b>\n节点: <b>{node}</b> ({info['ip']})\n最后上报: {updated or '从未'}\n已沉默: {int(age_min)}分钟")
                    alerted.add(node)
            else:
                if node in alerted:
                    send(f"✅ <b>Scanner 恢复</b>\n节点: <b>{node}</b> ({info['ip']})\n已恢复正常上报")
                    alerted.discard(node)
    except Exception as e:
        send(f"⚠️ <b>Watchdog 异常</b>\n无法访问 API: {e}")

    time.sleep(CHECK_INTERVAL)
