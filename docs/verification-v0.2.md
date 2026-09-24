# v0.2 發布驗證

> **English summary:** This release is checked with fictional offline fixtures, a clean Python 3.12 environment, package installation and demo, documentation links and rendered diagrams, credential scans, and an independent contextual privacy review. Live provider and Discord behavior require operator validation.

[文件索引](index.md) · [驗證方式](testing.md) · [發布範圍](release.md)

此頁記錄公開 `v0.2.0-alpha.1` 的驗證範圍；CI 結果以該 release 對應 revision 的 [Actions](https://github.com/KerberosClaw/kc_agent_a2a/actions/workflows/tests.yml) 為準。

| 項目 | 方法與證據界線 |
| --- | --- |
| 回歸 | 65 個 A2A、156 個 Party、1 個 native guard、5 個 public setup 測試；全為虛構資料 |
| 安裝 | 新 Python 3.12 venv、雜湊鎖定依賴、wheel 建置／安裝及安裝後 `agent-a2a-demo` |
| 新資料設定 | 新安裝預設關閉，範例 pending 不可直接用；核准後只回傳人物允許欄位，背景引擎沿用房間模型設定 |
| 邊界 | 自己的 journal 隔離、修訂／撤回、工作鎖、引用、分頁、MCP 期限、未確認通知不當作送達 |
| 文件 | 相對連結／anchor 檢查，Mermaid 實際渲染；技術文件英文摘要加正體中文正文 |
| 去敏 | 完整候選檔案與可達歷史的憑證掃描；專案情境掃描與 commit 前獨立審查 |

離線通過不是實際 Discord 驗收。公開 release 沒有使用正式聊天憑證傳送測試訊息，沒有改動既有私人服務，也沒有執行真實重開機。模型 CLI／MCP 相容性、權限、通知器送達與長期聊天品質，請在自己的隔離房間確認。

這次不更新外部公開 Poke adapter、不提供任意知識庫匯入或多主機部署，不附任何真實人格、日記、聊天、審查政策或執行狀態。機制來源的私人環境觀察不當作這份公開候選版的 live 證據。
