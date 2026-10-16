import { ConfigSidebar } from "@/components/config/config-sidebar";

/**
 * Every Settings page (T6): the grouped settings menu on the left at lg (a scrolling chip
 * row above the content below that), the editor to its right.
 */
export default function ConfigLayout({ children }: LayoutProps<"/config">) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-8">
      <aside className="min-w-0">
        <ConfigSidebar />
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
