# 參與開發

> **English summary:** Use pull requests, fictional fixtures, and targeted failure/recovery tests. Preserve runtime state and document changes in Traditional Chinese with an English summary, keeping both READMEs aligned.

從 [文件索引](docs/index.md) 開始，再讀要修改元件的契約與測試。使用 Python 3.12+、虛構測試資料及暫存狀態。文件用 Markdown，圖表用 Mermaid；新增頁面時一併更新導覽連結。技術文件正文使用正體中文（臺灣用語），開頭附簡短英文摘要。獨立英文 README 保持英文，並與正體中文 README 描述相同行為；指令、程式碼、API／schema 識別字及授權原文不翻譯。

執行 [驗證指令](docs/testing.md)。修改授權、受眾、送達、回執或正式人格存檔時，需要失敗／復原測試，不能只提供成功畫面。不要把個人軼事加進 prompt，也不要把額度上限當成必須回滿的句數。

使用工作分支與 pull request，描述修改後的行為、驗證及剩餘限制。不要為了移除一般作者署名而重寫共用歷史。Runtime 檔案和憑證不得提交；疑似外洩依 [安全問題回報](SECURITY.md) 處理。

本預覽版的穩定邊界是文件記載的行為，不保證每個內部 Python 函式簽章都不變。影響相容性的變更應先提出討論，不要默默遷移既有狀態。保留帳本與無關的 dirty 檔案，不能靠重設使用者狀態來修升級問題。
