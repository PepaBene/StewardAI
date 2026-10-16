import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Six-digit TOTP code input, keyboard- and password-manager-friendly. */
export function TotpCodeField({ disabled }: { disabled?: boolean }) {
  return (
    <div className="space-y-2">
      <Label htmlFor="code">6-digit code</Label>
      <Input
        id="code"
        name="code"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]{6}"
        maxLength={6}
        required
        disabled={disabled}
        className="num font-mono"
      />
    </div>
  );
}

export function readTotpCode(form: HTMLFormElement): string {
  return String(new FormData(form).get("code") ?? "").replace(/\s+/g, "");
}
