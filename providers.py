"""
Claude subscription backend for snip2md.

AuthN: the same Claude Code login already on this PC (`claude auth login
--claudeai`). No API keys, no paste, no Console billing. We pop
ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN for the request so a leftover
Console key cannot silently take over.

Agency: tools=[], skills=[], setting_sources=[], max_turns=1, empty temp cwd.
Screenshot stays in memory and is size-capped.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROMPT = (
    "Transcribe this Danish UI screenshot into GitHub-flavored Markdown "
    "for a coding agent. Copy labels, values, buttons, links, and table "
    "cells exactly (keep å/æ/ø). ATX headings only for on-screen titles. "
    "Label/value rows become one GFM table (label | value). "
    "Checklist or timeline steps become a bullet list. "
    "Metric cards: one GFM table, value then caption, no HTML <br>. "
    "Charts: for each series, one short line `Name: value` (include 0). "
    "Copy chart captions and footnotes exactly. Never write 'Plotted values', "
    "and never dump axis ticks (0, 25, 50, 75, 100) or grid numbers. "
    "Skip icons and colors. No invented headings. "
    "Output only the Markdown body."
)

# Fast vision models, newest first. Never use composer-2.5-fast — several
# accounts list it as default but reject it on the SDK.
CURSOR_FAST_MODELS = (
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "claude-haiku-4-5",
    "composer-2.5",
)
_BLOCKED_CURSOR_MODELS = frozenset({"default", "composer-2.5-fast"})
_cursor_model_id: str | None = None

CLAUDE_SYSTEM = (
    "You transcribe screenshots into GitHub-flavored Markdown. "
    "Do not use tools. Do not explain. Output only the Markdown body."
)

CLAUDE_SETUP = "https://code.claude.com/docs/en/setup"
ROOT = Path(__file__).resolve().parent
CURSOR_SDK_AUTH = Path.home() / ".cursor" / "sdk" / "auth.json"
SETTINGS_PATH = Path.home() / ".snip2md" / "settings.json"
WORKSPACE_PATH = SETTINGS_PATH.parent / "workspace"
CREATE_NO_WINDOW = 0x08000000
_HOTKEY_CHARS = re.compile(r"^[a-z0-9+]{3,24}$")

_cursor_lock = threading.Lock()
_settings_lock = threading.Lock()
_cursor_client = None
_cursor_agent = None
_atexit_registered = False

CLAUDE_DISALLOWED_TOOLS = [
    "Agent",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "Edit",
    "Glob",
    "Grep",
    "KillShell",
    "NotebookEdit",
    "Read",
    "Skill",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
    "mcp",
]


class ProviderError(RuntimeError):
    """User-facing failure. Message must not contain secrets."""


def public_error_message(exc: BaseException) -> str:
    """Short UI/CLI text. Never includes credentials or full traces."""
    raw = str(exc).strip()
    lower = raw.lower()
    if "10038" in raw or "not a socket" in lower or "ikke en socket" in lower:
        return "Cursor failed to start on Windows. Close Snip2MD and open it again."
    if "cannot use this model" in lower or "invalid_argument" in lower:
        return "AI polish could not start. OCR is on the clipboard."
    first = raw.splitlines()[0][:180] if raw else type(exc).__name__
    if "cursor_" in first or "api_key" in lower or "apikey" in lower:
        return "The snip could not be converted. Try again."
    return f"The snip could not be converted. {first}"


def _log_last_error(exc: BaseException) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(mode=0o700, exist_ok=True)
        (SETTINGS_PATH.parent / "last-error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _patch_windows_bridge_discovery() -> None:
    """Cursor's Python SDK waits on bridge stderr with select().

    On Windows, select() only works on sockets, so Agent.prompt dies with
    WinError 10038 before any model runs. Read stderr on a thread instead.
    """
    if sys.platform != "win32":
        return
    import queue
    import threading

    import cursor_sdk._bridge as bridge
    from cursor_sdk.errors import CursorSDKError

    if getattr(bridge._read_discovery, "_snip2md_windows", False):
        return

    def _read_discovery_windows(process, timeout: float):
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")
        lines: queue.Queue = queue.Queue()

        def _reader() -> None:
            try:
                for line in process.stderr:
                    lines.put(line)
            except Exception as exc:
                lines.put(exc)

        threading.Thread(target=_reader, daemon=True).start()
        stderr_lines: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = lines.get(timeout=0.1)
            except queue.Empty:
                code = process.poll()
                if code is not None and lines.empty():
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {code}: "
                        + "".join(stderr_lines)
                    )
                continue
            if isinstance(item, Exception):
                raise CursorSDKError("Failed reading bridge discovery") from item
            stderr_lines.append(item)
            discovery = bridge.parse_discovery_line(item)
            if discovery is not None:
                return discovery
        raise CursorSDKError("Timed out waiting for bridge discovery")

    _read_discovery_windows._snip2md_windows = True  # type: ignore[attr-defined]
    bridge._read_discovery = _read_discovery_windows


def _cursor_workspace() -> Path:
    SETTINGS_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    WORKSPACE_PATH.mkdir(mode=0o700, exist_ok=True)
    return WORKSPACE_PATH


def _reset_cursor_client() -> None:
    global _cursor_client, _cursor_agent
    with _cursor_lock:
        agent = _cursor_agent
        client = _cursor_client
        _cursor_agent = None
        _cursor_client = None
    if agent is not None:
        try:
            agent.close()
        except Exception:
            pass
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _get_cursor_client():
    """Reuse one local Cursor bridge for the life of the process."""
    global _cursor_client
    from cursor_sdk import Client

    _patch_windows_bridge_discovery()
    with _cursor_lock:
        if _cursor_client is not None:
            return _cursor_client
        client = Client.launch_bridge(workspace=str(_cursor_workspace()))
        _cursor_client = client
        return client


def _parse_available_models(exc: BaseException) -> list[str]:
    text = str(exc)
    marker = "Available models:"
    index = text.find(marker)
    if index < 0:
        return []
    rest = text[index + len(marker) :].split("Use Cursor")[0]
    names: list[str] = []
    for part in rest.split(","):
        name = part.strip().rstrip(".")
        if name and name.lower() not in _BLOCKED_CURSOR_MODELS:
            names.append(name)
    return names


def _update_settings(updates: dict) -> None:
    with _settings_lock:
        SETTINGS_PATH.parent.mkdir(mode=0o700, exist_ok=True)
        current = _read_json(SETTINGS_PATH) or {}
        current.update(updates)
        tmp = SETTINGS_PATH.with_name("settings.json.tmp")
        tmp.write_text(json.dumps(current), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)


def _save_cursor_model(name: str) -> None:
    global _cursor_model_id
    if not name or name.lower() in _BLOCKED_CURSOR_MODELS:
        return
    _cursor_model_id = name
    try:
        _update_settings({"cursor_model": name})
    except OSError:
        pass


def _pick_cursor_model(available: list[str] | None = None) -> str:
    override = os.environ.get("SNIP2MD_MODEL", "").strip()
    saved = str((_read_json(SETTINGS_PATH) or {}).get("cursor_model") or "").strip()
    preferred: list[str] = []
    for name in (override, saved, *CURSOR_FAST_MODELS):
        if name and name.lower() not in _BLOCKED_CURSOR_MODELS and name not in preferred:
            preferred.append(name)
    if available:
        aliases = {item.lower(): item for item in available}
        for name in preferred:
            hit = aliases.get(name.lower())
            if hit:
                return hit
        for name in available:
            if name.lower() not in _BLOCKED_CURSOR_MODELS:
                return name
    return preferred[0] if preferred else CURSOR_FAST_MODELS[0]


def _refresh_cursor_model() -> str:
    ids: list[str] = []
    try:
        client = _get_cursor_client()
        listed = client.models.list()
        ids = [item.id for item in listed if getattr(item, "id", None)]
    except Exception:
        ids = []
    picked = _pick_cursor_model(ids or None)
    _save_cursor_model(picked)
    return picked


def _cursor_agent_options(key: str, model: str):
    from cursor_sdk import AgentOptions, LocalAgentOptions

    return AgentOptions(
        api_key=key,
        model={"id": model},
        tools=[],
        disallowed_tools=["shell", "mcp", "task"],
        mcp_servers={},
        local=LocalAgentOptions(
            cwd=str(_cursor_workspace()),
            setting_sources=[],
        ),
    )


def _create_cursor_agent():
    from cursor_sdk import Agent

    key = cursor_api_key()
    if not key:
        raise ProviderError("Not signed in to Cursor. Click Sign in with Cursor.")
    tried: list[str] = []
    current = default_model("cursor")
    last_exc: BaseException | None = None
    for _ in range(5):
        if current in tried:
            break
        tried.append(current)
        try:
            agent = Agent.create(
                _cursor_agent_options(key, current),
                model={"id": current},
                client=_get_cursor_client(),
            )
            _save_cursor_model(current)
            return agent
        except Exception as exc:
            last_exc = exc
            _log_last_error(exc)
            available = _parse_available_models(exc)
            if not available and "cannot use this model" not in str(exc).lower():
                raise
            nxt = _pick_cursor_model(available)
            if nxt in tried:
                leftover = [name for name in available if name not in tried]
                if not leftover:
                    break
                nxt = leftover[0]
            current = nxt
    if last_exc is not None:
        raise last_exc
    raise ProviderError("AI polish could not start. OCR is on the clipboard.")


def _close_agent(agent) -> None:
    if agent is None:
        return
    try:
        agent.close()
    except Exception:
        pass


def _arm_cursor_agent() -> None:
    """Keep one unused agent ready so the next snip skips CreateAgent."""
    global _cursor_agent
    try:
        agent = _create_cursor_agent()
    except Exception as exc:
        _log_last_error(exc)
        return
    with _cursor_lock:
        old = _cursor_agent
        _cursor_agent = agent
    _close_agent(old)


def _take_cursor_agent():
    global _cursor_agent
    with _cursor_lock:
        agent = _cursor_agent
        _cursor_agent = None
    return agent


def warmup_cursor() -> None:
    """Start the Cursor helper and pre-create an agent."""
    if not ai_polish_enabled():
        return
    if not has_cursor_subscription():
        return
    try:
        _get_cursor_client()
        _refresh_cursor_model()
        _arm_cursor_agent()
    except Exception as exc:
        _log_last_error(exc)


def reap_snip2md_bridges() -> None:
    """Kill leftover Cursor SDK Node helpers for this app's workspace only."""
    marker = str(WORKSPACE_PATH)
    if not marker or ".." in marker:
        return
    env = os.environ.copy()
    env["SNIP2MD_BRIDGE_MARK"] = marker
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.CommandLine -and "
                    "$_.CommandLine.Contains($env:SNIP2MD_BRIDGE_MARK) -and "
                    "$_.CommandLine.Contains('cursor-sdk-bridge') } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                    "-ErrorAction SilentlyContinue }"
                ),
            ],
            env=env,
            timeout=20,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def close_cursor_runtime() -> None:
    _reset_cursor_client()
    reap_snip2md_bridges()


def register_runtime_shutdown() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(close_cursor_runtime)
    _atexit_registered = True


def _read_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


ALLOWED_PROVIDERS = ("auto", "cursor", "claude")


def preferred_provider() -> str:
    data = _read_json(SETTINGS_PATH) or {}
    value = str(data.get("provider") or os.environ.get("SNIP2MD_PROVIDER") or "auto")
    name = value.strip().lower()
    return name if name in ALLOWED_PROVIDERS else "auto"


def set_preferred_provider(name: str) -> None:
    value = str(name or "").strip().lower()
    if value not in ALLOWED_PROVIDERS:
        raise ProviderError("Unknown subscription.")
    _update_settings({"provider": value})


def ai_polish_enabled() -> bool:
    env = os.environ.get("SNIP2MD_AI_POLISH", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    data = _read_json(SETTINGS_PATH) or {}
    if "ai_polish" in data:
        return bool(data["ai_polish"])
    return False


def set_ai_polish(enabled: bool) -> None:
    _update_settings({"ai_polish": bool(enabled)})


def configured_hotkey() -> str:
    data = _read_json(SETTINGS_PATH) or {}
    saved = str(data.get("hotkey") or "").strip().lower().replace(" ", "")
    if saved and _HOTKEY_CHARS.fullmatch(saved):
        return saved
    env = os.environ.get("SNIP2MD_HOTKEY", "").strip().lower().replace(" ", "")
    if env and _HOTKEY_CHARS.fullmatch(env):
        return env
    return "ctrl+alt+m"


def set_hotkey(spec: str) -> None:
    value = str(spec or "").strip().lower().replace(" ", "")
    if not _HOTKEY_CHARS.fullmatch(value):
        raise ProviderError("Invalid shortcut.")
    _update_settings({"hotkey": value})


def cursor_api_key() -> str | None:
    env = os.environ.get("CURSOR_API_KEY", "").strip()
    if env:
        return env
    data = _read_json(CURSOR_SDK_AUTH) or {}
    key = data.get("apiKey") or data.get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    creds = data.get("credentials")
    if isinstance(creds, dict):
        nested = creds.get("apiKey") or creds.get("api_key")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def has_cursor_subscription() -> bool:
    return cursor_api_key() is not None


def start_cursor_login() -> None:
    """Open Cursor's browser login. No key paste — same subscription as the IDE."""
    node = shutil.which("node")
    if not node:
        raise ProviderError(
            "Node.js is needed for Cursor sign-in. Install it from https://nodejs.org"
        )
    sdk = ROOT / "node_modules" / "@cursor" / "sdk"
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    script = ROOT / "cursor_login.mjs"
    if sdk.exists():
        subprocess.Popen([node, str(script)], cwd=str(ROOT), creationflags=flags)
    else:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            raise ProviderError(
                "npm was not found. Install Node.js from https://nodejs.org"
            )
        subprocess.Popen(
            ["cmd.exe", "/c", "npm install && node cursor_login.mjs && pause"],
            cwd=str(ROOT),
            creationflags=flags,
        )
    set_preferred_provider("cursor")


def claude_executable() -> str | None:
    return shutil.which("claude")


def claude_auth_status() -> dict | None:
    exe = claude_executable()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def has_claude_subscription() -> bool:
    data = claude_auth_status()
    if not data or not data.get("loggedIn"):
        return False
    method = str(data.get("authMethod") or "").lower()
    return method not in ("console", "api", "anthropic_api")


def require_claude_subscription() -> dict:
    if not claude_executable():
        raise ProviderError(
            "Claude Code is not installed. Install it from\n"
            f"{CLAUDE_SETUP}"
        )
    data = claude_auth_status()
    if data and data.get("loggedIn"):
        method = str(data.get("authMethod") or "").lower()
        if method in ("console", "api", "anthropic_api"):
            raise ProviderError(
                "You are signed in to Anthropic Console (API billing). "
                "Sign in with your Claude subscription instead."
            )
        return data
    raise ProviderError("Not signed in to Claude. Click Sign in.")


def login_claude_subscription() -> int:
    exe = claude_executable()
    if not exe:
        print("Claude Code is not installed.")
        print(f"Install it from {CLAUDE_SETUP}")
        print("Then run: python snip2md.py login")
        return 1
    print("Opening the same Claude subscription login you use in the terminal...")
    return subprocess.call([exe, "auth", "login", "--claudeai"])


def start_claude_login() -> None:
    """Open Claude's normal terminal login in a new console window."""
    exe = claude_executable()
    if not exe:
        raise ProviderError(
            "Claude Code is not installed.\n"
            f"Install it from {CLAUDE_SETUP}"
        )
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen([exe, "auth", "login", "--claudeai"], creationflags=flags)
    set_preferred_provider("claude")


def default_model(provider: str | None = None) -> str:
    override = os.environ.get("SNIP2MD_MODEL", "").strip()
    if override:
        return override
    if (provider or active_provider_name()) == "cursor":
        global _cursor_model_id
        if _cursor_model_id:
            return _cursor_model_id
        saved = str((_read_json(SETTINGS_PATH) or {}).get("cursor_model") or "").strip()
        if saved and saved.lower() not in _BLOCKED_CURSOR_MODELS:
            _cursor_model_id = saved
            return saved
        return CURSOR_FAST_MODELS[0]
    return "claude-haiku-4-5-20251001"


def active_provider_name() -> str:
    pref = preferred_provider()
    if pref == "cursor":
        if has_cursor_subscription():
            return "cursor"
        raise ProviderError("Not signed in to Cursor. Click Sign in with Cursor.")
    if pref == "claude":
        require_claude_subscription()
        return "claude"
    if has_cursor_subscription():
        return "cursor"
    if has_claude_subscription():
        return "claude"
    raise ProviderError("Not signed in. Click Sign in with Cursor or Sign in with Claude.")


def subscription_label(data: dict | None = None) -> str:
    try:
        provider = active_provider_name()
    except ProviderError:
        return "Not signed in"
    if provider == "cursor":
        return "Cursor subscription"
    data = data if data is not None else claude_auth_status()
    kind = str((data or {}).get("subscriptionType") or "").strip()
    if kind:
        return f"Claude {kind} subscription"
    return "Claude subscription"


def unwrap_markdown(text: str) -> str:
    text = text.strip()
    if not text.startswith("```") or "```" not in text[3:]:
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _polish_prompt(ocr_markdown: str | None) -> str:
    draft = (ocr_markdown or "").strip()
    if len(draft) < 8:
        return PROMPT
    clipped = draft[:6000]
    return (
        f"{PROMPT} Local OCR already read the text below — keep every string, "
        "fix reading order (heading, label/value table, then step list), "
        "and do not drop values.\n\nOCR draft:\n\n"
        f"{clipped}"
    )


def image_to_markdown(
    image_bytes: bytes,
    mime_type: str,
    size: tuple[int, int] | None = None,
    ocr_markdown: str | None = None,
) -> str:
    provider = active_provider_name()
    if provider == "cursor":
        return _cursor_to_markdown(image_bytes, mime_type, size, ocr_markdown)
    return asyncio.run(_claude_to_markdown(image_bytes, mime_type, ocr_markdown))


def _cursor_to_markdown(
    image_bytes: bytes,
    mime_type: str,
    size: tuple[int, int] | None = None,
    ocr_markdown: str | None = None,
) -> str:
    try:
        from cursor_sdk import (
            CursorAgentError,
            RateLimitError,
            SDKImage,
            SDKImageDimension,
            UserMessage,
        )
    except ImportError as exc:
        raise ProviderError(
            "Missing Cursor SDK. Run: pip install -r requirements.txt"
        ) from exc

    key = cursor_api_key()
    if not key:
        raise ProviderError("Not signed in to Cursor. Click Sign in with Cursor.")
    dimension = None
    if size:
        dimension = SDKImageDimension(width=size[0], height=size[1])
    message = UserMessage(
        text=_polish_prompt(ocr_markdown),
        images=[SDKImage.from_data(image_bytes, mime_type, dimension=dimension)],
    )

    def _run(agent):
        result = agent.send(message).wait()
        status = getattr(result, "status", None)
        if status and status not in ("finished", "success"):
            raise ProviderError(f"Cursor run ended with status {status}.")
        text = unwrap_markdown((getattr(result, "result", None) or "").strip())
        if not text:
            raise ProviderError("Cursor returned no text. Try snipping a larger area.")
        return text

    agent = _take_cursor_agent()
    try:
        if agent is None:
            agent = _create_cursor_agent()
        try:
            return _run(agent)
        except RateLimitError as exc:
            _log_last_error(exc)
            raise ProviderError(
                "Cursor is out of usage right now. Try again later."
            ) from exc
        except CursorAgentError as exc:
            _log_last_error(exc)
            detail = str(exc).lower()
            if "cannot use this model" in detail:
                available = _parse_available_models(exc)
                nxt = _pick_cursor_model(available)
                _save_cursor_model(nxt)
                _close_agent(agent)
                agent = None
                _reset_cursor_client()
                agent = _create_cursor_agent()
                try:
                    return _run(agent)
                except Exception as retry_exc:
                    _log_last_error(retry_exc)
                    raise ProviderError(public_error_message(retry_exc)) from retry_exc
            if "invalid_argument" in detail:
                raise ProviderError(public_error_message(exc)) from exc
            _close_agent(agent)
            agent = None
            _reset_cursor_client()
            agent = _create_cursor_agent()
            try:
                return _run(agent)
            except Exception as retry_exc:
                _log_last_error(retry_exc)
                raise ProviderError(public_error_message(retry_exc)) from retry_exc
        except Exception as exc:
            _log_last_error(exc)
            raise ProviderError(public_error_message(exc)) from exc
    finally:
        _close_agent(agent)
        threading.Thread(target=_arm_cursor_agent, daemon=True).start()


async def _claude_to_markdown(
    image_bytes: bytes,
    mime_type: str,
    ocr_markdown: str | None = None,
) -> str:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
        )
    except ImportError as exc:
        raise ProviderError(
            "Missing Claude Agent SDK. Run: pip install -r requirements.txt"
        ) from exc

    popped: dict[str, str] = {}
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        value = os.environ.pop(name, None)
        if value is not None:
            popped[name] = value

    b64 = base64.b64encode(image_bytes).decode("ascii")
    model = default_model("claude")

    async def _messages():
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _polish_prompt(ocr_markdown)},
                ],
            },
        }

    texts: list[str] = []
    result_text = ""
    result_error = ""

    try:
        with tempfile.TemporaryDirectory(prefix="snip2md-") as cwd:
            options = ClaudeAgentOptions(
                system_prompt=CLAUDE_SYSTEM,
                model=model,
                tools=[],
                allowed_tools=[],
                disallowed_tools=CLAUDE_DISALLOWED_TOOLS,
                max_turns=1,
                max_budget_usd=1.0,
                setting_sources=[],
                skills=[],
                strict_mcp_config=True,
                cwd=cwd,
            )
            async with ClaudeSDKClient(options=options) as client:
                await client.query(_messages())
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                texts.append(block.text)
                    elif isinstance(message, ResultMessage):
                        subtype = getattr(message, "subtype", None) or ""
                        if subtype and subtype != "success":
                            result_error = subtype
                        raw = getattr(message, "result", None)
                        if isinstance(raw, str):
                            result_text = raw
    except Exception as exc:
        _log_last_error(exc)
        detail = str(exc).lower()
        if any(word in detail for word in ("rate", "usage", "limit", "quota", "429")):
            raise ProviderError(
                "Claude is out of usage. Click Sign in with Cursor."
            ) from exc
        raise ProviderError(public_error_message(exc)) from exc
    finally:
        os.environ.update(popped)

    text = unwrap_markdown(result_text or "\n\n".join(texts))
    if not text:
        extra = f" ({result_error})" if result_error else ""
        raise ProviderError(
            f"Claude returned no text{extra}. Try snipping a larger area."
        )
    return text
