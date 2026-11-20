/** Moderator console shell. T6.3 owns this route group; message text is shown in memory only. */
export default function ModLayout({ children }: { children: React.ReactNode }) {
  return <div data-console="moderator">{children}</div>
}
