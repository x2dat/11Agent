import os
import sys
import json
import uuid
import time
import threading
import webbrowser
from datetime import datetime
from typing import Dict, List, Any, Optional

# Third-party imports
import uvicorn
from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
import pystray
from PIL import Image, ImageDraw
# pyrefly: ignore [missing-import]
import webview

# Local imports
import utils
# pyrefly: ignore [missing-import]
from google import genai
# pyrefly: ignore [missing-import]
from google.genai import types

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history.json")
CONVERSATIONS_PATH = os.path.join(BASE_DIR, "conversations.json")

# FastAPI App
app = FastAPI(title="Win11 AI Agent Daemon")

# Enable CORS for local testing if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
config = {
    "api_key": "",
    "gemini_api_key": "",
    "kimi_api_key": "",
    "openrouter_api_key": "",
    "model": "gemini-3.1-flash-lite",
    "guardrail": "MAX",
    "active_gemini_key_id": "",
    "active_kimi_key_id": "",
    "active_openrouter_key_id": ""
}
history_log = []
sessions: Dict[str, Dict[str, Any]] = {}
active_window = None

# Models
class SettingsUpdate(BaseModel):
    active_gemini_key_id: Optional[str] = ""
    active_kimi_key_id: Optional[str] = ""
    active_openrouter_key_id: Optional[str] = ""
    model: str
    guardrail: str

class APIKeyAdd(BaseModel):
    name: str
    provider: str
    key: str

class PromptRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    model: Optional[str] = None

class ConfirmRequest(BaseModel):
    session_id: str
    approve: bool

# Serialization helpers
def content_to_dict(content: types.Content) -> dict:
    parts_list = []
    for part in content.parts:
        part_dict = {}
        if part.text:
            part_dict["text"] = part.text
        elif part.function_call:
            part_dict["function_call"] = {
                "name": part.function_call.name,
                "args": dict(part.function_call.args) if part.function_call.args else {}
            }
        elif part.function_response:
            part_dict["function_response"] = {
                "name": part.function_response.name,
                "response": dict(part.function_response.response) if part.function_response.response else {}
            }
        parts_list.append(part_dict)
    return {
        "role": content.role,
        "parts": parts_list
    }

def dict_to_content(d: dict) -> types.Content:
    parts = []
    for p in d.get("parts", []):
        if "text" in p:
            parts.append(types.Part.from_text(text=p["text"]))
        elif "function_call" in p:
            fc = p["function_call"]
            parts.append(types.Part(
                function_call=types.FunctionCall(
                    name=fc["name"],
                    args=fc["args"]
                )
            ))
        elif "function_response" in p:
            fr = p["function_response"]
            parts.append(types.Part.from_function_response(
                name=fr["name"],
                response=fr["response"]
            ))
    return types.Content(role=d["role"], parts=parts)

def save_conversations():
    try:
        data_to_save = {}
        for session_id, session in sessions.items():
            # Get or build title
            title = session.get("title")
            if not title:
                for msg in session["messages"]:
                    if msg.role == "user" and msg.parts:
                        text_part = [part.text for part in msg.parts if part.text]
                        if text_part:
                            title = text_part[0][:26] + "..." if len(text_part[0]) > 26 else text_part[0]
                            break
                if not title:
                    title = "New Conversation"
                session["title"] = title
                
            timestamp = session.get("timestamp")
            if not timestamp:
                timestamp = datetime.now().isoformat()
                session["timestamp"] = timestamp
                
            data_to_save[session_id] = {
                "title": title,
                "timestamp": timestamp,
                "model": session.get("model", config.get("model")),
                "fallback_mode": session.get("fallback_mode", False),
                "messages": [content_to_dict(msg) for msg in session["messages"]]
            }
        with open(CONVERSATIONS_PATH, "w") as f:
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error saving conversations: {e}")

# Load configuration and history on startup
def load_data():
    global config, history_log, sessions
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                saved_config = json.load(f)
                config.update(saved_config)
        except Exception as e:
            print(f"Error loading config: {e}")
            
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r") as f:
                history_log = json.load(f)
        except Exception as e:
            print(f"Error loading history: {e}")

    if os.path.exists(CONVERSATIONS_PATH):
        try:
            with open(CONVERSATIONS_PATH, "r") as f:
                saved_sessions = json.load(f)
                for session_id, s_data in saved_sessions.items():
                    sessions[session_id] = {
                        "title": s_data.get("title", "Saved Conversation"),
                        "timestamp": s_data.get("timestamp", datetime.now().isoformat()),
                        "model": s_data.get("model", config.get("model")),
                        "fallback_mode": s_data.get("fallback_mode", False),
                        "messages": [dict_to_content(msg) for msg in s_data["messages"]],
                        "pending_command": None,
                        "pending_call_id": None,
                        "pending_call_name": None
                    }
        except Exception as e:
            print(f"Error loading conversations: {e}")
            
    # Migrate legacy API key settings
    if "api_keys" not in config:
        config["api_keys"] = []
        
    legacy_gemini = config.get("gemini_api_key") or config.get("api_key")
    if legacy_gemini and not any(k["provider"] == "gemini" for k in config["api_keys"]):
        config["api_keys"].append({
            "id": "legacy_gemini",
            "name": "Imported Gemini Key",
            "provider": "gemini",
            "key": legacy_gemini
        })
        config["active_gemini_key_id"] = "legacy_gemini"
        
    legacy_kimi = config.get("kimi_api_key")
    if legacy_kimi and not any(k["provider"] == "kimi" for k in config["api_keys"]):
        config["api_keys"].append({
            "id": "legacy_kimi",
            "name": "Imported Kimi Key",
            "provider": "kimi",
            "key": legacy_kimi
        })
        config["active_kimi_key_id"] = "legacy_kimi"

    legacy_or = config.get("openrouter_api_key")
    if legacy_or and not any(k["provider"] == "openrouter" for k in config["api_keys"]):
        config["api_keys"].append({
            "id": "legacy_openrouter",
            "name": "Imported OpenRouter Key",
            "provider": "openrouter",
            "key": legacy_or
        })
        config["active_openrouter_key_id"] = "legacy_openrouter"

# Load initial data on import
load_data()

def get_key_by_id(key_id: str) -> Optional[str]:
    for k in config.get("api_keys", []):
        if k["id"] == key_id:
            return k["key"]
    return None

def save_config():
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

def save_history():
    try:
        with open(HISTORY_PATH, "w") as f:
            json.dump(history_log, f, indent=4)
    except Exception as e:
        print(f"Error saving history: {e}")

def add_to_history(command: str, risk: str, status: str, details: str):
    item = {
        "timestamp": datetime.now().isoformat(),
        "command": command,
        "risk": risk,
        "status": status,
        "details": details
    }
    history_log.insert(0, item)
    if len(history_log) > 100:
        history_log.pop()
    save_history()

# Helper: Create system tray icon image dynamically
def create_tray_icon_image():
    # 64x64 icon
    image = Image.new('RGBA', (64, 64), color=(0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    
    # Windows 11 Blue gradient/color rounded rectangle
    dc.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(0, 120, 215, 255))
    
    # Win11 styled logo (4 squares grid)
    dc.rectangle([16, 16, 28, 28], fill=(255, 255, 255, 255))
    dc.rectangle([32, 16, 44, 28], fill=(255, 255, 255, 255))
    dc.rectangle([16, 32, 28, 44], fill=(255, 255, 255, 255))
    dc.rectangle([32, 32, 44, 44], fill=(255, 255, 255, 255))
    
    return image

# API Routes
@app.get("/api/status")
def get_status():
    model_name = config.get("model") or "gemini-3.1-flash-lite"
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    is_openrouter = "/" in model_name
    
    if is_openrouter:
        active_id = config.get("active_openrouter_key_id")
        provider = "openrouter"
    elif is_kimi:
        active_id = config.get("active_kimi_key_id")
        provider = "kimi"
    else:
        active_id = config.get("active_gemini_key_id")
        provider = "gemini"
        
    has_key = bool(get_key_by_id(active_id))
    
    if not has_key:
        keys_list = config.get("api_keys", [])
        has_key = any(k["provider"] == provider for k in keys_list)
        
    return {
        "api_key_configured": has_key,
        "guardrail": config.get("guardrail"),
        "model": model_name
    }

@app.get("/api/settings")
def get_settings():
    def mask_key(k):
        if not k: return ""
        return k[:6] + "*" * (len(k) - 10) + k[-4:] if len(k) > 10 else "******"
        
    masked_keys = []
    for k in config.get("api_keys", []):
        masked_keys.append({
            "id": k["id"],
            "name": k["name"],
            "provider": k["provider"],
            "key": mask_key(k["key"])
        })
        
    return {
        "api_keys": masked_keys,
        "active_gemini_key_id": config.get("active_gemini_key_id", ""),
        "active_kimi_key_id": config.get("active_kimi_key_id", ""),
        "active_openrouter_key_id": config.get("active_openrouter_key_id", ""),
        "model": config.get("model") or "gemini-3.1-flash-lite",
        "guardrail": config.get("guardrail", "MAX")
    }

@app.post("/api/settings")
def post_settings(settings: SettingsUpdate):
    config["active_gemini_key_id"] = settings.active_gemini_key_id
    config["active_kimi_key_id"] = settings.active_kimi_key_id
    config["active_openrouter_key_id"] = settings.active_openrouter_key_id
    config["model"] = settings.model
    config["guardrail"] = settings.guardrail
    save_config()
    return {"status": "success"}

@app.post("/api/settings/keys")
def add_api_key(item: APIKeyAdd):
    key_val = item.key.strip()
    if not key_val:
        raise HTTPException(status_code=400, detail="API Key value cannot be empty.")
        
    keys_list = config.get("api_keys", [])
    if any(k["name"].lower() == item.name.lower() for k in keys_list):
        raise HTTPException(status_code=400, detail="An API Key with this name already exists.")
        
    key_id = f"key_{uuid.uuid4().hex[:8]}"
    new_key = {
        "id": key_id,
        "name": item.name.strip(),
        "provider": item.provider.strip(),
        "key": key_val
    }
    
    keys_list.append(new_key)
    config["api_keys"] = keys_list
    
    if item.provider == "gemini" and not config.get("active_gemini_key_id"):
        config["active_gemini_key_id"] = key_id
    elif item.provider == "kimi" and not config.get("active_kimi_key_id"):
        config["active_kimi_key_id"] = key_id
    elif item.provider == "openrouter" and not config.get("active_openrouter_key_id"):
        config["active_openrouter_key_id"] = key_id
        
    save_config()
    return {"status": "success"}

@app.delete("/api/settings/keys/{key_id}")
def delete_api_key(key_id: str):
    keys_list = config.get("api_keys", [])
    new_list = [k for k in keys_list if k["id"] != key_id]
    
    if len(new_list) == len(keys_list):
        raise HTTPException(status_code=404, detail="API Key not found.")
        
    config["api_keys"] = new_list
    
    if config.get("active_gemini_key_id") == key_id:
        left_gemini = [k for k in new_list if k["provider"] == "gemini"]
        config["active_gemini_key_id"] = left_gemini[0]["id"] if left_gemini else ""
        
    if config.get("active_kimi_key_id") == key_id:
        left_kimi = [k for k in new_list if k["provider"] == "kimi"]
        config["active_kimi_key_id"] = left_kimi[0]["id"] if left_kimi else ""
        
    if config.get("active_openrouter_key_id") == key_id:
        left_or = [k for k in new_list if k["provider"] == "openrouter"]
        config["active_openrouter_key_id"] = left_or[0]["id"] if left_or else ""
        
    save_config()
    return {"status": "success"}

@app.put("/api/settings/keys/{key_id}")
def update_api_key(key_id: str, item: APIKeyAdd):
    keys_list = config.get("api_keys", [])
    for k in keys_list:
        if k["id"] == key_id:
            k["name"] = item.name.strip()
            k["provider"] = item.provider.strip()
            # If the submitted key value does not contain masked asterisks, update it
            if "*" not in item.key:
                k["key"] = item.key.strip()
            save_config()
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="API Key not found.")

@app.get("/api/history")
def get_history():
    return history_log

@app.get("/api/conversations")
def get_conversations():
    conv_list = []
    for session_id, session in sessions.items():
        conv_list.append({
            "session_id": session_id,
            "title": session.get("title", "Saved Conversation"),
            "timestamp": session.get("timestamp", datetime.now().isoformat())
        })
    # Sort by timestamp descending
    conv_list.sort(key=lambda x: x["timestamp"], reverse=True)
    return conv_list

@app.get("/api/conversations/{session_id}")
def get_conversation_details(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Conversation session not found.")
    
    session = sessions[session_id]
    # Return formatted serializable messages list
    serializable_messages = [content_to_dict(msg) for msg in session["messages"]]
    return {
        "session_id": session_id,
        "title": session.get("title", "Saved Conversation"),
        "model": session.get("model", config.get("model")),
        "messages": serializable_messages
    }

@app.delete("/api/conversations/{session_id}")
def delete_conversation(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
        save_conversations()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Conversation session not found.")

@app.post("/api/conversations/{session_id}/model")
def post_conversation_model(session_id: str, payload: dict = Body(...)):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Conversation session not found.")
    model = payload.get("model")
    if model:
        sessions[session_id]["model"] = model
        save_conversations()
    return {"status": "success"}

@app.post("/api/prompt")
def post_prompt(req: PromptRequest):
    session_id = req.session_id
    
    # Determine which model to use
    if session_id and session_id in sessions:
        session = sessions[session_id]
        if req.model:
            session["model"] = req.model
        model_name = session.get("model") or config.get("model") or "gemini-3.1-flash-lite"
    else:
        model_name = req.model or config.get("model") or "gemini-3.1-flash-lite"
        
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    is_openrouter = "/" in model_name
    
    if is_openrouter:
        active_id = config.get("active_openrouter_key_id")
        api_key = get_key_by_id(active_id)
        if not api_key:
            or_keys = [k for k in config.get("api_keys", []) if k["provider"] == "openrouter"]
            if or_keys:
                api_key = or_keys[0]["key"]
        if not api_key:
            raise HTTPException(status_code=400, detail="No active OpenRouter API Key selected. Please add and select one in Settings.")
    elif is_kimi:
        active_id = config.get("active_kimi_key_id")
        api_key = get_key_by_id(active_id)
        if not api_key:
            kimi_keys = [k for k in config.get("api_keys", []) if k["provider"] == "kimi"]
            if kimi_keys:
                api_key = kimi_keys[0]["key"]
        if not api_key:
            raise HTTPException(status_code=400, detail="No active Kimi API Key selected. Please add and select one in Settings.")
    else:
        active_id = config.get("active_gemini_key_id")
        api_key = get_key_by_id(active_id)
        if not api_key:
            gemini_keys = [k for k in config.get("api_keys", []) if k["provider"] == "gemini"]
            if gemini_keys:
                api_key = gemini_keys[0]["key"]
        if not api_key:
            raise HTTPException(status_code=400, detail="No active Gemini API Key selected. Please add and select one in Settings.")
            
    if session_id and session_id in sessions:
        # Resume existing session
        session = sessions[session_id]
        session["messages"].append(
            types.Content(
                role="user", 
                parts=[types.Part.from_text(text=req.prompt)]
            )
        )
    else:
        # Create a new session
        session_id = str(uuid.uuid4())
        messages = [
            types.Content(
                role="user", 
                parts=[types.Part.from_text(text=req.prompt)]
            )
        ]
        sessions[session_id] = {
            "messages": messages,
            "model": model_name,
            "pending_command": None,
            "pending_call_id": None,
            "pending_call_name": None
        }
        
    save_conversations()
    
    if is_kimi or is_openrouter:
        return process_kimi_turn(session_id)
    else:
        return process_gemini_turn(session_id)

@app.post("/api/confirm")
def post_confirm(req: ConfirmRequest):
    session_id = req.session_id
    if session_id not in sessions:
        raise HTTPException(status_code=400, detail="Session not found or expired.")
        
    session = sessions[session_id]
    command = session.get("pending_command")
    call_id = session.get("pending_call_id")
    call_name = session.get("pending_call_name")
    
    if not command:
        raise HTTPException(status_code=400, detail="No pending command found for this session.")
        
    risk = utils.classify_command(command)
    
    if req.approve:
        # Execute the command
        print(f"Executing approved command: {command}")
        result = utils.run_system_command(command)
        
        status_log = "success" if result["success"] else "failed"
        details_log = f"Exit code {result['returncode']}"
        add_to_history(command, risk, status_log, details_log)
        
        # Prepare tool response
        stdout_output = result["stdout"]
        stderr_output = result["stderr"]
        
        # Combine output
        execution_result = ""
        if stdout_output:
            execution_result += f"Stdout:\n{stdout_output}\n"
        if stderr_output:
            execution_result += f"Stderr:\n{stderr_output}\n"
        if not execution_result:
            execution_result = "Command completed with no output."
            
        tool_response = {"result": execution_result}
    else:
        # User denied execution
        print(f"Command denied: {command}")
        add_to_history(command, risk, "denied", "User refused to execute command")
        tool_response = {"result": "Error: Command denied by user confirmation."}
        
    # Append function response part to history
    # Make sure we reset pending command
    session["pending_command"] = None
    session["pending_call_id"] = None
    session["pending_call_name"] = None
    
    tool_part = types.Part.from_function_response(
        name=call_name,
        response=tool_response
    )
    
    session["messages"].append(
        types.Content(role="tool", parts=[tool_part])
    )
    
    save_conversations()
    
    session = sessions[session_id]
    model_name = session.get("model") or config.get("model") or "gemini-3.1-flash-lite"
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    is_openrouter = "/" in model_name
    if is_kimi or is_openrouter:
        return process_kimi_turn(session_id)
    else:
        return process_gemini_turn(session_id)

def send_kimi_request(api_key: str, model: str, messages: list, disable_tools: bool = False) -> dict:
    import urllib.request
    import urllib.error
    
    is_openrouter = "/" in model
    if is_openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://127.0.0.1:18888",
            "X-Title": "Win11 AI Agent"
        }
    else:
        url = "https://api.moonshot.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "execute_terminal_command",
                "description": "Executes a PowerShell terminal command on the local Windows machine.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute in PowerShell."
                        },
                        "reason": {
                            "type": "string",
                            "description": "A brief explanation of why this command is being run."
                        }
                    },
                    "required": ["command", "reason"]
                }
            }
        }
    ]
    
    payload = {
        "model": model,
        "messages": messages
    }
    if not disable_tools:
        payload["tools"] = openai_tools
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            res_data = response.read().decode("utf-8")
            return json.loads(res_data)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_content)
            err_msg = err_json.get("error", {}).get("message", err_content)
        except Exception:
            err_msg = err_content or str(e)
        provider_name = "OpenRouter" if is_openrouter else "Kimi"
        raise Exception(f"{provider_name} API Error (HTTP {e.code}): {err_msg}")
    except Exception as e:
        provider_name = "OpenRouter" if is_openrouter else "Kimi"
        raise Exception(f"{provider_name} Connection Error: {str(e)}")

def convert_to_openai_messages(messages: List[types.Content], fallback_mode: bool = False) -> List[dict]:
    openai_msgs = []
    pending_ids = []
    
    for msg in messages:
        role = msg.role
        if role == "model":
            role = "assistant"
        
        parts_text = []
        tool_calls = []
        is_tool_response = False
        tool_response_val = ""
        tool_name = ""
        
        for part in msg.parts:
            if part.text:
                parts_text.append(part.text)
            elif part.function_call:
                if fallback_mode:
                    cmd = part.function_call.args.get("command", "")
                    reason = part.function_call.args.get("reason", "")
                    parts_text.append(
                        f"===EXECUTE_COMMAND===\ncommand: {cmd}\nreason: {reason}\n====================="
                    )
                else:
                    call_id = getattr(part.function_call, "id", None) or f"call_{uuid.uuid4().hex[:12]}"
                    pending_ids.append(call_id)
                    tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": part.function_call.name,
                            "arguments": json.dumps(part.function_call.args) if part.function_call.args else "{}"
                        }
                    })
            elif part.function_response:
                is_tool_response = True
                tool_name = part.function_response.name
                resp_obj = part.function_response.response
                if resp_obj and "result" in resp_obj:
                    tool_response_val = str(resp_obj["result"])
                else:
                    tool_response_val = json.dumps(resp_obj) if resp_obj else ""
        
        if is_tool_response:
            if fallback_mode:
                openai_msgs.append({
                    "role": "user",
                    "content": f"===COMMAND_RESULT===\n{tool_response_val}\n===================="
                })
            else:
                call_id = pending_ids.pop(0) if pending_ids else f"call_{uuid.uuid4().hex[:12]}"
                openai_msgs.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": tool_response_val
                })
        else:
            openai_msg = {
                "role": role,
                "content": "\n".join(parts_text)
            }
            if tool_calls and not fallback_mode:
                openai_msg["tool_calls"] = tool_calls
            openai_msgs.append(openai_msg)
            
    return openai_msgs

def process_kimi_turn(session_id: str):
    session = sessions[session_id]
    messages = session["messages"]
    
    model_name = session.get("model") or config.get("model") or "moonshot-v1-8k"
    is_openrouter = "/" in model_name
    
    if is_openrouter:
        active_id = config.get("active_openrouter_key_id")
        api_key = get_key_by_id(active_id)
        if not api_key:
            or_keys = [k for k in config.get("api_keys", []) if k["provider"] == "openrouter"]
            if or_keys:
                api_key = or_keys[0]["key"]
    else:
        active_id = config.get("active_kimi_key_id")
        api_key = get_key_by_id(active_id)
        if not api_key:
            kimi_keys = [k for k in config.get("api_keys", []) if k["provider"] == "kimi"]
            if kimi_keys:
                api_key = kimi_keys[0]["key"]
                
    if not api_key:
        api_key = config.get("api_key")
        
    guardrail_level = config.get("guardrail", "MAX")
    fallback_mode = session.get("fallback_mode", False)
    
    try:
        openai_messages = convert_to_openai_messages(messages, fallback_mode=fallback_mode)
    except Exception as e:
        print(f"Error converting messages: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion error: {str(e)}")
        
    system_instruction = (
        "You are a system automation agent for Windows 11. You help the user manage their computer by "
        "executing PowerShell commands. Always explain what you are planning to do and why in a text response "
        "before invoking the execute_terminal_command tool (this documents your actions for the user). "
        "If a command fails, do not give up; analyze the error and try a second alternative approach "
        "(such as using different cmdlets, checking folders, or installing prerequisites). Do not enter an "
        "infinite loop trying the exact same command. Speak concisely."
    )
    
    if fallback_mode:
        fallback_system_instruction = (
            "You are running in fallback mode because this model does not support native tool use. "
            "If you need to execute a command, write it in your response exactly like this:\n"
            "===EXECUTE_COMMAND===\n"
            "command: <your command here>\n"
            "reason: <your reason here>\n"
            "=====================\n"
            "Do not use markdown backticks around the command or output format."
        )
        system_instruction += "\n" + fallback_system_instruction
        
    formatted_messages = [
        {"role": "system", "content": system_instruction}
    ] + openai_messages
    
    try:
        response_json = send_kimi_request(api_key, model_name, formatted_messages, disable_tools=fallback_mode)
    except Exception as e:
        err_str = str(e)
        if not fallback_mode and ("No endpoints found that support tool use" in err_str or "disable_tools" in err_str or "404" in err_str):
            print("Model does not support native tools. Activating text-based fallback mode...")
            session["fallback_mode"] = True
            save_conversations()
            return process_kimi_turn(session_id)
        else:
            if len(messages) > 0 and messages[-1].role == "user":
                messages.pop()
            provider_name = "OpenRouter" if is_openrouter else "Kimi"
            print(f"{provider_name} API Request Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        
    try:
        if "choices" not in response_json or not response_json["choices"]:
            raise Exception("Invalid API response: 'choices' field is missing or empty.")
            
        choice = response_json["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        
        import re
        parts = []
        if content:
            parts.append(types.Part.from_text(text=content))
            
        # Parse fallback command if tools are disabled/fallback is enabled
        cmd_match = re.search(
            r"===EXECUTE_COMMAND===\s*\n\s*command:\s*(.*?)\n\s*reason:\s*(.*?)(?:\n\s*=====================|\n\s*|$)",
            content,
            re.IGNORECASE | re.DOTALL
        )
        
        if not tool_calls and not cmd_match:
            # Loose match for models that omit "command:" and "reason:" labels
            cmd_match_loose = re.search(
                r"===EXECUTE_COMMAND===\s*\n\s*(.*?)(?:\n\s*reason:\s*(.*?)(?:\n\s*=====================|\n\s*|$)|\n\s*=>\s*(.*?)(?:\n\s*=====================|\n\s*|$)|\n\s*=====================|\n\s*|$)",
                content,
                re.IGNORECASE | re.DOTALL
            )
            if cmd_match_loose:
                command_val = cmd_match_loose.group(1).strip()
                if command_val.lower().startswith("command:"):
                    command_val = command_val[8:].strip()
                
                reason_val = (cmd_match_loose.group(2) or cmd_match_loose.group(3) or "System command execution").strip()
                
                if command_val and "===" not in command_val:
                    class MockMatch:
                        def __init__(self, full_match, cmd, rsn):
                            self._full_match = full_match
                            self._cmd = cmd
                            self._rsn = rsn
                        def group(self, idx):
                            if idx == 0: return self._full_match
                            if idx == 1: return self._cmd
                            if idx == 2: return self._rsn
                    
                    cmd_match = MockMatch(cmd_match_loose.group(0), command_val, reason_val)
        
        if not tool_calls and cmd_match:
            command_val = cmd_match.group(1).strip()
            reason_val = cmd_match.group(2).strip()
            
            # Clean up the Matched execution text block from the content
            clean_content = content.replace(cmd_match.group(0), "").strip()
            parts = []
            if clean_content:
                parts.append(types.Part.from_text(text=clean_content))
            else:
                parts.append(types.Part.from_text(text="Executing command..."))
                
            dummy_call_id = f"call_fallback_{uuid.uuid4().hex[:8]}"
            session["pending_command"] = command_val
            session["pending_call_id"] = dummy_call_id
            session["pending_call_name"] = "execute_terminal_command"
            
            parts.append(types.Part(
                function_call=types.FunctionCall(
                    name="execute_terminal_command",
                    args={"command": command_val, "reason": reason_val}
                )
            ))
            
            # Mock tool_calls
            tool_calls = [{"id": dummy_call_id, "function": {"name": "execute_terminal_command"}}]
            content = clean_content if clean_content else "Executing command..."
            
        if tool_calls:
            # We fetch call args
            if hasattr(parts[-1], 'function_call') and parts[-1].function_call:
                call_args = parts[-1].function_call.args
                func_name = parts[-1].function_call.name
            else:
                call_args = {"command": session["pending_command"], "reason": "System command execution"}
                func_name = "execute_terminal_command"
                
            response_content = types.Content(role="model", parts=parts)
            session["messages"].append(response_content)
            save_conversations()
            
            command = session["pending_command"]
            reason = call_args.get("reason", "")
            risk = utils.classify_command(command)
            
            if utils.should_confirm(command, guardrail_level):
                return {
                    "status": "requires_confirmation",
                    "command": command,
                    "reason": reason,
                    "risk": risk,
                    "session_id": session_id,
                    "thought": content
                }
            else:
                return {
                    "status": "auto_run",
                    "command": command,
                    "reason": reason,
                    "risk": risk,
                    "session_id": session_id,
                    "thought": content
                }
        else:
            response_content = types.Content(role="model", parts=parts)
            session["messages"].append(response_content)
            save_conversations()
            return {
                "status": "completed",
                "response": content,
                "session_id": session_id
            }
            
    except Exception as e:
        if len(messages) > 0 and messages[-1].role == "user":
            messages.pop()
        print(f"Parsing Error: {e}")
        raise HTTPException(status_code=500, detail=f"Error parsing model response: {str(e)}")

def process_gemini_turn(session_id: str):
    session = sessions[session_id]
    messages = session["messages"]
    
    active_id = config.get("active_gemini_key_id")
    api_key = get_key_by_id(active_id)
    if not api_key:
        gemini_keys = [k for k in config.get("api_keys", []) if k["provider"] == "gemini"]
        if gemini_keys:
            api_key = gemini_keys[0]["key"]
    if not api_key:
        api_key = config.get("api_key")
        
    model_name = session.get("model") or config.get("model") or "gemini-3.1-flash-lite"
    guardrail_level = config.get("guardrail", "MAX")
    
    client = genai.Client(api_key=api_key)
    
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=messages,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[
                    types.FunctionDeclaration(
                        name="execute_terminal_command",
                        description="Executes a PowerShell terminal command on the local Windows machine.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "command": types.Schema(
                                    type=types.Type.STRING,
                                    description="The shell command to execute in PowerShell."
                                ),
                                "reason": types.Schema(
                                    type=types.Type.STRING,
                                    description="A brief explanation of why this command is being run."
                                )
                            },
                            required=["command", "reason"]
                        )
                    )
                ])],
                system_instruction="You are a system automation agent for Windows 11. You help the user manage their computer by executing PowerShell commands. Always explain what you are planning to do and why in a text part before invoking the execute_terminal_command tool (this documents your actions for the user). If a command fails, do not give up; analyze the error and try a second alternative approach (such as using different cmdlets, checking folders, or installing prerequisites). Do not enter an infinite loop trying the exact same command. Speak concisely."
            )
        )
        
        # Check if Gemini returned content/parts
        if not response.candidates or not response.candidates[0].content:
            raise Exception("Empty response from Gemini API.")
            
        # Append assistant response to history
        session["messages"].append(response.candidates[0].content)
        save_conversations()
        
        # Extract thought text if present
        thought = ""
        if response.candidates[0].content.parts:
            text_parts = [part.text for part in response.candidates[0].content.parts if part.text]
            if text_parts:
                thought = "\n".join(text_parts).strip()
        
        # Check for function calls
        function_calls = response.function_calls
        if function_calls:
            call = function_calls[0]
            command = call.args.get("command")
            reason = call.args.get("reason", "")
            
            # Save pending call details
            session["pending_command"] = command
            session["pending_call_id"] = getattr(call, "id", None)
            session["pending_call_name"] = call.name
            
            risk = utils.classify_command(command)
            
            # Check guardrails
            if utils.should_confirm(command, guardrail_level):
                # Request UI confirmation
                return {
                    "status": "requires_confirmation",
                    "command": command,
                    "reason": reason,
                    "risk": risk,
                    "session_id": session_id,
                    "thought": thought
                }
            else:
                # Auto-run requested: delegate execution loader display to the frontend
                return {
                    "status": "auto_run",
                    "command": command,
                    "reason": reason,
                    "risk": risk,
                    "session_id": session_id,
                    "thought": thought
                }
        else:
            # Final textual response
            text_response = response.text or "No text response returned."
            return {
                "status": "completed",
                "response": text_response,
                "session_id": session_id
            }
            
    except Exception as e:
        # Remove last message if it caused an error
        if len(messages) > 0 and messages[-1].role == "user":
            messages.pop()
        print(f"Gemini API Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Gemini API Error: {str(e)}")

# Daemon Control routes
@app.post("/api/minimize")
def post_minimize():
    global active_window
    if active_window:
        try:
            active_window.hide()
        except Exception:
            pass
    return {"status": "success"}

@app.post("/api/exit")
def post_exit():
    # Graceful exit
    print("Shutting down daemon...")
    def terminate():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=terminate).start()
    return {"status": "success"}

# Serve static files
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# Server Background Thread
def run_fastapi_server():
    uvicorn.run(app, host="127.0.0.1", port=18888, log_level="warning")

# GUI manager
def open_gui_window():
    global active_window
    try:
        active_window = webview.create_window(
            title="Agent",
            url="http://127.0.0.1:18888",
            width=1000,
            height=700,
            resizable=True,
            text_select=True,
            min_size=(800, 600),
            maximized=True
        )
        webview.start()
    except Exception as e:
        print(f"Webview failed: {e}. Falling back to default web browser.")
        webbrowser.open("http://127.0.0.1:18888")

# Main execution entry
def main():
    # Load configuration
    load_data()
    
    # 1. Start FastAPI server thread
    server_thread = threading.Thread(target=run_fastapi_server, daemon=True)
    server_thread.start()
    time.sleep(1.0) # Wait for server to bind port
    
    # 2. Setup System Tray Icon
    icon = None
    def on_tray_clicked(icon, item):
        action = str(item)
        if action == "Open Console":
            # Reopen webview window or fallback browser
            threading.Thread(target=open_gui_window).start()
        elif action == "Exit":
            icon.stop()
            os._exit(0)
            
    icon = pystray.Icon(
        "win11_ai_agent",
        create_tray_icon_image(),
        title="Win11 AI Agent",
        menu=pystray.Menu(
            pystray.MenuItem("Open Console", on_tray_clicked),
            pystray.MenuItem("Exit", on_tray_clicked)
        )
    )
    
    # Run pystray in a separate thread so main thread runs webview
    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()
    
    # 3. Open UI window (main thread)
    open_gui_window()

if __name__ == "__main__":
    main()
