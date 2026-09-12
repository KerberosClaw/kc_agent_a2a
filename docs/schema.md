# SQLite schema 附錄

> **English summary:** This appendix records actual table and index DDL from fresh synthetic stores. SQL identifiers remain unchanged; never regenerate public documentation from real runtime data.

[文件索引](index.md) · [資料模型](data-model.md)

本附錄由全新合成資料庫初始化後，讀取 `sqlite_master` 產生。包含現行資料表與明確建立的索引，略過只在遷移過程使用的中間表。不可使用真實狀態重新產生公開文件；SQL 與識別字保留原樣。

## A2A 與信箱

### attempts

```sql
CREATE TABLE attempts(
          id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), agent TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('chat','prepare','retry')),
          day TEXT NOT NULL, parent TEXT REFERENCES attempts(id), status TEXT NOT NULL,
          result TEXT, UNIQUE(parent));
```

### cursors

```sql
CREATE TABLE cursors(agent TEXT PRIMARY KEY, value TEXT NOT NULL);
```

### exports

```sql
CREATE TABLE exports(
          run TEXT PRIMARY KEY REFERENCES runs(id), payload TEXT NOT NULL,
          exported INTEGER NOT NULL DEFAULT 0);
```

### mail_actions

```sql
CREATE TABLE mail_actions(
          token TEXT NOT NULL, operation TEXT NOT NULL, result TEXT NOT NULL,
          PRIMARY KEY(token,operation));
```

### mail_candidates

```sql
CREATE TABLE mail_candidates(note TEXT PRIMARY KEY, token TEXT NOT NULL, claim TEXT NOT NULL);
```

### mail_capabilities

```sql
CREATE TABLE mail_capabilities(
          token TEXT PRIMARY KEY, session TEXT NOT NULL, input_id TEXT NOT NULL,
          agent TEXT NOT NULL, engine TEXT NOT NULL, transcript TEXT NOT NULL,
          status TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(session,input_id));
```

### mail_events

```sql
CREATE TABLE mail_events(
          id INTEGER PRIMARY KEY, note TEXT NOT NULL, event TEXT NOT NULL, at TEXT NOT NULL);
```

### mail_operations

```sql
CREATE TABLE mail_operations(token TEXT PRIMARY KEY, result TEXT NOT NULL);
```

### mail_turns

```sql
CREATE TABLE mail_turns(
          token TEXT PRIMARY KEY, session TEXT NOT NULL, input_id TEXT NOT NULL,
          agent TEXT NOT NULL, engine TEXT NOT NULL, transcript TEXT NOT NULL,
          status TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(session,input_id));
```

### messages

```sql
CREATE TABLE messages(
          request TEXT PRIMARY KEY REFERENCES attempts(id), run TEXT NOT NULL,
          agent TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
          UNIQUE(run, ordinal));
```

### notes

```sql
CREATE TABLE notes(
          id TEXT PRIMARY KEY, origin TEXT UNIQUE NOT NULL, courier TEXT NOT NULL,
          recipient TEXT NOT NULL, status TEXT NOT NULL, session TEXT, turn TEXT,
          claim TEXT, injected INTEGER NOT NULL DEFAULT 0);
```

### runs

```sql
CREATE TABLE runs(
          id TEXT PRIMARY KEY, window TEXT UNIQUE NOT NULL, day TEXT NOT NULL,
          status TEXT NOT NULL, stop_reason TEXT, stop INTEGER NOT NULL DEFAULT 0);
```

## 各 bot 的 Party

### attempts

```sql
CREATE TABLE attempts (
 request_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, source_id TEXT NOT NULL,
 grant_version TEXT NOT NULL, status TEXT NOT NULL);
```

### config

```sql
CREATE TABLE config (registry TEXT NOT NULL, version INTEGER NOT NULL);
```

### events

```sql
CREATE TABLE events (
 channel_id TEXT NOT NULL, message_id TEXT NOT NULL, author_id TEXT NOT NULL,
 created_at TEXT NOT NULL, received_at TEXT NOT NULL, role TEXT NOT NULL,
 kind TEXT NOT NULL, content TEXT NOT NULL, historical INTEGER NOT NULL,
 attempted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel_id,message_id));
```

### outbox

```sql
CREATE TABLE outbox (
 request_id TEXT PRIMARY KEY REFERENCES attempts(request_id), epoch TEXT NOT NULL,
 nonce TEXT UNIQUE NOT NULL, content TEXT NOT NULL, closing INTEGER NOT NULL,
 status TEXT NOT NULL, message_id TEXT UNIQUE, counted INTEGER NOT NULL DEFAULT 0);
```

### state

```sql
CREATE TABLE state (
 id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT, remaining INTEGER NOT NULL CHECK(remaining BETWEEN 0 AND 10),
 paused INTEGER NOT NULL, control_id TEXT, participation TEXT NOT NULL,
 ready INTEGER NOT NULL, armed INTEGER NOT NULL, grant_version TEXT NOT NULL,
 cursor TEXT, last_sent REAL NOT NULL DEFAULT 0);
```

### one_generation

```sql
CREATE UNIQUE INDEX one_generation ON attempts((1)) WHERE status='generating';
```

### one_reservation

```sql
CREATE UNIQUE INDEX one_reservation ON outbox((1)) WHERE status IN ('queued','submitted','unknown');
```

## 經歷延續（Continuity）

### assessed

```sql
CREATE TABLE assessed(
          agent TEXT, batch TEXT REFERENCES batches(id), save TEXT REFERENCES saves(id),
          PRIMARY KEY(agent,batch));
```

### batches

```sql
CREATE TABLE batches(
          id TEXT PRIMARY KEY, agent TEXT NOT NULL, grant_version TEXT NOT NULL,
          kind TEXT NOT NULL, refs TEXT NOT NULL, status TEXT NOT NULL,
          token TEXT, lease REAL NOT NULL, body TEXT, created REAL NOT NULL,
          error TEXT, failures INTEGER NOT NULL DEFAULT 0);
```

### claims

```sql
CREATE TABLE claims(
          id TEXT PRIMARY KEY, agent TEXT, session TEXT, turn TEXT, refs TEXT, body TEXT,
          state TEXT, created REAL, UNIQUE(agent,session,turn));
```

### coverage

```sql
CREATE TABLE coverage(
          agent TEXT, seq INTEGER REFERENCES sources(seq), batch TEXT REFERENCES batches(id),
          PRIMARY KEY(agent,seq));
```

### received

```sql
CREATE TABLE received(
          agent TEXT, batch TEXT REFERENCES batches(id), session TEXT, turn TEXT, at REAL,
          PRIMARY KEY(agent,batch));
```

### saves

```sql
CREATE TABLE saves(
          id TEXT PRIMARY KEY, agent TEXT, refs TEXT, base TEXT, status TEXT, commit_id TEXT,
          created REAL, manifest TEXT);
```

### seen

```sql
CREATE TABLE seen(
          agent TEXT, session TEXT, batch TEXT REFERENCES batches(id), PRIMARY KEY(agent,session,batch));
```

### sources

```sql
CREATE TABLE sources(
          seq INTEGER PRIMARY KEY, source_key TEXT NOT NULL, revision TEXT NOT NULL,
          body TEXT NOT NULL, observed REAL NOT NULL, UNIQUE(source_key,revision));
```

### one_save

```sql
CREATE UNIQUE INDEX one_save ON saves(agent) WHERE status='open';
```

## digest_hook 補充資料表

```sql
CREATE TABLE IF NOT EXISTS digest_seen(session TEXT,run TEXT,PRIMARY KEY(session,run));
        CREATE TABLE IF NOT EXISTS digest_pending(session TEXT PRIMARY KEY,turn TEXT,runs TEXT);
```

## continuity_hook 補充資料表

```sql
CREATE TABLE IF NOT EXISTS native_claims(
        claim TEXT PRIMARY KEY REFERENCES claims(id), engine TEXT, transcript TEXT);
      CREATE TABLE IF NOT EXISTS context_resets(agent TEXT,session TEXT,PRIMARY KEY(agent,session));
```
