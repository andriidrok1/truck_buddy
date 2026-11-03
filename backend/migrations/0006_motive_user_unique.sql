-- One Motive user is one driver of a fleet (set_motive_user checks it under a lock); the index makes the store
-- hold it too. A fleet that already has two drivers on one Motive user stops the migration with their names.
DO $$
DECLARE dup TEXT;
BEGIN
    SELECT string_agg(org_id || ' / Motive user ' || motive_user_id, ', ') INTO dup FROM (
        SELECT org_id, motive_user_id FROM drivers WHERE motive_user_id IS NOT NULL
        GROUP BY org_id, motive_user_id HAVING count(*) > 1) d;
    IF dup IS NOT NULL THEN
        RAISE EXCEPTION 'drivers share a Motive user, unmap one first: %', dup;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS drivers_org_motive_user ON drivers (org_id, motive_user_id) WHERE motive_user_id IS NOT NULL;
