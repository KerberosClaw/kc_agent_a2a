# 選用的 Party 故障通知

> **English summary:** An optional per-installation macOS LaunchAgent checks Party every five minutes. It attempts one start per new boot only when every managed process is down; partial failures alert without restart loops. A caller-supplied notifier uses JSON stdin and a confirmed delivery receipt.

[文件索引](index.md) · [維運](operations.md) · [安裝](installation.md)

watchdog 是選用的 macOS 登入後 LaunchAgent；不等於無人登入也會啟動的系統 daemon。它支援全新安裝的固定 checkout／venv，也支援既有 `party/current` 版本指標。保留來源與 interpreter，並確認 GUI 登入環境中的 HOME、USER、PATH、原生 CLI 認證都可用。

每五分鐘檢查兩隻 bot、封存程序及兩隻的 READY。新 boot 且三個程序全停時，只嘗試啟動一次再查狀態；同一次開機不會不停重啟。部分程序死亡或 READY 不完整時保留帳本並通知操作者。READY 不能證明模型會正常回覆，watchdog 不會替你自動重送未知結果。

## 通知器契約

`--notifier` 必須指向你自己的可執行檔，沒有預設依賴作者的私人通知服務。它從 stdin 讀取一個 JSON object：

```json
{"source":"agent-a2a","status":"needs-input","message":"Party needs attention","incident":"synthetic-incident","event_id":"synthetic-stable-event-id"}
```

成功送達後 stdout 必須回傳：

```json
{"delivered":true,"message_id":"provider-confirmed-id"}
```

只排入佇列、沒有 message_id 或非零退出都視為未通知；同一事件會在後續 tick 重試。**通知器必須用 event_id 去重，並在重試時回傳同一次送達的回執**，避免傳送成功但本機記錄失敗時重複發送。憑證由通知器自行從安全位置取得，不放 CLI 參數或 repo。通知器最多執行 55 秒；stdout 僅回契約 JSON，診斷寫 stderr。例子中的 ID 只是格式示意，不能當送達證據。

```bash
python scripts/discord_party/watchdog.py install \
  --runtime "$HOME/agent-a2a-preview/party" \
  --notifier /absolute/path/to/your/notifier \
  --source agent-a2a
```

這個指令會寫 LaunchAgent 並立即啟用檢查，可能啟動已初始化的 Party 或發出通知。僅在完成隔離房間驗收、通知器去重／送達測試後執行。label 使用安裝路徑 hash，避免不同安裝互相暫停。既有同名 plist 不會被覆寫。

## 維護、排錯與移除

安裝回傳 `installed` label 與 `plist` 路徑；維護前用這兩個實際值暫停，結束後恢復：

```bash
launchctl bootout "gui/$(id -u)/YOUR_RETURNED_LABEL"
# 完成這套 Party 的維護，再恢復：
launchctl bootstrap "gui/$(id -u)" /absolute/path/to/returned.plist
```

永久停用後可移除該 plist。不要移除其他安裝的服務。版本化 continuity 升級器會暫停／恢復同一個 runtime 的 watchdog；手動停止服務仍須自己暫停，否則可能收到預期中的故障通知。

狀態位於 `party/watchdog/state.json`，日誌在 `party/logs/watchdog.log` 與 `watchdog.err`。健康恢復可發一次恢復通知；目前恢復通知失敗不持續重試，故障通知則會重試。檢查本機 `notified` 和 message_id，不可只憑退出碼宣稱通知送達。全新安裝的實際重開機、Discord 通知及各主機的認證相容性仍需操作者驗收；離線測試只證明狀態機與產生的 plist。

實作：[watchdog.py](../scripts/discord_party/watchdog.py)；驗證：[test_watchdog.py](../tests/discord_party/test_watchdog.py)。
