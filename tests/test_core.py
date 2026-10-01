from datetime import date

import pytest

from app.connectors import CONNECTORS, build_ics
from app.dates import resolve_due
from app.evaluation import run_eval
from app.extract_rules import classify_destination, extract_with_rules
from app.transcript import TranscriptError, load_sample, parse_transcript

THURSDAY = date(2026, 10, 1)


def sample(slug: str):
    return parse_transcript(load_sample(slug, THURSDAY))


class TestTranscript:
    def test_parses_header_participants_and_turns(self):
        t = sample("northfield-pilot-scoping")
        assert t.title == "Northfield Health – Pilot scoping call"
        assert t.meeting_date == THURSDAY
        brooks = t.participant("Alan")
        assert brooks and brooks.is_external and brooks.organization == "Northfield Health"
        assert t.utterances[0].timestamp == "00:00:04"
        assert t.external_orgs == ["Northfield Health"]

    def test_unlisted_speakers_become_internal_participants(self):
        t = parse_transcript("[00:01] Alex: I'll send the notes tomorrow.")
        assert t.participant("Alex") is not None
        assert not t.participant("Alex").is_external

    def test_rejects_text_without_utterances(self):
        with pytest.raises(TranscriptError):
            parse_transcript("just some notes without speakers")


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("by Friday", date(2026, 10, 2)),
        ("by Thursday", date(2026, 10, 8)),  # same weekday -> next week
        ("tomorrow", date(2026, 10, 2)),
        ("within a week", date(2026, 10, 8)),
        ("by the end of next week", date(2026, 10, 9)),
        ("before the 14th", date(2026, 10, 14)),
        ("two weeks from now", date(2026, 10, 15)),
        ("next week and send the invite today", date(2026, 10, 8)),
        ("no date here", None),
    ],
)
def test_resolve_due(phrase, expected):
    assert resolve_due(phrase, THURSDAY) == expected


def test_destination_prefers_explicit_tool_mentions():
    assert classify_destination("Produce the diagram", "I'll do it and track it in Notion", True) == "notion"
    assert classify_destination("Book the onboarding session", "", True) == "calendar"
    assert classify_destination("Draft the help-center article", "once the fix is out", False) == "notion"
    assert classify_destination("Send the proposal", "", True) == "attio"


class TestRulesExtractor:
    def test_delegation_and_acknowledged_requests(self):
        a = extract_with_rules(sample("summit-team-plan"))
        by_owner = {(i.owner, i.destination) for i in a.action_items}
        assert ("Dev Patel", "linear") in by_owner  # "I'll ask Dev to scope ..."
        assert ("Javier Monestel", "attio") in by_owner  # "Javier, can you ...?" -> "Will do."
        assert a.decisions and "twenty percent" in a.decisions[0].text

    def test_restated_acceptance_is_not_double_counted(self):
        a = extract_with_rules(sample("leadership-sync"))
        hiring = [i for i in a.action_items if "hiring page" in i.title.lower()]
        assert len(hiring) == 1 and hiring[0].owner == "Javier Monestel"

    def test_pronouns_are_resolved_from_our_side(self):
        titles = [i.title for i in extract_with_rules(sample("investor-checkin")).action_items]
        assert "Intro us to Dr. Kim" in titles
        assert "Send Nora the October metrics deck" in titles

    def test_every_item_has_verbatim_evidence(self):
        t = sample("northfield-pilot-scoping")
        lines = {u.timestamp: u.text for u in t.utterances}
        for item in extract_with_rules(t).action_items:
            assert item.evidence.quote in lines[item.evidence.timestamp]

    def test_internal_meetings_get_no_external_email(self):
        assert extract_with_rules(sample("leadership-sync")).follow_up_email is None
        email = extract_with_rules(sample("northfield-pilot-scoping")).follow_up_email
        assert email and email.to == ["Dr. Alan Brooks"]


def test_eval_dev_set_stays_green():
    """Regression guard for the rules engine on the meetings it was built against."""
    report = run_eval("rules").split("dev")
    assert report.f1 >= 0.95
    assert report.destination_accuracy >= 0.95


class TestConnectors:
    item = extract_with_rules(sample("northfield-pilot-scoping")).action_items

    def test_linear_payload_is_a_graphql_issue_create(self):
        dev = next(i for i in self.item if i.destination == "linear")
        req = CONNECTORS["linear"].build(dev, "Pilot call")
        assert req.url == "https://api.linear.app/graphql"
        assert "issueCreate" in req.body["query"]
        assert req.body["variables"]["input"]["dueDate"] == "2026-10-14"

    def test_attio_task_links_the_partner_company(self):
        sam = next(i for i in self.item if i.owner == "Sam Rivera")
        body = CONNECTORS["attio"].build(sam, "Pilot call").body["data"]
        assert body["linked_records"][0]["target_object"] == "companies"
        assert body["deadline_at"].startswith("2026-10-02")
        assert body["is_completed"] is False

    def test_notion_uses_data_source_parent(self):
        notion = next(i for i in self.item if i.destination == "notion")
        req = CONNECTORS["notion"].build(notion, "Pilot call")
        assert "data_source_id" in req.body["parent"]
        assert req.headers["Notion-Version"] == "2026-03-11"

    def test_secrets_are_redacted_in_previews(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "lin_api_secret")
        req = CONNECTORS["linear"].build(self.item[0], "x").redacted()
        assert req.headers["Authorization"] == "<redacted>"

    def test_ics_event(self):
        cal = next(i for i in self.item if i.destination == "calendar")
        ics = build_ics(cal, "Pilot call")
        assert ics.startswith("BEGIN:VCALENDAR") and "DTSTART:20261008T150000Z" in ics
