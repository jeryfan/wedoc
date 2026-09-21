BEGIN;

-- InsertUsers
INSERT INTO "users" (
  "id",
  "name",
  "email",
  "is_system",
  "created_time"
) 
SELECT 
  'appRobot',
  'App Robot',
  'appRobot@system.@UPSTREAM_BRAND@.ai',
  true,
  CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM "users" WHERE "id" = 'appRobot');

COMMIT;
