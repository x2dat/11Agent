import subprocess
import shlex
import re
import os
import sys

# Command Risk Levels
LEVEL_MAX = "MAX"
LEVEL_MEDIUM = "MEDIUM"
LEVEL_LOW = "LOW"
LEVEL_NONE = "NONE"

def classify_command(command: str) -> str:
    """
    Classifies a command into 'readonly', 'modifying', or 'destructive'.
    """
    cmd_clean = command.strip()
    
    # 1. Check for destructive/critical commands (High-risk)
    destructive_patterns = [
        r"\b(rm|del|erase|rmdir|rd|remove-item)\b",
        r"\b(format-volume|format(?!-)|diskpart|bootrec)\b",
        r"\b(shutdown|reboot|restart-computer|stop-computer)\b",
        r"\b(reg\s+delete|reg\s+add)\b",
        r"\b(net\s+user|net\s+localgroup)\b",
        r"\b(sc\s+delete|sc\s+config|sc\s+stop)\b",
        r"\b(taskkill|kill|stop-process)\b",
        r"\b(disable-windownspf|disable-)\b",
    ]
    
    for pattern in destructive_patterns:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return "destructive"
            
    # 2. Check for redirect output/pipes that write files
    # e.g., > file.txt, >> file.txt, Out-File, Set-Content
    modifying_patterns = [
        r"[>]", # Redirection to write files
        r"\b(mkdir|md|new-item|ni)\b",
        r"\b(mv|move|move-item)\b",
        r"\b(cp|copy|copy-item)\b",
        r"\b(ren|rename|rename-item)\b",
        r"\b(git\s+add|git\s+commit|git\s+push|git\s+clone|git\s+checkout|git\s+branch|git\s+merge|git\s+reset|git\s+rebase)\b",
        r"\b(pip|npm|yarn|winget|choco|cargo|go\s+get|nuget|dotnet\s+add|install-module|install-package|install-windowsupdate)\b",
        r"\b(set-content|add-content|out-file|write-output\s+.*>)\b",
    ]
    
    for pattern in modifying_patterns:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return "modifying"
            
    # 3. Read-only commands (default behavior if it doesn't match above, but we can verify)
    readonly_patterns = [
        r"\b(dir|ls|get-childitem|gci)\b",
        r"\b(echo|write-output|write-host)\b",
        r"\b(cat|type|get-content|gc)\b",
        r"\b(pwd|get-location)\b",
        r"\b(where|which|get-command)\b",
        r"\b(git\s+status|git\s+diff|git\s+log|git\s+show)\b",
        r"\b(ipconfig|ping|tracert|nslookup|netstat|get-netipaddress|get-netadapter|get-netipinterface|get-netroute)\b",
        r"\b(hostname|systeminfo|whoami|ver|get-computerinfo)\b",
        r"\b(tasklist|get-process|gps)\b",
        r"\b(sc\s+query|get-service|gsv)\b",
        r"\b(env|set|get-childitem\s+env:)\b",
        r"\b(get-date|get-time|get-culture|get-timezone|get-hotfix)\b",
        r"\b(get-disk|get-volume|get-partition|get-physicaldisk)\b",
        r"\b(get-wulist|get-localuser|get-localgroup)\b",
        r"\b(sort-object|select-object|where-object|group-object|measure-object|out-string|out-host|select|format-table|ft|format-list|fl|format-wide|fw|format-custom)\b",
    ]
    
    for pattern in readonly_patterns:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return "readonly"
            
    # If it is unrecognized, default to modifying to be safe
    return "modifying"

def should_confirm(command: str, level: str) -> bool:
    """
    Determines whether a command requires user confirmation based on the guardrail level.
    """
    level = level.upper()
    if level == LEVEL_MAX:
        return True
        
    cmd_class = classify_command(command)
    
    if level == LEVEL_MEDIUM:
        # Medium requires confirmation for modifying or destructive
        return cmd_class in ("modifying", "destructive")
        
    if level == LEVEL_LOW:
        # Low only requires confirmation for destructive
        return cmd_class == "destructive"
        
    if level == LEVEL_NONE:
        # None requires no confirmation (Warning!)
        return False
        
    return True # Default to safe confirmation

def run_system_command(command: str) -> dict:
    """
    Executes a system command via PowerShell and returns the output.
    """
    try:
        # We run inside PowerShell since the user is on Windows 11
        # Using -NoProfile and -NonInteractive to run in a clean shell
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180 # 3 minute timeout
        )
        
        return {
            "success": process.returncode == 0,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "returncode": process.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Error: Command timed out after 180 seconds.",
            "returncode": -1
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Error executing command: {str(e)}",
            "returncode": -2
        }
