# 隱私與信任邊界

> **English summary:** Sharing approvals bind specific content, rooms, and audiences. Model review and source-write guards have limits; keep private data outside this repository, and treat encryption, consent, retention, and revocation as distinct responsibilities.

[文件索引](index.md) · [安全問題回報](../SECURITY.md) · [整合](integration.md)

## 哪些資料可以跨越邊界

| 來源 | 使用者／程序 | 授權與限制 |
| --- | --- | --- |
| 真人撰寫的紙條本文 | 指定收件者的原生 session | 當前回合明確授權轉達；驗證回執後才移除本文 |
| 自己的基線與所有現行 patches | 自己的夜聊原生 session | 明確設定來源根目錄，禁止寫入來源 |
| 正式私人原人格 | 私人整理器與模型審查 | 明確啟用共享；能讀私人來源不等於獲准向房間揭露 |
| 核准的人格卡／視圖 | 房間原生 worker | Hash 綁定的 manifest，以及確切房間與受眾 |
| 房間歷史 | 摘要 worker 與該人格主對話 | 有歸屬的已接受事件、授權歷史時間範圍、限定讀回範圍 |
| 公開 poke 話題／非私人夜聊素材 | 先整理器，再到房間 | 指定來源類型、確切出處、獨立模型審查 |
| 原生結構化回覆 | Discord | 送出前重查授權／epoch／額度並驗證工具軌跡 |

啟用共享允許整理抽象語氣／互動特徵、已審查人格卡中的事實、公開話題及非私人對話素材，不代表核准新的個人事實、私密引文、健康／工作／位置細節，或其他參與者的私人歷史。兩階段模型審查可能誤判，不能證明絕不外洩。敏感用途可維持共享停用，改用人工審查的靜態人格卡。

群聊資料是不受信任的輸入，不能授權操作電腦、擴充收件者、改人格根目錄、確認紙條回執或提交正式人格存檔。摘要與整理工作停用網頁工具。房間網頁查詢是選用功能，只限允許的原生工具；查詢可能被供應者看見，也可能遇到惡意網頁。工具軌跡檢查會拒絕未授權操作，但本版不宣稱原生 worker 是能抵禦遭入侵 CLI 的安全沙箱。

夜聊來源防護禁止寫入設定中的來源路徑，仍允許原生 CLI 所需的其他主機行為。受信任協調程式的檔案存取、本機帳號遭入侵、惡意依賴及供應者端資料處理，是另外的風險。評估時請使用獨立作業系統帳號／測試房間。

## 儲存與公開發布

公開原始碼只含通用樣板及合成測試。Token、本機 ID、審查卡、聊天、SQLite 狀態、日誌、正式人格包與加密 key 都保留在 repo 外。`.gitignore` 不是保證，每次公開 commit 前仍須檢查 staging tree。

Token 存在權限為 700 的目錄內，以權限 600 的本機檔保存。Token helper 不回顯，拒絕覆寫及 symlink 目標；輪替由操作者明確執行。Git-crypt 保護已提交的內容 blob；**檔名、commit metadata、解鎖的檔案與本機 SQLite 對該帳號仍然可讀**。Commit message 不要包含私人細節，Git 備份須指向另行確認為 private 的 remote。

來源訊息、摘要與存檔可能含第三方資料。須取得對預定受眾分享的同意，新增成員時也包含舊歷史。不可從顯示名稱推定身分、代名詞或同意。獨立人格放在各自根目錄，不得把另一隻的 patch 語料當作自己的載入。

預覽版不會在 Discord 編輯／刪除後自動刪掉所有衍生副本，也不承諾密碼學抹除。保存期限與撤回機制由操作者規劃，詳見 [發布限制](release.md)。
