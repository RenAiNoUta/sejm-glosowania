/*
 * Liczenie zgodności użytkownika z posłami i klubami.
 * Plik działa w przeglądarce (window.Score) i w Node (require) – dla testów.
 *
 * answers:  { billId: 1 | -1 }       1 = chcę, żeby ustawa przeszła; -1 = nie chcę
 * stances:  { mpIds: [...], bills: { billId: "+-0._" } }
 *           '+' poparł, '-' sprzeciw, '0' wstrzymał się, '.' nieobecny, '_' nie był posłem
 * clubBill: { billId: { club: [poparli, przeciw, wstrzymali, nieobecni] } }
 *
 * Punktacja za jedną ustawę: ten sam kierunek = 1, wstrzymanie = 0,5, przeciwny = 0.
 * Nieobecność domyślnie się nie liczy; z opcją absentAsDisagree liczy się jak 0.
 */
(function (root) {
  "use strict";

  function stanceValue(ch) {
    if (ch === "+") return 1;
    if (ch === "-") return -1;
    if (ch === "0") return 0;
    return null;
  }

  function pointsFor(userAnswer, value) {
    if (value === 0) return 0.5;
    return value === userAnswer ? 1 : 0;
  }

  function scoreMps(answers, stances, opts) {
    opts = opts || {};
    var ids = stances.mpIds;
    var res = ids.map(function (id, i) {
      return { idx: i, id: id, points: 0, counted: 0, voted: 0, absent: 0, eligible: 0, perBill: {} };
    });
    Object.keys(answers).forEach(function (billId) {
      var u = answers[billId];
      var row = stances.bills[billId];
      if (!row || (u !== 1 && u !== -1)) return;
      for (var i = 0; i < row.length; i++) {
        var ch = row[i];
        if (ch === "_") continue;
        var r = res[i];
        r.eligible++;
        r.perBill[billId] = ch;
        if (ch === ".") {
          r.absent++;
          if (opts.absentAsDisagree) r.counted++;
          continue;
        }
        r.voted++;
        r.counted++;
        r.points += pointsFor(u, stanceValue(ch));
      }
    });
    res.forEach(function (r) {
      r.score = r.counted ? r.points / r.counted : null;
    });
    return res;
  }

  function scoreClubs(answers, clubBill, opts) {
    opts = opts || {};
    var acc = {};
    Object.keys(answers).forEach(function (billId) {
      var u = answers[billId];
      var clubs = clubBill[billId];
      if (!clubs || (u !== 1 && u !== -1)) return;
      Object.keys(clubs).forEach(function (club) {
        var c = clubs[club];
        var support = c[0], oppose = c[1], abstain = c[2], absent = c[3];
        var denom = support + oppose + abstain + (opts.absentAsDisagree ? absent : 0);
        if (!denom) return;
        var agree = (u === 1 ? support : oppose) + 0.5 * abstain;
        var a = acc[club] || (acc[club] = { club: club, sum: 0, bills: 0, members: 0 });
        a.sum += agree / denom;
        a.bills++;
        a.members = Math.max(a.members, support + oppose + abstain + absent);
      });
    });
    return Object.keys(acc).map(function (k) {
      var a = acc[k];
      return { club: a.club, score: a.sum / a.bills, bills: a.bills, members: a.members };
    });
  }

  function rank(list, minCounted) {
    return list
      .filter(function (r) { return r.score !== null && (r.counted === undefined || r.counted >= (minCounted || 1)); })
      .sort(function (a, b) {
        if (b.score !== a.score) return b.score - a.score;
        return (b.counted || b.bills || 0) - (a.counted || a.bills || 0);
      });
  }

  var api = { scoreMps: scoreMps, scoreClubs: scoreClubs, rank: rank, stanceValue: stanceValue };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Score = api;
})(this);
