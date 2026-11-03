UPDATE vehicles SET profile = json_remove(profile, '$.driver_name') WHERE profile IS NOT NULL AND json_extract(profile, '$.driver_name') IS NOT NULL;
