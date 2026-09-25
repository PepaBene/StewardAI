import { ThemeToggle } from "@/components/theme-toggle";

/** Minimal chrome for the sign-in and MFA steps (reachable below aal2). */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 items-center justify-between px-4">
        <span className="text-base font-semibold tracking-tight">StewardAI</span>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-8 pb-16 sm:pt-16">
        <div className="w-full max-w-sm">{children}</div>
      </main>
    </div>
  );
}
