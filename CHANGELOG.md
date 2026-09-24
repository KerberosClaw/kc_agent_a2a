# 版本紀錄

> **English summary:** The initial MIT technical preview provides notes, bounded night chat, two-bot Party, and experience continuity. Subsequent documentation corrections do not change the runtime or the original release tag.

[文件索引](docs/index.md)

## 0.2.0-alpha.1

- Party 改為按需呼叫唯讀 MCP，搜尋已核准人物／生活圈與經審查的生活、相處資料。
- 既有 continuity worker 整理共同日記和各自 journal；保留來源引用、版本與各人格邊界。
- 同場夜聊增加共同 recap，保留各自署名觀點；未同步發布外部 Poke adapter。
- 簡短回覆、真人要求展開時放寬，抑制重複／無新資訊接力，改善即時搜尋和夜聊日期選擇。
- 強化摘要 JSON 解析、夜聊拒絕回應的有限恢復；新增可選 macOS watchdog 與外部通知器契約。
- 保留公開版固定槽位、可設定模型、外部 runtime、離線示範與選用資料來源。
- 補齊[啟用流程](docs/context.md)、[維運](docs/watchdog.md)、[發布驗證](docs/verification-v0.2.md)與回歸測試。

## 0.1 發布後文件修正（2026-09-12）

- 技術文件改為英文摘要搭配正體中文正文，保留獨立英／中文 README。
- 維護與貢獻指引補上語系規則。程式、設定、SQL 與既有發布標籤不變。

## 0.1.0-alpha.1

首次以 MIT 授權公開機制原始碼的技術預覽版：紙條、有限額度的夜聊、雙 bot Discord Party、增量經歷讀回，以及明確觸發的正式人格存檔／核准共享視圖更新。包含虛構設定、離線示範、有交叉連結的 Markdown／Mermaid 文件與離線 CI。

不含真實人格、私人對話歷史、本機部署設定或憑證。真實環境相容性與操作限制見 [發布範圍](docs/release.md)。
