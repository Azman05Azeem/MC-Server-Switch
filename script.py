import discord
from discord.ext import commands, tasks
import asyncio
import subprocess
import threading
import json
import logging
import os
import sys
import signal
import atexit
import datetime
import pystray
from PIL import Image, ImageDraw
from mcstatus import JavaServer
from colorama import init, Fore, Style

init(autoreset=True)

# ══════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except Exception as e:
    sys.exit(f"[FATAL] Failed to load config.json: {e}")

_REQUIRED_KEYS = ['TOKEN', 'START_SCRIPT', 'SERVER_DIR', 'SERVER_IP',
                  'SERVER_PORT', 'MY_SERVER_ID', 'CONSOLE_CHANNEL_ID', 'IDLE_TIMEOUT']
_missing = [k for k in _REQUIRED_KEYS if k not in config]
if _missing:
    sys.exit(f"[FATAL] config.json is missing required keys: {', '.join(_missing)}")

TOKEN                = config['TOKEN']
START_SCRIPT         = config['START_SCRIPT']
SERVER_DIR           = config['SERVER_DIR']
SERVER_IP            = config['SERVER_IP']
SERVER_PORT          = config['SERVER_PORT']
# PUBLIC_IP is shown in Discord announcements (e.g. a domain or external IP).
# Falls back to SERVER_IP if not set.
PUBLIC_IP            = config.get('PUBLIC_IP', SERVER_IP)
MY_SERVER            = discord.Object(id=config['MY_SERVER_ID'])
CONSOLE_CHANNEL_ID   = config['CONSOLE_CHANNEL_ID']
# Separate announce channel — falls back to console channel if not set
ANNOUNCE_CHANNEL_ID  = config.get('ANNOUNCE_CHANNEL_ID', CONSOLE_CHANNEL_ID)
IDLE_TIMEOUT         = config['IDLE_TIMEOUT']
ENABLE_CONSOLE_LOGS  = config.get('ENABLE_CONSOLE_LOGS', True)
# Cap console buffer so it never grows unbounded when Discord is unreachable
CONSOLE_BUFFER_LIMIT = config.get('CONSOLE_BUFFER_LIMIT', 500)

# Warn at 50%, 25%, 10% of timeout and at 1 min remaining.
# The filter ensures milestones are within (0, IDLE_TIMEOUT).
# If IDLE_TIMEOUT <= 1 no warnings fire — the shutdown message itself is sufficient.
WARNING_MILESTONES = {m for m in {
    int(IDLE_TIMEOUT * 0.5),
    int(IDLE_TIMEOUT * 0.25),
    int(IDLE_TIMEOUT * 0.1),
    1,
} if 0 < m < IDLE_TIMEOUT}

# ══════════════════════════════════════════════════════════════════
#  MESSAGES  — all Discord-facing strings live in messages.json
# ══════════════════════════════════════════════════════════════════
_DEFAULTS = {
    "server_already_running":  "🔴 The server is **already running**.",
    "server_starting":         "🟢 Booting up the server! Check the console channel… ⚙️",
    "server_start_failed":     "🔴 Failed to start the server: `{error}`",
    "server_not_running":      "⚪ The server is **not running**.",
    "server_stop_sent":        "🔴 Stop command sent. The server is shutting down…",
    "server_stop_failed":      "❌ Could not reach the server process.",
    "status_offline":          "⚪ **Server Status:** Offline",
    "status_online": (
        "🟢 **Server Status:** Online\n"
        "👥 Players: **{online}/{maximum}** — {players}\n"
        "📶 Latency: {latency} ms\n"
        "⏳ Idle timer: {idle}/{timeout} min"
    ),
    "status_starting":         "🟡 **Server Status:** Process running, but not yet reachable (still starting…)",
    "cmd_sent":                "✅ Sent to console: `{command}`",
    "cmd_not_sent":            "🔴 Server is not running — command not sent.",
    "idle_warning":            "⚠️ **Idle Warning:** No players online. Auto-shutdown in **{time_left} minute{plural}**.",
    "player_joined":           "🟢 **Player joined!** Idle auto-shutdown canceled.",
    "auto_shutdown":           "🔴 **Idle Timeout Reached.** Shutting down automatically to save power.",
    "server_online_announce":  "🎉 **Server is now ONLINE!** Connect at `{ip}`",
    "server_offline_announce": "🔴 **Server has gone OFFLINE.**",
    "no_permission":           "🚫 You need **Administrator** permissions to use this command.",
}

try:
    with open('messages.json', 'r', encoding='utf-8') as _f:
        MSG = {**_DEFAULTS, **json.load(_f)}
except FileNotFoundError:
    MSG = _DEFAULTS
except Exception as _e:
    print(f"{Fore.YELLOW}[WARN] messages.json error: {_e}. Using built-in defaults.{Style.RESET_ALL}")
    MSG = _DEFAULTS

def msg(key: str, **kwargs) -> str:
    """Fetch and format a Discord-facing message by key."""
    template = MSG.get(key, f"[missing message key: {key}]")
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template

# ══════════════════════════════════════════════════════════════════
#  LOGGING  — rotating files in ./logs/
# ══════════════════════════════════════════════════════════════════
cli_mode = 'bot'        # 'bot' | 'console'

class _ModeFilter(logging.Filter):
    """Suppress bot log output to the terminal while in console-view mode."""
    def filter(self, record):
        return cli_mode == 'bot'

class _ColorFormatter(logging.Formatter):
    _C = {
        logging.DEBUG:    Fore.CYAN,
        logging.INFO:     Fore.GREEN,
        logging.WARNING:  Fore.YELLOW,
        logging.ERROR:    Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }
    def format(self, record):
        c   = self._C.get(record.levelno, Fore.WHITE)
        fmt = (f"{Fore.LIGHTBLACK_EX}[%(asctime)s]{Style.RESET_ALL} "
               f"{c}[%(levelname)s]{Style.RESET_ALL} %(message)s")
        return logging.Formatter(fmt, datefmt='%Y-%m-%d %H:%M:%S').format(record)

def _new_log_path() -> str:
    """Return logs/YYYY-MM-DD-N.log, incrementing N until the path is free."""
    os.makedirs('logs', exist_ok=True)
    date = datetime.date.today().strftime('%Y-%m-%d')
    n = 1
    while os.path.exists(f'logs/{date}-{n}.log'):
        n += 1
    return f'logs/{date}-{n}.log'

_root     = logging.getLogger()
_root.setLevel(logging.INFO)
_log_path = _new_log_path()

_fh = logging.FileHandler(_log_path, encoding='utf-8')
_fh.setFormatter(logging.Formatter(
    '[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
_root.addHandler(_fh)

if ENABLE_CONSOLE_LOGS:
    _ch = logging.StreamHandler()
    _ch.setFormatter(_ColorFormatter())
    _ch.addFilter(_ModeFilter())
    _root.addHandler(_ch)

logging.info(f"Bot starting. Log: {_log_path}")

# ══════════════════════════════════════════════════════════════════
#  SHARED STATE
# ══════════════════════════════════════════════════════════════════
server_process        = None
idle_minutes          = 0
console_buffer        = []
console_lock          = threading.Lock()
server_was_alive      = False
run_channel_id        = None
_tray_icon            = None
# Set when auto-shutdown fires; cleared once the process exits.
# Prevents /run being blocked by a process that is mid-shutdown.
_server_shutting_down = False

# ══════════════════════════════════════════════════════════════════
#  GRACEFUL SHUTDOWN
# ══════════════════════════════════════════════════════════════════
_shutdown_event = threading.Event()

def _emergency_stop():
    """
    Send /stop to the MC server and wait up to 30 s for it to finish.
    The threading.Event ensures this body runs only once even if called
    from multiple sources (signal, atexit, tray quit) simultaneously.
    """
    if not _shutdown_event.is_set():
        _shutdown_event.set()
        if server_process and server_process.poll() is None:
            logging.warning("Shutdown detected — stopping MC server to prevent data loss…")
            try:
                server_process.stdin.write('stop\n')
                server_process.stdin.flush()
                server_process.wait(timeout=30)
                logging.info("MC server stopped cleanly.")
            except Exception as e:
                logging.error(f"Error during emergency stop: {e}")

def _signal_exit(sig, frame):
    _emergency_stop()
    os._exit(0)

atexit.register(_emergency_stop)
signal.signal(signal.SIGINT,  _signal_exit)
signal.signal(signal.SIGTERM, _signal_exit)

# ══════════════════════════════════════════════════════════════════
#  SYSTEM TRAY
# ══════════════════════════════════════════════════════════════════
def _make_icon(online: bool) -> Image.Image:
    color = (43, 175, 43) if online else (175, 43, 43)
    img   = Image.new('RGB', (64, 64), color=color)
    dc    = ImageDraw.Draw(img)
    dc.rectangle((16, 16, 48, 48), fill=(30, 30, 30))
    return img

def _update_tray(online: bool):
    """Recolor the tray icon and tooltip to reflect the current server state."""
    if _tray_icon:
        _tray_icon.icon  = _make_icon(online)
        _tray_icon.title = f"Minecraft Bot — {'Online 🟢' if online else 'Offline 🔴'}"
        # update_menu() is best-effort; not all backends support it
        try:
            _tray_icon.update_menu()
        except Exception:
            pass

def _tray_toggle_console(icon, item):
    """Toggle between 'bot log' and 'server console' view modes."""
    global cli_mode
    cli_mode = 'console' if cli_mode == 'bot' else 'bot'
    label = 'SERVER CONSOLE' if cli_mode == 'console' else 'BOT LOGS'
    print(f"\n{Fore.YELLOW}[CLI] Switched to {label} mode.{Style.RESET_ALL}")
    if cli_mode == 'console':
        print(f"{Fore.CYAN}[CLI] Type any text + Enter to send to the MC server.{Style.RESET_ALL}\n")

def _tray_quit(icon, item):
    _emergency_stop()
    icon.stop()
    os._exit(0)

def _tray_status_label(item):
    return "🟢 Server: Online" if server_was_alive else "🔴 Server: Offline"

def _setup_tray():
    global _tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(_tray_status_label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Toggle Console / Bot View', _tray_toggle_console, default=True),
        pystray.MenuItem('Quit Server Bot', _tray_quit),
    )
    icon       = pystray.Icon("ServerBot", _make_icon(False), "Minecraft Bot — Offline 🔴", menu)
    _tray_icon = icon
    icon.run()

threading.Thread(target=_setup_tray, daemon=True).start()

# ══════════════════════════════════════════════════════════════════
#  CLI INPUT THREAD
# ══════════════════════════════════════════════════════════════════
def _send_to_server(command: str) -> bool:
    """Write a command to the MC server's stdin. Returns False if unavailable."""
    if server_process and server_process.poll() is None and server_process.stdin:
        try:
            server_process.stdin.write(command + '\n')
            server_process.stdin.flush()
            return True
        except OSError:
            return False
    return False

def _cli_start_server():
    """Start the MC server directly from the terminal (used in console mode)."""
    global server_process, idle_minutes, _server_shutting_down
    if server_process and server_process.poll() is None:
        display = PUBLIC_IP if SERVER_PORT == 25565 else f"{PUBLIC_IP}:{SERVER_PORT}"
        print(f"{Fore.YELLOW}[CLI] Server is already running. Connect at {display}{Style.RESET_ALL}")
        return
    _server_shutting_down = False
    print(f"{Fore.GREEN}[CLI] Starting server…{Style.RESET_ALL}")
    logging.info("Server start initiated from CLI.")
    try:
        server_process = subprocess.Popen(
            START_SCRIPT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=SERVER_DIR, bufsize=1,
        )
        idle_minutes = 0
        threading.Thread(target=console_reader, daemon=True).start()
        print(f"{Fore.GREEN}[CLI] Server process launched. Output will appear here.{Style.RESET_ALL}")
    except Exception as e:
        logging.error(f"CLI start failed: {e}")
        print(f"{Fore.RED}[CLI] Failed to start server: {e}{Style.RESET_ALL}")

def _cli_thread():
    print(
        f"\n{Fore.CYAN}"
        f"╔══════════════════════════════════════════════════════╗\n"
        f"║              Minecraft Bot — Terminal CLI            ║\n"
        f"╠══════════════════════════════════════════════════════╣\n"
        f"║  console → switch to live MC server output           ║\n"
        f"║  bot     → switch back to bot log view               ║\n"
        f"║  In console mode:                                    ║\n"
        f"║    start → launch the MC server                      ║\n"
        f"║    any other text → sent directly to the server      ║\n"
        f"║  Double-click the tray icon to toggle view mode too  ║\n"
        f"╚══════════════════════════════════════════════════════╝"
        f"{Style.RESET_ALL}\n"
    )

    while True:
        try:
            line = input()
        except (EOFError, OSError):
            break

        stripped = line.strip()
        if not stripped:
            continue

        global cli_mode

        if stripped.lower() == 'console':
            cli_mode = 'console'
            print(f"{Fore.YELLOW}[CLI] Console mode ON — server output shown here. Type to send commands.{Style.RESET_ALL}")
        elif stripped.lower() == 'bot':
            cli_mode = 'bot'
            print(f"{Fore.YELLOW}[CLI] Bot mode ON — bot logs restored.{Style.RESET_ALL}")
        elif cli_mode == 'console':
            if stripped.lower() == 'start':
                _cli_start_server()
            elif _send_to_server(stripped):
                print(f"{Fore.GREEN}[CLI] ▶ Sent: {stripped}{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}[CLI] Server not running. Type 'start' to launch it.{Style.RESET_ALL}")
        else:
            print(
                f"{Fore.CYAN}[CLI] You are in bot-log mode. "
                f"Type 'console' to switch, or double-click the tray icon.{Style.RESET_ALL}"
            )

threading.Thread(target=_cli_thread, daemon=True).start()

# ══════════════════════════════════════════════════════════════════
#  DISCORD BOT
# ══════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

# ──────────────────────────────────────────────────────────────────
#  Console reader + Discord feed
# ──────────────────────────────────────────────────────────────────
def console_reader():
    """Daemon thread: reads MC server stdout into the buffer.
    Also prints to the terminal when in console-view mode.

    If decoding or reading ever fails, the thread exits silently.
    """
    try:
        if server_process and server_process.stdout:
            for line in iter(server_process.stdout.readline, ''):
                if line:
                    with console_lock:
                        if len(console_buffer) < CONSOLE_BUFFER_LIMIT:
                            console_buffer.append(line)
                    if cli_mode == 'console':
                        try:
                            print(line, end='', flush=True)
                        except Exception:
                            pass
    except Exception:
        # Worst case: ignore console-feed failures completely.
        pass

@tasks.loop(seconds=3.0)
async def send_console_feed():
    """Drain the buffer into Discord in ≤ 1 900-char code blocks."""
    with console_lock:
        if not console_buffer:
            return
        lines = console_buffer.copy()
        console_buffer.clear()

    channel = bot.get_channel(CONSOLE_CHANNEL_ID)
    if not channel:
        return

    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 1900:
            await channel.send(f"```\n{chunk}```")
            chunk = ""
        chunk += line
    if chunk.strip():
        await channel.send(f"```\n{chunk}```")

# ──────────────────────────────────────────────────────────────────
#  Bot events
# ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    global server_was_alive
    logging.info(f'Logged in as {bot.user}.')
    try:
        bot.tree.copy_global_to(guild=MY_SERVER)
        await bot.tree.sync(guild=MY_SERVER)
        logging.info("Slash commands synced.")
    except Exception as e:
        logging.error(f"Sync failed: {e}")
    # Check if the MC server is already up (e.g. bot restarted while server was running)
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: JavaServer.lookup(f"{SERVER_IP}:{SERVER_PORT}").status()
        )
        # Ping succeeded — server is already online
        server_was_alive = True
        _update_tray(True)
        display = PUBLIC_IP if SERVER_PORT == 25565 else f"{PUBLIC_IP}:{SERVER_PORT}"
        await _announce("server_online_announce", ip=display)
        logging.info("MC server was already running on bot connect.")
    except Exception:
        pass  # Server not up — normal startup, nothing to announce
    # Guard against double-start on reconnect
    if not check_idle_server.is_running():
        check_idle_server.start()
    if not send_console_feed.is_running():
        send_console_feed.start()

# ──────────────────────────────────────────────────────────────────
#  Permission helpers
# ──────────────────────────────────────────────────────────────────
def _is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

async def _deny(interaction: discord.Interaction):
    await interaction.response.send_message(msg("no_permission"), ephemeral=True)

# ──────────────────────────────────────────────────────────────────
#  /run  (.run_server)  — public
# ──────────────────────────────────────────────────────────────────
async def _do_start(send_fn, channel_id=None):
    global server_process, idle_minutes, run_channel_id, _server_shutting_down
    # If auto-shutdown just fired, wait up to 15 s for the process to actually exit
    if _server_shutting_down and server_process and server_process.poll() is None:
        await send_fn("⏳ Server is shutting down, please wait…")
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, server_process.wait),
                timeout=15.0
            )
        except Exception:
            pass
        _server_shutting_down = False
    if server_process is not None and server_process.poll() is None:
        await send_fn(msg("server_already_running"))
        return
    await send_fn(msg("server_starting"))
    logging.info("Server start initiated.")
    if channel_id:
        run_channel_id = channel_id
    try:
        server_process = subprocess.Popen(
            START_SCRIPT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=SERVER_DIR, bufsize=1,
        )
        idle_minutes = 0
        threading.Thread(target=console_reader, daemon=True).start()
    except Exception as e:
        logging.error(f"Start failed: {e}")
        await send_fn(msg("server_start_failed", error=e))

@bot.tree.command(name="run", description="Starts the Minecraft server", guild=MY_SERVER)
async def run_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await _do_start(interaction.followup.send, interaction.channel_id)

@bot.command(name="run_server")
async def run_text(ctx):
    await _do_start(ctx.send, ctx.channel.id)

# ──────────────────────────────────────────────────────────────────
#  /stop  (.stop_server)  — ADMIN ONLY
# ──────────────────────────────────────────────────────────────────
async def _do_stop(send_fn):
    global idle_minutes, server_was_alive, _server_shutting_down
    if server_process is None or server_process.poll() is not None:
        await send_fn(msg("server_not_running"))
        return
    if _send_to_server('stop'):
        idle_minutes = 0
        server_was_alive = False
        _server_shutting_down = True
        _update_tray(False)
        await send_fn(msg("server_stop_sent"))
        logging.info("Manual stop sent.")
    else:
        await send_fn(msg("server_stop_failed"))

@bot.tree.command(name="stop", description="[ADMIN] Stops the Minecraft server", guild=MY_SERVER)
@discord.app_commands.default_permissions(administrator=True)
async def stop_slash(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await _deny(interaction); return
    await interaction.response.defer()
    await _do_stop(interaction.followup.send)

@bot.command(name="stop_server")
@commands.has_permissions(administrator=True)
async def stop_text(ctx):
    await _do_stop(ctx.send)

@stop_text.error
async def _stop_err(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(msg("no_permission"))

# ──────────────────────────────────────────────────────────────────
#  /status  (.server_status)  — public
# ──────────────────────────────────────────────────────────────────
async def _do_status(send_fn):
    if server_process is None or server_process.poll() is not None:
        await send_fn(msg("status_offline"))
        return
    try:
        loop = asyncio.get_running_loop()
        # JavaServer.lookup + status() are blocking network calls; run off the event loop
        s = await loop.run_in_executor(
            None, lambda: JavaServer.lookup(f"{SERVER_IP}:{SERVER_PORT}").status()
        )
        players = ", ".join(p.name for p in s.players.sample) if s.players.sample else "—"
        await send_fn(msg("status_online",
                          online=s.players.online, maximum=s.players.max,
                          players=players, latency=round(s.latency, 1),
                          idle=idle_minutes, timeout=IDLE_TIMEOUT))
    except Exception:
        await send_fn(msg("status_starting"))

@bot.tree.command(name="status", description="Shows the current server status", guild=MY_SERVER)
async def status_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await _do_status(interaction.followup.send)

@bot.command(name="server_status")
async def status_text(ctx):
    await _do_status(ctx.send)

# ──────────────────────────────────────────────────────────────────
#  /cmd  (.server_cmd)  — ADMIN ONLY
# ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="cmd", description="[ADMIN] Send a raw command to the MC console", guild=MY_SERVER)
@discord.app_commands.default_permissions(administrator=True)
async def cmd_slash(interaction: discord.Interaction, command: str):
    if not _is_admin(interaction):
        await _deny(interaction); return
    await interaction.response.defer()
    if _send_to_server(command):
        await interaction.followup.send(msg("cmd_sent", command=command))
    else:
        await interaction.followup.send(msg("cmd_not_sent"))

@bot.command(name="server_cmd")
@commands.has_permissions(administrator=True)
async def cmd_text(ctx, *, command: str):
    if _send_to_server(command):
        await ctx.send(msg("cmd_sent", command=command))
    else:
        await ctx.send(msg("cmd_not_sent"))

@cmd_text.error
async def _cmd_err(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(msg("no_permission"))

# ══════════════════════════════════════════════════════════════════
#  IDLE CHECK, AUTO-SHUTDOWN & STATUS ANNOUNCEMENTS
#
#  Messages go to BOTH the announce channel AND the channel where
#  /run was called (a set deduplicates them when they're the same).
#  Warnings fire at mathematically-spaced milestones (50%, 25%,
#  10%, and 1 minute) computed once at startup from IDLE_TIMEOUT.
# ══════════════════════════════════════════════════════════════════
async def _announce(key: str, **kwargs):
    for cid in {ANNOUNCE_CHANNEL_ID, run_channel_id}:
        if cid and (ch := bot.get_channel(cid)):
            await ch.send(msg(key, **kwargs))

@tasks.loop(minutes=1.0)
async def check_idle_server():
    global idle_minutes, server_was_alive, _server_shutting_down

    if server_process is None or server_process.poll() is not None:
        _server_shutting_down = False  # process is confirmed dead, clear the flag
        if server_was_alive:
            server_was_alive = False
            _update_tray(False)
            await _announce("server_offline_announce")
        return

    try:
        loop = asyncio.get_running_loop()
        # Blocking network call — run off the event loop
        status = await loop.run_in_executor(
            None, lambda: JavaServer.lookup(f"{SERVER_IP}:{SERVER_PORT}").status()
        )

        if not server_was_alive:
            server_was_alive = True
            _update_tray(True)
            display = PUBLIC_IP if SERVER_PORT == 25565 else f"{PUBLIC_IP}:{SERVER_PORT}"
            await _announce("server_online_announce", ip=display)

        if status.players.online == 0:
            idle_minutes += 1
            time_left = IDLE_TIMEOUT - idle_minutes

            if idle_minutes >= IDLE_TIMEOUT:
                logging.warning("Idle timeout reached — initiating auto-shutdown.")
                await _announce("auto_shutdown")
                _server_shutting_down = True
                _send_to_server('stop')
                idle_minutes = 0
                return

            logging.warning(
                f"Server empty — idle {idle_minutes}/{IDLE_TIMEOUT} min "
                f"({time_left} min until auto-shutdown)."
            )
            if time_left in WARNING_MILESTONES:
                plural = "s" if time_left != 1 else ""
                await _announce("idle_warning", time_left=time_left, plural=plural)

        else:
            if idle_minutes > 0:
                logging.info("Player joined — idle timer reset.")
                await _announce("player_joined")
            idle_minutes = 0

    except Exception:
        # MC not yet pingable (still booting) — skip this tick silently
        pass

# ══════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════
bot.run(TOKEN, log_handler=None)
