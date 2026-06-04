# lawyer-liff

王律行政系統「LINE 打卡」LIFF 靜態頁（公開託管用）。

- 內容：單一自包含 `index.html`（LINE LIFF SDK + GPS + JSONP→GAS /exec）。
- **本 repo 刻意公開**（GitHub Pages 服務 LINE 內開啟的打卡頁需公開）。
- **絕不放任何 secret**：頁面只含公開的 GAS /exec 端點與 LIFF ID，無 Notion/LINE token。
- 服務網址：https://windsorya.github.io/lawyer-liff/
- 主程式碼（含 token、走 Script Properties）在另一個 **private** repo `lawyer-scripts`。
