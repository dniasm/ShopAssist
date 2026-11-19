import { BrowserRouter, Route, Routes } from "react-router";
import { AppShell } from "./components/AppShell";
import { AuthProvider } from "./features/auth/AuthContext";
import { CartProvider } from "./features/cart/CartContext";
import { DrawerProvider } from "./features/drawer/DrawerContext";
import { SessionSocketProvider } from "./features/session/SessionSocket";
import { CheckoutPage } from "./pages/CheckoutPage";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OrderDetailPage } from "./pages/OrderDetailPage";
import { OrderProgressPage } from "./pages/OrderProgressPage";
import { OrdersPage } from "./pages/OrdersPage";
import { ProductPage } from "./pages/ProductPage";
import { ShopPage } from "./pages/ShopPage";
import { SystemPage } from "./pages/SystemPage";

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <DrawerProvider>
          <SessionSocketProvider>
            <CartProvider>
              <Routes>
                <Route element={<AppShell />}>
                  <Route index element={<HomePage />} />
                  <Route path="shop" element={<ShopPage />} />
                  <Route path="p/:productId" element={<ProductPage />} />
                  <Route path="checkout" element={<CheckoutPage />} />
                  <Route path="checkout/:orderId" element={<OrderProgressPage />} />
                  <Route path="orders" element={<OrdersPage />} />
                  <Route path="orders/:orderId" element={<OrderDetailPage />} />
                  <Route path="system" element={<SystemPage />} />
                  <Route path="*" element={<NotFoundPage />} />
                </Route>
              </Routes>
            </CartProvider>
          </SessionSocketProvider>
        </DrawerProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
