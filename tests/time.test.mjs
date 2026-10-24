import assert from "node:assert/strict";
import test from "node:test";
import { parseTime, timeLabel, buildRanges } from "../app/web/time.js";

test("time inputs support timestamps, explicit units, and an empty end", () => {
  assert.equal(parseTime("2m"), 120);
  assert.equal(parseTime("2.5m"), 150);
  assert.equal(parseTime("150s"), 150);
  assert.equal(parseTime("150"), 150);
  assert.equal(parseTime("02:30"), 150);
  assert.equal(parseTime("01:02:30"), 3750);
  assert.equal(parseTime("0:02.5"), 2.5);
  assert.equal(parseTime(""), null);
});

test("malformed timestamps cannot silently become a different range", () => {
  for (const input of [
    "-1",
    "1:99",
    "1:60:00",
    "abc",
    "1:2",
    "NaN",
    "Infinity",
  ]) {
    assert.throws(() => parseTime(input));
  }
});

test("labels handle an hour boundary and an unknown duration", () => {
  assert.equal(timeLabel(59.9), "1:00");
  assert.equal(timeLabel(3600), "1:00:00");
  assert.equal(timeLabel(null), "Unknown duration");
});

test("multiple clips preserve separate ranges and their listed order", () => {
  assert.deepEqual(
    buildRanges(
      [
        { start: "01:00", end: "02:00" },
        { start: "10:00", end: "15:00" },
      ],
      1000,
    ),
    [
      { start: 60, end: 120 },
      { start: 600, end: 900 },
    ],
  );
  assert.throws(
    () =>
      buildRanges([
        { start: "1m", end: "2m" },
        { start: "15m", end: "10m" },
      ]),
    /Clip 2/,
  );
  assert.throws(
    () => buildRanges([{ start: "10m", end: "15m" }], 800),
    /beyond/,
  );
  assert.deepEqual(buildRanges([{ start: "2m", end: "" }]), [
    { start: 120, end: null },
  ]);
});
