"""Generate Python gRPC code from protos/ into the repo root (package shopassist_proto)."""
import pathlib
import sys

from grpc_tools import protoc

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "protos"

protos = [str(p) for p in PROTO_DIR.rglob("*.proto")]
if not protos:
    sys.exit("no .proto files found under protos/")

args = [
    "grpc_tools.protoc",
    f"-I{PROTO_DIR}",
    f"--python_out={ROOT}",
    f"--pyi_out={ROOT}",
    f"--grpc_python_out={ROOT}",
    *protos,
]
code = protoc.main(args)
if code != 0:
    sys.exit(f"protoc failed with exit code {code}")
(ROOT / "shopassist_proto" / "__init__.py").touch()
print(f"generated code for {len(protos)} proto file(s)")
