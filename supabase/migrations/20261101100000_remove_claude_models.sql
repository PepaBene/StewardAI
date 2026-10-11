-- Claude is no longer used. Any active config that still names a claude-* model gets a new
-- version (rows are append-only) that names gpt-6-luna (for haiku models) or gpt-6.1-sol
-- (for every other claude model) instead, drops the claude price rows, and no longer lists
-- "anthropic" as a prompt-override provider. Old versions stay as history.

do $$
declare
  r record;
  b jsonb;
begin
  for r in
    select id, domain, body, schema_version
    from public.config_version
    where active and body::text ~* 'claude-|"anthropic"'
  loop
    b := r.body;

    if r.domain = 'budget' and jsonb_typeof(b -> 'prices') = 'array' then
      b := jsonb_set(
        b,
        '{prices}',
        coalesce(
          (select jsonb_agg(e)
             from jsonb_array_elements(b -> 'prices') e
            where e ->> 'model' not like 'claude-%'),
          '[]'::jsonb
        )
      );
    end if;

    if r.domain = 'prompts' and jsonb_typeof(b -> 'override_providers') = 'object' then
      b := jsonb_set(
        b,
        '{override_providers}',
        coalesce(
          (select jsonb_object_agg(k, kept)
             from (
               select o.key as k,
                      (select jsonb_agg(p)
                         from jsonb_array_elements(o.value) p
                        where p <> '"anthropic"'::jsonb) as kept
                 from jsonb_each(b -> 'override_providers') o
             ) x
            where kept is not null),
          '{}'::jsonb
        )
      );
    end if;

    b := regexp_replace(
           regexp_replace(b::text, '"claude-haiku-[a-z0-9.-]*"', '"gpt-6-luna"', 'g'),
           '"claude-[a-z0-9.-]*"',
           '"gpt-6.1-sol"',
           'g'
         )::jsonb;

    if b <> r.body then
      update public.config_version set active = false where id = r.id;
      insert into public.config_version
        (domain, body, schema_version, parent_id, note, active)
      values
        (r.domain, b, r.schema_version, r.id,
         'Claude removed: its models were replaced by gpt-6-luna / gpt-6.1-sol', true);
    end if;
  end loop;
end
$$;
