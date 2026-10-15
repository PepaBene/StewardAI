drop policy if exists backend_explain on public.owner_override;
revoke update (reason_code) on public.owner_override from app_backend;
