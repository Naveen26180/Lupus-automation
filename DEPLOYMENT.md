# Deployment — Oracle Cloud Always-Free VPS

This guide takes the bot from "running on your laptop" to "running 24/7 on a free
server in the cloud". It is written for someone who has never used a cloud server
before — every concept is explained in plain language as we go.

**Total time:** ~1 hour, most of it one-time setup. After that the bot runs
forever with zero monthly cost.

---

## 0. The big picture (read this first)

### What a VPS is
A VPS (Virtual Private Server) is a computer that lives in a data center and is
always on. You connect to it over the internet, copy your code onto it, and run
your bot there. Your laptop can go to sleep, restart, or die — the server keeps
running.

### Why Oracle Cloud?
Oracle Cloud has a genuinely free "Always Free" tier: a 2-core ARM server with
12 GB of RAM and 200 GB of disk, **free for as long as you keep the account**.
(Note: in July 2026 Oracle halved the free allowance from 4 OCPU / 24 GB to
2 OCPU / 12 GB — the numbers in this guide already reflect the new limit.)
It is the only major cloud that gives a real always-on server for $0. It's the
same kind of machine you'd get from a paid provider, just free.

It also matters for your long-term plan: 12 GB of RAM is still enough to run a
small quantized local LLM (e.g. a 7B model via Ollama, ~5 GB) later, so the
same server can host both the bot and your future local model.

### How the pieces fit together

```
Your laptop ──(SSH)──> Oracle VPS (always on)
                          │
                          ├── runs main.py as a systemd service (auto-restart)
                          ├── reads .env (Telegram token, Gemini key, Google IDs)
                          ├── uses the service-account JSON for Drive/Sheets
                          └── polls Telegram for messages (outbound only)
```

- **SSH** = the secure way to control a remote computer from your terminal.
- **systemd** = the built-in tool on Linux that keeps a program running — if it
  crashes, systemd restarts it. It also starts the bot automatically when the
  server boots.
- **The bot needs no open ports and no website** — it only makes outgoing
  connections (to Telegram, Gemini, Google). Nothing to configure on a firewall.

---

## 1. What you need before starting

| Thing | Where to get it | Do you have it? |
|---|---|---|
| GitHub account with the `Lupus-automation` repo (private) | Done — already pushed | ✅ |
| Telegram bot token (`BOT_TOKEN`) | BotFather on Telegram | Get if missing |
| Gemini API key (`GEMINI_API_KEY`) | Google AI Studio → API Keys | Get if missing |
| Google service-account JSON | `gen-lang-client-0805362925-6909de6a53cf.json` on your laptop | ✅ |
| Google Sheet ID + 4 Drive folder IDs | From the sheet/folder URLs (your `.env` has them) | ✅ |
| A credit/debit card | For Oracle's free signup — **never charged** (see below) | — |

**About the card:** Oracle requires a card to create an account (to prevent
abuse). They do not charge it. You may see a temporary ~$1 authorization that
disappears after a few days. Debit cards sometimes get rejected — if yours does,
try a different card or a prepaid one.

---

## 2. Create the Oracle Cloud account

1. Go to **oracle.com/cloud/free** → click **Start for free**.
2. Fill in your details (email, password, country). You'll get a verification
   email — click the link.
3. Enter the card + billing address. (This is the step that can be finicky —
   if it fails, retry or use another card.)
4. **Choose your home region.** Pick the one closest to you (e.g. Mumbai if
   you're in India). ⚠️ **You cannot change the home region later** — the free
   ARM server is only guaranteed in your home region, so pick carefully now.
5. Sign in at **cloud.oracle.com**.

---

## 3. Create the free VM (the server)

1. In the Oracle console, go to the hamburger menu → **Compute** → **Instances**.
2. Click **Create instance**.
3. **Name:** `lupus-bot`
4. **Placement:** leave default (any availability domain).
5. **Image:** click **Change image** → select **Canonical Ubuntu 24.04** →
   **Select image**. (Any recent Ubuntu LTS works; this guide assumes Ubuntu.)
6. **Shape:** click **Change shape** → check **"Specialty and legacy"** /
   select **Ampere → VM.Standard.A1.Flex** (the Always Free ARM shape).
   Set **OCPUs: 2** and **RAM: 12 GB**.
   - If you get *"Out of capacity"* on create: retry in a **different
     availability domain**, and keep retrying over the next few days (capacity
     frees up in waves). 2 OCPU / 12 GB is the current free maximum.
7. **SSH keys:** this is how you'll log in. Choose **"Generate a key pair for
   me"** → Oracle downloads a private key (`.key`) — **save it somewhere safe**,
   you'll use it to connect. (Or paste your own public key if you already have
   one.)
8. **Boot volume:** leave default (50 GB) — or set up to 100 GB if you want
   room for a local LLM later. Always Free includes 200 GB total.
9. Click **Create**. Wait ~1 minute for the instance to go to **Running**.
10. Copy the **Public IP address** shown on the instance page. Keep it handy.

> 💡 **You can also use Windows Terminal or PowerShell instead of Git Bash for
> SSH** — anything that can run `ssh` works. All commands in this guide are
> bash, so if you're on Windows use **Git Bash** (which you already have).

---

## 4. First connection (SSH)

From **Git Bash on your laptop**, connect to the server. If you downloaded the
key pair from Oracle, it's saved somewhere like `~/Downloads/`:

```bash
ssh -i ~/Downloads/lupus-bot.key ubuntu@YOUR_PUBLIC_IP
```

(On Windows, you may need to `chmod 600 ~/Downloads/lupus-bot.key` first — run
`chmod 600` only if ssh complains about permissions.)

The first time, ssh will ask *"Are you sure you want to continue connecting?"* —
type `yes` and press Enter.

You should now see a prompt like `ubuntu@lupus-bot:~$`. **You are now inside
your free server.** Every command from here on runs on the server (the
`$` prompt), not your laptop.

---

## 5. Copy your code onto the server

The server needs read access to your **private** GitHub repo. The clean way is
a **deploy key** — a special SSH key that the server uses only to read this one
repo.

### 5a. Create a deploy key on the server

While logged into the server:

```bash
ssh-keygen -t ed25519 -C "lupus-bot-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Copy the whole output (starts with `ssh-ed25519 AAAA...`).

### 5b. Add it to GitHub

On github.com → **Lupus-automation** → **Settings** → **Deploy keys** →
**Add deploy key**:
- **Title:** `oracle-vps`
- **Key:** paste the copied line
- ⚠️ Leave **"Allow write access"** **unchecked** — read-only is all it needs.

### 5c. Clone the repo (on the server)

```bash
cd /opt
sudo git clone git@github.com:Naveen26180/Lupus-automation.git
sudo chown -R ubuntu:ubuntu /opt/Lupus-automation
cd /opt/Lupus-automation
```

(If git asks about host authenticity, type `yes`.)

---

## 6. Install dependencies — one command

I've packaged the whole install into a script in the repo:

```bash
bash deploy/setup.sh
```

This installs Python tooling, creates a virtual environment, installs the pinned
dependencies, creates `.env` from the template, and installs the systemd
service. It will **not** start the bot yet — that's step 8, after secrets.

---

## 7. Secrets on the server

### 7a. Upload the Google service-account JSON

On your **laptop** (Git Bash), from the folder where the JSON file lives:

```bash
scp "gen-lang-client-0805362925-6909de6a53cf.json" ubuntu@YOUR_PUBLIC_IP:/tmp/
```

Then on the **server**, move it next to the repo:

```bash
sudo mv /tmp/gen-lang-client-0805362925-6909de6a53cf.json /opt/Lupus-automation/
sudo chown ubuntu:ubuntu /opt/Lupus-automation/gen-lang-client-0805362925-6909de6a53cf.json
```

### 7b. Fill in .env

On the server:

```bash
cd /opt/Lupus-automation
nano .env
```

Set these (copy from your laptop's `.env`):

```
BOT_TOKEN=<your Telegram token>
GEMINI_API_KEY=<your Gemini key>
AI_CLASSIFICATION_ENABLED=false        # flip to true when you want Pass 2
GOOGLE_DRIVE_CREDENTIALS=gen-lang-client-0805362925-6909de6a53cf.json
GOOGLE_SHEET_ID=<your sheet ID>
INCOMING_FOLDER_ID=<...>
PROCESSED_FOLDER_ID=<...>
DUPLICATE_FOLDER_ID=<...>
REJECTED_FOLDER_ID=<...>
LOG_LEVEL=INFO                          # INFO, never DEBUG on the server
ENRICHMENT_ENABLED=false                # keep the scraper paused (as you have it)
```

> The credentials path is **relative** now — the JSON sits next to `.env` in the
> repo folder. Save with **Ctrl+O, Enter**, exit with **Ctrl+X**.

### 7c. Give the service account access to your Google resources

Open your **Google Sheet** → **Share** → add this email (it's inside the JSON,
field `client_email`):
`resumebot@gen-lang-client-0805362925.iam.gserviceaccount.com` → **Editor**.

Do the same for the **4 Drive folders** (Incoming / Processed / Duplicate /
Rejected) → **Editor**. The bot uses the service account for everything, so
without this step it will hit permission errors.

---

## 8. Test run (before going live)

On the server, run the bot once in the foreground to confirm everything is
configured:

```bash
cd /opt/Lupus-automation
venv/bin/python main.py
```

You should see log lines ending in *"Bot is ready — starting polling..."*.
Press **Ctrl+C** to stop.

### ⚠️ Stop your local bot FIRST

Telegram only allows **one** polling connection per token. Your laptop's bot
(still running from earlier) will fight the server bot for updates and you'll
see `Conflict: terminated by other getUpdates request` errors.

**Before the server goes live:** stop the bot on your laptop (Ctrl+C in the
terminal running it, or Task Manager if needed). Also feel free to delete the
11.5 MB `logs/app.log` your laptop bot was holding open.

---

## 9. Go live — run as a service (24/7)

```bash
sudo systemctl enable --now lupus-bot
```

Check it's healthy:

```bash
sudo systemctl status lupus-bot
journalctl -u lupus-bot -f        # live log stream (Ctrl+C to exit)
```

You should see `Active: active (running)` and the startup logs. Send a test
message to your bot on Telegram — it should reply.

**What you now have:**
- The bot runs 24/7, starts automatically on boot, and restarts if it crashes.
- It survives your laptop being off, asleep, or dead.
- All runtime files (`logs/`, `audit/`, `data/`) are created automatically.

---

## 10. Updating the bot later

```bash
cd /opt/Lupus-automation
sudo git pull
sudo systemctl restart lupus-bot
```

Changing a credential (Telegram token, Gemini key, Google folder IDs) = edit
`.env`, then `sudo systemctl restart lupus-bot`. **No code change, no redeploy.**

---

## 11. Troubleshooting

| Symptom | What to check |
|---|---|
| `Conflict: terminated by other getUpdates request` | Another instance is polling with the same token — stop the local bot, restart the service |
| `Active: failed` / bot exits immediately | `journalctl -u lupus-bot -n 50` — usually a missing/empty `.env` value |
| Google permission errors (403 / `insufficientPermissions`) | Service-account email not shared on the sheet/folders (step 7c) |
| `Out of capacity` when creating the VM | Retry, or change OCPU/RAM or availability domain (step 3) |
| Bot slow to reply to first message | Server is waking from idle — rare on Oracle, not a problem |
| Want logs on disk | `LOG_LEVEL=INFO` writes `logs/app.log` automatically |

**Useful commands**

```bash
sudo systemctl restart lupus-bot   # restart the bot
journalctl -u lupus-bot -n 100     # last 100 log lines
sudo reboot                        # reboot the server (bot comes back on its own)
```

---

## 12. Monthly cost: $0

Always Free includes: the ARM VM (2 OCPU / 12 GB — halved from 4 OCPU / 24 GB
in July 2026), 200 GB block storage, and 10 TB of outbound data per month. A Telegram polling bot uses kilobytes of data
per day. There is nothing to "turn off" — just remember to keep the account
active (Oracle warns before it reclaims free resources if an account is
completely idle for a long time; your always-running bot prevents that).

---

## 13. Final checklist

- [ ] Oracle account created, home region chosen, VM running (Ubuntu 24.04, ARM)
- [ ] SSH works: `ssh -i <key> ubuntu@<IP>`
- [ ] Deploy key added to GitHub, repo cloned at `/opt/Lupus-automation`
- [ ] `bash deploy/setup.sh` completed without errors
- [ ] Service-account JSON uploaded next to `.env`
- [ ] `.env` filled (token, Gemini key, IDs, `LOG_LEVEL=INFO`, relative creds path)
- [ ] Service-account email shared as Editor on the Sheet + 4 folders
- [ ] Foreground test run works (`venv/bin/python main.py`)
- [ ] **Local bot stopped**
- [ ] `sudo systemctl enable --now lupus-bot` → `active (running)`
- [ ] Test message through Telegram works
