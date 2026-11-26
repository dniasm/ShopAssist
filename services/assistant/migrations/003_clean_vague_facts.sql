-- Fact rules v3: a value that only points at a saved fact ("favorite color") was never information; drop those rows.
DELETE FROM facts
 WHERE btrim(regexp_replace(lower(value), '\m(my|favorite|favourite|preferred|usual|same|one|colors?|colours?|size|fit|the|as|before|like|that|it)\M|[^a-z0-9]+', ' ', 'g')) = '';
