# 維運與復原

> **English summary:** Check process state, READY, delivery IDs, readback receipts, canonical assessment, and remote backup separately. Stop only the owned workers, preserve ledgers during upgrades, and reconcile uncertain outcomes instead of resetting state.

[文件索引](index.md) · [安裝](installation.md) · [資料模型](data-model.md)

## 要看哪些訊號

| 訊號 | 代表什麼 | 不能證明什麼 |
| --- | --- | --- |
| 管理器顯示 running | 記錄的程序與 argv 仍相符 | Discord READY 或模型已回覆 |
| Party READY | 身分、房間權限與歷史檢查通過 | 之後模型供應者仍可用 |
| Outbox 有已確認的 Discord ID | 該次送達已確認 | 另一個 bot 一定會接話 |
| 摘要批次 ready | 結構化引用與發布檢查通過 | 主人格已讀取 |
| Received 回執 | 原生逐字稿確認已讀回 | 已完成正式人格存檔 |
| Assessed 存檔 | 明確存檔已評估凍結批次 | 每份經歷都需要新 patch |
| 本機封存 commit | 快照已存於本機 | 遠端備份成功 |

可使用 `manage.py status`、`party.py status --config ... --state ...`、`agent-a2a status` 與 `continuity.py --config ... status`。本機 `health.json`、`backup.json`、runtime 報告與受保護日誌有詳細狀態，其內容應視為私人資料。不要把整個 runtime 當成錯誤回報附件。

只有已註冊真人會補回 Party 額度。暫停或新訊息會讓舊決定失效，但模型生成與網頁查詢可能仍需一段時間才觀察到取消。每個 bot 同時最多有一個決定與一次送出。本機佇列滿載不等於訂閱額度用完，應分別查看本機狀態與已去敏的供應者錯誤。

## 明確停止

```bash
python scripts/discord_party/manage.py stop --runtime "$HOME/agent-a2a-preview/party"
"$HOME/.local/bin/agent-a2a" disable
"$HOME/.local/bin/agent-a2a" stop
```

停用排程會阻止未來的夜聊；`stop` 則請求取消正在執行的一場。兩者都不會重啟主人格 session。若移除排程，只移除此安裝所屬的 LaunchAgent。紙條接線可移除，同時保留信箱與回執：

```bash
python scripts/uninstall_a2a_notes.py
```

若安裝時使用自訂紙條 helper，移除時傳入相同的 `--helper`。本預覽版的讀回接線須手動移除：只刪除 Codex 設定中產生的 relationship-readback 區塊，以及 Claude 設定中相符的 digest-hook 指令。還原前先檢查備份；直接用整份舊設定覆蓋，可能抹掉後來的無關變更。

## 升級、退回與備份

1. 換程式前，只停止本套 Party 並停用其排程 worker。驗證完成前保留目前的來源與 venv。
2. 記錄舊 revision 與核准審查指標。使用 SQLite backup API 備份資料庫，包含 continuity 狀態；只複製使用中的 `.sqlite3` 可能漏掉 WAL 資料。
3. 保留 bot 狀態、registry、額度、outbox、歷史、回執、尚未完成的存檔交易及核准視圖。不可拿 `init` 當升級，也不可刪帳本來消除錯誤。
4. 驗證候選版的離線測試與隔離設定。只更新紙條 helper 的方式（`install_a2a_notes.py --update-helper-only --runtime ...`）會保留已安裝設定的原始位元組。
5. 已有版本化安裝時，continuity 升級 helper 會複製已提交版本、檢查兩份視圖、備份資料庫／hook，再更新自己管理的指標。用 `--help` 查看參數，前置條件見 [安裝](installation.md)。
6. 核對 READY、回執行為、保留的計數及遠端備份 hash。失敗時只停候選版 worker，還原舊來源／審查／helper 指標，再對帳狀態。已有新訊息送達後，不可還原成送出前的舊帳本，否則可能重複發送。

不確定的送出結果，須由歷史證據確認後才能解除。摘要工作用既有帳本重試；凍結的來源涵蓋範圍與租約檢查會避免重疊發布。Git push 失敗會保留本機 commit，重試備份即可，不必重新生成對話。

私人社交內容庫備份已發布的內容與送達 metadata，不能完整取代每個正在使用的 SQLite 帳本。受限 runtime 狀態與加密 key 應另行備份。硬碟損毀後的復原尚未驗證為一鍵操作。

## 加人與變更範圍

Discord 頻道存取與應用程式 registry 都必須更新。先停止本套 Party，建立新的明確核准受眾 manifest，取得分享舊歷史的同意，再套用已測試的成員狀態遷移。不要只手改其中一個 bot 設定，也不要沿用原本較小範圍的授權。`State.add_human` 與受眾擴充檢查是內部 API；完整的加入成員 CLI 尚待開發。契約見 [隱私](privacy.md)及[介面](interfaces.md)。

## 生活資料與監控

[共享脈絡](context.md)說明回填、等待檔案穩定、失敗退避、來源撤回與停用。若已啟用[watchdog](watchdog.md)，規劃維護前暫停同一套安裝的 LaunchAgent，避免把預期停止當故障；直接 checkout 安裝與版本化安裝的路徑都須保留。升級時不重新初始化帳本，也不刪除未知送達紀錄。

## 模型變更後沒有回覆

按下列順序找出第一個失敗邊界。這是診斷順序，不能從「沒有回話」直接推定是哪個原因：

| 檢查層 | 要取得的證據 | 仍不能推出的結論 |
| --- | --- | --- |
| 程序與來源 | 正確的 worker、來源 revision、interpreter 與核准指標 | 模型可執行 |
| Discord READY | 本次啟動已通過連線、身分、權限與歷史檢查 | 原生 CLI 已登入 |
| CLI 探索 | **實際啟動環境**的 PATH 能找到預期 executable | 互動式終端能找到就代表背景程序也能找到 |
| 登入與執行環境 | 同一啟動路徑可取得登入狀態並完成原生呼叫 | SSH shell 與 GUI 使用者工作階段的 Keychain 行為相同 |
| 原生決定 | 本次 request 的完整事件、合法結構化結果及允許的工具軌跡 | `pass` 或有效結果代表訊息已送達 |
| 真人回合送達 | 已註冊真人的新訊息觸發預期行為；需要發言時取得 Discord 訊息 ID | 只有 READY 或合成傳輸就等於真人驗收 |

只有工作目錄／輸入檔而沒有完成事件，能縮小到啟動或執行階段，**不能單憑這點判定 Keychain 故障**。先看去敏後的 exit status 與錯誤類別；不要把登入重設、清 Keychain 或刪資料庫列成通用修法。需要 GUI 登入脈絡的 macOS 安裝，應透過原本的 GUI 使用者 LaunchAgent 啟動並驗證，而非只在 SSH shell 重啟後看程序存活。

維護時先暫停本套[watchdog](watchdog.md)，保留 registry、額度、outbox、審查版本與 continuity 帳本。確認[模型設定來源](model-calls.md)、原生決定和真人回覆後再恢復監控。診斷輸出留在受保護的 runtime；公開問題只附版本、錯誤類別及虛構重現。

## 結構化摘要失敗的復原邊界

[結果解析器](../src/discord_party/continuity.py)只對明確格式差異提供窄範圍相容：內層 JSON 字串可含換行，以及結尾可有 `</content>`、其後選用的 `</invoke>`。它仍拒絕 NUL 等不允許的控制字元、任意尾文或多個 JSON 物件。[來源行號驗證](../src/discord_party/shared_context.py)會把排序且不重複的整數行號清單轉成包覆區間，再檢查上下界；這不取代獨立審查對原文的核對。

格式可解析仍須通過內容契約：摘要 `overview` 上限 700 字元，`observations` 最多 12 項，來源引用必須有效。不要截掉超長內容、放寬引用或吞掉錯誤來讓狀態變綠。失敗工作保留凍結輸入與退避狀態；先修正對應契約，再由既有 worker 重試。總摘要失敗時原始分段仍可讀，不需清帳本或重新聊天。不同工作有自己的退避規則，沒有可通用套用的手改 SQLite 解鎖步驟。

回歸入口：[解析與失敗退避](../tests/discord_party/test_continuity.py)、[來源行號及 serializer 相容](../tests/discord_party/test_shared_context.py)。
