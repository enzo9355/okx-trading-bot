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
- 每日最大虧損預設為 10%，達到後當天停止新開倉，但既有部位的停損、平倉與對帳仍持續執行。
- 合約固定使用 isolated margin 與 3x leverage。
- 合約開倉會附帶 `stopLoss` 與 `takeProfit`。
- 合約會檢查 margin ratio，低於門檻時嘗試平倉。
- 每日風控狀態寫入 `data/risk_state.json`。
- 單筆訂單若因精度進位、低於交易所最小量或暫時抓不到價格而無法送出，bot 只會跳過該筆並繼續運作。每日虧損上限只封鎖新開倉；只有風控狀態檔或持倉 registry 無法可靠讀寫時才會整台停機。
- **現貨停損**：bot 自己買入的現貨部位，若價格跌破加權平均進場價的 `SPOT_STOP_LOSS_PCT`（預設 2%），每個交易週期檢查一次並市價出場。只賣 bot 自己開的量，不動你原本的持倉。
- **最大同時持倉數**：現貨 + 合約合計超過 `MAX_OPEN_POSITIONS`（預設 3）就不再開新倉，避免在趨勢行情中對高度相關的幣種過度曝險。
- **停損後冷卻**：任何停損出場（含合約交易所端 SL 觸發）後，同一交易對 `STOP_OUT_COOLDOWN_SECONDS`（預設 15 分鐘）內不再進場，避免報復性交易。
- **單筆金額硬上限**：`MAX_ORDER_NOTIONAL_USDT` 設大於 0 時，單筆訂單金額不得超過此 USDT 值，與 % 上限獨立（0 = 停用）。
- 持倉與冷卻狀態存於 `data/positions.json`，重啟不會遺失。


## 回測

[#回測](#回測)

`backtest/` 模組用與實盤完全相同的訊號函式重放歷史 K 線（OKX 公開行情，不需 API key）：

```bash
.venv/bin/python -m backtest.run --symbol BTC/USDT --timeframe 15m --days 30
.venv/bin/python -m backtest.run --symbol BTC/USDT --timeframe 1m --days 7 --stop-loss 0.02
```

輸出包含交易次數、勝率、策略報酬、**買入持有基準**、最大回撤與手續費成本。改任何策略參數前先回測，並與買入持有比較——跑輸持有就是在白繳手續費。

另外兩個工具：
- `scripts/report.py`：把 `data/trades.csv` 配對成完整進出場回合，計算實際勝率與毛損益。
- `scripts/verify_sl_attachment.py`：在模擬倉下一筆極小實單，驗證合約停損真的有掛到 OKX 伺服器上（上真錢前必跑）。


## 交易紀錄

[#交易紀錄](#交易紀錄)

每次下單（包含 dry-run 的模擬下單）都會記錄一行到 `data/trades.csv`，欄位包含時間、現貨/合約、交易對、方向、訊號原因（`reason`）、數量、價格、RSI、ATR%、慢均線斜率、訂單編號，以及當下是否為 sandbox / dry-run。

這個紀錄是日後評估策略績效、或比較多種策略好壞的基礎：要判斷一個新策略是否比較好，必須先有目前策略實際做了什麼的紀錄。`data/` 已被 `.gitignore` 忽略，所以 `trades.csv` 只存在本機，不會被推上公開的 GitHub。

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
MA_MIN_TREND_SLOPE_PCT=0.0003
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
