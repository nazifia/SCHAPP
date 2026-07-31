-- Each school is provisioned into its own `schapp_<school>` database at
-- runtime, so the application account must be able to create and drop those.
--
-- A grant on a database *pattern* is what permits CREATE DATABASE for names
-- matching it, so no global CREATE/DROP is needed — and must not be given.
-- The `\_` escapes the wildcard: plain `schapp_%` would also match `schappX…`
-- and anything else another application put on a shared server.
GRANT ALL PRIVILEGES ON `schapp`.* TO 'schapp'@'%';
GRANT ALL PRIVILEGES ON `schapp\_%`.* TO 'schapp'@'%';
FLUSH PRIVILEGES;
