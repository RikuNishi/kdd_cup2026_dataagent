# Failed task resolution report

`artifacts/runs/20260517T050754Z` の easy / medium 評価で低スコアだった task のうち、以下 7 件について、人手で解いた過程と agent 改善観点を整理します。

対象 task:

- `task_80`
- `task_86`
- `task_89`
- `task_145`
- `task_163`
- `task_169`
- `task_173`

このメモは公開 `gold.csv` に対するローカル近似評価の分析です。hidden test の公式スコアを説明するものではありません。

## 全体方針

今回の失敗は、単純な計算能力よりも次の問題が中心です。

- 質問で要求される出力属性と、filter / join / ranking 用の helper 属性を分離できていない。
- `rank` / `position` / `positionOrder`、`type` / `category`、entity ごとの `number` など、同名・類義列を比較せず決め打ちしている。
- `knowledge.md` にある metric formula を最終式へ反映していない。
- exact match が空のとき、日付 coverage、time format、関連 dimension への fallback を検討できていない。
- 集計問題で不要な entity 情報を探し続け、group by だけで答えられる構造に気づけていない。

agent に入れるべき改善は、特定データ名の対応表ではなく、汎用的に `requested output attributes` と `helper attributes` を分けることです。

## task_145

質問:

> Among the events attended by more than 10 members of the Student_Club, how many of them are meetings?

### 解いた過程

1. `context/` には `csv/attendance.csv` と `db/event.db` がある。
2. `attendance.csv` は `link_to_event`, `link_to_member` の対応表。
3. 質問は「参加者が 10 人超の event のうち meeting は何件か」なので、member の属性は不要。
4. `attendance.csv` を `link_to_event` ごとに group by し、`COUNT(*) > 10` の event を抽出する。
5. `event.db` の `event` table と `event.event_id = attendance.link_to_event` で join する。
6. `event.type = 'Meeting'` を数える。

再現 SQL:

```sql
SELECT COUNT(*)
FROM (
  SELECT link_to_event
  FROM attendance
  GROUP BY link_to_event
  HAVING COUNT(*) > 10
) AS attended
JOIN event AS e
  ON e.event_id = attended.link_to_event
WHERE e.type = 'Meeting';
```

正解は `4`。

### agent の失敗

agent は「Student_Club members」という語に引っ張られ、member table や member 情報を探し続けました。しかしこの問題で必要なのは member の属性ではなく、attendance row の件数です。

### 改善案

- prompt に「attended by more than N members は、member 属性が要求されていない限り attendance rows を group by して数える」と入れる。
- `link_to_event` / `link_to_member` のような対応表では、片側 entity の属性が不要なら bridge table の count だけで条件判定できる、と指示する。
- max steps 近くで entity 属性探索が続く場合、既に見つけた bridge table と target table の join / aggregation を試す。

## task_80

質問:

> What is his number of the driver who finished 0:01:54 in the Q3 of qualifying race No.903?

### 解いた過程

1. `csv/qualifying.csv` に `raceId`, `driverId`, `number`, `q1`, `q2`, `q3` がある。
2. `json/drivers.json` に driver entity 側の `number` がある。
3. `raceId = 903` の `q3` 値を一覧する。
4. exact `0:01:54` は存在しない。
5. `knowledge.md` では time metrics が `MM:SS.mmm` 形式と説明されているため、`0:01:54` は `1:54.xxx` の秒単位表現と解釈する。
6. `q3 LIKE '1:54.%'` で候補を抽出する。
7. 質問の “his number” は driver entity の number を指す可能性があるため、`drivers.json.number` も確認する。

再現 SQL:

```sql
SELECT d.number
FROM qualifying AS q
JOIN drivers AS d
  ON d.driverId = q.driverId
WHERE q.raceId = 903
  AND q.q3 LIKE '1:54.%';
```

該当 driver は Daniel Ricciardo と Sebastian Vettel で、driver entity の `number` は `3`, `5`。

### agent の失敗

agent は exact match の空結果に固執し、同様の空クエリを繰り返して max steps を消費しました。また、`qualifying.number` と `drivers.number` のどちらが “his number” かを比較しませんでした。

### 改善案

- exact match が空なら、source の time format に合わせて `0:01:54` を `1:54.xxx` や秒数へ変換する。
- 同じ空条件を繰り返さず、distinct values を見て形式を合わせる。
- 同名属性が複数 entity にある場合、質問で参照される entity に紐づく列を優先する。

## task_86

質問:

> Which race was Alex Yoong in when he was in track number less than 20?

### 解いた過程

1. `drivers.json` で Alex Yoong を検索し、`driverId = 62` を得る。
2. `driverStandings.csv` と `races.csv` を `raceId` で join する。
3. “track number” に対応しそうな列を比較する。
   - `races.round`
   - `races.circuitId`
   - `driverStandings.position`
4. 素直に `races.round < 20` と読むと 18 件になり、前回 agent と同じ過剰出力になる。
5. `driverStandings.position < 20` と読むと Australian Grand Prix から United States Grand Prix までの 16 件になる。
6. public gold は Australian Grand Prix から Monaco Grand Prix までの 7 件で、文面から一意に説明するのは難しい。

### agent の失敗

agent は “track number” を `races.round` と即断して、ほぼ全レースを返しました。

### 改善案

- 非カラム語や曖昧語が出た場合、候補列を小表で比較してから filter を選ぶ。
- 候補 filter ごとの件数を比較し、質問の単数・複数表現や entity と整合するか確認する。
- ただしこの task は文面と gold の対応が弱く、prompt だけでは完全解決が難しい可能性があります。

## task_89

質問:

> What's the finish time for the driver who ranked second in 2008's Chinese Grand Prix?

### 解いた過程

1. `races.json` から `year = 2008` かつ `name = 'Chinese Grand Prix'` を検索し、`raceId = 34` を得る。
2. `results.csv` には `position`, `positionOrder`, `rank`, `time` がある。
3. `knowledge.md` の ambiguity section を確認する。
   - `positionOrder`: final race finishing order
   - `rank`: fastestLapTime に基づく driver ranking
4. 質問は “ranked second” と書いているため、`rank = 2` を使う。
5. `rank = 2` の row の `time` は `+16.445`。

再現 SQL:

```sql
SELECT time
FROM results
WHERE raceId = 34
  AND rank = 2;
```

### agent の失敗

agent は `position = 2` を使い、`+14.925` を返しました。`rank`, `position`, `positionOrder` の意味を比較していません。

### 改善案

- `rank`, `position`, `positionOrder` が同時にある場合は、必ず `knowledge.md` の定義を確認する。
- “ranked” はまず `rank` 候補を確認し、finish order と明示される場合だけ `positionOrder` を優先する。
- 同名・類義列の候補を 1 件ずつ query し、出力候補を比較する。

## task_163

質問:

> Identify the type of expenses and their total value approved for 'October Meeting' event.

### 解いた過程

1. `event.db` で `event_name = 'October Meeting'` を検索する。
2. 対象 event は `event_id = recggMW2eyCYceNcy`, `type = Meeting`。
3. `budget.json` の `link_to_event` で、この event に紐づく budget を探す。
4. `expense.csv` の `link_to_budget` で approved expense を抽出する。
5. approved expense の cost は `54.25`, `69.33`, `51.81`。
6. 合計は `175.39`。
7. 質問の `type` は budget の `category` ではなく、linked event の `type` として扱うと gold に一致する。

再現 SQL:

```sql
SELECT e.type, SUM(x.cost)
FROM expense AS x
JOIN budget AS b
  ON x.link_to_budget = b.budget_id
JOIN event AS e
  ON b.link_to_event = e.event_id
WHERE e.event_name = 'October Meeting'
  AND x.approved = 'true'
GROUP BY e.type;
```

### agent の失敗

agent は `type of expenses` を budget `category` と解釈し、`Advertisement`, `Food` に分けて返しました。

### 改善案

- `type of X` が出た場合、`type` という実列が linked entity にあるか確認する。
- `category` は `type` の同義とは限らないため、質問語と source column name が直接一致する列を優先候補にする。
- grouping に使う列が helper なのか requested output attribute なのかを最終回答前に確認する。

## task_169

質問:

> What was the average monthly consumption of customers in SME for the year 2013?

### 解いた過程

1. `customers.db` の `customers` table で `Segment = 'SME'` の customer を特定する。
2. `yearmonth.csv` を `CustomerID` で join する。
3. `Date` は `YYYYMM` 形式なので、`Date LIKE '2013%'` で 2013 年を抽出する。
4. raw average は `AVG(Consumption) = 5519.475171...`。
5. `knowledge.md` に `Average Monthly Consumption = Total Annual Consumption / 12` と定義されている。
6. よって最終式は `AVG(Consumption) / 12`。

再現 SQL:

```sql
SELECT AVG(y.Consumption) / 12
FROM yearmonth AS y
JOIN customers AS c
  ON y.CustomerID = c.CustomerID
WHERE c.Segment = 'SME'
  AND CAST(y.Date AS TEXT) LIKE '2013%';
```

正解は `459.9562642871061`。

### agent の失敗

agent は `AVG(Consumption)` で止まり、`knowledge.md` の `/12` を適用しませんでした。

### 改善案

- metric phrase が question にあり、`knowledge.md` に formula がある場合、最終 SQL / Python 式に formula を反映したか確認する。
- `average monthly`, `ratio`, `percentage increase`, `difference` などは、単純集計前に knowledge formula check を行う。
- `validate_answer` の notes に formula を明記させる。

## task_173

質問:

> Please list the countries of the gas stations with transactions taken place in June, 2013.

### 解いた過程

1. `transactions_1k.db` の `Date` range を確認する。
2. `transactions_1k` は `2012-08-23` から `2012-08-26` までしか含まない。
3. 直接 `June 2013` filter を適用すると空になる。
4. gas station country を求める join path は `transactions_1k.GasStationID -> gasstations.json.GasStationID`。
5. available transaction に出る gas station の country は `CZE`, `SVK`。
6. public gold も `CZE`, `SVK`。

再現 SQL:

```sql
SELECT DISTINCT g.Country
FROM transactions_1k AS t
JOIN gasstations AS g
  ON g.GasStationID = t.GasStationID;
```

### agent の失敗

agent は requested date range が source coverage に存在しないことを確認したあと、そのまま空回答にしました。coverage mismatch 時の fallback がありません。

### 改善案

- zero-row の場合、すぐ空回答にせず source coverage を明示的に確認する。
- coverage mismatch がある場合、関連 dimension で答えられる最小集合を検討する。
- empty answer より、確認済みの join path から得られる fallback answer の方が有効な場合があります。

## 実装に反映すべき汎用ルール

特定データ名に依存した対応表は避け、次の汎用ルールに落とすのがよいです。

### requested output attributes と helper attributes の分離

最終回答前に、以下を分ける。

- requested output attributes: 質問が直接求めている値。
- helper attributes: filter、join、grouping、ranking、sorting、tie check、formula calculation、verification に使っただけの値。

出力するのは requested output attributes のみ。helper attributes は質問が明示的に求めない限り出さない。

### ambiguous columns の比較

次のような列が複数ある場合、候補列を比較してから選ぶ。

- `rank`, `position`, `positionOrder`
- entity ごとの `number`
- `type`, `category`, `class`
- `name`, `title`, `label`

質問で参照された entity に紐づく列を優先し、他の同名列は helper attribute として扱う。

### knowledge formula の適用確認

質問に metric phrase がある場合、`knowledge.md` の formula を最終式に反映する。

例:

- `average monthly`
- `ratio`
- `percentage`
- `difference`
- `increase`

### zero-row fallback

空結果になった場合は、次の順に確認する。

1. spelling / case / whitespace
2. date / time / unit format
3. data type
4. source coverage
5. linked dimension table から答えられる範囲

同じ空クエリを繰り返さず、候補値の一覧や coverage 確認に切り替える。

### bridge table aggregation

`attendance`, `membership`, `transaction`, `relation` などの bridge / fact table では、関連 entity の属性を探す前に、bridge row の group by で条件判定できるか確認する。

例:

- “events attended by more than N members” は `attendance` を event ごとに count する。
- member の名前や属性が出力に不要なら、member table は不要。

## 2026-05-17 regression 検証メモ

`requested output attributes` / `helper attributes` 分離、`validate_answer` warning 強化、`answer_repair` 追加後に、低スコア 13 task の regression subset で再評価しました。

対象 dataset / config:

- input: `data/public_regression_20260517/input`
- gold: `data/public_regression_20260517/output`
- config: `configs/react_regression_20260517.local.yaml`
- run: `artifacts/runs/20260517T111709Z`

比較対象:

| run | 内容 | average_score | missing_predictions |
| --- | --- | ---: | ---: |
| `20260517T050754Z` | 初回 easy / medium run の該当 13 件 | 0.215201 | 4 |
| `20260517T072718Z` | prompt / validation 改善後 | 0.230769 | 2 |
| `20260517T105457Z` | requested/helper 汎用ルール後 | 0.153846 | 3 |
| `20260517T111709Z` | `answer_repair` 後 | 0.230769 | 4 |

### 改善した点

`answer_repair` は、余分列・helper 列の削除に対して効果がありました。

- `task_259`: `0.0 -> 1.0`
  - repair 前は `Id`, `Score`, `Text` を含む候補でした。
  - `answer_repair` が 1 回発火し、最終的に `Text` だけを出力しました。
  - gold と一致し、score が 1.0 になりました。

`task_19` と `task_303` は 1.0 を維持しました。

### 未改善の点

`answer_repair` は「余分列を削る」には効きますが、値や式の解釈が間違っている場合は改善できませんでした。

- `task_25`
  - 余分列は削れたものの、値が `Officers meeting - ...` になり、gold の `November Speaker`, `October Speaker`, `September Speaker` と一致しませんでした。
  - 問題は出力列ではなく、`lowest cost` の解釈と集計対象の選択です。

- `task_89`
  - 依然として `position = 2` 相当の `+14.925` を返しました。
  - gold は `rank = 2` の `+16.445` です。
  - `rank` / `position` / `positionOrder` の候補比較が不足しています。

- `task_163`
  - 依然として `category`, `total_cost` を返しました。
  - gold は linked event の `type` と合計額です。
  - `type` と `category` の語義比較が不足しています。

- `task_169`
  - 依然として `AVG(Consumption)` の `5519...` を返しました。
  - gold は `AVG(Consumption) / 12` の `459...` です。
  - `knowledge.md` の metric formula を最終式へ反映できていません。

- `task_173`
  - 依然として空回答でした。
  - requested date range が source coverage にない場合の fallback が不足しています。

- `task_80`, `task_145`, `task_38`, `task_180`
  - missing / max_steps / model response missing text content が残りました。
  - API 応答安定性と、探索ループ脱出の両方が課題です。

### 結論

`answer_repair` は、次のようなケースに限定して有効です。

- 正しい値を含んでいるが、余分な helper 列を出している。
- `validate_answer` が helper 属性 warning を出している。
- 1 回の修正で requested output attributes だけに絞れる。

一方、次のケースには効きません。

- filter / aggregation の対象自体を間違えている。
- `knowledge.md` の formula を使っていない。
- 類義列の意味を比較せず、誤った列を選んでいる。
- empty result を見て fallback せず空回答にしている。

### 次の施策候補

1. `knowledge.md` formula 適用チェック
   - `average monthly`, `ratio`, `percentage`, `difference`, `increase` などの metric phrase が question にある場合、notes や SQL / Python 式に formula 適用の根拠があるか検査する。
   - `task_169` のような `/12` 忘れを狙う。

2. ambiguous column comparison repair
   - `rank`, `position`, `positionOrder` などが同時に存在する場合、候補列を比較した evidence がないまま answer しようとしたら 1 回だけ repair する。
   - `task_89` を狙う。

3. `type` / `category` の requested attribute 確認
   - 質問語が `type` のとき、source に `type` column と `category` column が両方ある場合は比較を促す。
   - `category` は helper/grouping 候補であり、`type` の同義とは限らない。
   - `task_163` を狙う。

4. zero-row fallback repair
   - zero-row answer の前に、date/time coverage、alternate format、linked dimension fallback を確認した evidence があるか見る。
   - `task_173` を狙う。

5. bridge table aggregation hint
   - `link_to_*` だけを持つ bridge / fact table では、関連 entity 属性を探す前に group by で条件判定できるか試す。
   - `task_145` を狙う。

これらは `answer_repair` と同じく、強制 block ではなく 1 回だけの repair observation として実装する方が missing 増加リスクを抑えやすいです。

## 2026-05-17 question_contract 導入後の regression 検証メモ

質問の意味解釈を前半で固定するため、`question_contract` 擬似 tool を追加し、`profile_context -> plan_knowledge -> question_contract` の workflow に更新しました。

追加した主な観点:

- requested output attributes / helper attributes の明示
- formula、grain、grouping、ranking、tie rule、join key の宣言
- `knowledge.md` の適用ルール、または `none applicable` の明記
- `validate_answer` notes に formula / grain / join key / tie / knowledge rule の根拠不足 warning を追加
- 古い step summary に query の columns、row_count、preview rows、question_contract 要約を残す
- scalar 以外でも ID、score、cost などの helper 列が出ている場合は warning / repair 対象にする

検証対象:

- input: `data/public_regression_20260517/input`
- gold: `data/public_regression_20260517/output`
- config: `configs/react_regression_20260517.local.yaml`
- run: `artifacts/runs/20260517T132706Z`

比較:

| run | 内容 | average_score | missing_predictions |
| --- | --- | ---: | ---: |
| `20260517T111709Z` | `answer_repair` 後 | 0.230769 | 4 |
| `20260517T132706Z` | `question_contract` / semantic validation / summary 改善後 | 0.384615 | 2 |

改善した task:

| task | before | after | 主因 |
| --- | ---: | ---: | --- |
| `task_25` | 0.0 | 1.0 | `lowest cost` を expense row の cost として扱い、helper の `cost` を出力から除外できた |
| `task_145` | 0.0 | 1.0 | bridge/fact table の attendee count を event grain で集計し、count のみを提出できた |

維持できた task:

- `task_19`: 1.0 を維持
- `task_259`: 最終調整後は `Text` のみを提出し、1.0 を維持
- `task_303`: 最終 run では 1.0 を維持

未改善:

- `task_169`: `average monthly` に対して `knowledge.md` の `/12` formula を適用できず、`AVG(Consumption)` のまま提出した。
- `task_180`: `Price / Amount > 29` の解釈はできたが、最終出力に `CustomerID` が残り、gold の `Consumption` のみと一致しなかった。
- `task_163`: `type` / `category` の linked entity 判定がまだ不十分。
- `task_173`: source coverage mismatch 時の fallback はまだ弱い。
- `task_38`, `task_80`: missing が残る。追加 step により max_steps 圧迫や API 応答安定性の影響を受けやすい。

次に有効そうな施策:

1. `question_contract` を prompt 任せにせず、`plan_knowledge` 後に未実行なら data query 前に 1 回だけ repair observation を出す。
2. `validate_answer` で `requested_output_attributes` と最終 columns の差分を見られるよう、直近 `question_contract` を参照する。
3. `average monthly`, `percentage`, `ratio`, `difference` などは、`knowledge_rules_used` と final notes に formula がない場合、answer 前に 1 回だけ semantic repair を出す。
4. `task_180` のような「their X」では、requested output が X だけか、entity identifier も必要かを contract で明示させる。

## 2026-05-17 near-step-limit submit / contract validation 追加メモ

`question_contract` の完全強制は、追加 step によって missing を増やすリスクがあるため採用しませんでした。代わりに次の2点を実装しました。

- `near_step_limit_submit`: `max_steps - 1` 以降で追加探索に進もうとした場合、既存の validate/query 候補を使って validate/answer を優先する。
- `validate_answer` が直近 `question_contract` を内部参照し、`requested_output_attributes` と answer columns の差分を warning する。

また、`validate_answer` が helper column warning を出している場合、`answer` 前に ID / score / cost / rank などの helper 列を機械的に projection できるケースでは、モデルに再判断させず projection 後の table を提出するようにしました。

検証:

- run: `artifacts/runs/20260517T144754Z`
- average_score: `0.384615`
- missing_predictions: `1`

改善確認:

- `task_180`: `CustomerID`, `Consumption` から helper の `CustomerID` を除外し、`Consumption` のみを提出して 1.0。
- `task_259`: `Text` のみを維持して 1.0。
- `task_25`: 1.0 を維持。
- `task_38`: 依然として timeout / missing。agent loop ではなく API 応答待ち系の可能性が高い。

注意:

- `near_step_limit_submit` は missing を減らすための保険であり、意味解釈を改善するものではありません。
- `task_145` は missing ではなく提出済みになりましたが、候補自体が schema 探索結果だったため 0.0 のままです。今後は「提出候補として妥当な query result」と「schema / exploratory query result」を区別する必要があります。
