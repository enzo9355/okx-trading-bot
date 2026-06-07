# OKX Auto Trading Bot

這是一個使用 Python 與 CCXT 串接 OKX 的自動交易 bot，可同時跑現貨與 USDT 永續合約。預設支援 OKX sandbox / demo trading，建議先用 `DRY_RUN=true` 觀察 log，再切到真實下單。

## 目前交易策略

目前策略不是 AI 預測，也不是直接讀 Bitcoin、Ethereum、Dogecoin、Solana、Chia 節點資料；它是技術指標策略：

1. MA5 / MA20 均線交叉產生初始訊號。
2. RSI 過濾過熱與過冷區域，避免在太高的位置追買、太低的位置追空。
3. ATR 波動率過濾，避免市場太安靜時被雜訊洗來洗去，也避免劇烈波動時追進去。
4. MA20 斜率確認趨勢品質，只有慢均線方向支持時才接受訊號。

訊號含義：

- `buy`: 現貨買入；合約做多。
- `sell`: 現貨賣出既有持倉；合約做空。
- `hold`: 不交易。

log 會顯示 `reason`，方便你理解為什麼交易或不交易：

- `ma_cross`: 均線交叉訊號通過所有檢查。
- `rsi_filter`: RSI 顯示過熱或過冷，因此不追單。
- `low_volatility`: ATR 太低，市場可能沒有足夠波動。
- `high_volatility`: ATR 太高，市場可能處於劇烈波動。
- `weak_uptrend` / `weak_downtrend`: 慢均線方向不夠支持交易。

## 風險控管

- 單筆最大名目倉位預設為帳戶權益的 5%。
- 每日最大虧損預設為 10%，達到後當天停止交易。
- 合約固定使用 isolated margin 與 3x leverage。
- 合約開倉會附帶 `stopLoss` 與 `takeProfit`。
- 合約會檢查 margin ratio，低於門檻時嘗試平倉。
- 每日風控狀態寫入 `data/risk_state.json`。

## 安裝

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

編輯 `.env`：

```env
API_KEY=your_okx_api_key
SECRET_KEY=your_okx_secret_key
PASSPHRASE=your_okx_api_passphrase
SANDBOX_MODE=true
DRY_RUN=true
```

## 重要設定

交易標的：

```env
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT
FUTURES_SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,DOGE/USDT:USDT
TIMEFRAME=1m
OHLCV_LIMIT=50
```

風險：

```env
RISK_MAX_POSITION_PCT=0.05
RISK_DAILY_MAX_LOSS_PCT=0.10
RISK_MARGIN_RATIO_THRESHOLD=0.20
```

合約：

```env
FUTURES_MARGIN_MODE=isolated
FUTURES_LEVERAGE=3
FUTURES_POSITION_MODE=net
FUTURES_STOP_LOSS_PCT=0.0075
FUTURES_TAKE_PROFIT_PCT=0.015
```

策略濾網：

```env
RSI_PERIOD=14
RSI_OVERBOUGHT=70
RSI_OVERSOLD=30
ATR_PERIOD=14
ATR_MIN_PCT=0.001
ATR_MAX_PCT=0.05
MA_MIN_TREND_SLOPE_PCT=0.0001
```

調參方向：

- 想要更少交易：提高 `ATR_MIN_PCT` 或 `MA_MIN_TREND_SLOPE_PCT`。
- 想要避開暴漲暴跌：降低 `ATR_MAX_PCT`。
- 想要 RSI 更保守：降低 `RSI_OVERBOUGHT`，提高 `RSI_OVERSOLD`。
- 想要觀察較長週期：把 `TIMEFRAME` 改成 `5m` 或 `15m`，並先用 dry-run。

## 執行

```bash
python main.py --mode spot
python main.py --mode futures
python main.py --mode both
```

測試單次 dry-run：

```bash
python main.py --mode spot --once --dry-run
python main.py --mode futures --once --dry-run
python main.py --mode both --once --dry-run
```

## GCP 需要串接什麼

如果你把 bot 架在 GCP VM 上，通常不需要串接 Bitcoin、Ethereum、Dogecoin、Solana 或 Chia 節點。這個 bot 的實際資料與下單來源是 OKX，所以需要的是：

- OKX API key / secret / passphrase。
- OKX API 權限：讀取帳戶、讀取行情、交易下單。
- 如果 OKX API 有 IP whitelist，要把 GCP VM 的外部固定 IP 加進白名單。
- VM 上的 `/etc/okx-trading-bot/okx-bot.env` 要放正確 `.env` 設定。
- 更新程式後要重啟 systemd 服務。

常見 GCP 更新流程：

```bash
cd /opt/okx-trading-bot
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart okx-bot
sudo journalctl -u okx-bot -f
```

如果你是用這個 repo 的 `deploy/gcp/setup.sh` 建置，環境變數檔預設在：

```text
/etc/okx-trading-bot/okx-bot.env
```

## 安全提醒

- 一開始請保持 `SANDBOX_MODE=true` 與 `DRY_RUN=true`。
- 確認 log 中的 `signal`、`reason`、`atr_pct`、`slow_slope_pct` 都合理後，再考慮真實下單。
- 永遠不要把 OKX API secret commit 到 GitHub。
- 合約交易風險高，建議先只跑現貨或極小資金。
