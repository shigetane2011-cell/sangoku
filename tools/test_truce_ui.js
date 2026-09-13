"use strict";

/* 休戦令の画面で使う純粋関数の契約試験。DOMやブラウザ時計に依存させない。 */
const {
  formatLocalDay, formatTruceDayLabel, formatHourRanges,
  truceDayCanMove, initialTruceDayIndex, truceHoursFor, truceSummaryText,
  tenkaDayStats,
} = require("../sim/webui/app.js");

const fails = [];
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${ok ? "  OK  " : "  NG  "}${name}`
    + (ok ? "" : `\n       actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`));
  if (!ok) fails.push(name);
}

check("連続8枠を時間帯へ畳む", formatHourRanges([0, 1, 2, 3, 4, 5, 6, 7]),
  "0〜8時");
check("分割した時間帯を保つ", formatHourRanges([0, 1, 5, 6]),
  "0〜2時・5〜7時");
check("日またぎを翌時刻で表す", formatHourRanges([22, 23, 0, 1, 2, 3, 4, 5]),
  "22〜翌6時");
check("地方日付に曜日を付ける", formatLocalDay("2026-08-29"), "8/29（土）");
check("今日・明日の呼び名を付ける", formatTruceDayLabel("2026-08-30", 1),
  "8/30（日）明日");

const todayHours = [0, 1, 2, 3, 4, 5, 6, 7];
const tomorrowHours = [8, 9, 10, 11, 12, 13, 14, 15];
const truce = {
  default_hours: [16, 17, 18, 19, 20, 21, 22, 23],
  days: [
    { day: "2026-08-29", hours: todayHours, source: "day",
      locked: Array.from({ length: 24 }, (_, h) => h) },
    { day: "2026-08-30", hours: tomorrowHours, source: "default", locked: [] },
  ],
};

check("全枠締切の今日は編集可能としない", truceDayCanMove(truce.days[0]), false);
const dayIndex = initialTruceDayIndex(truce);
check("今日が動かせなければ明日を選ぶ", dayIndex, 1);
check("明日表示の初期選択は明日の8枠", truceHoursFor(truce, dayIndex),
  tomorrowHours);
check("通常設定タブは通常設定の8枠", truceHoursFor(truce, dayIndex, "default"),
  truce.default_hours);
check("折り畳み要約は通常設定でなく今日の有効設定",
  truceSummaryText(truce), "今日 0〜8時休戦（個別変更）");

check("天下の日別勝敗はBO3一括ごとに数える",
  tenkaDayStats([{ marks: "○○●" }, { marks: "●●○" }, { marks: "○●△" }]),
  { n: 3, w: 1, l: 1, d: 1 });

console.log();
if (fails.length) {
  console.error("失敗:", fails.join("、"));
  process.exit(1);
}
console.log("全部通った");
