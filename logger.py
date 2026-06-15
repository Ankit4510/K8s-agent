import logging
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler

# ---------------- CONFIG ---------------- #

handler = RotatingFileHandler("agent.log", maxBytes=5*1024*1024, backupCount=3)

logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(message)s"
)

# ---------------- LOGGER ---------------- #

def log_action(user_id, tool, args, result):
    log_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "user": user_id,
        "tool": tool,
        "args": args,
        "result": str(result)[:500]
    }
    logging.info(json.dumps(log_data))


def log_event(skill_name, step, details):
    str_details = {}
    for k, v in details.items():
        str_details[k] = str(v) if not isinstance(v, (str, int, float, bool, list, dict)) else v
    log_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "skill": skill_name,
        "step": step,
        "details": str_details,
    }
    logging.info(json.dumps(log_data))