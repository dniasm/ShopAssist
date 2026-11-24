"""Container healthcheck: exit 0 when the gRPC server on localhost:<port> answers grpc.health.v1 Check with SERVING."""

import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

with grpc.insecure_channel(f"localhost:{sys.argv[1]}") as channel:
    reply = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), timeout=3)
sys.exit(0 if reply.status == health_pb2.HealthCheckResponse.SERVING else 1)
