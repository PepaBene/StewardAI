import { ThemeToggle } from "@/components/theme-toggle";

/** Minimal chrome for the sign-in and MFA steps (reachable below aal2): the mark, a centred card. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex h-[60px] max-w-[1440px] items-center justify-between px-4 md:px-8">
          <span className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="grid size-[26px] place-items-center bg-accent font-heading text-lg leading-none font-bold text-accent-foreground"
            >
              S
            </span>
            <span className="font-heading text-[22px] font-semibold tracking-[-0.01em]">StewardAI</span>
          </span>
          <ThemeToggle />
        </div>
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-10 pb-16 sm:pt-[12vh]">
        <div className="w-full max-w-sm space-y-4">
          <p className="kicker text-center">Private instance</p>
          {children}
        </div>
      </main>
    </div>
  );
}
