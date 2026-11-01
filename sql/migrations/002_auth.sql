ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx
    ON users (lower(username)) WHERE username IS NOT NULL;
