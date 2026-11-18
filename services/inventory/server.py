"""InventoryService gRPC server. Run: python -m services.inventory.server"""
import logging
from concurrent import futures

import grpc
from dotenv import load_dotenv

from services.common import SEED_DIR, bind_address, env_path, serve
from services.inventory import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc


class InventoryServicer(pb_grpc.InventoryServiceServicer):
    def __init__(self, db_path):
        self.db_path = db_path

    def GetStock(self, request, context):
        rows = db.get_stock(self.db_path, request.product_id, request.size)
        return pb.StockResponse(
            levels=[pb.StockLevel(product_id=r["product_id"], size=r["size"], quantity=r["quantity"]) for r in rows]
        )


def create_server(db_path, seed_path, address="127.0.0.1:0"):
    """Build (not start) the server. Returns (server, bound_port); port 0 picks a free one."""
    db.init_db(db_path, seed_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(db_path), server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"could not bind InventoryService to {address}")
    return server, port


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s inventory %(levelname)s %(message)s")
    address = bind_address("INVENTORY_ADDR", "localhost:50052")
    server, _ = create_server(env_path("INVENTORY_DB", "data/inventory.db"), SEED_DIR / "inventory.json", address)
    serve(server, "inventory", address)


if __name__ == "__main__":
    main()
