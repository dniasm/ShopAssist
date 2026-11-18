import { Link, useLocation } from "react-router";
import { EmptyState } from "../components/StateBlocks";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

export function NotFoundPage() {
  const { pathname } = useLocation();
  useDocumentTitle("Page not found");
  return (
    <>
      <div className="title-row"><h1>Page not found</h1></div>
      <EmptyState
        title="There is nothing at this address"
        action={(<><Link to="/" className="btn primary">Go home</Link><Link to="/shop" className="btn">Browse the shop</Link></>)}
      >
        <code>{pathname}</code> is not a page in this shop.
      </EmptyState>
    </>
  );
}
