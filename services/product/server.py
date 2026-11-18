"""ProductService gRPC server. Run: python -m services.product.server"""
import logging
from concurrent import futures

import grpc
from dotenv import load_dotenv

from services.common import SEED_DIR, bind_address, env_path, serve
from services.product import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc


def _to_pb(row) -> pb.Product:
    return pb.Product(
        product_id=row["product_id"], name=row["name"], category=row["category"],
        color=row["color"], price=row["price"], description=row["description"],
    )


class ProductServicer(pb_grpc.ProductServiceServicer):
    def __init__(self, db_path):
        self.db_path = db_path

    def SearchProducts(self, request, context):
        rows = db.search_products(self.db_path, request.text, request.category, request.max_price, request.limit)
        return pb.SearchProductsResponse(products=[_to_pb(r) for r in rows])

    def GetProduct(self, request, context):
        row = db.get_product(self.db_path, request.product_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"product {request.product_id} not found")
        return _to_pb(row)


def create_server(db_path, seed_path, address="127.0.0.1:0"):
    """Build (not start) the server. Returns (server, bound_port); port 0 picks a free one."""
    db.init_db(db_path, seed_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_ProductServiceServicer_to_server(ProductServicer(db_path), server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"could not bind ProductService to {address}")
    return server, port


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s product %(levelname)s %(message)s")
    address = bind_address("PRODUCT_ADDR", "localhost:50051")
    server, _ = create_server(env_path("PRODUCT_DB", "data/products.db"), SEED_DIR / "products.json", address)
    serve(server, "product", address)


if __name__ == "__main__":
    main()
