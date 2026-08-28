# バランス計器の入口

次の作業者（人・AIを問わず）は、バランスへ触る前にこのページと
`docs/balance/baselines/latest.md` を読む。

```bash
# 変更中の短時間検査（固定相手・左右両側・明示seed）
python3 tools/balance_suite.py run --profile quick \
  --baseline docs/balance/baselines/latest.json

# 共有するJSON＋Markdownを作る
python3 tools/balance_suite.py run --profile quick \
  --output docs/balance/baselines/latest.json

# リリース候補でだけ使う。結果を見てから同じ候補を調整しない
python3 tools/balance_suite.py run --profile release \
  --output docs/balance/baselines/<commit>-release.json
```

個別に回す場合:

```bash
python3 tools/balance_suite.py check
python3 tools/balance_suite.py distribution
python3 tools/balance_suite.py battle --profile standard
python3 tools/balance_suite.py archetype --profile standard
python3 tools/balance_suite.py cadence --profile standard
python3 tools/one_ruler.py --json one-ruler.json
```

## 何を保存しているか

`fixtures-v1.json` は、commit `8986fc8` 時点の次の編成を、陣形・並び順を含めて
固定したスナップショットである。

| 集合 | 数 | 扱い |
|---|---:|---|
| チャッピー | 1登録（3部隊） | 攻略対象 |
| 破陣 | 1登録（3部隊） | 当時の最終対抗セット |
| official24 | 24登録 | 調整に何度も使った訓練集合 |
| special48 | 48登録 | 未見・試作・特殊。ただし破陣の最終選定に使ったため現在は `retired_validation` |
| final_blind | 0登録 | 次回リリース判定用の封印枠 |

`special48` を「未見48」と呼び続けても、もう盲検ではない。新しい候補をこの集合で
選んだ時点で、その候補にとっても訓練データになる。`final_blind` は別担当が作り、
調整を終えるまで中身を見ない。

## 計器の役割

| 計器 | 測るもの | 測れないもの |
|---|---|---|
| `check` | 名簿120枚、登録合法性、固定fixtureのドリフト、設計検算 | 実戦の強さ |
| `distribution` | 人物・兵種・役割・勢力・コスト・発動型の採用偏り | 採用理由、勝因 |
| `battle` | 破陣対固定集合。戦場別・BO3・最悪相手 | 未探索の最適応答 |
| `archetype` | 7つの陣形×前衛×後衛型の相性表 | 個札単体の値札 |
| `cadence` | 同じ戦場・陣形・満額で手数0〜6枚を積む局所的な限界効果 | 発動型だけの純粋因果、3部隊へ分ける人物重複禁止の機会費用 |
| `one_ruler` | 合法な行内での個札残差 | 壁として線を保つ部隊仕事 |

どれも単一の総合点へ足さない。個札が高くても部隊として弱い騎兵の事例があるため、
`one_ruler` と `archetype` は必ず組で読む。

`cadence` は1部隊へ手数札を集中させたときの危険を測る。4〜6枚が強くても、その札は
残り2戦場で使えないため、3部隊登録全体の最適解を直接意味しない。

## 採用分布の読み方

生の出現数を120枚一様と比較すると、雁行の後衛4枠があるだけで弓兵が強く見える。
新計器は、前衛・後衛へ合法に置けるカード集合を分母にした
`row_conditioned_expected` と `lift` を併記する。

それでも次は警報とする。

- 行条件補正後の採用liftが `0.3未満` または `2.5超`
- 最多カードが編成の `50%超` に出る
- 120枚中の有効カード枚数が `80未満`

ただし、この分母にはコスト上限、同一人物禁止、生成器の性格・強欲度が入っていない。
警報は「カードを直せ」ではなく「分母を追加して原因を分離せよ」の意味である。

## 比較可能性を守るmanifest

JSONレポートは毎回次を保存する。

- git commit・dirty状態
- `generals.csv` / `skills.csv` / `traits.csv` のSHA-256
- 相手集合とfixture元commit
- profile、seed一覧、左右、dt
- ゲージ、余勢、余剰、陣形の主要定数
- Pythonと実行環境

「ラダー幅28.6」と「幅55.6」のような数字は、相手集合・人数・seed・左右が違えば
別の量である。manifestが一致しない結果を時系列の増減として扱わない。

## profileの使い分け

| profile | 用途 | 反復 |
|---|---|---|
| `quick` | 実装中の配線切れ・大崩れ | 変更ごと |
| `standard` | 数値を採用する前の判断 | 節目ごと |
| `release` | 候補を凍結した後の確認 | 1候補につき1回 |

`quick` の勝率を細かく合わせない。seedが少ないため、見るのは登録違反、符号反転、
支配型の出現、手数密度の単調暴走など大きな変化だけである。

`--baseline` はprofile・dt・seed・左右・相手集合が一致した場合だけ勝率差を出す。
どれかが違えば「protocol不一致」で止め、見かけの増減を作らない。

## 旧計器の扱い

`tools/price_audit.py` は不合法配置を混ぜることが判明したため通常実行を停止した。
弓兵を魚鱗の先頭へ置き、さらに6枠すべてへ回すので、司馬懿などの残差を値付けへ
使えない。旧資料の再現に限り `--legacy-invalid` で動く。

## 変更時の最小手順

1. `check` を通す。
2. 個札を触ったら `one_ruler`、兵種・陣形を触ったら `archetype`、手数型を触ったら
   `cadence` を回す。
3. `battle` でチャッピー・official24・special48への退行を確認する。
4. JSONのmanifestが比較対象と同じか確認してから差を読む。
5. 使った集合は以後ひとつ下の区分へ落とす（盲検 → retired_validation → training）。
