-- A fleet driver's PUT /me/profile wrote their name onto the truck every driver of it uses (bug 2); the name
-- lives in drivers.display_name now and comes off the trucks.
UPDATE vehicles SET profile = profile - 'driver_name' WHERE profile IS NOT NULL AND profile ? 'driver_name';
