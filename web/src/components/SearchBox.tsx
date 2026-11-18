import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { parseCatalogQuery, serializeCatalogQuery, withText } from "../features/catalog/query";
import { Icon } from "./Icon";
import styles from "./SearchBox.module.css";

/** The header search. It always lands on the catalog: on /shop it keeps the active filters and replaces only the text,
 *  elsewhere it starts a fresh search. The box shows the URL's `q`, so Back and shared links stay truthful. */
export function SearchBox() {
  const location = useLocation();
  const navigate = useNavigate();
  const onShop = location.pathname === "/shop";
  const urlText = onShop ? parseCatalogQuery(location.search).q : "";
  const [text, setText] = useState(urlText);

  useEffect(() => setText(urlText), [urlText]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const base = onShop ? parseCatalogQuery(location.search) : parseCatalogQuery("");
    const search = serializeCatalogQuery(withText(base, text));
    navigate({ pathname: "/shop", search: search ? `?${search}` : "" });
  }

  return (
    <form className={styles.form} role="search" onSubmit={submit}>
      <label htmlFor="global-search" className="sr-only">Search products</label>
      <span className={styles.glyph}><Icon name="search" /></span>
      <input
        id="global-search"
        className={styles.input}
        type="search"
        name="q"
        value={text}
        maxLength={200}
        enterKeyHint="search"
        autoComplete="off"
        placeholder="Search products"
        onChange={(e) => setText(e.target.value)}
      />
    </form>
  );
}
