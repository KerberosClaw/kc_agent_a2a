# 介面與契約

> **English summary:** The preview exposes CLI, Python, hook, and file contracts rather than an HTTP API. Grants bind exact content and audiences; delivery, readback, and explicit canonical saves validate their own authority and evidence.

[文件索引](index.md) · [資料模型](data-model.md) · [維運](operations.md)

本預覽版提供 Python／CLI、原生 hook 與檔案契約，沒有網路 API，不需要安裝 REST endpoint 或 OpenAPI 文件。

| 入口 | 輸入 | 輸出／授權 |
| --- | --- | --- |
| `agent-a2a-demo` | 無 | 合成 JSON 指標，不呼叫外部服務 |
| `setup_preview.py` | 明確根目錄；設定房間時提供房間與參與者 ID | 本機設定與待審查內容；核准使用另一個子指令 |
| 受防護的 `agent-a2a` | `preflight`、`start`、`status`、`tick`、`enable`、`disable`、`stop` | 本機執行狀態；start／tick 可能呼叫真實模型 |
| `agent-note` | 當前原生 hook token、操作與有大小限制的 JSON 本文 | 紙條 ID、受限的 receive／ack 指示，或去敏錯誤 |
| `party.py` | Registry JSON、獨立狀態目錄、受限 token 檔與核准授權 | 本機狀態或真實 Discord client |
| `manage.py` | `start`、`stop`、`status` 與明確 runtime | 所管理的程序紀錄；start 不等於 READY |
| `continuity.py` | 設定、`tick`、`status`、`save-*` 操作 | 摘要工作、帳本狀態與明確存檔生命週期 |

旗標以 CLI help 為準。適合的指令會輸出 JSON，拒絕操作時回傳非零 exit code。部分 runtime 狀態報告含私人內容，即使錯誤訊息已去敏，整份輸出仍應視為私人資料。

## Registry 與核准

Registry 包含 `guild_id`、`channel_id`、`human_ids`、`bot_owners`、`self_id` 與 `protocol: 1`。ID 使用十進位字串；恰好兩個 bot 身分對應 `agent_a`／`agent_b`，manifest 必須與它們及已註冊受眾相符。`intended_scope`、`approved_by`、`approved_at`，以及兩份人格卡和 `room_boundary.md` 的 SHA-256 hash，會把授權綁定到確切內容。`review/current` 選擇目前有效的核准 revision。

Manifest 改變會使執行中的授權失效。擴充受眾需要明確的歷史分享欄位與狀態遷移；改顯示名稱不代表加入成員。內部相容性由 registry／schema 檢查維持，不代表承諾穩定的 plugin API。

## 原生決定與 hook

Party 依 adapter 的結構化輸出 schema 回傳 `request_id`、`action`（`speak` 或 `wait`）與 `content`。送出前檢查請求身分、內容限制與允許的工具軌跡。輸入帶有作者／訊息 ID 及時間，模型不可捏造來源引用。完整 schema 與允許清單在 [native.py](../src/discord_party/native.py) 及測試中。

Hook 要求預期的 session、根目錄、事件、輸入 ID 與原生逐字稿；排除 subagent 事件及根目錄不符的輸入。紙條寄送授權只涵蓋當前真人回合。`receive` 和 `ack` 登記候選處理，對帳會核對真實原生回合，才建立最終回執並移除本文。Party 經歷摘要即使由 hook 印出，也不能直接算已讀，還需要原生逐字稿對帳。較早的夜聊 digest hook 則從成功的 Stop 事件與 assistant 回覆記錄完成，沒有使用同一套逐字稿回執帳本。

## 正式人格存檔生命週期

在註冊的人格根目錄執行存檔操作，使用公開 helper 與設定的絕對路徑：

```text
continuity.py --config CONFIG save-begin [--claim-id CLAIM]
continuity.py --config CONFIG save-resume --save-id SAVE
continuity.py --config CONFIG save-commit --save-id SAVE --file RELATIVE_FILE --message GENERIC_MESSAGE
continuity.py --config CONFIG save-abort --save-id SAVE
```

`save-begin` 凍結批次引用與正式人格 Git base。人格自行評估這些經歷並寫出有理由的檔案，helper 不會自動編輯人格。`save-commit` 只接受支援的 patch／journal 路徑，檢查 staged 內容、base／lock 與加密後，記錄不含私人細節的 commit 及備份狀態。Resume 透過 manifest 對帳先前的 commit；abort 保留檔案，批次維持待評估。撰寫整合前先讀 [continuity_save.py](../src/discord_party/continuity_save.py)。
