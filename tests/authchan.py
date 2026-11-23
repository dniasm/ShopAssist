"""Channels for tests that call a service directly: signed as a caller (the orchestrator by default), as real clients are."""
import grpc

from shopassist_proto import svcauth


class _SyncAuth(grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor):
    def __init__(self, caller: str):
        self._caller, self._key = caller, svcauth.load_key(caller)

    def _details(self, details):
        class Details(grpc.ClientCallDetails):
            pass

        d = Details()
        d.method, d.timeout, d.credentials = details.method, details.timeout, details.credentials
        d.wait_for_ready, d.compression = details.wait_for_ready, details.compression
        d.metadata = svcauth.signed_metadata(details, self._key, self._caller, None)
        return d

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return continuation(self._details(client_call_details), request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        return continuation(self._details(client_call_details), request)


def channel(address: str, caller: str = "orchestrator") -> grpc.Channel:
    return grpc.intercept_channel(grpc.insecure_channel(address), _SyncAuth(caller))


def aio_channel(address: str, caller: str = "orchestrator") -> grpc.aio.Channel:
    return svcauth.aio_channel(address, caller)
