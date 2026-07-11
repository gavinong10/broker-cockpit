import { describe, expect, it } from "vitest";
import {
  applyGuess,
  HISTORY_LEN,
  MIN_PRICE,
  newGame,
  nextPrice,
  START_PRICE,
  type GameState,
} from "./tickerGame";

/** Feeds a fixed sequence of draws; two are consumed per tick. */
function seq(...values: number[]): () => number {
  let i = 0;
  return () => values[i++];
}

describe("newGame", () => {
  it("starts at the base price with empty stats", () => {
    expect(newGame()).toEqual({
      price: START_PRICE,
      history: [START_PRICE],
      streak: 0,
      best: 0,
      rounds: 0,
      wins: 0,
      last: null,
    });
  });
});

describe("nextPrice", () => {
  it("moves up when the direction draw is below 0.5", () => {
    // magnitude draw 0.5 → 100 * (0.002 + 0.0115) = 1.35
    expect(nextPrice(100, seq(0.0, 0.5))).toBe(101.35);
  });
  it("moves down when the direction draw is 0.5 or above", () => {
    // magnitude draw 0 → minimum 0.2% move
    expect(nextPrice(100, seq(0.9, 0.0))).toBe(99.8);
  });
  it("never returns the same price — tiny moves round up to one cent", () => {
    // 2 * 0.002 = 0.004, rounds to 0.00 → clamped to 0.01
    expect(nextPrice(2, seq(0.9, 0.0))).toBe(1.99);
  });
  it("bounces up instead of crossing the price floor", () => {
    expect(nextPrice(MIN_PRICE, seq(0.9, 0.0))).toBe(1.01);
  });
});

describe("applyGuess", () => {
  it("counts a correct guess and extends the streak", () => {
    const s1 = applyGuess(newGame(), "up", seq(0.0, 0.5));
    expect(s1.price).toBe(101.35);
    expect(s1.last).toEqual({ guess: "up", correct: true, from: 100, to: 101.35 });
    expect(s1.streak).toBe(1);
    expect(s1.best).toBe(1);
    expect(s1.rounds).toBe(1);
    expect(s1.wins).toBe(1);
  });
  it("resets the streak on a wrong guess but keeps the best", () => {
    let s: GameState = newGame();
    s = applyGuess(s, "up", seq(0.0, 0.5)); // correct
    s = applyGuess(s, "up", seq(0.0, 0.5)); // correct — streak 2
    s = applyGuess(s, "down", seq(0.0, 0.5)); // wrong
    expect(s.streak).toBe(0);
    expect(s.best).toBe(2);
    expect(s.rounds).toBe(3);
    expect(s.wins).toBe(2);
    expect(s.last?.correct).toBe(false);
  });
  it("appends to history and caps its length", () => {
    let s: GameState = newGame();
    for (let i = 0; i < HISTORY_LEN + 5; i++) {
      s = applyGuess(s, "up", seq(0.0, 0.5));
    }
    expect(s.history).toHaveLength(HISTORY_LEN);
    expect(s.history[s.history.length - 1]).toBe(s.price);
  });
  it("does not mutate the previous state", () => {
    const before = newGame();
    applyGuess(before, "up", seq(0.0, 0.5));
    expect(before).toEqual(newGame());
  });
});
