# 技術預覽版範圍

> **English summary:** Version v0.2.0-alpha.1 publishes generic A2A mechanisms, not private personas or deployment data. Native execution targets two fixed macOS slots; onboarding, recovery, provider compatibility, and model-assisted review retain explicit limits.

[文件索引](index.md) · [版本紀錄](../CHANGELOG.md) · [驗證](testing.md)

本次原始碼發布為 `v0.2.0-alpha.1`（Python 套件版本 `0.2.0a1`），前版為 `v0.1.0-alpha.1`，匯出使用通用槽位與虛構設定的機制，不公開私人原人格內容、私人 Git 歷史、部署 metadata 或正式憑證。

包含：範圍受限的紙條轉達、有限額度的夜聊、Discord Party 狀態／傳輸、選用房間網頁查詢、加密內容匯出、獨立摘要工作／總摘要、原生讀回回執、明確觸發的正式人格存檔，以及核准的衍生房間視圖。

已知限制：

- 真實原生執行以 macOS 與兩個固定 Claude／Codex 槽位為目標；Linux 測試只涵蓋離線機制。
- 原生 CLI 的 hook／輸出／登入格式是外部依賴，升級後須重新驗證，不宣稱通用版本相容。
- Party 以文字為主，不含完整讀圖、編輯／刪除傳遞、討論串支援或任意額外 agent。
- 新真人加入需要 Discord 存取、明確核准及已測試的遷移 API，沒有一鍵介面。
- 全新安裝使用明確的原始碼／venv 流程；版本化 continuity 安裝器專供升級。選用 watchdog 提供 macOS 登入後有限復原／通知；真實重開機與通知整合須自行驗收，不提供完整自動復原或多主機協調。
- 人格／素材審查由模型輔助，可能誤判；私人來源存取是信任決策，不保證完美去敏或對話品質。
- 不提供 SLA、延遲目標、模型額度繞過或付費訂閱權益。

後續應優先補原生 CLI 相容性 fixtures、更簡單的加入成員／復原流程，以及經審查的 adapter 介面，再擴充 agent 與平台。這是規劃方向，不是已交付功能。

發布檢查包含：離線測試；全新套件／示範／設定驗證；Markdown 目標與 anchor 檢查；實際 Mermaid 渲染；憑證及專案情境兩層掃描；commit 前獨立去敏審查；候選 commit 的 CI；PR 審查／squash merge；公開連結、metadata 與 release tag 核對。不得把私人歷史複製到公開 repo。

此次新增[按需生活／相處查詢](context.md)、共同夜聊摘要、簡短發言／缺資料停止接力及[選用 watchdog](watchdog.md)。從 0.1 升級先備份帳本、停止自己管理的 worker，依[維運](operations.md)保留狀態更新來源；新資料功能預設不啟用，勿把範例政策直接當批准。變更、驗證與未驗證界線見[版本紀錄](../CHANGELOG.md)及[版本驗證](verification-v0.2.md)。
