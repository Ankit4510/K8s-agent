import redis
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# -------- USER SESSION -------- #

def get_user_session(user_id):
    data = r.get(f"session:{user_id}")
    return json.loads(data) if data else []

def save_user_session(user_id, session):
    r.set(f"session:{user_id}", json.dumps(session))


# -------- PENDING ACTIONS -------- #

def get_pending_actions(user_id):
    data = r.get(f"pending:{user_id}")
    return json.loads(data) if data else []

def save_pending_actions(user_id, actions):
    r.set(f"pending:{user_id}", json.dumps(actions))


# -------- LAST ACTION CONTEXT -------- #

def set_last_action(user_id, action_data):
    r.set(f"last_action:{user_id}", json.dumps(action_data))

def get_last_action(user_id):
    data = r.get(f"last_action:{user_id}")
    return json.loads(data) if data else None

def clear_last_action(user_id):
    r.delete(f"last_action:{user_id}")


# -------- PENDING CONFIRMATIONS -------- #

def get_pending_confirmation(user_id):
    data = r.get(f"confirm:{user_id}")
    return json.loads(data) if data else None

def save_pending_confirmation(user_id, confirm):
    r.set(f"confirm:{user_id}", json.dumps(confirm))

def clear_pending_confirmation(user_id):
    r.delete(f"confirm:{user_id}")