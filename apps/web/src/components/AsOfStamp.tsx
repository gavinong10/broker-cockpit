"use client";

// "Data as of {timestamp}" — client component so the timestamp localizes to
// the VIEWER's locale/timezone, not the server container's (usually UTC).
// suppressHydrationWarning: the server-rendered string may differ from the
// client's localization; the client value wins after hydration.
// Visible to every role: a sync timestamp reveals no dollar amounts.

export default function AsOfStamp({ lastSyncedAt }: { lastSyncedAt: string | null }) {
  return (
    <p className="text-sm text-zinc-500 dark:text-zinc-400">
      {lastSyncedAt === null ? (
        "Data as of: never synced"
      ) : (
        <>
          Data as of{" "}
          <time dateTime={lastSyncedAt} suppressHydrationWarning>
            {new Date(lastSyncedAt).toLocaleString()}
          </time>
        </>
      )}
    </p>
  );
}
