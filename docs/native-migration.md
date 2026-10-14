# 從舊紙條移轉

> **English summary:** Preview then explicitly retire one public note installation, preserving original mailbox bytes and unrelated hooks. Backups and a no-op compatibility shim support recovery; migration never dispatches messages or restarts sessions.

[文件索引](index.md) · [原生傳訊](native-messaging.md) · [維運](operations.md)

舊版紙條的寄收、票券與有信提醒已由原生傳訊取代。夜聊／Party 摘要讀回 hook 仍在使用；只移除該 installation 的紙條命令。先安裝並檢查原生傳訊，再移轉自己管理的舊紙條。舊版本與 Git 歷史保留，不能把舊公開 helper 當作被拒傳訊的備援通道。

```bash
python scripts/migrate_native_messages.py \
  --runtime "$HOME/agent-a2a-preview/runtime" \
  --archive "$HOME/agent-a2a-preview/runtime/retired-notes/migration-1"
# 看過預覽後，使用同一組參數加 --apply。
```

預設 dry-run 零改動。要求既有 `note-config.json`、其明列 helper／mail_root，以及確切的 Codex managed note block。信箱必須在該 runtime 內；自訂 legacy layout 不符合時停止，需先人工核對，不能搬錯安裝。

`--apply` 取得舊信箱 coordinator lock，拒絕忙碌或執行中的紙條 helper。備份原設定／helper，移除精確命令並確認 TOML 其他設定、trust、夜聊與 Party hook 逐項相同。保留所有狀態與本文，完整搬到私人 archive；逐檔雜湊核對，不簽收、清理或重寄未讀信。失敗時還原本輪設定與搬移；archive 已存在時停止，先查結果，不能盲目重跑。

舊 helper 留極小 shim：已載入的 `hook` 空回傳 exit 0；寄收／查詢 exit 2 明示退役，沒有信箱功能。下次正常 session reload 後不再呼叫它，不需為此重啟人格。移轉不部署原生工具、不啟動 daemon 或傳送訊息。

Archive、備份、manifest 都含私人設定／內容，留在 Git 外並保護檔案權限。復原由操作者明確處理：停止新的寄送，核對 manifest／before 檔與後來設定，把必要的舊資料及精確 note hook 合併回去；不可整檔覆蓋後來的設定、重送歷史訊息或自動回滾夜聊／Party。

程式：[移轉 script](../scripts/migrate_native_messages.py)；[失敗／復原測試](../tests/a2a/test_native_migration.py)。
