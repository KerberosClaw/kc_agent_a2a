# 驗證指引

> **English summary:** Offline tests use synthetic data and temporary stores; CI covers Linux and macOS mechanisms. Check links and render diagrams separately, then verify native authentication, Discord, hooks, and conversation quality in an isolated live environment.

[文件索引](index.md) · [發布範圍](release.md)

```bash
python -m pip install --require-hashes -r scripts/discord_party/requirements.lock
python scripts/run_tests.py
python scripts/check_docs.py
PYTHONPATH=src python -m a2a.demo
```

Runner 會探索各個獨立測試根目錄，回報數量、失敗及略過項。測試使用暫存目錄、合成身分／訊息與假的原生引擎。Git-crypt 測試使用暫存本機 repo，須安裝 git-crypt 才不會因缺依賴失敗。MacOS 防護測試需要 macOS，Linux CI 不能證明原生隔離有效。

| 契約 | 證據 |
| --- | --- |
| 紙條授權、回執、本文移除 | A2A 核心與紙條 runtime 測試 |
| 每日時間窗口、停止、額度、備份失敗 | Nightly、trial、backup 測試 |
| 重啟去重、未知送達、過期決定 | Party state／runtime／connector 測試 |
| 授權、成員、歷史限制、token 儲存 | Native gate、membership、pilot、installation 測試 |
| 摘要競態防護、涵蓋範圍、回執、存檔對帳 | Continuity 與 canonical save 測試 |
| 整理來源歸屬與拒絕發布 | Sharing 測試 |
| 按需搜尋／分頁、權限撤回、短效 MCP、無資料接力與回覆重複 | Life context／tools、dialogue、native、state 測試 |
| 最近五篇回填、修訂撤回、自己的 journal 隔離、引用、競態及背景模型設定 | Shared context／native 測試 |
| 新 boot 一次啟動、部分死亡不重啟、通知去重與每安裝 label | Watchdog／continuity install 測試 |
| 通用設定與升級保留狀態 | Public setup、note installation、manager、continuity install 測試 |

CI 在 Linux 與 macOS 執行離線測試、示範及連結檢查。本機發布驗證另在新的 venv 建置／安裝 Python 套件，並實際渲染 Mermaid。連結檢查器驗證 repo 目標與 anchors，不會連網，也不能證明 Mermaid 可渲染，因此渲染是獨立的發布檢查。

真實登入、Discord 權限、hook 信任／回執、模型工具軌跡格式、選用搜尋與對話品質，須由操作者在隔離房間驗收。公開 CI 沒有真實憑證，離線測試通過不能證明這些外部系統已通過。

有效的錯誤回報應包含版本、作業系統／原生 CLI 版本、移除本機路徑與 ID 的失敗指令、預期／實際狀態，以及最小虛構重現案例。不可附 token、真實人格、逐字稿、資料庫或原始供應者回應。

本次 `v0.2.0-alpha.1` 的發布驗證紀錄見[版本驗證](verification-v0.2.md)。測試 fixtures 全為合成角色與事件，不能用真實日記取代。

## 公開衍生版的設定與測試隔離

背景整理器包裝 grant 時，應從原始核准 manifest 取得 `native_models`，不能因 `CuratorGrant` 或 `ContextGrant` 換了 root 就讀到另一份設定。[背景模型設定測試](../tests/discord_party/test_native.py)直接驗證這項契約；檢查具體入口時搭配[模型呼叫盤點](model-calls.md)。

新的 venv 只隔離 Python 套件，不會隔離主機的 PATH、已安裝 CLI、Keychain 或登入狀態。Command 建構測試應明確替換 executable 探索與平台條件，驗證 argv／allowlist；[MCP 測試](../tests/discord_party/test_life_tools.py)採用這種方式。實際 OS 防護由 [native 測試](../tests/discord_party/test_native.py)的 macOS 測項驗證，登入與真人送達則另外 live 驗收。三者不可互相代替，也不可為了讓 CI 通過而移除原生隔離測試。

## 能力、品質與隱私分開驗收

下列都是合成情境與驗收方法，**不是已完成的 live 驗收紀錄**。保存結果時記錄程式／CLI／模型版本、核准政策與測試輸入版本；只公開虛構案例及去敏狀態。

| 層次 | 合成情境 | 成功證據與不涵蓋的部分 |
| --- | --- | --- |
| 工具能力 | 問虛構人物「林登」的基本關係 | 原生軌跡確實呼叫允許的 search／read；不代表答案引用正確 |
| 時間與來源 | 問昨晚夜聊，提供不同日期的核准素材 | 只選符合窗口的來源，沒有就說缺資料；不能以呼叫成功代替來源核對 |
| 回答品質 | 找虛構住家周邊的圖書館 | 區域符合不等於距離近；需有可核對的距離／交通依據，否則明說不確定 |
| 真人送達 | 真人提出需要回答的新問題 | 原生合法決定、outbox 及 Discord ID 可對帳；READY 不足以驗收 |
| 隱私 | 原文含未獲房間批准的關係推論 | 人工對照候選、審查結果與原文，檢查是否把推論寫成事實；雙模型審查不能自證無外洩 |
| 失敗復原 | 摘要超長、引用不存在或 serializer 多出尾文 | 拒絕不合契約結果、保留輸入並退避；總摘要失敗不抹掉原始分段 |

已通過的人工抽查只支持該批輸入、政策和模型版本，不能代表所有未來批次。隱私檢查細節見[信任邊界](privacy.md#審查結果也需要抽查)，摘要格式／退避回歸見[continuity 測試](../tests/discord_party/test_continuity.py)及[shared context 測試](../tests/discord_party/test_shared_context.py)。
