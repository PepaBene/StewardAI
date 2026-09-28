import { ConfigSidebar } from "@/components/config/config-sidebar";

/**
 * Every /config page: the config domains in a sidebar flush with the page's left edge (at
 * lg; a scrolling strip above the content below that), the page to its right.
 */
export default function ConfigLayout({ children }: LayoutProps<"/config">) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:-mt-7 lg:-mb-11 lg:-ml-8 lg:grid-cols-[250px_minmax(0,1fr)] lg:gap-0">
      <aside className="min-w-0 border-b border-border lg:border-r lg:border-b-0 lg:py-[26px]">
        <ConfigSidebar />
      </aside>
      <div className="min-w-0 lg:pt-[26px] lg:pb-11 lg:pl-8">{children}</div>
    </div>
  );
}
