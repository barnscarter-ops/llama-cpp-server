"""
Slack ↔ llama-server bridge (Socket Mode)
==========================================
Forwards Slack DMs and @mentions to your local llama-server and replies
in-thread. Uses Socket Mode — no public URL or port forwarding needed.

Required env vars (set in .env or shell before running):
  SLACK_BOT_TOKEN   xoxb-... (Bot User OAuth Token)
  SLACK_APP_TOKEN   xapp-... (App-Level Token with connections:write scope)
  LLAMA_BASE_URL    http://127.0.0.1:8080  (default)
  LLAMA_MODEL       qwen3.6-35b-a3b        (must match --alias in your start script)
  SYSTEM_PROMPT     (optional override)

Usage:
  pip install -r slack_bridge_requirements.txt
  python slack_agent_bridge.py
"""

import os
import re
import json
import logging
import threading
import requests
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
APP_TOKEN   = os.environ["SLACK_APP_TOKEN"]
LLAMA_URL   = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "qwen3.6-35b-a3b")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY_TURNS", "20"))  # messages kept per thread

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful local AI assistant. Be concise and direct. "
    "You are being accessed via Slack from a mobile device, so keep responses readable on a phone."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("slack-bridge")

# ── Conversation history store ─────────────────────────────────────────────────
# Keyed by (channel_id, thread_ts or None-for-DM).
# Thread-safe: each Slack event is dispatched in its own thread by Bolt.

_history: dict[str, list[dict]] = {}
_lock = threading.Lock()

def _history_key(channel: str, thread_ts: Optional[str]) -> str:
    return f"{channel}:{thread_ts or 'dm'}"

def get_history(channel: str, thread_ts: Optional[str]) -> list[dict]:
    key = _history_key(channel, thread_ts)
    with _lock:
        return list(_history.get(key, []))

def append_history(channel: str, thread_ts: Optional[str], role: str, content: str):
    key = _history_key(channel, thread_ts)
    with _lock:
        hist = _history.setdefault(key, [])
        hist.append({"role": role, "content": content})
        # Trim to max turns (pairs), keep system context tight
        if len(hist) > MAX_HISTORY * 2:
            _history[key] = hist[-(MAX_HISTORY * 2):]

def clear_history(channel: str, thread_ts: Optional[str]):
    key = _history_key(channel, thread_ts)
    with _lock:
        _history.pop(key, None)

# ── llama-server call ──────────────────────────────────────────────────────────

def call_llama(messages: list[dict]) -> str:
    payload = {
        "model": LLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": False,
    }
    try:
        resp = requests.post(
            f"{LLAMA_URL}/v1/chat/completions",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.ConnectionError:
        return "⚠️ llama-server is not running. Start it with your PowerShell script and try again."
    except requests.exceptions.Timeout:
        return "⚠️ llama-server timed out (>120s). The model may be swapping or the prompt is too long."
    except Exception as e:
        log.exception("llama-server error")
        return f"⚠️ Error calling llama-server: {e}"

# ── Strip bot mention from text ────────────────────────────────────────────────

def clean_text(text: str, bot_user_id: str) -> str:
    """Remove @BotMention prefix Slack prepends to messages in channels."""
    return re.sub(rf"<@{bot_user_id}>\s*", "", text).strip()

# ── Core handler ───────────────────────────────────────────────────────────────

def handle_message(text: str, channel: str, thread_ts: Optional[str],
                   say, client, bot_user_id: str):
    text = text.strip()
    if not text:
        return

    # Special commands
    if text.lower() in ("/clear", "!clear", "/reset", "!reset"):
        clear_history(channel, thread_ts)
        say(text="🗑️ Conversation history cleared.", thread_ts=thread_ts)
        return

    if text.lower() in ("/help", "!help"):
        say(
            text=(
                "*Slash commands:*\n"
                "• `/clear` or `!clear` — wipe this thread's history\n"
                "• `/help` — this message\n\n"
                "Just send any message to chat. History is kept per thread."
            ),
            thread_ts=thread_ts,
        )
        return

    # Post a "thinking" placeholder
    placeholder = client.chat_postMessage(
        channel=channel,
        text="_thinking…_",
        thread_ts=thread_ts,
    )
    placeholder_ts = placeholder["ts"]

    # Build and send
    append_history(channel, thread_ts, "user", text)
    history = get_history(channel, thread_ts)
    reply = call_llama(history)
    append_history(channel, thread_ts, "assistant", reply)

    # Update placeholder with real reply
    client.chat_update(
        channel=channel,
        ts=placeholder_ts,
        text=reply,
    )
    log.info("Replied to %s (thread=%s) len=%d", channel, thread_ts, len(reply))

# ── Bolt app ───────────────────────────────────────────────────────────────────

app = App(token=BOT_TOKEN)

@app.event("app_mention")
def on_mention(event, say, client):
    bot_user_id = client.auth_test()["user_id"]
    text = clean_text(event.get("text", ""), bot_user_id)
    channel = event["channel"]
    # In channels, reply in-thread
    thread_ts = event.get("thread_ts") or event["ts"]
    handle_message(text, channel, thread_ts, say, client, bot_user_id)

@app.event("message")
def on_dm(event, say, client):
    # Only handle DMs (channel_type == "im") to avoid double-processing channel msgs
    if event.get("channel_type") != "im":
        return
    if event.get("subtype"):  # ignore bot messages, edits, etc.
        return

    bot_user_id = client.auth_test()["user_id"]
    text = clean_text(event.get("text", ""), bot_user_id)
    channel = event["channel"]
    # DMs: thread_ts = None so history is per-DM-channel (one convo per person)
    handle_message(text, channel, None, say, client, bot_user_id)

# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Connecting to Slack via Socket Mode...")
    log.info("llama-server: %s  model: %s", LLAMA_URL, LLAMA_MODEL)
    handler = SocketModeHandler(app, APP_TOKEN)
    handler.start()
