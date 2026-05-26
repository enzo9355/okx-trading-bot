# GCP Compute Engine Deployment

This deployment path runs the bot on a Compute Engine VM with `systemd`.

Use this for a 24/7 trading process. Do not deploy this project as a normal Cloud Run service unless you add an HTTP server, because the current bot is a long-running CLI process.

## Recommended VM

- OS: Ubuntu 24.04 LTS
- Machine type: `e2-micro` for sandbox testing, larger machine for production
- Region: use a US free-tier region if cost is the priority, or choose a region with stable latency to OKX
- Network: reserve a static external IP if you plan to use OKX IP allowlisting

## Create The VM

Example using `gcloud`:

```bash
gcloud compute instances create okx-bot-vm \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB \
  --tags=okx-bot
```

SSH into the VM:

```bash
gcloud compute ssh okx-bot-vm --zone=us-central1-a
```

## Install The Bot

Run this on the VM:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/enzo9355/okx-trading-bot.git
cd okx-trading-bot
sudo BOT_MODE=both bash deploy/gcp/setup.sh
```

`BOT_MODE` can be:

```text
spot
futures
both
```

## Configure Secrets

Edit the environment file on the VM:

```bash
sudo nano /etc/okx-trading-bot/okx-bot.env
```

Required values:

```env
API_KEY=your_okx_api_key
SECRET_KEY=your_okx_secret_key
PASSPHRASE=your_okx_api_passphrase
SANDBOX_MODE=true
DRY_RUN=true
```

Keep `SANDBOX_MODE=true` and `DRY_RUN=true` until you have checked logs and order behavior.

## Start And Monitor

```bash
sudo systemctl start okx-bot
sudo systemctl status okx-bot
sudo journalctl -u okx-bot -f
```

Restart after changing `.env` values:

```bash
sudo systemctl restart okx-bot
```

Stop the bot:

```bash
sudo systemctl stop okx-bot
```

## Update From GitHub

```bash
cd ~/okx-trading-bot
git pull
sudo BOT_MODE=both bash deploy/gcp/setup.sh
sudo systemctl restart okx-bot
```

The setup script installs the latest code into `/opt/okx-trading-bot` and keeps secrets in `/etc/okx-trading-bot/okx-bot.env`.
