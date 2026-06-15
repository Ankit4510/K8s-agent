from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import time

from agent import run_agent
from cluster_store import get_active, get_cluster, list_clusters
from memory import clear_last_action, get_pending_actions, save_pending_actions, set_last_action
from tools import AVAILABLE_TOOLS, kubectl_cmd
from skills import SKILL_REGISTRY
from llm import call_llm, CHAT_SYSTEM_PROMPT
from logger import log_action

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# Module-level cache for online status
_online_cache = {"data": None, "ts": 0}


@app.get("/api/cluster/active")
def active_cluster():
    name = get_active()
    if not name:
        return {"name": None, "online": False}
    info = get_cluster(name)
    now = time.time()
    if now - _online_cache["ts"] > 15:
        raw = kubectl_cmd(["cluster-info", "--request-timeout=3s"], timeout=5)
        _online_cache["data"] = not raw.startswith("Error")
        _online_cache["ts"] = now
    return {
        "name": name,
        "zone": info.get("zone") if info else None,
        "project": info.get("project") if info else None,
        "online": _online_cache["data"],
        "last_verified": info.get("last_used") if info else None,
    }


@app.get("/api/cluster/list")
def list_known():
    return {"clusters": list_clusters()}


@app.get("/")
def root():
    return {"message": "Agent is running"}

@app.get("/chat")
def chat():
    return FileResponse("static/index.html")


@app.post("/message")
async def receive_message(request: Request):
    data = await request.json()
    user_id = data.get("from", {}).get("id", "default")

    # ---- BUTTON ACTION ---- #
    if "value" in data:
        action = data["value"]["action"]
        action_id = data["value"]["action_id"]

        actions = get_pending_actions(user_id)
        pending = next((a for a in actions if a["id"] == action_id), None)

        if not pending:
            return {"type": "message", "text": "No pending action found"}

        if action == "approve":
            # Skill-based approval
            if "skill" in pending:
                result = SKILL_REGISTRY.run(
                    pending["skill"],
                    pending["args"],
                    {"approved": True, "last_namespace": pending["args"].get("namespace")},
                )
                log_action(user_id, f"skill:{pending['skill']}", pending["args"], result.status)
                if pending["skill"] == "restart" and result.status == "success":
                    kind = result.data.get("kind")
                    res_name = result.data.get("name")
                    res_ns = result.data.get("namespace")
                    if kind and res_name:
                        set_last_action(user_id, {
                            "type": "restart",
                            "kind": kind,
                            "name": res_name,
                            "namespace": res_ns or "default",
                        })
                actions = [a for a in actions if a["id"] != action_id]
                save_pending_actions(user_id, actions)
                return {"type": "message", "text": result.message, "data": result.data}

            # Tool-based approval (legacy)
            result = AVAILABLE_TOOLS[pending["tool_name"]](**pending["args"])
            log_action(user_id, pending["tool_name"], pending["args"], result)

            actions = [a for a in actions if a["id"] != action_id]
            save_pending_actions(user_id, actions)

            explain_prompt = f"""
User originally asked: {pending.get("original_input")}

Action executed: {pending["tool_name"]}
Arguments: {pending["args"]}

Result:
{result}

Respond like a DevOps engineer:
- Clearly confirm what action was taken
- Mention the resource name
- If successful → say it's restarted/deleted
- If failed → explain why

Keep it short and professional.
"""

            final_answer = call_llm(explain_prompt, system=CHAT_SYSTEM_PROMPT)

            return {"type": "message", "text": final_answer}

        else:
            action_name = pending.get("skill") or pending.get("tool_name") or "unknown"
            log_action(user_id, action_name, pending["args"], "REJECTED")
            clear_last_action(user_id)
            actions = [a for a in actions if a["id"] != action_id]
            save_pending_actions(user_id, actions)
            return {"type": "message", "text": "❌ Action rejected"}

    # ---- NORMAL MESSAGE ---- #
    return run_agent(user_id, data.get("text", ""))