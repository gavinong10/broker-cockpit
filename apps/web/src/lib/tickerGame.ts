/** Pure logic for the Bull or Bear mini-game: a random-walk "ticker" where the
 * player guesses whether the next tick goes up or down. Randomness is injected
 * (two draws per tick) so the logic stays deterministic and unit-testable; the
 * client component passes Math.random. No real market data is involved. */

export type Guess = "up" | "down";

export type RoundResult = {
  guess: Guess;
  correct: boolean;
  from: number;
  to: number;
};

export type GameState = {
  price: number;
  /** Oldest → newest, includes the current price. Capped at HISTORY_LEN. */
  history: number[];
  streak: number;
  best: number;
  rounds: number;
  wins: number;
  last: RoundResult | null;
};

export const START_PRICE = 100;
export const MIN_PRICE = 1;
export const HISTORY_LEN = 12;

export function newGame(): GameState {
  return {
    price: START_PRICE,
    history: [START_PRICE],
    streak: 0,
    best: 0,
    rounds: 0,
    wins: 0,
    last: null,
  };
}

/** One tick: direction from the first draw (< 0.5 is up), size 0.2%–2.5% of
 * price from the second. The move is rounded to cents but never below one
 * cent, and a move that would cross MIN_PRICE bounces upward instead — so the
 * price always actually changes and every round has a definite winner. */
export function nextPrice(price: number, rand: () => number): number {
  const up = rand() < 0.5;
  const move = Math.max(
    0.01,
    Math.round(price * (0.002 + rand() * 0.023) * 100) / 100,
  );
  const next = up || price - move < MIN_PRICE ? price + move : price - move;
  return Math.round(next * 100) / 100;
}

export function applyGuess(
  state: GameState,
  guess: Guess,
  rand: () => number,
): GameState {
  const to = nextPrice(state.price, rand);
  const correct = guess === "up" ? to > state.price : to < state.price;
  const streak = correct ? state.streak + 1 : 0;
  return {
    price: to,
    history: [...state.history, to].slice(-HISTORY_LEN),
    streak,
    best: Math.max(state.best, streak),
    rounds: state.rounds + 1,
    wins: state.wins + (correct ? 1 : 0),
    last: { guess, correct, from: state.price, to },
  };
}
