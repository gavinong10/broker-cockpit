// "Data as of {timestamp}" — rendered in America/Chicago with an explicit
// zone label (e.g. "CDT") so freshness is unambiguous wherever it's viewed.
// Fixed zone = identical server/client output, so this can be a server
// component (no hydration concerns). Visible to every role: a sync
// timestamp reveals no dollar amounts.

const CHICAGO_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

export default function AsOfStamp({ lastSyncedAt }: { lastSyncedAt: string | null }) {
  return (
    <p className="text-sm text-zinc-500 dark:text-zinc-400">
      {lastSyncedAt === null ? (
        "Data as of: never synced"
      ) : (
        <>
          Data as of{" "}
          <time dateTime={lastSyncedAt}>
            {CHICAGO_FMT.format(new Date(lastSyncedAt))}
          </time>
        </>
      )}
    </p>
  );
}
