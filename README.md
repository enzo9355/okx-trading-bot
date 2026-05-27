# OKX Auto Trading Bot

Python + CCXT 的 OKX 自動交易 bot，支援多幣種現貨與 USDT 永續合約。預設使用 OKX sandbox / demo trading。

## 功能

- 現貨：支援多個 `*/USDT` 交易對
- 合約：支援多個 `*/USDT:USDT` USDT 永續合約
- 現貨支援市價單與限價單
- 合約支援開多、開空、平倉
- 合約預設逐倉模式，固定 3x 槓桿
- 合約開倉附帶 `stopLoss` 與 `takeProfit`
- 策略：每個交易對獨立跑 MA5 / MA20 均線交叉
- 風控：單筆 5% 倉位上限、20% 保證金率保護、10% 每日虧損停機

## 本機安裝

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

填入 `.env`：

```env
API_KEY=your_okx_api_key
SECRET_KEY=your_okx_secret_key
PASSPHRASE=your_okx_api_passphrase
SANDBOX_MODE=true
DRY_RUN=true
```

## 多幣種設定

使用逗號分隔交易對：

```env
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT
FUTURES_SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,DOGE/USDT:USDT
```

注意：

- `SPOT_SYMBOLS` 用現貨格式，例如 `ETH/USDT`
- `FUTURES_SYMBOLS` 用 OKX USDT 永續格式，例如 `ETH/USDT:USDT`
- 每個 symbol 會建立獨立 worker
- 目前策略會交易符合 MA5 / MA20 訊號的 symbol，不保證最大獲利
- 幣種越多，API 呼叫與同時持倉風險越高

舊設定仍可用：

```env
SPOT_SYMBOL=BTC/USDT
FUTURES_SYMBOL=BTC/USDT:USDT
```

但多幣種建議使用 `SPOT_SYMBOLS` 與 `FUTURES_SYMBOLS`。

## 執行

```bash
python main.py --mode spot
python main.py --mode futures
python main.py --mode both
```

測試指令：

```bash
python main.py --mode spot --once --dry-run
python main.py --mode futures --once --dry-run
python main.py --mode both --dry-run
```

## GCP 部署

建議用 Compute Engine VM + systemd，不建議直接部署成一般 Cloud Run Service。原因是這個 bot 是長時間常駐的 CLI 程式，不是 HTTP server。

GCP 部署檔在：

```text
deploy/gcp/
```

快速流程：

```bash
git clone https://github.com/enzo9355/okx-trading-bot.git
cd okx-trading-bot
sudo BOT_MODE=both bash deploy/gcp/setup.sh
sudo nano /etc/okx-trading-bot/okx-bot.env
sudo systemctl start okx-bot
sudo journalctl -u okx-bot -f
```

完整說明見 `deploy/gcp/README.md`。

## 風控

- 單筆最大名目倉位：總資金 5%
- 合約保證金率低於 20%：強制平倉
- 每日最大虧損 10%：停止新交易
- 每日風控狀態預設寫入 `data/risk_state.json`

## 注意

這是可執行骨架，不是獲利保證。正式切到實盤前，至少要確認：

- OKX API 權限只開必要範圍
- 先用 `SANDBOX_MODE=true` 與 `DRY_RUN=true` 跑完測試
- 確認 OKX 帳戶倉位模式與 `.env` 的 `FUTURES_POSITION_MODE` 一致
- 確認 `stopLoss` / `takeProfit` 在你的 OKX 帳戶模式中可正常建立
- 實盤前應使用固定 IP 並在 OKX 設定 API IP 白名單
