// Testy logiki dopasowania (node tests/test_score.js)
const assert = require("assert");
const Score = require("../docs/score.js");

const stances = {
  mpIds: [1, 2, 3, 4],
  //        mp1 mp2 mp3 mp4
  bills: {
    a: "+-0.",  // ustawa A
    b: "++-_",  // ustawa B (mp4 nie był jeszcze posłem)
  },
};

// Użytkownik chce A i nie chce B
const answers = { a: 1, b: -1 };

let r = Score.scoreMps(answers, stances);
assert.strictEqual(r[0].score, 0.5);       // A zgodnie (1), B niezgodnie (0)
assert.strictEqual(r[1].score, 0);         // A przeciw, B za
assert.strictEqual(r[2].score, 0.75);      // A wstrzymał (0,5), B przeciw jak user (1)
assert.strictEqual(r[3].score, null);      // tylko nieobecność, nic do policzenia
assert.strictEqual(r[3].eligible, 1);      // B się nie liczy – nie był posłem
assert.strictEqual(r[3].absent, 1);

r = Score.scoreMps(answers, stances, { absentAsDisagree: true });
assert.strictEqual(r[3].score, 0);         // nieobecność = niezgodność

const ranked = Score.rank(Score.scoreMps(answers, stances), 1);
assert.deepStrictEqual(ranked.map((x) => x.id), [3, 1, 2]);

const clubBill = {
  a: { X: [8, 2, 0, 0], Y: [0, 10, 0, 5] },
  b: { X: [10, 0, 0, 0], Y: [2, 6, 2, 0] },
};
const clubs = Object.fromEntries(Score.scoreClubs(answers, clubBill).map((c) => [c.club, c]));
assert.ok(Math.abs(clubs.X.score - (0.8 + 0) / 2) < 1e-9);
assert.ok(Math.abs(clubs.Y.score - (0 + 0.7) / 2) < 1e-9);  // B: (6 + 0,5*2)/10
const clubsAbs = Object.fromEntries(Score.scoreClubs(answers, clubBill, { absentAsDisagree: true }).map((c) => [c.club, c]));
assert.ok(Math.abs(clubsAbs.Y.score - (0 + 0.7) / 2) < 1e-9);  // A: 0/15 nadal 0

// odpowiedzi do nieistniejących ustaw są ignorowane
assert.strictEqual(Score.scoreMps({ zzz: 1 }, stances)[0].score, null);

console.log("test_score.js: OK");
