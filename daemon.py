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
    "model": "gemini-3.1-flash-lite",
    "guardrail": "MAX"
}
history_log = []
sessions: Dict[str, Dict[str, Any]] = {}
active_window = None

# Models
class SettingsUpdate(BaseModel):
    api_key: Optional[str] = ""
    gemini_api_key: Optional[str] = ""
    kimi_api_key: Optional[str] = ""
    model: str
    guardrail: str

class PromptRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None

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
                config.update(json.load(f))
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
                        "messages": [dict_to_content(msg) for msg in s_data["messages"]],
                        "pending_command": None,
                        "pending_call_id": None,
                        "pending_call_name": None
                    }
        except Exception as e:
            print(f"Error loading conversations: {e}")

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
    # Keep only last 100 history items
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
    model_name = config.get("model", "gemini-3.1-flash-lite")
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    has_key = bool(config.get("kimi_api_key" if is_kimi else "gemini_api_key") or config.get("api_key"))
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
        
    return {
        "api_key": mask_key(config.get("api_key")),
        "gemini_api_key": mask_key(config.get("gemini_api_key") or config.get("api_key")),
        "kimi_api_key": mask_key(config.get("kimi_api_key")),
        "model": config.get("model"),
        "guardrail": config.get("guardrail")
    }

@app.post("/api/settings")
def post_settings(settings: SettingsUpdate):
    # Update Gemini key
    new_gemini = settings.gemini_api_key.strip() if settings.gemini_api_key else ""
    if new_gemini:
        if "*" not in new_gemini:
            config["gemini_api_key"] = new_gemini
            config["api_key"] = new_gemini
    else:
        new_legacy = settings.api_key.strip() if settings.api_key else ""
        if new_legacy and "*" not in new_legacy:
            config["gemini_api_key"] = new_legacy
            config["api_key"] = new_legacy
            
    # Update Kimi key
    new_kimi = settings.kimi_api_key.strip() if settings.kimi_api_key else ""
    if new_kimi and "*" not in new_kimi:
        config["kimi_api_key"] = new_kimi
        
    config["model"] = settings.model
    config["guardrail"] = settings.guardrail
    save_config()
    return {"status": "success"}

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
        "messages": serializable_messages
    }

@app.delete("/api/conversations/{session_id}")
def delete_conversation(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
        save_conversations()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Conversation session not found.")

@app.post("/api/prompt")
def post_prompt(req: PromptRequest):
    model_name = config.get("model", "gemini-3.1-flash-lite")
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    
    if is_kimi:
        api_key = config.get("kimi_api_key") or config.get("api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="Kimi API Key is not configured. Please add one in Settings.")
    else:
        api_key = config.get("gemini_api_key") or config.get("api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="Gemini API Key is not configured. Please add one in Settings.")
            
    session_id = req.session_id
    
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
            "pending_command": None,
            "pending_call_id": None,
            "pending_call_name": None
        }
        
    save_conversations()
    
    if is_kimi:
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
    
    model_name = config.get("model", "gemini-3.1-flash-lite")
    is_kimi = model_name.startswith("kimi-") or model_name.startswith("moonshot-")
    if is_kimi:
        return process_kimi_turn(session_id)
    else:
        return process_gemini_turn(session_id)

def send_kimi_request(api_key: str, model: str, messages: list) -> dict:
    import urllib.request
    import urllib.error
    
    url = "https://api.moonshot.cn/v1/chat/completions"
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
        "messages": messages,
        "tools": openai_tools
    }
    
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
        raise Exception(f"Kimi API Error (HTTP {e.code}): {err_msg}")
    except Exception as e:
        raise Exception(f"Kimi Connection Error: {str(e)}")

def convert_to_openai_messages(messages: List[types.Content]) -> List[dict]:
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
            if tool_calls:
                openai_msg["tool_calls"] = tool_calls
            openai_msgs.append(openai_msg)
            
    return openai_msgs

def process_kimi_turn(session_id: str):
    session = sessions[session_id]
    messages = session["messages"]
    api_key = config.get("kimi_api_key") or config.get("api_key")
    model_name = config.get("model", "moonshot-v1-8k")
    guardrail_level = config.get("guardrail", "MAX")
    
    try:
        openai_messages = convert_to_openai_messages(messages)
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
    
    formatted_messages = [
        {"role": "system", "content": system_instruction}
    ] + openai_messages
    
    try:
        response_json = send_kimi_request(api_key, model_name, formatted_messages)
    except Exception as e:
        if len(messages) > 0 and messages[-1].role == "user":
            messages.pop()
        print(f"Kimi API Request Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    try:
        if "choices" not in response_json or not response_json["choices"]:
            raise Exception("Invalid API response: 'choices' field is missing or empty.")
            
        choice = response_json["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        
        parts = []
        if content:
            parts.append(types.Part.from_text(text=content))
            
        if tool_calls:
            call = tool_calls[0]
            func = call.get("function", {})
            func_name = func.get("name")
            func_args_str = func.get("arguments", "{}")
            try:
                func_args = json.loads(func_args_str)
            except Exception:
                func_args = {}
                
            session["pending_command"] = func_args.get("command")
            session["pending_call_id"] = call.get("id")
            session["pending_call_name"] = func_name
            
            parts.append(types.Part(
                function_call=types.FunctionCall(
                    name=func_name,
                    args=func_args
                )
            ))
            
        response_content = types.Content(role="model", parts=parts)
        session["messages"].append(response_content)
        save_conversations()
        
        if tool_calls:
            command = session["pending_command"]
            reason = func_args.get("reason", "")
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
            return {
                "status": "completed",
                "response": content,
                "session_id": session_id
            }
            
    except Exception as e:
        if len(messages) > 0 and messages[-1].role == "user":
            messages.pop()
        print(f"Kimi Parsing Error: {e}")
        raise HTTPException(status_code=500, detail=f"Error parsing Kimi response: {str(e)}")

def process_gemini_turn(session_id: str):
    session = sessions[session_id]
    messages = session["messages"]
    api_key = config.get("api_key")
    model_name = config.get("model", "gemini-3.1-flash-lite")
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
