# 安全問題回報

> **English summary:** Report suspected vulnerabilities privately with a fictional reproduction and sanitized environment details. This preview offers no response-time SLA; never publish credentials or private evidence in an issue.

使用真實資料前，先讀 [隱私邊界](docs/privacy.md)。此技術預覽版沒有保證回覆時間，也沒有支援版本的 SLA。

若 repo 已啟用私人漏洞回報管道，請使用該管道。若尚未提供，請只開一張要求私人回報管道的 issue，不要附攻擊內容、憑證、識別 ID、私人內容或日誌。不要為了讓問題被注意而公開敏感證據。

請提供最小虛構重現案例、受影響版本、作業系統／原生 CLI 版本，以及失效的邊界。已暴露的憑證須向對應服務撤銷；從最新 commit 刪除檔案不會移除先前副本。未備妥復原方案並與維護者明確協調前，不要重寫歷史或執行破壞性清理。
