# binance_trading_bot

幣安(Binance)量化交易機器人專案。目前為初始空架構,技術棧(Python / Node.js 等)與策略細節尚未決定,待後續開發時再補齊。

A Binance quantitative trading bot project. Currently an empty starter scaffold — the tech stack (Python / Node.js / etc.) and strategy details have not been decided yet and will be filled in as development progresses.

## 規劃方向 / Planned Scope

尚未定案,預期會涵蓋以下模組(實際結構將依選定技術棧調整):

Not finalized yet; the project is expected to eventually cover the following areas (actual structure will depend on the chosen tech stack):

- **資料取得 / Market data** — 透過 Binance API 取得即時/歷史行情
- **策略邏輯 / Strategy logic** — 訊號產生與交易決策
- **回測 / Backtesting** — 在歷史資料上驗證策略表現
- **下單執行 / Order execution** — 對接 Binance API 送出/管理訂單
- **風險控管 / Risk management** — 部位大小、停損停利、異常處理

## ⚠️ 安全注意事項 / Security Notes

- **絕對不要將 API Key / Secret 提交到 git**。請使用環境變數或 `.env` 檔案(已加入 `.gitignore`),並參考 `.env.example` 建立自己的本機設定。
  **Never commit API keys or secrets to git.** Use environment variables or a `.env` file (already excluded via `.gitignore`); copy `.env.example` to create your local config.
- 建議先在 Binance **Testnet**(測試網)驗證策略與下單邏輯,確認無誤後再切換到正式環境。
  It is strongly recommended to validate strategies and order logic on Binance **Testnet** before switching to production/mainnet.
- 建立 API Key 時,若非必要請**不要開啟提幣權限**,並視情況搭配 IP 白名單。
  When creating API keys, avoid enabling withdrawal permissions unless strictly necessary, and consider IP whitelisting.

## 目前狀態 / Current Status

專案剛建立,尚無程式碼。技術棧與目錄結構待決定後補上。

Freshly created, no code yet. Tech stack and directory layout to be added once decided.
