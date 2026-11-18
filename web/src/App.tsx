import { BrowserRouter, Route, Routes } from "react-router";
import { AppShell } from "./components/AppShell";
import { AuthProvider } from "./features/auth/AuthContext";
import { DrawerProvider } from "./features/drawer/DrawerContext";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProductPage } from "./pages/ProductPage";
import { ShopPage } from "./pages/ShopPage";
import { SystemPage } from "./pages/SystemPage";

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <DrawerProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<HomePage />} />
              <Route path="shop" element={<ShopPage />} />
              <Route path="p/:productId" element={<ProductPage />} />
              <Route path="system" element={<SystemPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </DrawerProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
