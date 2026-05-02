"""
QuantForce Apex — Cluster Watchdog (runs on .101 Sentry)
Monitors all nodes every 60s, Telegram alert on anomaly.
"""
import os, time, socket, subprocess, logging, requests
from datetime import datetime
import psycopg2, pytz

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Watchdog")

TG_TOKEN       = os.getenv("TG_TOKEN",  "7019506529:AAHGd21YXchNiqaJrMYAH7qNE3O7TmNRRB8")
TG_CHAT        = os.getenv("TG_CHAT",   "6318635327")
CHECK_INTERVAL = 60
ALERT_COOLDOWN = 300

PORTS = [
    {"name": "PG@.18",         "host": "192.168.0.18", "port": 5432},
    {"name": "IB_Gateway@.11", "host": "192.168.0.11", "port": 4002},
]

SSH_NODES = [
    {"name": "Brain(.18)",    "host": "192.168.0.18"},
    {"name": "Exec(.11)",     "host": "192.168.0.11"},
    {"name": "Compute(.143)", "host": "192.168.0.143"},
]

PG_CFG = {
    "host": "192.168.0.18", "dbname": "quantforce",
    "user": "heng", "password": "newpassword123"
}

PG_CHECKS = [
    {
        "name": "signals_active",
        "sql": "SELECT COUNT(*) FROM signals_raw WHERE created_at > NOW()-INTERVAL '2 hours'",
        "warn_if": "== 0",
        "msg": "⚠️ 2小时内无信号写入",
        "trading_hours_only": True,
    },
    {
        "name": "account_balance",
        "sql": "SELECT COALESCE(MAX(total_balance),1200) FROM account_state",
        "warn_if": "< 600",
        "msg": "🚨 账户余额低于$600",
    },
]


def send_tg(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=8
        )
    except Exception as e:
        logger.error(f"TG failed: {e}")


def check_port(host, port, timeout=3.0):
    try:
        s = socket.socket()
        s.settimeout(timeout)
        r = s.connect_ex((host, port))
        s.close()
        return r == 0
    except:
        return False


def check_ssh(host, timeout=6.0):
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes",
             f"heng@{host}", "echo ok"],
            capture_output=True, timeout=timeout
        )
        return r.returncode == 0
    except:
        return False


def is_trading_hours():
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    return (now.weekday() < 5 and
            (9, 35) <= (now.hour, now.minute) <= (15, 45))


def check_pg():
    results = []
    try:
        conn = psycopg2.connect(**PG_CFG, connect_timeout=5)
        cur  = conn.cursor()
        for chk in PG_CHECKS:
            if chk.get("trading_hours_only") and not is_trading_hours():
                continue
            try:
                cur.execute(chk["sql"])
                val = float(cur.fetchone()[0] or 0)
                triggered = eval(f"{val} {chk['warn_if']}")
                results.append((chk["name"], not triggered,
                                 chk["msg"] if triggered else ""))
            except Exception as e:
                results.append((chk["name"], False, f"SQL error: {e}"))
        cur.close()
        conn.close()
    except Exception as e:
        results.append(("pg_connect", False, f"🚨 PG连接失败: {e}"))
    return results


class Watchdog:
    def __init__(self):
        self._alerts = {}

    def _should_alert(self, key):
        if time.time() - self._alerts.get(key, 0) > ALERT_COOLDOWN:
            self._alerts[key] = time.time()
            return True
        return False

    def _recover(self, key, name):
        if key in self._alerts:
            del self._alerts[key]
            send_tg(f"✅ <b>恢复正常</b>: {name}")

    def _alert(self, key, msg):
        if self._should_alert(key):
            send_tg(msg)
            logger.warning(f"ALERT: {key}")

    def run_once(self):
        issues = []

        for p in PORTS:
            key = f"port_{p['host']}_{p['port']}"
            if check_port(p["host"], p["port"]):
                self._recover(key, p["name"])
            else:
                issues.append(p["name"])
                self._alert(key,
                    f"🔴 <b>端口不可达</b>\n"
                    f"节点: {p['name']}\n"
                    f"{p['host']}:{p['port']}\n"
                    f"{datetime.now().strftime('%H:%M:%S')}")

        for s in SSH_NODES:
            key = f"ssh_{s['host']}"
            if check_ssh(s["host"]):
                self._recover(key, s["name"])
            else:
                issues.append(s["name"])
                self._alert(key,
                    f"🔴 <b>节点不可达</b>\n"
                    f"{s['name']} ({s['host']})\n"
                    f"{datetime.now().strftime('%H:%M:%S')}")

        for name, ok, msg in check_pg():
            key = f"pg_{name}"
            if ok:
                self._recover(key, name)
            else:
                issues.append(name)
                self._alert(key, f"{msg}\n{datetime.now().strftime('%H:%M:%S')}")

        logger.info(f"Check: issues={issues or 'none'}")
        return issues

    def run(self):
        logger.info("Watchdog started")
        send_tg(
            f"🐕 <b>QuantForce Watchdog 启动</b>\n"
            f"Sentry .101 | 间隔{CHECK_INTERVAL}s | 冷却{ALERT_COOLDOWN}s\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        last_daily = ""
        while True:
            try:
                issues = self.run_once()

                # 每日09:00 ET晨报
                et  = pytz.timezone("America/New_York")
                now = datetime.now(et)
                day = now.strftime("%Y-%m-%d")
                if now.hour == 9 and now.minute == 0 and last_daily != day:
                    last_daily = day
                    send_tg(
                        f"🌅 <b>每日晨报</b> {day}\n"
                        f"{'✅ 集群全部正常' if not issues else '⚠️ ' + str(issues)}"
                    )
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)

            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    Watchdog().run()
