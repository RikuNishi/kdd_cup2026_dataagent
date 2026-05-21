# Public task summary

`data/public/input/` に含まれる 50 件の public task の概要です。各タスクの `task.json` と `context/` 配下のファイル構成を確認し、質問の主題と必要になりそうなデータ操作を整理しています。

## 集計

| 難易度 | 件数 |
| --- | ---: |
| easy | 15 |
| medium | 23 |
| hard | 11 |
| extreme | 1 |
| 合計 | 50 |

## easy / medium 全件評価

- run_dir: `artifacts/runs/20260517T050754Z`
- gold_dir: `data/public/output`
- 評価日: 2026-05-17
- 対象: easy / medium 38 件
- 備考: 公開 `gold.csv` に対するローカル近似評価。hidden test の公式スコアではない。

| 指標 | 値 |
| --- | ---: |
| tasks | 38 |
| scored_tasks | 34 |
| missing_predictions | 4 |
| average_score | 0.731516 |
| average_recall | 0.736842 |
| penalty_lambda | 0.100000 |

満点は 25 件、部分点は 3 件、0 点は 10 件です。0 点のうち 4 件は `prediction.csv` 欠損、6 件は prediction は生成されていますが gold と一致していません。

## タスク一覧

| Task | 難易度 | 領域 | 概要 | コンテキスト | Score | Recall | Matched | Gold | Pred | Extra | Status |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| task_11 | easy | 医療 | 重度 thrombosis の患者について、患者 ID、性別、診断疾患を抽出する。 | json, md | 1.000000 | 1.000000 | 3 | 3 | 3 | 0 | ok |
| task_19 | easy | Student Club | Illinois 州出身の Student_Club メンバーのフルネームを列挙する。 | csv, json, md | 0.000000 | 0.000000 | 0 | 2 | 1 | 1 | ok |
| task_22 | easy | Student Club | Connor Hilton が dues を支払った日付を特定する。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_24 | easy | Student Club | "Women's Soccer" イベントの参加メンバー数を数える。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_25 | easy | Student Club | 最もコストが低いイベントを特定する。 | csv, json, md | 0.950000 | 1.000000 | 1 | 1 | 2 | 1 | ok |
| task_26 | easy | Student Club | 専攻が "Physics Teaching" のメンバー数を数える。 | json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_27 | easy | Student Club | 指定メンバー ID のフルネームと発生した合計コストを求める。 | json, md | 1.000000 | 1.000000 | 3 | 3 | 3 | 0 | ok |
| task_38 | easy | 金融取引 | client id 3356 の現金引き出し取引を列挙する。 | csv, json, md | 0.000000 | 0.000000 | 0 | 1 | 0 | 0 | missing |
| task_64 | easy | Superhero | 3-D Man が持つ全 superpower を列挙する。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_67 | easy | Superhero | 女性 superhero の平均体重を計算する。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_74 | easy | Superhero | Karen Beecher-Duncan の eye colour を特定する。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_75 | easy | Formula 1 | race 19 の Q2 で最速 lap time を出した driver の surname を特定する。 | csv, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_80 | easy | Formula 1 | race No.903 の Q3 が 0:01:54 の driver number を特定する。 | csv, json, md | 0.000000 | 0.000000 | 0 | 1 | 0 | 0 | missing |
| task_86 | easy | Formula 1 | Alex Yoong が track number 20 未満だった race を特定する。 | csv, json, md | 0.000000 | 0.000000 | 0 | 1 | 1 | 1 | ok |
| task_89 | easy | Formula 1 | 2008 Chinese Grand Prix で 2 位だった driver の finish time を特定する。 | csv, json, md | 0.000000 | 0.000000 | 0 | 1 | 1 | 1 | ok |
| task_145 | medium | Student Club | 参加者が 10 人超のイベントのうち、meeting の件数を数える。 | csv, db, md | 0.000000 | 0.000000 | 0 | 1 | 0 | 0 | missing |
| task_163 | medium | Student Club | "October Meeting" で承認された expense type と合計額を求める。 | csv, db, json, md | 0.000000 | 0.000000 | 0 | 2 | 2 | 2 | ok |
| task_169 | medium | 消費データ | 2013 年の SME 顧客の月平均消費量を計算する。 | csv, db, md | 0.000000 | 0.000000 | 0 | 1 | 1 | 1 | ok |
| task_173 | medium | 消費データ | 2013 年 6 月に取引があった gas station の国を列挙する。 | csv, db, json, md | 0.000000 | 0.000000 | 0 | 1 | 1 | 1 | ok |
| task_180 | medium | 消費データ | product id 5 を unit price 29.00 超で購入した人について、2012 年 8 月の consumption status を求める。 | csv, db, md | 0.933333 | 1.000000 | 1 | 1 | 3 | 2 | ok |
| task_194 | medium | 化学 | phosphorus と nitrogen を atom element として持つ bond を特定する。 | csv, db, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_196 | medium | 化学 | iodine element の atom が持つ bond 数の平均を計算する。 | csv, db, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_199 | medium | 学校 | Riverside 関連 district のうち、学校平均 SAT math score が 400 超の学校名と funding type を列挙する。 | csv, db, md | 1.000000 | 1.000000 | 2 | 2 | 2 | 0 | ok |
| task_200 | medium | 化学 | phosphorus または bromine を含む triple-bond molecule の atom 総数を計算する。 | csv, db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_214 | medium | Magic: The Gathering | Commander block 内の Brazilian Portuguese translated set 数を数える。 | csv, db, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_218 | medium | 学校 | Fresno Unified で average reading score が最低の学校の電話番号を特定する。 | db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_243 | medium | Stack Exchange | user No.24 について、posts 数が votes 数の何倍かを求める。 | csv, db, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_249 | medium | Stack Exchange | 10 件超の posts を作成した users について、up votes 平均と user age 平均を計算する。 | db, json, md | 1.000000 | 1.000000 | 2 | 2 | 2 | 0 | ok |
| task_250 | medium | Stack Exchange | slashnick の posts のうち answers count が最大の post ID を特定する。 | csv, db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_257 | medium | Stack Exchange | "Computer Game Datasets" post の total views と、最後に投稿した user 名を特定する。 | db, json, md | 1.000000 | 1.000000 | 2 | 2 | 2 | 0 | ok |
| task_259 | medium | Stack Exchange | views が 100 から 150 の posts に対し、score が最高の comment を特定する。 | csv, db, md | 0.914286 | 1.000000 | 1 | 1 | 7 | 6 | ok |
| task_261 | medium | Superhero | "Super Strength" を持つ superhero のうち、身長 200cm 超の人数を数える。 | csv, db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_269 | medium | Superhero | death touch の power を持つ superhero 名を列挙する。 | csv, db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_283 | medium | Superhero | blue eyes の superhero の割合を計算する。 | db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_287 | medium | Superhero | Phoenix Force の能力を持つ superhero の gender を特定する。 | csv, db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_292 | medium | Formula 1 | race No.9 で最高 points の constructor について introduction website を特定する。 | db, json, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_303 | medium | Formula 1 | European Grand Prix のうち Germany 開催の割合を計算する。 | db, json, md | 0.000000 | 0.000000 | 0 | 1 | 0 | 0 | missing |
| task_305 | medium | Formula 1 | 2009 Spanish Grand Prix の全 drivers における fastest lap speed を求める。 | csv, db, md | 1.000000 | 1.000000 | 1 | 1 | 1 | 0 | ok |
| task_330 | hard | サッカー | 2008-09-24 の Belgian Jupiler League で、home team と away team の最終スコアを特定する。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_344 | hard | 医療 | 男性患者かつ white blood cells が normal の患者のうち、fibrinogen が abnormal の人数を数える。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_349 | hard | Student Club | Angela Sanders の major を特定する。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_350 | hard | Student Club | "Women's Soccer" 参加者のうち、medium size の T-shirt を希望する人数を数える。 | csv, db, doc, md |  |  |  |  |  |  | 未評価 |
| task_352 | hard | Student Club | "Yearly Kickoff" meeting の Advertisement budget が "October Meeting" の何倍かを求める。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_355 | hard | Student Club | water、veggie tray、supplies に支出したメンバーのフルネームとコストを求める。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_379 | hard | 化学 | carcinogenic な molecule ごとに 4 番目の atom の toxicology element を集計する。 | csv, doc, md |  |  |  |  |  |  | 未評価 |
| task_396 | hard | Superhero | 身長 150 から 180 の heroes のうち、Marvel Comics published の割合を計算する。 | doc, json, md |  |  |  |  |  |  | 未評価 |
| task_408 | hard | Formula 1 | 2008 Australian Grand Prix で champion が最下位 driver より何 percent 速いかを計算する。 | db, doc, md |  |  |  |  |  |  | 未評価 |
| task_415 | hard | Formula 1 | 2009 Singapore Grand Prix の champion constructor reference name と website を特定する。 | db, doc, json, md |  |  |  |  |  |  | 未評価 |
| task_418 | extreme | 医療 | creatinine level が abnormal の患者のうち、70 歳未満の人数を数える。 | doc, md |  |  |  |  |  |  | 未評価 |
| task_420 | hard | Magic: The Gathering | commander format かつ legal status の cards のうち、content warning がない割合を計算する。 | db, doc, md |  |  |  |  |  |  | 未評価 |

## 傾向

- easy は単純な join、filter、count、lookup が中心で。
- medium は SQLite と CSV/JSON の横断参照が増え、平均・割合・最大値・条件付、CSV/JSON の組み合わせが多いき集計が中心になる。
- hard は Markdown document からの情報抽出と構造化データの結合が増え、曖昧な表現や日付・名称の照合が必要になる。
- extreme の `task_418` は document のみから医療条件を読み取り、年齢条件と検査値条件を組み合わせる必要がある。
