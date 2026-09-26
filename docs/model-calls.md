# 模型呼叫與設定盤點

> **English summary:** This code-level map separates scheduled night chat, manual calibration, Party decisions, and background curation. Configuration precedence and application caches do not reveal provider requests, prompt-cache behavior, or subscription usage.

[文件索引](index.md) · [安裝](installation.md) · [維運](operations.md) · [共享脈絡](context.md)

以下核對公開程式 `v0.2.0-alpha.1`／`d1e9b30`，不是任何操作者的 live 設定清單。`agent_a`、`agent_b` 是通用槽位；未明確傳入 model／effort 表示由 CLI 決定，不能解讀成固定型號、零推理或不消耗額度。

## 哪些地方會呼叫模型

「一次」指程式的一次原生 adapter 呼叫；原生 CLI 內部可能有多次供應者請求與工具輪次。

| 入口／工作 | 觸發與呼叫方式 | 實作 |
| --- | --- | --- |
| 排程混合夜聊 | 每隻 prepare，之後逐回合 chat；確認的拒絕回應可在獨立 retry 額度內重送 | [nightly](../src/a2a/nightly.py)、[trial](../src/a2a/trial.py) |
| 手動試聊／校準 | 預設兩個槽位都用 Codex；校準另有帳本窗口，同樣是真實模型工作 | [trial](../src/a2a/trial.py) |
| Party 原生決定 | 狀態／授權／額度檢查允許後才呼叫；決定 `pass` 也已付出一次呼叫，不是每個 Discord event 都呼叫 | [runtime](../src/discord_party/runtime.py)、[native](../src/discord_party/native.py) |
| Party 經歷分段 | 各 agent 有合格分段工作時，一個工作一次摘要呼叫 | [continuity worker](../src/discord_party/continuity_worker.py) |
| 未讀經歷總摘要 | 各 agent 有合格 rollup 時另呼叫一次；不是每個分段必做 | [continuity](../src/discord_party/continuity.py)、[worker](../src/discord_party/continuity_worker.py) |
| 房間人格／素材共享更新 | 風格整理、素材選擇、獨立審查三階段；快取可能跳過前段，整體未變可能零次 | [sharing](../src/discord_party/sharing.py) |
| 生活與自己的 journal 整理 | 每篇合格來源先整理一次；空候選即結束，有候選再審查一次；共同日記只做一份，各自 journal 分開 | [shared context](../src/discord_party/shared_context.py) |
| 夜聊共同 recap | 以既有署名 digest 組合文字、引用與指紋，**這一步不呼叫 LLM**；digest 原先由夜聊回合產生 | [night recap](../src/a2a/night_recap.py) |

以上數量不含後續失敗重試，也不包括外部人格主對話／Poke 自己的模型工作。紙條投遞、hook 讀回、檔案封存和指紋計算本身不另啟模型；它們接到的原生人格回合仍由各平台計量。回歸入口：[夜聊測試](../tests/a2a)、[sharing](../tests/discord_party/test_sharing.py)、[shared context](../tests/discord_party/test_shared_context.py)。

## Model 與 effort 從哪裡來

| 呼叫路徑 | Model 選擇 | Effort 選擇 |
| --- | --- | --- |
| 混合夜聊 `agent_a` | `ClaudeAdapter` 明確建構參數優先，否則 `A2A_CLAUDE_MODEL`，否則 `sonnet` | adapter 預設 `medium` |
| 混合夜聊 `agent_b` | `MixedAdapter` 讀 `A2A_CODEX_MODEL`；未設就不傳 `--model` | `MixedAdapter` 明確傳 `medium` |
| 預設手動試聊／校準 | `CodexAdapter` 未傳 model，交給 CLI | 未傳覆寫，交給 CLI |
| Party 及使用 `NativeEngine` 的背景工作 | 明確建構參數 → 原始核准 manifest 的 `native_models[agent]` → Claude 的 `sonnet`／Codex 不傳 model | 明確建構參數，否則 `medium` |

Party 預設 `agent_a` 用 Claude、`agent_b` 用 Codex。背景工作的 `CuratorGrant`／`ContextGrant` 仍讀其 `original` grant 的 manifest，不能因 wrapper root 不同就遺失模型設定。共同日記的整理／審查使用 `agent_a`，各自 journal 用自己的 agent；審查是獨立 session，不代表換供應者或模型。其他摘要與共享更新按各自 agent 執行。

這些建構參數是 Python 介面，不能當成既有 CLI 旗標或使用者設定欄位；本版沒有一個涵蓋所有入口的 model／effort 總開關。環境變數也須存在於實際 LaunchAgent／程序的啟動環境。程式依據：[Claude 與混合 adapter](../src/a2a/claude_adapter.py)、[Codex adapter](../src/a2a/codex_adapter.py)、[NativeEngine](../src/discord_party/native.py)；設定繼承回歸：[test_native.py](../tests/discord_party/test_native.py)。

## 額度與兩種不同的快取

夜聊協調器的每隻上限為 chat 10、prepare 1、retry 3，分開記帳；十次 chat 不是十次供應者 API request，也不包含所有背景整理。取消、拒絕或最後沒有發言，都不能單憑結果推定未消耗 token。看帳本時分清 reserved／dispatched／accepted 和失敗狀態；依據：[coordinator](../src/a2a/coordinator.py)。

應用程式快取決定**要不要呼叫**：sharing 輸入指紋未變且已發布／仍退避時可直接返回；風格快取避免重做相同人格，素材快取避免重做同一候選，待處理的審查仍可能呼叫模型。Shared context 則依來源 hash、政策版本、穩定時間與退避選擇工作，沒合格來源就不呼叫。不能把每次 worker tick 一律換算為三次或兩次 LLM。

供應者 prompt cache 影響已發出請求如何處理或計量，與上述應用程式快取不同。公開帳本不足以判斷 cache 到期是否造成訂閱額度耗盡；`usage` 缺值表示未知，不是零。診斷時在私人 runtime 對照時間、入口／agent、request／job、要求的 model／effort、成功／失敗與供應者實際回傳的 usage；不要把 token、逐字稿或完整模型輸入放進公開 issue。
