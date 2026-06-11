# 🎮 Minecraft Discord Bot

A self-hosted discord bot that lets you start, stop, and monitor your Minecraft Java server directly from Discord — with idle auto-shutdown, live console streaming, a system tray icon, and a terminal CLI.

---

## ✨ Features

- **Slash Commands & Prefix Commands** — control the server from any Discord channel.
- **Live Console Feed** — server output streams into a designated Discord channel in real time.
- **Idle Auto-Shutdown** — automatically stops the server when no players are online, with configurable warnings at 50%, 25%, 10%, and 1 minute remaining.
- **Online/Offline Announcements** — posts to a Discord channel when the server comes up or goes down.
- **System Tray Icon** — color-coded indicator (green = online, red = offline) with a quit and console-toggle option.
- **Terminal CLI** — type commands directly into the server console from your terminal, or switch views between bot logs and server output.
- **Graceful Shutdown** — sends `/stop` to the Minecraft server on SIGINT/SIGTERM or tray quit to prevent data loss.
- **Fully Customizable Messages** — every Discord-facing string lives in `messages.json`; no code edits needed.

---

## 📋 Requirements

- Python 3.10+
- A Minecraft Java server with a start script (`.bat` or `.sh`)
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

Install dependencies:

```bash
pip install discord.py mcstatus pystray Pillow colorama
```

---

## ⚙️ Configuration

Copy `config.json` and fill in your values:

```json
{
  "TOKEN": "your-discord-bot-token",
  "START_SCRIPT": "D:\\Server\\start.bat",
  "SERVER_DIR": "D:\\Server",
  "SERVER_IP": "127.0.0.1",
  "SERVER_PORT": 25565,
  "MY_SERVER_ID": 123456789012345678,
  "CONSOLE_CHANNEL_ID": 123456789012345678,
  "IDLE_TIMEOUT": 30,
  "ENABLE_CONSOLE_LOGS": true,
  "PUBLIC_IP": "your.domain.or.ip"
}
```

| Key | Required | Description |
|---|---|---|
| `TOKEN` | ✅ | Your Discord bot token |
| `START_SCRIPT` | ✅ | Absolute path to your server start script |
| `SERVER_DIR` | ✅ | Working directory for the start script |
| `SERVER_IP` | ✅ | IP the bot uses to ping the server (usually `127.0.0.1`) |
| `SERVER_PORT` | ✅ | Minecraft server port |
| `MY_SERVER_ID` | ✅ | Discord server (guild) ID |
| `CONSOLE_CHANNEL_ID` | ✅ | Channel for console output and bot messages |
| `IDLE_TIMEOUT` | ✅ | Minutes of zero players before auto-shutdown |
| `ANNOUNCE_CHANNEL_ID` | ☑️ | Separate channel for online/offline announcements (defaults to `CONSOLE_CHANNEL_ID`) |
| `PUBLIC_IP` | ☑️ | Domain or external IP shown in the "server is online" message (defaults to `SERVER_IP`) |
| `ENABLE_CONSOLE_LOGS` | ☑️ | Show colored bot logs in the terminal (default: `true`) |
| `CONSOLE_BUFFER_LIMIT` | ☑️ | Max buffered console lines when Discord is unreachable (default: `500`) |

---

## 🚀 Running the Bot

```bash
python mc_bot.py
```

Make sure `config.json` and `messages.json` are in the same directory as the script.

---

## 💬 Commands

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/run` | Everyone | Starts the Minecraft server |
| `/stop` | Admin Only | Stops the Minecraft server |
| `/status` | Everyone | Shows player count, latency, and idle timer |
| `/cmd <command>` | Admin Only | Sends a raw command to the server console |

### Prefix Commands (`.`)

The same actions are also available as prefix commands for legacy use:

| Command | Permission |
|---|---|
| `.run_server` | Everyone |
| `.stop_server` | Admin Only |
| `.server_status` | Everyone |
| `.server_cmd <command>` | Admin Only |

---

## 🖥️ Terminal CLI

When the bot is running, the terminal accepts input:

| Input | Action |
|---|---|
| `console` | Switch to live server console view |
| `bot` | Switch back to bot log view |
| `start` *(in console mode)* | Start the Minecraft server from the terminal |
| Any other text *(in console mode)* | Send the text as a command to the server |

You can also double-click the system tray icon to toggle between views.

---

## ✏️ Customizing Messages

All Discord-facing messages are stored in `messages.json`. Edit any value freely — the bot will pick up changes on the next restart. If the file is missing or malformed, built-in defaults are used automatically.

Available placeholders per message key:

| Key | Placeholders |
|---|---|
| `status_online` | `{online}`, `{maximum}`, `{players}`, `{latency}`, `{idle}`, `{timeout}` |
| `server_start_failed` | `{error}` |
| `cmd_sent` | `{command}` |
| `idle_warning` | `{time_left}`, `{plural}` |
| `server_online_announce` | `{ip}` |

---

## 📁 File Structure

```
mc_bot.py          # Main bot script
config.json        # Your configuration (excluded from version control)
messages.json      # Customizable Discord message strings
logs/              # Auto-created; rotating daily log files (YYYY-MM-DD-N.log)
```

> **Note:** Never commit `config.json` with your real bot token. Add it to `.gitignore`.

---

## 🔒 Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, enable **Message Content Intent**.
3. Under **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes, and grant **Administrator** permissions (or at minimum: Send Messages, Read Message History, Use Slash Commands).
4. Invite the bot to your server with the generated URL.
5. Copy your bot token into `config.json`.

---

## 📝 License

MIT — use and modify freely.
