# OKX Auto Trading Bot

Python + CCXT 的 OKX 自動交易 bot，支援現貨與 USDT 永續合約。預設使用 OKX sandbox / demo trading。

## 安裝

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
```

## 執行

```bash
python main.py --mode spot
python main.py --mode futures
python main.py --mode both
```

常用參數：

```bash
python main.py --mode spot --once
python main.py --mode futures --interval 30
python main.py --mode both --dry-run
```

## 策略

- 現貨：`BTC/USDT`，MA5 / MA20 均線交叉。
- 黃金交叉：市價買入。
- 死亡交叉：市價賣出。
- 合約：`BTC/USDT:USDT`，逐倉、3x 槓桿。
- 合約黃金交叉：開多。
- 合約死亡交叉：開空。
- 每筆合約開倉都會帶 `stopLoss` 與 `takeProfit`。

## 風控

- 單筆最大名目倉位：總資金 5%。
- 合約保證金率低於 20%：強制平倉。
- 每日最大虧損 10%：停止新交易。

## 注意

這是可執行骨架，不是獲利保證。正式切到實盤前，請至少確認：

- OKX API 權限只開必要範圍。
- sandbox 交易與實盤交易對、最小下單量、合約張數精度一致。
- 帳戶倉位模式是 `net`，或把 `.env` 的 `FUTURES_POSITION_MODE` 改成 `long_short`。
- `stopLoss` / `takeProfit` 是否符合你目前 OKX 帳戶模式。

