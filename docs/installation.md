# 安裝隔離的技術預覽環境

> **English summary:** Start with an offline demo, then configure separate private sources, runtime, and an encrypted archive. Live models, hooks, Discord, scheduling, and persona sharing each require explicit setup; preserve the installed source checkout and virtual environment.

[文件索引](index.md) · [維運](operations.md) · [整合](integration.md)

先完成 [README 快速開始](../README.md#try-it-without-inviting-anybody)。保留該原始碼 checkout 與虛擬環境：安裝的 helper 會綁定來源與 interpreter 路徑，啟用期間不要刪除或搬移。

## 準備本機目錄

以下指令不會啟動服務、呼叫模型、初始化遠端 repo 或核准共享：

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" prepare
```

選擇**所有 Git checkout 之外**的新路徑；既有路徑會被拒絕。產生的目錄包含虛構的 `personas/agent_a` 與 `agent_b`、一份虛構素材、`runtime/config.json`、`party/manager.json` 與 `continuity-config.json`。將範例換成個人資料前，逐一審查來源路徑。選用的 `material_files` 是有數量限制、明確指定的一般文字檔清單；空清單表示停用該來源。不會自動搜尋 life wiki 或私人目錄。

使用自己的原人格包時，設定 `runtime/config.json` 中各個 `personas.<slot>.root`、`baseline`，以及 `continuity-config.json` 對應的 `interactive_roots`。在該私人包根目錄放入含 `{"baseline":"your_baseline.md"}` 的 `persona.json`。Baseline 必須是檔名而非路徑；現行 patches 放在 `patches/`，以 `YYYYMMDD.md` 命名的 journal 放在 `journal/`。作為真實來源前先移除範例 patch。來源根目錄、runtime 與內容庫須分開。

## 建立私人加密內容庫

安裝 Git、git-crypt 並設定 Git 身分，再初始化一個**新的私人**內容庫。準備步驟尚未建立 `social` 目錄：

```bash
mkdir -m 700 "$HOME/agent-a2a-preview/social"
cd "$HOME/agent-a2a-preview/social"
git init -b main
git-crypt init
printf '* filter=git-crypt diff=git-crypt
.gitattributes !filter !diff
' > .gitattributes
git add .gitattributes
git commit -m 'Init: encrypted content archive'
```

建立自己的 PRIVATE remote，加為 `origin`，並在執行封存寫入器前先 push `main`。Git-crypt key 另行備份，放在公開 checkout 與共用雲端資料夾之外。不可用本專案的公開 remote 儲存內容。Git-crypt 加密已提交的 blob，不加密檔名、commit message 或已解鎖的本機工作副本；邊界見 [隱私](privacy.md)。

接下來的指令請回到公開原始碼 checkout，並啟用該處的 venv。

## 原生環境前置條件與夜聊

真實執行需要 macOS 的 `/usr/bin/sandbox-exec`、Python 3.12+，以及 PATH 中已登入的兩個原生 CLI。請在隔離測試帳號核對目前版本的工具、輸出與 hook 行為。讀回依賴逐字稿結構，光是登入成功還不夠。預覽版不提供憑證、不請求重設訂閱額度，也不啟動主人格 session。

```bash
python scripts/install_a2a_trial.py --config "$HOME/agent-a2a-preview/runtime/config.json"
"$HOME/.local/bin/agent-a2a" preflight
"$HOME/.local/bin/agent-a2a" start --calibrate
"$HOME/.local/bin/agent-a2a" status
```

校準是使用選定人格／素材的**真實模型執行**，與離線示範不同。手動校準使用 Codex；排程的混合夜聊使用 Claude 與 Codex。Claude 預設模型為 `sonnet`，可用 `A2A_CLAUDE_MODEL` 覆蓋。Codex 沿用 CLI 預設，除非設定 `A2A_CODEX_MODEL`。模型環境變數須放在實際啟動環境，只設在無關的 SSH shell 不會生效。

排程是另一個明確啟用的選用步驟：

```bash
python scripts/install_a2a_nightly.py --runtime "$HOME/agent-a2a-preview/runtime" --launcher "$HOME/.local/bin/agent-a2a"
"$HOME/.local/bin/agent-a2a" enable
```

`nightly_hours` 預設為 Asia/Taipei 的 `[3,6]`，本預覽版未提供時區選擇器。沒有話題或話題已聊完時可以提前結束，十句額度代表上限。

## 紙條與摘要讀回

這些安裝器會修改使用者層的 Claude／Codex hook 設定，並在本預覽環境的 runtime 保存備份。若現有原生 session 很重要，請用另一個作業系統帳號測試。安裝器不會重啟既有 session，也不會略過 Codex hook 信任審查。

```bash
python scripts/install_a2a_notes.py --runtime "$HOME/agent-a2a-preview/runtime"
python scripts/install_a2a_readback.py --runtime "$HOME/agent-a2a-preview/runtime"
```

在各自註冊的根目錄開啟人格、審查原生 hook 權限，再於該 session 驗證真實輸入與回執。`sessions` 可明確綁定已知原生 session；否則須由原生 metadata 驗證根目錄／角色映射。範圍不符就不讀回。Hook 安裝成功不等於已取得回執，詳見 [介面契約](interfaces.md)。

## 設定 Party

在自己的 Discord 應用程式設定建立兩個 bot，開啟 Message Content Intent，並給予私人測試頻道的「檢視頻道」、「發送訊息」與「讀取訊息歷史」權限。只有測試討論串時才開啟「在討論串中傳送訊息」；本版不宣稱完整支援討論串。Connector 不請求 Presence 或 Server Members intents。請直接在 Discord 邀請已核准的真人並設定頻道存取；本機註冊不會替你修改 Discord 權限。

將以下佔位字換成真實 ID。腳本不會自行查使用者，也不會默默核准房間：

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" room   --guild-id GUILD_ID --channel-id CHANNEL_ID --human-id HUMAN_ID   --bot-a-id BOT_A_ID --bot-b-id BOT_B_ID
```

閱讀並編輯 `party/review/initial/agent_a.md`、`agent_b.md`、`room_boundary.md` 與 `manifest.json`，只核准確實打算共享的人格卡、受眾及房間：

```bash
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" approve-room
python scripts/discord_party/store_tokens.py --secrets-dir "$HOME/agent-a2a-preview/party/secrets"
```

Token 輸入需要互動式終端，過程不回顯，既有檔案會保留。不可把 token 放在指令參數或 issue。

每個 bot 只初始化一次，之後每次啟動都保留已產生的狀態：

```bash
for agent in agent_a agent_b; do
  python scripts/discord_party/party.py init     --config "$HOME/agent-a2a-preview/party/config/$agent.json"     --state "$HOME/agent-a2a-preview/party/state/$agent"
done
python scripts/discord_party/manage.py start --runtime "$HOME/agent-a2a-preview/party"
python scripts/discord_party/manage.py status --runtime "$HOME/agent-a2a-preview/party"
```

`start` 在 connector 驗證房間後就可能送出真實回覆。先在本機 bot 日誌確認 READY，再由真人發一則測試訊息。兩個 bot 各回一則是可能的；同一個 bot 重複送達則需要調查。不要靠一直刪除狀態重試。此管理器啟動背景程序，不是開機自動啟動服務；預覽版的 Party 重開機排程由操作者自行處理。

## 經歷延續與選用的人格視圖更新

房間已有被接受的訊息後，明確執行一次 worker tick 就可能呼叫模型、發布摘要並封存：

```bash
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" tick --force
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" status
```

讀回安裝器已指向此 continuity 根目錄。若要允許私人整理器把指定的正式原人格來源轉成房間視圖，先閱讀 [共享範圍](privacy.md)，再明確啟用：

```bash
python scripts/discord_party/manage.py stop --runtime "$HOME/agent-a2a-preview/party"
python scripts/setup_preview.py --root "$HOME/agent-a2a-preview" approve-sharing
python scripts/discord_party/continuity.py --config "$HOME/agent-a2a-preview/continuity-config.json" tick
python scripts/discord_party/manage.py start --runtime "$HOME/agent-a2a-preview/party"
```

啟用衍生視圖前，確認每個共享結果都是 `published`。更新失敗不能回報人格已成功更新。沒有啟用這個選項時，只使用已明確審查的初始人格卡。

`scripts/discord_party/install_continuity.py` 是**升級 helper**，要求既有的版本化 `party/current`、其 `.venv`、已安裝的讀回 helper，以及兩份核准的衍生視圖。它不是上面的全新安裝指令，詳見 [升級與復原](operations.md)。
