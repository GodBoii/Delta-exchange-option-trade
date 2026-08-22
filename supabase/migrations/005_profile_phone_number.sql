-- Store the phone number collected during email/password account creation.
-- Run this entire file in Supabase Dashboard > SQL Editor as the project owner.
begin;

alter table public.profiles
  add column if not exists phone_number text;

comment on column public.profiles.phone_number is
  'User contact number in E.164 format. Null for existing and OAuth-only accounts.';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'profiles_phone_number_e164'
      and conrelid = 'public.profiles'::regclass
  ) then
    alter table public.profiles
      add constraint profiles_phone_number_e164
      check (phone_number is null or phone_number ~ '^\+[1-9][0-9]{7,14}$');
  end if;
end;
$$;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name, avatar_url, phone_number)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', split_part(new.email, '@', 1)),
    new.raw_user_meta_data ->> 'avatar_url',
    case
      when new.raw_user_meta_data ->> 'phone_number' ~ '^\+[1-9][0-9]{7,14}$'
        then new.raw_user_meta_data ->> 'phone_number'
      else null
    end
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

update public.profiles profile
set phone_number = auth_user.raw_user_meta_data ->> 'phone_number'
from auth.users auth_user
where profile.id = auth_user.id
  and profile.phone_number is null
  and auth_user.raw_user_meta_data ->> 'phone_number' ~ '^\+[1-9][0-9]{7,14}$';

commit;
