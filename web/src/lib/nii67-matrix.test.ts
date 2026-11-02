import assert from "node:assert/strict";
import { test } from "node:test";

import { MATRIX_STATE_FIELDS, cardFromUnknown, defaultCard, updateDoor } from "./nii67.ts";

test("cardFromUnknown keeps matrix fields from server", () => {
  const card = cardFromUnknown({
    doors: [
      { id: "door1", move: "death_world", state: { death_cause: "пепел" }, blocks: [] },
      { id: "door2", move: "creature", creature_action: "kill", blocks: [] },
    ],
  });
  assert.equal(card.doors[0].move, "death_world");
  assert.equal(card.doors[0].state.death_cause, "пепел");
  assert.equal(card.doors[1].creature_action, "kill");
});

test("cardFromUnknown drops unknown move and action", () => {
  const card = cardFromUnknown({
    doors: [
      { id: "door1", move: "fly", creature_action: "hug", blocks: [] },
      { id: "door2", blocks: [] },
    ],
  });
  assert.equal(card.doors[0].move, "");
  assert.equal(card.doors[0].creature_action, "");
});

test("updateDoor sets move and clears action outside creature", () => {
  let card = updateDoor(defaultCard(), "door1", {
    move: "creature",
    creature_action: "kill",
    state: { death_cause: "удар" },
  });
  assert.equal(card.doors[0].move, "creature");
  assert.equal(card.doors[0].creature_action, "kill");
  assert.equal(card.doors[0].state.death_cause, "удар");

  card = updateDoor(card, "door1", { move: "neutral" });
  assert.equal(card.doors[0].move, "neutral");
  assert.equal(card.doors[0].creature_action, "");
  assert.equal(card.doors[0].state.death_cause, "удар");
});

test("matrix state has every field from the data map", () => {
  const state = defaultCard().doors[0].state;
  assert.equal(Object.keys(state).length, 11);
  assert.deepEqual(Object.keys(state).sort(), MATRIX_STATE_FIELDS.map((item) => item.key).sort());
});
