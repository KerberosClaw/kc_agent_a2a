# 送達與經歷延續流程

> **English summary:** Human interruptions invalidate stale Party decisions, and uncertain sends wait for reconciliation. Continuity freezes source revisions, summarizes them in independent sessions, and distinguishes native readback from explicit canonical assessment.

[文件索引](index.md) · [介面契約](interfaces.md)

```mermaid
sequenceDiagram
    participant Human as 真人
    participant State as Party 帳本
    participant Model as 原生 worker
    participant Discord
    Human->>State: 已註冊真人的訊息
    State->>State: 去重、更新 epoch、重設獲准額度
    State->>Model: 預留一次有界決定
    Human->>State: 新訊息或暫停
    State->>State: 推進 epoch 或暫停
    Model-->>State: 結構化 speak 或 wait
    State->>State: 重查 epoch、授權、額度與暫停
    alt 仍有授權
        State->>Discord: 送出已持久化的 outbox
        Discord-->>State: 確認訊息 ID
    else 過期或結果不明
        State->>State: 丟棄或保留待查，不盲目重送
    end
```

一般 bot 互相傳訊不會補回額度。真人插話會讓生成中的舊決定失效，下一個決定便能接上新主題。無法確認送達的訊息會保留等待對帳；重啟程序不會給出新的可用額度。

```mermaid
sequenceDiagram
    participant Chat as 已接受的房間歷史
    participant Ledger as 經歷延續 SQLite
    participant Job as 獨立摘要 session
    participant Main as 主人格 session
    participant Git as 正式人格私人 Git
    Chat->>Ledger: 加入來源身分與修訂
    Ledger->>Job: 凍結未涵蓋分段與租約 token
    Job-->>Ledger: 有來源歸屬的結構化摘要
    Ledger->>Ledger: 驗證引用並原子發布
    Ledger->>Job: 需要時彙整待讀批次，限制大小
    Main->>Ledger: 領取可用摘要脈絡
    Main-->>Ledger: 原生逐字稿回執確認已讀
    Main->>Ledger: 明確開始存檔並凍結批次引用
    Main->>Git: 只寫評估後的 patch 或 journal
    Git-->>Ledger: 已核對的加密 commit
    Ledger->>Ledger: 標記該批次已評估
```

只用上次時間戳切割，可能漏掉晚到事件。來源身分使用 `channel_id/message_id` 加上內容修訂指紋；每個人格分別追蹤批次實際涵蓋的來源。預設分段條件是閒置 20 分鐘、最長經過兩小時、累積 60 則訊息，或達到 24,000 字元上限。輸入在資料庫交易內凍結，模型與 Git 呼叫不會一直占住該交易。

租約與 token 會拒絕過期 worker。主人格尚未回來時，可先把待讀分段彙整成總摘要；原始分段仍保留作為來源。`received` 表示原生回合已讀取批次；`assessed` 表示明確觸發的正式存檔已評估該批次。不能只憑工作回報成功就推定這兩個狀態。

帳本可接收編輯／刪除修訂，但預覽版 Discord connector 尚不保證完整接收編輯／刪除事件。局部歷史查詢少了一筆，不代表該訊息被刪除。詳見 [發布限制](release.md)。
