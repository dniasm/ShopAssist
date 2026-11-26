"""Facts come back in insertion order, even when a whole batch shares one created_at."""
from tests.test_assistant import as_user, pb, remember  # noqa: F401


def test_same_timestamp_batch_keeps_insertion_order(assistant):
    names = [f"key_{i:02d}" for i in range(30)]
    changes = [pb.FactChange(op="remember", kind="permanent", key=n, value="v") for n in names]
    r = assistant.ApplyFacts(pb.ApplyFactsRequest(changes=changes), metadata=as_user("U-01"))
    assert [f.key for f in r.facts] == names
    listed = assistant.ListFacts(pb.ListFactsRequest(), metadata=as_user("U-01"))
    assert [f.key for f in listed.facts] == names
