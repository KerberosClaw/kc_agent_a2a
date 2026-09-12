# Agent A2A 維護指引

> **English summary:** Maintain public mechanism code with fictional fixtures, preserved runtime state, and verified delivery boundaries. Technical documentation uses an English summary followed by Traditional Chinese prose; the separate English README remains English.

本庫保存公開的機制程式碼。不要扮演測試中的虛構人格，也不要把個人資料加進 prompt。

修改前先讀 [文件索引](docs/index.md)、受影響的契約與測試。Runtime 放在 Git 之外；保留既有狀態，分開保存程式與核准的私人內容。測試使用虛構資料，離線測試不需要呼叫真實模型或 Discord。

文件使用 Markdown，圖表使用 Mermaid。技術文件以正體中文（臺灣用語）撰寫，開頭附簡短英文摘要；獨立英文 `README.md` 維持英文，與 `README_zh.md` 同步內容並互連。指令、程式碼、API／schema 識別字與授權原文保留原樣。維護 repo 相對連結，並執行 [文件與程式驗證](docs/testing.md)。程序啟動不等於 READY，摘要發布也不等於原生主對話已讀回。修改送達或隱私機制時，要驗證相關失敗與復原情境。

使用 pull request。不得 force-push 共用歷史、重設無關改動、刪除正式帳本，或因修改公開 repo 就順便部署到既有私人 runtime。
