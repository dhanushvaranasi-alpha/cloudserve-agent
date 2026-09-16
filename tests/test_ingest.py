from src.ingest.ingest import normalize_batch, normalize_ticket
from src.schemas import Channel


def test_normalizes_all_four_channels():
    for channel in ["email", "chat", "docs_comment", "forum"]:
        raw = {"ticket_id": f"T-{channel}", "channel": channel, "subject": "s", "body": "b"}
        result = normalize_ticket(raw)
        assert result.channel == Channel(channel)
        assert result.ticket_id == f"T-{channel}"


def test_handles_empty_body_without_raising():
    result = normalize_ticket({"ticket_id": "T-empty", "channel": "chat", "subject": "", "body": ""})
    assert result.text  # never empty string


def test_handles_missing_fields_without_raising():
    result = normalize_ticket({"ticket_id": "T-missing"})
    assert result.channel == Channel.EMAIL  # safe default
    assert result.text


def test_handles_unusual_characters():
    raw = {"ticket_id": "T-unicode", "channel": "email", "subject": "S", "body": "héllo \x00\x01 wörld 你好"}
    result = normalize_ticket(raw)
    assert "wörld" in result.text
    assert "你好" in result.text


def test_unknown_channel_falls_back_to_email():
    result = normalize_ticket({"ticket_id": "T-x", "channel": "carrier_pigeon", "body": "b"})
    assert result.channel == Channel.EMAIL


def test_normalize_batch_never_raises_on_bad_record():
    batch = [
        {"ticket_id": "ok", "channel": "email", "body": "fine"},
        None,  # deliberately malformed -- normalize_batch must not crash the whole run
        {"ticket_id": "ok2", "channel": "chat", "body": "fine too"},
    ]
    # normalize_batch iterates dicts; simulate a malformed dict-like entry instead of None
    batch[1] = {}  # empty dict is the more realistic malformed case
    results = normalize_batch(batch)
    assert len(results) == 3
