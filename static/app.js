// API Base URL (empty string means relative to current domain)
const API_BASE = "";

// State
let chatSessionId = null;
let currentSessionId = null;
let currentCommand = null;
let currentReason = null;
let currentRisk = null;
let chartCount = 0;

// Initialize on load
document.addEventListener("DOMContentLoaded", () => {
    loadSettings();
    loadHistory();
    checkDaemonStatus();
    loadConversations();
    
    // Auto-scroll input textarea
    const input = document.getElementById("chat-input");
    input.addEventListener("input", function() {
        this.style.height = "auto";
        this.style.height = (this.scrollHeight - 16) + "px";
    });

    // Enter key binds
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
});

// Tab navigation
function switchTab(tabName) {
    document.querySelectorAll(".nav-item").forEach(btn => btn.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(panel => panel.classList.remove("active"));
    
    document.getElementById(`nav-${tabName}`).classList.add("active");
    document.getElementById(`panel-${tabName}`).classList.add("active");
    
    if (tabName === "history") {
        loadHistory();
    }
}

// Show Toast message
function showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    toast.innerText = message;
    
    // Reset classes
    toast.className = "fluent-toast";
    if (type === "error") {
        toast.style.borderLeft = "4px solid var(--danger-color)";
    } else if (type === "success") {
        toast.style.borderLeft = "4px solid var(--success-color)";
    } else {
        toast.style.borderLeft = "4px solid var(--accent-color)";
    }
    
    toast.classList.add("active");
    setTimeout(() => {
        toast.classList.remove("active");
    }, 3000);
}

// Get daemon details
async function checkDaemonStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const statusEl = document.getElementById("connection-status");
        if (res.ok) {
            const data = await res.json();
            if (statusEl) {
                statusEl.className = "status-text online";
                statusEl.innerText = "Linked";
            }
            
            if (!data.api_key_configured) {
                appendSystemMessage("⚠️ Warning: No Gemini API Key configured. Please go to Settings to add one.");
            }
        } else {
            if (statusEl) {
                statusEl.className = "status-text offline";
                statusEl.innerText = "Disconnected";
            }
        }
    } catch (err) {
        const statusEl = document.getElementById("connection-status");
        if (statusEl) {
            statusEl.className = "status-text offline";
            statusEl.innerText = "Disconnected";
        }
    }
}

// Load Settings from Backend
async function loadSettings() {
    try {
        const res = await fetch(`${API_BASE}/api/settings`);
        if (res.ok) {
            const settings = await res.json();
            if (settings.api_key) {
                document.getElementById("settings-api-key").value = settings.api_key;
            }
            if (settings.model) {
                document.getElementById("settings-model").value = settings.model;
            }
            if (settings.guardrail) {
                document.getElementById("settings-guardrail").value = settings.guardrail;
            }
        }
    } catch (err) {
        console.error("Failed to load settings:", err);
    }
}

// Save Settings to Backend
async function saveSettings() {
    const api_key = document.getElementById("settings-api-key").value.trim();
    const model = document.getElementById("settings-model").value;
    const guardrail = document.getElementById("settings-guardrail").value;
    
    try {
        const res = await fetch(`${API_BASE}/api/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key, model, guardrail })
        });
        
        if (res.ok) {
            showToast("Settings saved successfully!", "success");
            checkDaemonStatus();
        } else {
            showToast("Failed to save settings.", "error");
        }
    } catch (err) {
        showToast("Error communicating with daemon.", "error");
    }
}

// Load history log
async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE}/api/history`);
        if (res.ok) {
            const history = await res.json();
            const tbody = document.getElementById("history-table-body");
            tbody.innerHTML = "";
            
            if (history.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="empty-table">No commands executed in this session yet.</td></tr>`;
                return;
            }
            
            history.forEach(item => {
                const tr = document.createElement("tr");
                const formattedTime = new Date(item.timestamp).toLocaleTimeString();
                
                let riskClass = "readonly";
                if (item.risk === "destructive") riskClass = "high";
                else if (item.risk === "modifying") riskClass = "medium";
                else if (item.risk === "readonly") riskClass = "readonly";
                
                let statusClass = "success";
                if (item.status === "failed") statusClass = "failed";
                else if (item.status === "denied") statusClass = "failed";
                else if (item.status === "pending") statusClass = "pending";
                
                tr.innerHTML = `
                    <td>${formattedTime}</td>
                    <td><code class="mono-code" style="font-family: var(--font-mono);">${escapeHtml(item.command)}</code></td>
                    <td><span class="risk-badge ${riskClass}">${item.risk.toUpperCase()}</span></td>
                    <td><span class="status-badge ${statusClass}">${item.status.toUpperCase()}</span></td>
                    <td>${escapeHtml(item.details || "")}</td>
                `;
                tbody.appendChild(tr);
            });
        }
    } catch (err) {
        console.error("Failed to load history:", err);
    }
}

// Messages helpers
function appendUserMessage(text) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "message user-message";
    div.innerHTML = `<div class="message-content">${escapeHtml(text)}</div>`;
    container.appendChild(div);
    scrollToBottom();
}

function appendAgentMessage(text) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "message agent-message";
    
    // Format code blocks in markdown simply
    const formatted = formatMarkdown(text);
    div.innerHTML = `<div class="message-content">${formatted}</div>`;
    container.appendChild(div);
    renderPendingCharts();
    scrollToBottom();
}

function appendSystemMessage(text) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "message system-message";
    div.innerHTML = `<div class="message-content">${text}</div>`;
    container.appendChild(div);
    scrollToBottom();
}

function scrollToBottom() {
    const container = document.getElementById("chat-messages");
    container.scrollTop = container.scrollHeight;
}

// Send Message Flow
async function sendMessage() {
    const input = document.getElementById("chat-input");
    const prompt = input.value.trim();
    if (!prompt) return;
    
    input.value = "";
    input.style.height = "auto";
    appendUserMessage(prompt);
    
    // Show loading system message
    const loader = showSystemLoading();
    
    try {
        const body = { prompt };
        if (chatSessionId) {
            body.session_id = chatSessionId;
        }
        
        const res = await fetch(`${API_BASE}/api/prompt`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        
        removeSystemLoading(loader);
        
        if (!res.ok) {
            const errData = await res.json();
            appendSystemMessage(`❌ Error: ${errData.detail || "Failed to process prompt."}`);
            return;
        }
        
        const data = await res.json();
        handleApiResponse(data);
    } catch (err) {
        removeSystemLoading(loader);
        appendSystemMessage("❌ Error: Unable to connect to the AI Agent daemon.");
    }
}

// Loading indicators
function showSystemLoading() {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "message system-message loading-indicator";
    div.innerHTML = `<div class="message-content">Thinking... <span class="spinner"></span></div>`;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function showExecutionLoading(command, reason) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "message system-message loading-indicator";
    div.innerHTML = `
        <div class="message-content">
            <div style="display: flex; align-items: center; gap: 10px;">
                <svg class="spin-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" stroke-dasharray="30 10"/>
                </svg>
                <strong>Executing system command...</strong>
            </div>
            ${reason ? `<div style="margin-top: 6px; font-size: 0.85rem; color: var(--text-secondary);"><strong>Action:</strong> ${escapeHtml(reason)}</div>` : ''}
            <pre style="margin-top: 8px; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.05);"><code style="font-family: var(--font-mono); font-size: 0.85rem;">${escapeHtml(command)}</code></pre>
            <div class="progress-bar-container">
                <div class="progress-bar-indeterminate"></div>
            </div>
        </div>
    `;
    container.appendChild(div);
    scrollToBottom();
    return div;
}

function removeSystemLoading(element) {
    if (element && element.parentNode) {
        element.parentNode.removeChild(element);
    }
}

// API Response Manager
function handleApiResponse(data) {
    if (data.session_id) {
        chatSessionId = data.session_id;
        loadConversations();
    }
    
    if (data.status === "completed") {
        appendAgentMessage(data.response);
    } else if (data.status === "requires_confirmation") {
        currentSessionId = data.session_id;
        currentCommand = data.command;
        currentReason = data.reason;
        currentRisk = data.risk;
        
        if (data.thought) {
            appendAgentMessage(data.thought);
        }
        
        showGuardrailModal(data.command, data.reason, data.risk);
    } else if (data.status === "auto_run") {
        currentSessionId = data.session_id;
        currentCommand = data.command;
        currentReason = data.reason;
        currentRisk = data.risk;
        
        if (data.thought) {
            appendAgentMessage(data.thought);
        }
        
        // Show execution loader immediately, then execute in background without confirmation prompt
        const loader = showExecutionLoading(data.command, data.reason);
        autoRunCommand(data.session_id, loader);
    }
}

async function autoRunCommand(sessionId, loader) {
    try {
        const res = await fetch(`${API_BASE}/api/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: sessionId,
                approve: true
            })
        });
        
        removeSystemLoading(loader);
        
        if (!res.ok) {
            appendSystemMessage("❌ Error processing automatic command execution.");
            return;
        }
        
        const data = await res.json();
        handleApiResponse(data);
    } catch (err) {
        removeSystemLoading(loader);
        appendSystemMessage("❌ Network error during automatic command execution.");
    }
}

// Guardrail Dialog Controller
function showGuardrailModal(command, reason, risk) {
    document.getElementById("modal-command-text").innerText = command;
    document.getElementById("modal-command-reason").innerText = reason || "Execution of system command requested by AI.";
    
    const riskBadge = document.getElementById("modal-risk-level");
    riskBadge.innerText = `Risk Level: ${risk}`;
    
    // Style risk warning
    riskBadge.className = "dialog-subtitle";
    if (risk === "destructive") {
        riskBadge.style.color = "var(--danger-color)";
    } else if (risk === "modifying") {
        riskBadge.style.color = "var(--warning-color)";
    } else {
        riskBadge.style.color = "var(--accent-color)";
    }
    
    document.getElementById("guardrail-modal").classList.add("active");
    
    // Add keybinds to modal
    const handleKey = (e) => {
        if (e.key === "y" || e.key === "Y") {
            respondGuardrail(true);
            document.removeEventListener("keydown", handleKey);
        } else if (e.key === "n" || e.key === "N") {
            respondGuardrail(false);
            document.removeEventListener("keydown", handleKey);
        }
    };
    document.addEventListener("keydown", handleKey);
}

// Confirm/Deny callback
async function respondGuardrail(approved) {
    document.getElementById("guardrail-modal").classList.remove("active");
    
    appendSystemMessage(approved ? `✔️ Command approved.` : `❌ Command execution denied.`);
    const loader = approved ? showExecutionLoading(currentCommand, currentReason) : showSystemLoading();
    
    try {
        const res = await fetch(`${API_BASE}/api/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: currentSessionId,
                approve: approved
            })
        });
        
        removeSystemLoading(loader);
        
        if (!res.ok) {
            appendSystemMessage("❌ Error processing confirmation.");
            return;
        }
        
        const data = await res.json();
        handleApiResponse(data);
    } catch (err) {
        removeSystemLoading(loader);
        appendSystemMessage("❌ Network error while executing command.");
    }
}

// Daemon control actions
async function minimizeToTray() {
    try {
        const res = await fetch(`${API_BASE}/api/minimize`, { method: "POST" });
        if (res.ok) {
            showToast("Application minimized to tray.", "info");
        }
    } catch (err) {
        console.error(err);
    }
}

async function exitDaemon() {
    try {
        await fetch(`${API_BASE}/api/exit`, { method: "POST" });
        showToast("Daemon shut down.", "error");
        setTimeout(() => {
            window.close();
        }, 1000);
    } catch (err) {
        console.error(err);
    }
}

// Utilities
function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

let chartsToRender = [];

function formatMarkdown(text) {
    if (!text) return "";
    
    // 1. Extract chart blocks to prevent escaping issues
    const chartBlocks = [];
    let processedText = text.replace(/```chart\s*\n([\s\S]*?)\n```/g, (match, jsonStr) => {
        const placeholder = `__CHART_PLACEHOLDER_${chartBlocks.length}__`;
        chartBlocks.push(jsonStr.trim());
        return placeholder;
    });
    
    // 2. Escape HTML and format standard markdown
    let html = escapeHtml(processedText);
    
    // Fenced Code blocks
    html = html.replace(/```(.*?)\n([\s\S]*?)```/g, (match, lang, code) => {
        return `<pre><code class="language-${lang.trim() || 'text'}">${code}</code></pre>`;
    });
    
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // Bullet points
    html = html.replace(/^\s*[-*]\s+(.*)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
    
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    
    // 3. Render tables: detect markdown tables and convert them to styled tables
    const tableRegex = /\|(.+)\|[\r\n]+\|([-| :]+)\|[\r\n]+((?:\|.+|[\r\n]+)*)/g;
    html = html.replace(tableRegex, (match, headerRow, separatorRow, bodyRows) => {
        const headers = headerRow.split('|').map(h => h.trim()).filter(h => h);
        const headerHtml = `<tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr>`;
        
        const rows = bodyRows.trim().split('\n').filter(r => r.trim());
        const bodyHtml = rows.map(row => {
            const cells = row.split('|').map(c => c.trim()).filter(c => c);
            return `<tr>${cells.map(c => `<td>${c}</td>`).join('')}</tr>`;
        }).join('');
        
        return `<div class="markdown-table-container"><table><thead>${headerHtml}</thead><tbody>${bodyHtml}</tbody></table></div>`;
    });
    
    // 4. Put back the chart canvases and register them
    chartBlocks.forEach((jsonStr, idx) => {
        const placeholder = `__CHART_PLACEHOLDER_${idx}__`;
        try {
            const config = JSON.parse(jsonStr);
            const chartId = `fluent-chart-${++chartCount}`;
            
            // Queue this chart to render after DOM update
            chartsToRender.push({ id: chartId, config });
            
            const canvasHtml = `<div class="chart-wrapper" style="position: relative; height:250px;"><canvas id="${chartId}"></canvas></div>`;
            html = html.replace(placeholder, canvasHtml);
        } catch (e) {
            console.error("Failed to parse chart JSON:", e);
            const fallbackCode = `<pre><code class="language-json">${escapeHtml(jsonStr)}</code></pre>`;
            html = html.replace(placeholder, fallbackCode);
        }
    });
    
    return html;
}

function renderPendingCharts() {
    chartsToRender.forEach(item => {
        const canvas = document.getElementById(item.id);
        if (canvas) {
            try {
                const isLight = document.documentElement.getAttribute("data-theme") === "light";
                const gridColor = isLight ? "rgba(0, 0, 0, 0.05)" : "rgba(255, 255, 255, 0.05)";
                const textColor = isLight ? "#1c1c1e" : "#f5f5f5";
                
                const config = item.config;
                
                // Set default colors if not provided
                if (config.data && config.data.datasets) {
                    config.data.datasets.forEach(dataset => {
                        if (!dataset.backgroundColor) {
                            dataset.backgroundColor = "rgba(96, 205, 255, 0.75)";
                            dataset.borderColor = "#60cdff";
                            dataset.borderWidth = 1.5;
                        }
                    });
                } else if (config.datasets) {
                    config.data = {
                        labels: config.labels,
                        datasets: config.datasets
                    };
                    delete config.labels;
                    delete config.datasets;
                    
                    config.data.datasets.forEach(dataset => {
                        if (!dataset.backgroundColor) {
                            dataset.backgroundColor = "rgba(96, 205, 255, 0.75)";
                            dataset.borderColor = "#60cdff";
                            dataset.borderWidth = 1.5;
                        }
                    });
                }
                
                config.options = config.options || {};
                config.options.responsive = true;
                config.options.maintainAspectRatio = false;
                config.options.plugins = config.options.plugins || {};
                config.options.plugins.legend = config.options.plugins.legend || {};
                config.options.plugins.legend.labels = config.options.plugins.legend.labels || {};
                config.options.plugins.legend.labels.color = textColor;
                config.options.plugins.legend.labels.font = { family: "Outfit" };
                
                if (config.type !== 'pie' && config.type !== 'doughnut') {
                    config.options.scales = config.options.scales || {};
                    config.options.scales.x = config.options.scales.x || {};
                    config.options.scales.x.grid = { color: gridColor };
                    config.options.scales.x.ticks = { color: textColor, font: { family: "Outfit" } };
                    
                    config.options.scales.y = config.options.scales.y || {};
                    config.options.scales.y.grid = { color: gridColor };
                    config.options.scales.y.ticks = { color: textColor, font: { family: "Outfit" } };
                }
                
                new Chart(canvas.getContext('2d'), config);
            } catch (e) {
                console.error("Failed to render chart:", e);
            }
        }
    });
    chartsToRender = [];
}

async function loadConversations() {
    try {
        const res = await fetch(`${API_BASE}/api/conversations`);
        if (res.ok) {
            const list = await res.json();
            const container = document.getElementById("recent-chats-list");
            container.innerHTML = "";
            
            if (list.length === 0) {
                container.innerHTML = `<div style="padding: 10px 14px; font-size: 0.8rem; color: var(--text-muted);">No recent chats</div>`;
                return;
            }
            
            list.forEach(chat => {
                const item = document.createElement("button");
                item.className = "recent-chat-item";
                if (chat.session_id === chatSessionId) {
                    item.classList.add("active");
                }
                item.onclick = () => loadActiveChat(chat.session_id);
                
                item.innerHTML = `
                    <div class="recent-chat-title" title="${escapeHtml(chat.title)}">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: middle; display: inline-block;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        <span>${escapeHtml(chat.title)}</span>
                    </div>
                    <button class="delete-chat-btn" onclick="event.stopPropagation(); deleteChat('${chat.session_id}')">
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                `;
                container.appendChild(item);
            });
        }
    } catch (err) {
        console.error("Failed to load conversations:", err);
    }
}

async function loadActiveChat(sessionId) {
    try {
        const res = await fetch(`${API_BASE}/api/conversations/${sessionId}`);
        if (res.ok) {
            const chat = await res.json();
            chatSessionId = chat.session_id;
            
            // Clear messages container
            const container = document.getElementById("chat-messages");
            container.innerHTML = "";
            
            if (chat.messages.length === 0) {
                startNewChat();
                return;
            }
            
            chat.messages.forEach(msg => {
                const textPart = msg.parts.find(p => p.text);
                if (textPart) {
                    if (msg.role === "user") {
                        appendUserMessage(textPart.text);
                    } else if (msg.role === "model") {
                        appendAgentMessage(textPart.text);
                    }
                }
            });
            
            // Set active class in list
            document.querySelectorAll(".recent-chat-item").forEach(item => {
                item.classList.remove("active");
            });
            loadConversations();
            showToast("Conversation loaded.", "success");
        }
    } catch (err) {
        showToast("Failed to load conversation.", "error");
    }
}

async function deleteChat(sessionId) {
    if (confirm("Are you sure you want to delete this conversation?")) {
        try {
            const res = await fetch(`${API_BASE}/api/conversations/${sessionId}`, { method: "DELETE" });
            if (res.ok) {
                showToast("Conversation deleted.", "success");
                if (chatSessionId === sessionId) {
                    startNewChat();
                } else {
                    loadConversations();
                }
            }
        } catch (err) {
            showToast("Failed to delete conversation.", "error");
        }
    }
}

function startNewChat() {
    chatSessionId = null;
    document.getElementById("chat-messages").innerHTML = `
        <div class="message system-message">
            <div class="message-content">
                <strong>Hello! I am your Windows 11 AI System Agent.</strong><br>
                I can execute terminal commands to help automate tasks. Please configure your Gemini API Key in the settings page to get started. Try prompts like:
                <ul>
                    <li>"Check my disk usage details"</li>
                    <li>"Show my current IP configuration"</li>
                    <li>"Create a folder named ProjectBackup on my desktop"</li>
                </ul>
            </div>
        </div>
    `;
    document.querySelectorAll(".recent-chat-item").forEach(item => {
        item.classList.remove("active");
    });
    loadConversations();
    showToast("New chat session started.", "success");
}
