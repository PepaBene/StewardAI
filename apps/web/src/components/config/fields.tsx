"use client";

import { useId, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { cn } from "@/lib/utils";

/*
 * Form fields for the config editors. Each takes the API's 422 errors and the field's
 * dotted path, and shows that field's messages under it (aria-invalid + described-by).
 */

const CONTROL =
  "w-full rounded-md border border-border bg-surface-raised px-3 py-1.5 text-base text-text shadow-xs outline-none placeholder:text-text-muted md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal aria-invalid:ring-2 aria-invalid:ring-warning-signal/30";

type Common = {
  label: string;
  path: string;
  errors: FieldErrors;
  hint?: ReactNode;
  className?: string;
  hideLabel?: boolean;
};

function Messages({ id, messages, hint }: { id: string; messages: string[]; hint?: ReactNode }) {
  return (
    <>
      {hint ? <p className="text-xs text-text-muted">{hint}</p> : null}
      {messages.length > 0 ? (
        <ul id={id} className="space-y-0.5 text-xs text-text">
          {messages.map((m) => (
            <li key={m} className="flex gap-1">
              <span aria-hidden="true" className="text-warning-signal">
                ●
              </span>
              {m}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

export function TextField({
  label,
  path,
  errors,
  hint,
  className,
  hideLabel,
  value,
  onChange,
  ...rest
}: Common & {
  value: string;
  onChange: (value: string) => void;
} & Omit<React.ComponentProps<"input">, "value" | "onChange">) {
  const id = useId();
  const messages = errorsAt(errors, path);
  return (
    <div className={cn("space-y-1", className)}>
      <Label htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
        {label}
      </Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={messages.length > 0 || undefined}
        aria-describedby={messages.length > 0 ? `${id}-errors` : undefined}
        {...rest}
      />
      <Messages id={`${id}-errors`} messages={messages} hint={hint} />
    </div>
  );
}

export function TextAreaField({
  label,
  path,
  errors,
  hint,
  className,
  value,
  onChange,
  rows = 3,
}: Common & { value: string; onChange: (value: string) => void; rows?: number }) {
  const id = useId();
  const messages = [
    ...errorsAt(errors, path),
    // List fields report errors per item ("aliases.2"); show them on the text area.
    ...Object.entries(errors)
      .filter(([p]) => p.startsWith(`${path}.`))
      .flatMap(([p, ms]) => ms.map((m) => `line ${Number(p.slice(path.length + 1)) + 1}: ${m}`)),
  ];
  return (
    <div className={cn("space-y-1", className)}>
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={messages.length > 0 || undefined}
        aria-describedby={messages.length > 0 ? `${id}-errors` : undefined}
        className={CONTROL}
      />
      <Messages id={`${id}-errors`} messages={messages} hint={hint} />
    </div>
  );
}

export function SelectField<T extends string | number>({
  label,
  path,
  errors,
  hint,
  className,
  hideLabel,
  value,
  options,
  onChange,
}: Common & {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  const id = useId();
  const messages = errorsAt(errors, path);
  return (
    <div className={cn("space-y-1", className)}>
      <Label htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
        {label}
      </Label>
      <select
        id={id}
        value={String(value)}
        onChange={(e) => {
          const match = options.find((o) => String(o.value) === e.target.value);
          if (match) onChange(match.value);
        }}
        aria-invalid={messages.length > 0 || undefined}
        className={cn(CONTROL, "h-9 py-1")}
      >
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
      <Messages id={`${id}-errors`} messages={messages} hint={hint} />
    </div>
  );
}
