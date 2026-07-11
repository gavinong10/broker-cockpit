"use client";

// Bull or Bear — guess whether the fake ticker's next move is up or down.
// All state lives in component memory; the price is a pure random walk
// (lib/tickerGame) with no connection to real market data or the worker.

import { useState } from "react";
import { usd } from "@/lib/format";
import {
  applyGuess,
  newGame,
  type GameState,
  type Guess,
} from "@/lib/tickerGame";

export default function TickerGame() {
  const [state, setState] = useState<GameState>(newGame);

  const guess = (g: Guess) => setState((s) => applyGuess(s, g, Math.random));
  const winRate =
    state.rounds > 0 ? Math.round((100 * state.wins) / state.rounds) : null;

  return (
    <section
      aria-label="Bull or Bear game"
      className="flex w-full max-w-md flex-col gap-5 rounded-xl border border-hairline bg-card p-6"
    >
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium text-ink">Bull or Bear</p>
        <span className="micro-label">Fake ticker · CPIT</span>
      </div>

      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-semibold tabular-nums text-ink">
          {usd(state.price)}
        </span>
        {state.last && (
          <span
            role="status"
            className={`text-sm tabular-nums ${
              state.last.to > state.last.from ? "text-gain" : "text-loss"
            }`}
          >
            {state.last.to > state.last.from ? "▲" : "▼"}{" "}
            {usd(Math.abs(state.last.to - state.last.from))} —{" "}
            {state.last.correct ? "called it" : `not ${state.last.guess}`}
          </span>
        )}
      </div>

      <p className="text-[12px] tabular-nums text-ink-3">
        {state.history.map((p) => p.toFixed(2)).join(" · ")}
      </p>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => guess("up")}
          className="rounded-full border border-hairline px-3.5 py-1.5 text-[13px] font-medium text-gain transition-colors hover:bg-hover"
        >
          Higher ▲
        </button>
        <button
          type="button"
          onClick={() => guess("down")}
          className="rounded-full border border-hairline px-3.5 py-1.5 text-[13px] font-medium text-loss transition-colors hover:bg-hover"
        >
          Lower ▼
        </button>
        <button
          type="button"
          onClick={() => setState(newGame())}
          className="ml-auto rounded-full px-3.5 py-1.5 text-[13px] text-ink-2 transition-colors hover:text-ink"
        >
          Reset
        </button>
      </div>

      <div className="flex gap-6 border-t border-hairline pt-4 text-sm tabular-nums text-ink-2">
        <span>
          Streak <span className="font-medium text-ink">{state.streak}</span>
        </span>
        <span>
          Best <span className="font-medium text-ink">{state.best}</span>
        </span>
        <span>
          Win rate{" "}
          <span className="font-medium text-ink">
            {winRate === null ? "—" : `${winRate}%`}
          </span>
          {state.rounds > 0 && (
            <span className="text-ink-3">
              {" "}
              ({state.wins}/{state.rounds})
            </span>
          )}
        </span>
      </div>
    </section>
  );
}
