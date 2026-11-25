-- Fact rules v2: colours are a list (liked_colors), so the old single favorite_color rows move there.
-- A row whose value the user already has under liked_colors is dropped instead of duplicated.
DELETE FROM facts f USING facts g
 WHERE f.key IN ('favorite_color', 'favorite_colour', 'favorite_colors')
   AND g.user_id = f.user_id AND g.key = 'liked_colors' AND lower(g.value) = lower(f.value);
UPDATE facts SET key = 'liked_colors' WHERE key IN ('favorite_color', 'favorite_colour', 'favorite_colors');
