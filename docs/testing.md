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
| 通用設定與升級保留狀態 | Public setup、note installation、manager、continuity install 測試 |

CI 在 Linux 與 macOS 執行離線測試、示範及連結檢查。本機發布驗證另在新的 venv 建置／安裝 Python 套件，並實際渲染 Mermaid。連結檢查器驗證 repo 目標與 anchors，不會連網，也不能證明 Mermaid 可渲染，因此渲染是獨立的發布檢查。

真實登入、Discord 權限、hook 信任／回執、模型工具軌跡格式、選用搜尋與對話品質，須由操作者在隔離房間驗收。公開 CI 沒有真實憑證，離線測試通過不能證明這些外部系統已通過。

有效的錯誤回報應包含版本、作業系統／原生 CLI 版本、移除本機路徑與 ID 的失敗指令、預期／實際狀態，以及最小虛構重現案例。不可附 token、真實人格、逐字稿、資料庫或原始供應者回應。
