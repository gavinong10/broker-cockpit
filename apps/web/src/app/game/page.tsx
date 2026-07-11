import SiteHeader from "@/components/SiteHeader";
import TickerGame from "@/components/TickerGame";
import { getViewerContext } from "@/lib/viewerContext";

export default async function GamePage() {
  const { role } = await getViewerContext();

  return (
    <>
      <SiteHeader role={role} active="/game" />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-10 px-6 py-10 font-sans">
        <TickerGame />
        <p className="text-[12px] text-ink-3">
          CPIT is a pure random walk generated in your browser — no real market
          data, no broker connection, and nothing is saved. Each tick moves
          0.2–2.5% in a coin-flip direction; see how long a streak you can call.
        </p>
      </main>
    </>
  );
}
