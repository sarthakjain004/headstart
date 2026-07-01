from headstart.bot import build_notifications, handle_update


def _msg(text, chat_id=7):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat_id}}}


def test_start_registers_and_welcomes():
    subs = {}
    out = handle_update(_msg("/start"), subs)
    assert subs["7"] == {}
    assert out[0][0] == "7" and "HeadStart" in out[0][1]


def test_set_status_clear_stop():
    subs = {}
    handle_update(_msg("/start"), subs)
    handle_update(_msg("/q backend engineer"), subs)
    assert subs["7"]["q"] == "backend engineer"
    handle_update(_msg("/remote"), subs)
    assert subs["7"].get("remote") is True
    handle_update(_msg("/remote"), subs)  # toggle off -> dropped
    assert "remote" not in subs["7"]
    out = handle_update(_msg("/status"), subs)
    assert "keywords: backend engineer" in out[0][1]
    handle_update(_msg("/clear"), subs)
    assert subs["7"] == {}
    handle_update(_msg("/stop"), subs)
    assert "7" not in subs


def test_invalid_ats_rejected():
    subs = {}
    handle_update(_msg("/start"), subs)
    out = handle_update(_msg("/ats workday"), subs)
    assert "must be" in out[0][1].lower()
    assert "ats" not in subs["7"]


def test_build_notifications_seeds_then_alerts_matching_only():
    state = {"seen_job_ids": [], "subscribers": {}}
    j1 = {
        "id": "a",
        "title": "Backend",
        "company": "X",
        "department": None,
        "location": "Remote",
        "remote": True,
        "ats": "greenhouse",
        "url": "u1",
    }
    assert build_notifications([j1], state) == []  # first run only seeds
    assert state["seen_job_ids"] == ["a"]

    state["subscribers"]["123"] = {"remote": True}
    j2 = {
        "id": "b",
        "title": "Onsite",
        "company": "Y",
        "department": None,
        "location": "NYC",
        "remote": False,
        "ats": "lever",
        "url": "u2",
    }
    j3 = {
        "id": "c",
        "title": "SRE",
        "company": "Z",
        "department": None,
        "location": "Remote",
        "remote": True,
        "ats": "ashby",
        "url": "u3",
    }
    out = build_notifications([j1, j2, j3], state)
    assert len(out) == 1 and out[0][0] == "123" and "SRE" in out[0][1]
    assert set(state["seen_job_ids"]) == {"a", "b", "c"}
